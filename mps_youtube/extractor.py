import concurrent.futures
import json
import os
import glob
import random
import re
import time
import urllib.parse
from urllib.parse import parse_qs, urlparse
import urllib.request
import urllib.error
import typing as T
from datetime import datetime, timezone

from . import g, paths, util
import yt_dlp
from .innertube import Innertube
from .streamurlfetcher import StreamURLFetcher

_innertube = Innertube()
_stream_fetcher = StreamURLFetcher(_innertube)


def _format_published_time(entry):
    """Helper to get a standard ISO8601 string from data (yt-dlp format)."""
    ts = entry.get("timestamp")
    if ts:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ud = entry.get("upload_date")
    if ud and len(ud) == 8:
        try:
            return f"{ud[:4]}-{ud[4:6]}-{ud[6:]}T00:00:00Z"
        except Exception:
            pass

    return None


class VideoInfo:
    """Standardized metadata object for videos."""

    def __init__(self, data: T.Dict[str, T.Any]):
        self._data = data
        self._ytid = data.get("id") or data.get("videoId")
        self.title = data.get("title") or "Unknown Title"
        self.author = data.get("uploader") or data.get("channel", {}).get("name") or "Unknown Author"
        
        duration = data.get("duration") or data.get("lengthSeconds")
        self.length = int(duration) if duration else 0
        
        views = data.get("view_count") or data.get("viewCount")
        self.view_count = int(views) if views else 0
        
        self.likes = data.get("likes") or 0
        self.dislikes = data.get("dislikes") or 0
        self.rating = data.get("averageRating") or 0
        
        expires = data.get("expires")
        self.expiry = time.time() + (expires if expires is not None else 3600)
        self.fresh = True

    @property
    def ytid(self):
        return self._ytid

    @property
    def videoid(self):
        return self._ytid

    def __getitem__(self, key):
        return self._data.get(key)

    def get(self, key, default=None):
        return self._data.get(key, default)


class ExtractionError(Exception):
    """Base exception for extraction issues."""

    pass


class MyLogger:
    def __init__(self, print_info=False):
        self.print_info = print_info

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            self.info(msg)

    def info(self, msg):
        if self.print_info:
            print(msg)

    def warning(self, msg):
        pass

    def error(self, msg):
        print(msg)


def get_ydl_opts(extra_opts: T.Optional[T.Dict[str, T.Any]] = None) -> T.Dict[str, T.Any]:
    """Factory for standard yt-dlp options."""
    cookiefile = _resolve_cookiefile()
    cookies_from_browser = _resolve_cookies_from_browser()
    visitor_data = _resolve_visitor_data()
    opts = {
        "logger": MyLogger(),
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        # Required for newer YouTube JS challenge/signature flows.
        "remote_components": "ejs:github",
        "user_agent": util.BROWSER_USER_AGENT,
        "http_headers": util.web_headers(),
        "format": "best/bestvideo+bestaudio",
        "ignore_no_formats_error": True,
    }
    if visitor_data:
        opts["extractor_args"] = {
            "youtube": {
                "visitor_data": [visitor_data],
                # Recommended by yt-dlp for visitor-data flow.
                "player_skip": ["webpage", "configs"],
                "player_client": ["default", "web"],
            },
            "youtubetab": {"skip": ["webpage"]},
        }
    elif cookiefile:
        opts["cookiefile"] = cookiefile
    elif cookies_from_browser:
        opts["cookiesfrombrowser"] = cookies_from_browser
    if extra_opts:
        opts.update(extra_opts)
    return opts


def _resolve_cookiefile() -> T.Optional[str]:
    """Find a usable cookie file for authenticated yt-dlp requests."""
    try:
        candidates = [
            g.cookies_file,
            os.path.join(paths.get_config_dir(), "cookies.txt"),
        ]
        for path in candidates:
            if not path:
                continue
            expanded = os.path.expanduser(path)
            if os.path.isfile(expanded):
                return expanded
    except AttributeError:
        pass
    return None


def _resolve_cookies_from_browser() -> T.Optional[T.Tuple[str, ...]]:
    """Parse --cookies-from-browser into yt-dlp's expected tuple form."""
    try:
        value = g.cookies_from_browser
        if not value:
            return None

        parts = value.split(":", 1)
        browser = parts[0].strip()
        if not browser:
            return None

        if len(parts) == 1:
            return (browser,)

        profile = parts[1].strip()
        return (browser, profile) if profile else (browser,)
    except AttributeError:
        return None


