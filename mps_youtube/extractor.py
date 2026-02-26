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

from . import util
import yt_dlp
from youtubesearchpython import (
    VideosSearch,
    ChannelsSearch,
    PlaylistsSearch,
    Suggestions,
    Playlist,
    playlist_from_channel_id,
    Comments,
    Video,
    Channel,
    ChannelSearch,
)


class VideoInfo:
    """Standardized metadata object for videos."""

    def __init__(self, data: T.Dict[str, T.Any]):
        self._data = data
        self._ytid = data.get("id") or data.get("videoId")
        self.title = data.get("title", "Unknown Title")
        self.author = data.get("uploader") or data.get("channel", {}).get("name")
        self.length = data.get("duration") or 0
        self.view_count = data.get("view_count") or 0
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
    }
    if extra_opts:
        opts.update(extra_opts)
    return opts


def get_video_streams(ytid):
    """Get different video / audio stream formats for a video id."""
    try:
        with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
            info_dict = ydl.extract_info(ytid, download=False)
            return [
                i
                for i in info_dict["formats"]
                if i.get("format_note") != "storyboard"
            ]
    except Exception as e:
        raise ExtractionError(f"Failed to extract streams for {ytid}: {str(e)}")


def download_video(ytid, folder, audio_only=False):
    """Download video or audio to the specified folder."""
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
    """Search for videos and return standard results.
    Tries youtubesearchpython first, falls back to yt-dlp if it crashes.
    """
    try:
        videosSearch = VideosSearch(query, limit=50)
        res = videosSearch.result()
        if not res or "result" not in res:
            return []
        wdata = res["result"]
        for _ in range(pages - 1):
            videosSearch.next()
            res = videosSearch.result()
            if res and "result" in res:
                wdata.extend(res["result"])
        return wdata
    except Exception as e:
        util.dbg("youtubesearchpython failed: %s. Falling back to yt-dlp.", str(e))
        return _search_videos_ytdl(query, pages)


def _search_videos_ytdl(query, pages):
    """Fallback search using yt-dlp."""
    count = pages * 50
    ydl_opts = get_ydl_opts({"extract_flat": True})
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
            results = []
            for entry in info.get("entries", []):
                # Map yt-dlp entry to youtubesearchpython format
                duration = entry.get("duration")
                if duration:
                    h = int(duration // 3600)
                    m = int((duration % 3600) // 60)
                    s = int(duration % 60)
                    if h > 0:
                        duration_str = f"{h}:{m:02d}:{s:02d}"
                    else:
                        duration_str = f"{m}:{s:02d}"
                else:
                    duration_str = "0:00"

                view_count = entry.get("view_count")
                view_count_str = f"{view_count:,} views" if view_count is not None else "?"

                results.append({
                    "type": "video",
                    "id": entry.get("id"),
                    "title": entry.get("title"),
                    "publishedTime": "Unknown",
                    "duration": duration_str,
                    "viewCount": {
                        "text": view_count_str,
                        "short": view_count_str
                    },
                    "channel": {
                        "name": entry.get("uploader") or entry.get("channel"),
                        "id": entry.get("uploader_id") or entry.get("channel_id"),
                    },
                    "link": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}",
                })
            return results
    except Exception as e:
        util.dbg("yt-dlp search fallback failed: %s", str(e))
        return []


def channel_search(query):
    """Search for channels."""
    channelsSearch = ChannelsSearch(query, limit=50, region="US")
    return channelsSearch.result()["result"]


def playlist_search(query):
    """Search for playlists using yt-dlp for better reliability."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={encoded_query}&sp=EgIQAw%3D%3D"
    
    ydl_opts = get_ydl_opts({"extract_flat": True})
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            results = []
            for entry in info.get("entries", []):
                # Map yt-dlp entry to expected format for get_pl_from_json
                results.append({
                    "type": "playlist",
                    "id": entry.get("id"),
                    "title": entry.get("title"),
                    "videoCount": entry.get("playlist_count") or "?",
                    "channel": {
                        "name": entry.get("uploader"),
                        "id": entry.get("uploader_id"),
                    },
                    "publishedAt": None, # Not usually available in flat extract
                    "description": entry.get("description", ""),
                })
            return results
    except Exception as e:
        raise ExtractionError(f"Playlist search failed: {str(e)}")



def get_playlist(playlist_id):
    """Get all videos of a playlist."""
    playlist = Playlist(
        "https://www.youtube.com/playlist?list=%s" % playlist_id
    )
    while playlist.hasMoreVideos:
        playlist.getNextVideos()
    return playlist


def get_video_title_suggestions(query):
    """Get search suggestions."""
    suggestions = Suggestions(language="en", region="US")
    related_searches = suggestions.get(query)["result"]
    if not related_searches:
        return query
    return related_searches[random.randint(0, len(related_searches) - 1)]


def channel_id_from_name(query):
    """Resolve channel ID and name from query."""
    results = channel_search(query)
    if not results:
        return None, None
    channel_info = results[0]
    return channel_info["id"], channel_info["title"]


def all_videos_from_channel(channel_id):
    """Get all videos from a channel."""
    playlist = Playlist(playlist_from_channel_id(channel_id))
    while playlist.hasMoreVideos:
        playlist.getNextVideos()
    return playlist.videos


def search_videos_from_channel(channel_id, query):
    """Search videos within a specific channel."""
    search = ChannelSearch(query, channel_id)
    return search.result()["result"]


def get_comments(video_id):
    """Get video comments."""
    comments = Comments.get(video_id)
    return comments["result"]


def get_video_info(video_id):
    """Get detailed video info including likes/dislikes."""
    try:
        video_info_raw = Video.getInfo(video_id)
        response = return_dislikes(video_id)
        video_info_raw["likes"] = response["likes"]
        video_info_raw["dislikes"] = response["dislikes"]
        video_info_raw["averageRating"] = response["rating"]
        return VideoInfo(video_info_raw)
    except Exception as e:
        raise ExtractionError(f"Can't get video info: {str(e)}")


def return_dislikes(video_id):
    """Fetch dislike counts from Return YouTube Dislike API."""
    url = f"https://returnyoutubedislikeapi.com/votes?videoId={video_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
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
    """Get all playlists belonging to a channel."""
    channel = Channel(channel_id)
    playlists = channel.result["playlists"]
    while channel.has_more_playlists():
        channel.next()
        playlists.extend(channel.result["playlists"])
    return playlists


def get_subtitles(ytid, output_dir):
    """Download and save subtitles for a video."""
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
