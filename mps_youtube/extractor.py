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
import youtubesearchpython as ysp


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
        if isinstance(duration, dict):
            duration = duration.get("secondsText")
        self.length = int(duration) if duration else 0
        
        views = data.get("view_count") or data.get("viewCount")
        if isinstance(views, dict):
            views = views.get("text")
        self.view_count = int(views) if views and str(views).isdigit() else 0
        
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
    opts = {
        "logger": MyLogger(),
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "remote_components": "ejs:github",
        "user_agent": util.BROWSER_USER_AGENT,
        "http_headers": util.web_headers(),
        "ignore_no_formats_error": True,
    }
    if extra_opts:
        opts.update(extra_opts)
    return opts


def get_video_streams(ytid):
    """Get video / audio stream formats for a video id using yt-dlp (Primary for reliability)."""
    url = f"https://www.youtube.com/watch?v={ytid}"
    try:
        with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            return _extract_streams_from_info(info_dict)
    except Exception as e:
        util.dbg("yt-dlp get_video_streams failed for %s: %s", ytid, str(e))
        
        # Fallback to YSP
        try:
            video_data = ysp.Video.get(ytid)
            if video_data:
                streaming_data = video_data.get("streamingData", {})
                formats = streaming_data.get("formats", []) + streaming_data.get("adaptiveFormats", [])
                if formats:
                    results = []
                    for f in formats:
                        url_val = f.get("url")
                        if not url_val: continue
                        mime = f.get("mimeType", "")
                        vcodec = "none" if "audio" in mime and "video" not in mime else mime.split(";")[0]
                        acodec = "none" if "video" in mime and "audio" not in mime else mime.split(";")[0]
                        results.append({
                            "url": url_val,
                            "ext": mime.split(";")[0].split("/")[-1] if mime else "mp4",
                            "quality": f.get("qualityLabel") or f.get("quality"),
                            "resolution": f.get("qualityLabel") or f.get("quality"),
                            "width": f.get("width"),
                            "height": f.get("height"),
                            "vcodec": vcodec,
                            "acodec": acodec,
                            "tbr": (f.get("bitrate", 0) or 0) / 1000,
                            "filesize": int(f.get("contentLength", 0) or 0),
                            "itag": f.get("itag"),
                        })
                    return results
        except Exception:
            pass
            
        raise ExtractionError(f"Failed to extract streams for {ytid}: {str(e)}")


def _extract_streams_from_info(info_dict):
    """Normalize yt-dlp extraction output into a list of format dictionaries."""
    raw_formats = info_dict.get("formats") or []
    
    # If no formats, check requested_formats (often case for some clients)
    if not raw_formats:
        raw_formats = info_dict.get("requested_formats") or []

    # Last resort fallback
    if not raw_formats and info_dict.get("url"):
        raw_formats = [info_dict]

    formats = []
    for f in raw_formats:
        if f.get("format_note") == "storyboard":
            continue
        
        # Ensure critical fields are present and normalized
        norm_f = f.copy()
        if "itag" not in norm_f and "format_id" in f:
            try:
                norm_f["itag"] = int(f["format_id"])
            except (ValueError, TypeError):
                pass

        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        norm_f["vcodec"] = str(vcodec).lower() if vcodec else "none"
        norm_f["acodec"] = str(acodec).lower() if acodec else "none"
        norm_f["resolution"] = f.get("resolution") or f.get("qualityLabel") or f.get("quality")
        formats.append(norm_f)

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
    """Search for videos using youtubesearchpython (Discovery is fast and has age metadata)."""
    try:
        search = ysp.VideosSearch(query, limit=pages * 20)
        results = search.result()
        if results and "result" in results:
            return results["result"]
    except Exception as e:
        util.dbg("YSP search failed: %s", str(e))
    return []


def channel_search(query):
    """Search for channels using youtubesearchpython."""
    try:
        search = ysp.ChannelsSearch(query, limit=20)
        results = search.result()
        if results and "result" in results:
            return results["result"]
    except Exception as e:
        util.dbg("YSP channel search failed: %s", str(e))
    return []


def playlist_search(query):
    """Search for playlists using youtubesearchpython."""
    try:
        search = ysp.PlaylistsSearch(query, limit=20)
        results = search.result()
        if results and "result" in results:
            return results["result"]
    except Exception as e:
        util.dbg("YSP playlist search failed: %s", str(e))
    return []