def _resolve_visitor_data() -> T.Optional[str]:
    """Return CLI-provided YouTube visitor data token, if any."""
    try:
        value = g.visitor_data
        if not value:
            return None
        value = value.strip()
        return value or None
    except AttributeError:
        return None


def get_video_streams(ytid):
    """Get video / audio stream formats for a video id using Innertube with yt-dlp fallback."""
    cookiefile = _resolve_cookiefile()
    cookies_from_browser = _resolve_cookies_from_browser()
    visitor_data = _resolve_visitor_data()

    try:
        streams = _innertube.get_video_streams(ytid)
        if streams:
            return streams
        util.dbg("Innertube returned no streams for %s", ytid)
    except Exception as e:
        util.dbg("Innertube get_video_streams failed: %s", str(e))

    # No-cookie path: try StreamURLFetcher before full yt-dlp extraction.
    if not (cookiefile or cookies_from_browser or visitor_data):
        try:
            player_response = _innertube.get_video_info(ytid)
            streams = _stream_fetcher.get_all(ytid, player_response=player_response)
            if streams:
                util.dbg("StreamURLFetcher resolved streams for %s", ytid)
                return streams
            util.dbg("StreamURLFetcher returned no streams for %s", ytid)
        except Exception as e:
            util.dbg("StreamURLFetcher failed for %s: %s", ytid, str(e))

    # Fallback to yt-dlp
    url = f"https://www.youtube.com/watch?v={ytid}"
    try:
        with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            return _extract_streams_from_info(info_dict)
    except Exception as e:
        raise ExtractionError(f"Failed to extract streams for {ytid}: {str(e)}")


def _extract_streams_from_info(info_dict):
    """Normalize yt-dlp extraction output into a list of format dictionaries."""
    formats = info_dict.get("formats") or []
    formats = [i for i in formats if i.get("format_note") != "storyboard"]

    if not formats:
        requested_formats = info_dict.get("requested_formats") or []
        formats = [i for i in requested_formats if i.get("url")]

    if not formats and info_dict.get("url"):
        formats = [
            {
                "url": info_dict.get("url"),
                "ext": info_dict.get("ext"),
                "resolution": info_dict.get("resolution"),
                "width": info_dict.get("width"),
                "height": info_dict.get("height"),
                "vcodec": info_dict.get("vcodec", "none"),
                "acodec": info_dict.get("acodec", "none"),
                "tbr": info_dict.get("tbr"),
                "abr": info_dict.get("abr"),
                "filesize": info_dict.get("filesize"),
                "filesize_approx": info_dict.get("filesize_approx"),
            }
        ]

    return formats


def download_video(ytid, folder, audio_only=False):
    """Download video or audio using yt-dlp."""
    ytdl_format_options = {
        "outtmpl": os.path.join(folder, "%(title)s-%(id)s.%(ext)s")
    }
    if audio_only:
        ytdl_format_options["format"] = "bestaudio/best"
        ytdl_format_options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    try:
        with yt_dlp.YoutubeDL(get_ydl_opts(ytdl_format_options)) as ydl:
            ydl.download(["https://www.youtube.com/watch?v=%s" % ytid])
            return True
    except Exception as e:
        raise ExtractionError(f"Download failed for {ytid}: {str(e)}")


def search_videos(query, pages):
    """Search for videos using internal Innertube engine."""
    try:
        results = _innertube.search(query, limit=pages * 20)
        # Exclude channel results and ensure we only have videos
        filtered = [
            r for r in results 
            if r.get("type") == "video"
        ]
        return filtered
    except Exception as e:
        util.dbg("Innertube search failed: %s", str(e))
        return []


def channel_search(query):
    """Search for channels using internal Innertube engine."""
    try:
        return _innertube.channel_search(query)
    except Exception as e:
        util.dbg("Innertube channel search failed: %s", str(e))
        return []


def playlist_search(query):
    """Search for playlists using internal Innertube engine."""
    try:
        return _innertube.search(query, limit=20, params='EgIQAw%3D%3D')
    except Exception as e:
        util.dbg("Innertube playlist search failed: %s", str(e))
        return []


def get_playlist(playlist_id):
    """Get all videos of a playlist using internal Innertube engine."""
    try:
        return _innertube.get_playlist(playlist_id)
    except Exception as e:
        util.dbg("Innertube get_playlist failed: %s", str(e))
        raise ExtractionError(f"Playlist extraction failed: {str(e)}")


