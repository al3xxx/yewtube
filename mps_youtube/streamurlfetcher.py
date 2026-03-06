"""Lightweight stream URL fetcher built on Innertube player responses."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .innertube import Innertube


class StreamURLFetcher:
    """Resolve stream URLs from Innertube player response formats."""

    def __init__(self, innertube: Optional[Innertube] = None):
        self._innertube = innertube or Innertube()

    @staticmethod
    def _normalize_format(fmt: Dict[str, Any], url: str) -> Dict[str, Any]:
        mime = fmt.get("mimeType", "")
        ext = mime.split(";")[0].split("/")[-1] if mime else None
        quality = fmt.get("qualityLabel") or fmt.get("quality")
        return {
            "url": url,
            "ext": ext,
            "quality": quality,
            "resolution": quality,
            "width": fmt.get("width"),
            "height": fmt.get("height"),
            "vcodec": "none" if "audio" in mime else mime,
            "acodec": (
                "none"
                if "video" in mime and "audio" not in mime
                else mime
            ),
            "tbr": (fmt.get("bitrate", 0) or 0) / 1000,
            "filesize": int(fmt.get("contentLength", 0) or 0),
            "itag": fmt.get("itag"),
        }

    def get_all(
        self, video_id: str, player_response: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Return all deciphered formats from player response."""
        response = player_response or self._innertube.get_video_info(video_id)
        streaming_data = response.get("streamingData", {})
        formats = (streaming_data.get("formats") or []) + (
            streaming_data.get("adaptiveFormats") or []
        )

        output: List[Dict[str, Any]] = []
        for fmt in formats:
            try:
                url = self._innertube._decipher_format_url(video_id, fmt)
            except Exception:
                url = None
            if not url:
                continue
            output.append(self._normalize_format(fmt, url))
        return output

    def get(
        self,
        video_id: str,
        itag: int,
        player_response: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Return one URL for a specific itag if available."""
        for fmt in self.get_all(video_id, player_response=player_response):
            if fmt.get("itag") == itag:
                return fmt.get("url")
        return None