def get_playlist(playlist_id):
    """Get all videos of a playlist using youtubesearchpython."""
    try:
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        return ysp.Playlist(url)
    except Exception as e:
        util.dbg("YSP get_playlist failed: %s", str(e))
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


import httpx

def all_videos_from_channel(channel_id):
    """Get all videos from a channel using Innertube browse for full metadata."""
    try:
        # Innertube browse endpoint for channel videos
        url = "https://www.youtube.com/youtubei/v1/browse"
        headers = util.web_headers({"Content-Type": "application/json"})
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20210224.06.00"
                }
            },
            "browseId": channel_id,
            "params": "EgZ2aWRlb3PyBgQKAjoA" # Videos tab
        }
        
        with httpx.Client(headers=headers) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            videos = []
            
            # Try to get channel title
            channel_name = "Unknown"
            metadata = data.get("metadata", {}).get("channelMetadataRenderer", {})
            if metadata:
                channel_name = metadata.get("title", "Unknown")

            # Helper to find all videoRenderers recursively
            def find_videos(obj):
                if isinstance(obj, dict):
                    renderer = obj.get("videoRenderer") or obj.get("gridVideoRenderer") or obj.get("compactVideoRenderer")
                    if renderer:
                        v = renderer
                        
                        # Extract publishedTime
                        pub_time = None
                        if "publishedTimeText" in v:
                            pub_time = v["publishedTimeText"].get("simpleText")
                        
                        # Extract duration
                        duration = "0:00"
                        if "lengthText" in v:
                            duration = v["lengthText"].get("simpleText")
                        
                        # Extract viewCount
                        views = "?"
                        if "viewCountText" in v:
                            views = v["viewCountText"].get("simpleText") or v["viewCountText"].get("runs", [{}])[0].get("text", "?")

                        videos.append({
                            "type": "video",
                            "id": v.get("videoId"),
                            "title": v.get("title", {}).get("runs", [{}])[0].get("text", "Unknown"),
                            "publishedTime": pub_time,
                            "duration": duration,
                            "viewCount": {"text": views},
                            "channel": {
                                "name": v.get("ownerText", {}).get("runs", [{}])[0].get("text", channel_name),
                                "id": channel_id
                            }
                        })
                    else:
                        for val in obj.values():
                            find_videos(val)
                elif isinstance(obj, list):
                    for item in obj:
                        find_videos(item)

            find_videos(data)
            return videos

    except Exception as e:
        util.dbg("all_videos_from_channel failed: %s", str(e))
    return []


def search_videos_from_channel(channel_id, query):
    """Search videos within a specific channel."""
    return search_videos(f"{query} channel:{channel_id}", 1)


def get_comments(video_id):
    """Get video comments using youtubesearchpython."""
    try:
        comments = ysp.Comments(video_id)
        res = comments.result()
        if res and "result" in res:
            return res["result"]
    except Exception as e:
        util.dbg("YSP get_comments failed: %s", str(e))
    return []


def get_video_info(video_id):
    """Get detailed video info using yt-dlp (Primary for metadata accuracy)."""
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
        util.dbg("yt-dlp get_video_info failed for %s: %s", video_id, e)
        # Fallback to YSP
        try:
            info = ysp.Video.getInfo(video_id)
            if info:
                dislikes = return_dislikes(video_id)
                info["likes"] = dislikes["likes"]
                info["dislikes"] = dislikes["dislikes"]
                info["averageRating"] = dislikes["rating"]
                return VideoInfo(info)
        except Exception:
            pass
    
    raise ExtractionError(f"Can't get video info for {video_id}")


def return_dislikes(video_id):
    """Fetch dislike counts from Return YouTube Dislike API."""
    url = f"https://returnyoutubedislikeapi.com/votes?videoId={video_id}"
    req = urllib.request.Request(
        url, headers=util.web_headers({"Accept": "application/json"})
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
    """Get all playlists belonging to a channel using youtubesearchpython."""
    try:
        # request_type 'EglwbGF5bGlzdHMYAyABcAA%3D' is for playlists
        channel = ysp.Channel(channel_id)
        return channel.result["result"] if channel.result else []
    except Exception as e:
        util.dbg("YSP all_playlists_from_channel failed: %s", str(e))
        return []


def get_subtitles(ytid, output_dir):
    """Download and save subtitles for a video."""
    return None