def get_video_title_suggestions(query):
    """Get search suggestions using direct Google API call."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&hl=en&q={encoded_query}"
    req = urllib.request.Request(
        url, headers=util.web_headers({"Accept-Language": "en-US,en;q=0.9"})
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            if not isinstance(data, list) or len(data) < 2 or not data[1]:
                return query
            return random.choice(data[1])
    except Exception:
        return query


def channel_id_from_name(query):
    """Resolve channel ID and name from query."""
    results = channel_search(query)
    if not results:
        return None, None
    channel_info = results[0]
    return channel_info["id"], channel_info["title"]


def all_videos_from_channel(channel_id):
    """Get all videos from a channel using internal Innertube engine."""
    try:
        return _innertube.get_channel_videos(channel_id)
    except Exception as e:
        util.dbg("Innertube get_channel_videos failed: %s", str(e))
        return []


def search_videos_from_channel(channel_id, query):
    """Search videos within a specific channel."""
    return search_videos(f"{query} channel:{channel_id}", 1)


def get_comments(video_id):
    """Get video comments using yt-dlp."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = get_ydl_opts({"getcomments": True, "extract_flat": False})
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            comments = []
            for comment in info.get("comments", []):
                comments.append({
                    "id": comment.get("id"),
                    "author": comment.get("author"),
                    "authorId": comment.get("author_id"),
                    "content": comment.get("text"),
                    "publishedAt": comment.get("timestamp"),
                    "votes": comment.get("like_count"),
                })
            return comments
    except Exception as e:
        util.dbg("Failed to get comments for %s: %s", video_id, str(e))
        return []


def get_video_info(video_id):
    """Get detailed video info using yt-dlp."""
    try:
        ydl_opts = get_ydl_opts()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_id, download=False)
            dislikes = return_dislikes(video_id)
            info["likes"] = dislikes["likes"]
            info["dislikes"] = dislikes["dislikes"]
            info["averageRating"] = dislikes["rating"]
            return VideoInfo(info)
    except Exception as e:
        raise ExtractionError(f"Can't get video info: {str(e)}")


def return_dislikes(video_id):
    """Fetch dislike counts from Return YouTube Dislike API."""
    url = f"https://returnyoutubedislikeapi.com/votes?videoId={video_id}"
    req = urllib.request.Request(
        url, headers=util.web_headers({"Accept-Language": "en-US,en;q=0.9"})
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            return {
                "likes": data.get("likes", 0),
                "dislikes": data.get("dislikes", 0),
                "rating": data.get("rating", 0),
            }
    except Exception:
        return {"likes": 0, "dislikes": 0, "rating": 0}


def extract_video_id(url: str) -> str:
    """Extract video ID from various YouTube URL formats."""
    idregx = re.compile(r"[\w-]{11}$")
    url = str(url).strip()

    if idregx.match(url):
        return url

    if "://" not in url:
        url = "//" + url
    parsedurl = urlparse(url)
    if parsedurl.netloc in (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "gaming.youtube.com",
    ):
        query = parse_qs(parsedurl.query)
        if "v" in query and idregx.match(query["v"][0]):
            return query["v"][0]
    elif parsedurl.netloc in ("youtu.be", "www.youtu.be"):
        vidid = parsedurl.path.split("/")[-1] if parsedurl.path else ""
        if idregx.match(vidid):
            return vidid

    raise ValueError(f"Could not extract video ID from {url}")


def all_playlists_from_channel(channel_id):
    """Get all playlists belonging to a channel using internal Innertube engine."""
    try:
        return _innertube.get_channel_playlists(channel_id)
    except Exception as e:
        util.dbg("Innertube get_channel_playlists failed: %s", str(e))
        return []


def get_subtitles(ytid, output_dir):
    """Download and save subtitles for a video using yt-dlp."""
    if output_dir.endswith("/"):
        output_dir = output_dir[:-1]
    outtmpl = f"{output_dir}/subtitles/{ytid}"

    existing_subtitles = glob.glob(os.path.join(outtmpl + "*.vtt"))
    if existing_subtitles:
        return existing_subtitles[0]

    url = f"https://www.youtube.com/watch?v={ytid}"
    ydl_opts = get_ydl_opts({
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "vtt",
        "outtmpl": outtmpl,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            subtitles = info_dict.get("subtitles", {})
            available_formats = list(subtitles.keys())
            lang = available_formats[0] if available_formats else "en"
            ydl.params["subtitleslangs"] = [lang]
            ydl.download([url])
            path = f"{outtmpl}.{lang}.vtt"
            return path if os.path.isfile(path) else None
    except Exception:
        return None
