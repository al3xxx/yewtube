"""Lightweight stream URL fetcher built on yt-dlp."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import yt_dlp
from . import extractor


class StreamURLFetcher:
    """Resolve stream URLs using yt-dlp."""

    def __init__(self, *args, **kwargs):
        pass

    def get_all(
        self, video_id: str, player_response: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Return all deciphered formats from player response."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL(extractor.get_ydl_opts()) as ydl:
                info_dict = ydl.extract_info(url, download=False)
                return extractor._extract_streams_from_info(info_dict)
        except Exception:
            return []

    def get(
        self,
        video_id: str,
        itag: int,
        player_response: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Return one URL for a specific itag if available."""
        for fmt in self.get_all(video_id):
            if fmt.get("itag") == itag:
                return fmt.get("url")
        return None
