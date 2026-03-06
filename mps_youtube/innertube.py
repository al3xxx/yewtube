import json
import re
import time
import copy
import os
import hashlib
import urllib.request
import http.cookiejar
from typing import Any, Dict, List, Optional, Union, Iterable, Mapping
from urllib.parse import urlencode, parse_qs, urlparse, urlunparse
from urllib.parse import urlencode as qs_urlencode

import httpx
from yt_dlp.jsinterp import JSInterpreter
from . import util, g

# Core constants and payloads
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
API_KEY = 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'
YOUTUBE_ORIGIN = "https://www.youtube.com"
WEB_CLIENT_NAME = "WEB"
WEB_CLIENT_VERSION = "2.20260227.00.00"
WEB_CLIENT_HEADER_NAME = "1"

REQUEST_PAYLOAD = {
    "context": {
        "client": {
            "clientName": "ANDROID_VR",
            "clientVersion": "1.50.2",
            "hl": "en",
            "gl": "US",
            "utcOffsetMinutes": 0,
        },
        "user": {
            "lockedSafetyMode": False
        }
    }
}

# Element keys
VIDEO_ELEMENT_KEY = 'videoRenderer'
CHANNEL_ELEMENT_KEY = 'channelRenderer'
PLAYLIST_ELEMENT_KEY = 'playlistRenderer'
SHELF_ELEMENT_KEY = 'shelfRenderer'
ITEM_SECTION_KEY = 'itemSectionRenderer'
CONTINUATION_ITEM_KEY = 'continuationItemRenderer'
RICH_ITEM_KEY = 'richItemRenderer'
PLAYLIST_VIDEO_KEY = 'playlistVideoRenderer'
PLAYLIST_PRIMARY_INFO_KEY = 'playlistSidebarPrimaryInfoRenderer'

# Paths
CONTENT_PATH = ['contents', 'twoColumnSearchResultsRenderer', 'primaryContents', 'sectionListRenderer', 'contents']
FALLBACK_CONTENT_PATH = ['contents', 'twoColumnSearchResultsRenderer', 'primaryContents', 'richGridRenderer', 'contents']

# Search modes
SEARCH_MODE_PLAYLISTS = 'EgIQAw%3D%3D'
SEARCH_MODE_CHANNELS = 'EgIQAg%3D%3D'

class Innertube:
    """Internal high-fidelity implementation of YouTube Innertube API."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.base_url = "https://www.youtube.com/youtubei/v1"
        self.cookies = {'CONSENT': 'YES+1'}
        self._player_js_url: Optional[str] = None
        self._player_js_code: Optional[str] = None
        self._sig_func_cache: Dict[str, Any] = {}
        self._nsig_func_cache: Dict[str, Any] = {}

    def _load_youtube_cookies(self) -> Dict[str, str]:
        """Load YouTube cookies from configured cookie file."""
        cookies = self.cookies.copy()
        if not g.cookies_file or not os.path.isfile(g.cookies_file):
            return cookies

        try:
            cj = http.cookiejar.MozillaCookieJar(g.cookies_file)
            cj.load(ignore_discard=True, ignore_expires=True)
            for cookie in cj:
                domain = cookie.domain or ""
                if "youtube.com" in domain:
                    cookies[cookie.name] = cookie.value
        except Exception as e:
            util.dbg(f"Failed to load cookies for Innertube: {e}")

        return cookies

    def _sapisid_from_cookies(self, cookies: Dict[str, str]) -> Optional[str]:
        """Return best available SAPISID cookie for auth header generation."""
        return cookies.get("SAPISID") or cookies.get("__Secure-3PAPISID")

    def _build_auth_headers(self, cookies: Dict[str, str]) -> Dict[str, str]:
        """Build authenticated YouTube request headers from cookie data."""
        sapisid = self._sapisid_from_cookies(cookies)
        if not sapisid:
            return {}

        timestamp = str(int(time.time()))
        digest_src = f"{timestamp} {sapisid} {YOUTUBE_ORIGIN}"
        digest = hashlib.sha1(digest_src.encode("utf-8")).hexdigest()
        return {
            "Authorization": f"SAPISIDHASH {timestamp}_{digest}",
            "X-Origin": YOUTUBE_ORIGIN,
            "X-Youtube-Client-Name": WEB_CLIENT_HEADER_NAME,
            "X-Youtube-Client-Version": WEB_CLIENT_VERSION,
        }

    def _build_payload(self, data: Dict[str, Any], authenticated: bool) -> Dict[str, Any]:
        """Build Innertube payload and switch context when authenticated."""
        payload = copy.deepcopy(REQUEST_PAYLOAD)
        if authenticated:
            payload["context"]["client"]["clientName"] = WEB_CLIENT_NAME
            payload["context"]["client"]["clientVersion"] = WEB_CLIENT_VERSION

        if g.visitor_data:
            payload["context"]["client"]["visitorData"] = g.visitor_data

        if "query" in data:
            payload["query"] = data["query"]
        if "params" in data:
            payload["params"] = data["params"]
        if "browseId" in data:
            payload["browseId"] = data["browseId"]
        if "continuation" in data:
            payload["continuation"] = data["continuation"]
        if "videoId" in data:
            payload["videoId"] = data["videoId"]

        return payload

    def _get_player_js_url(self) -> Optional[str]:
        """Resolve current YouTube player JS URL from iframe API."""
        if self._player_js_url:
            return self._player_js_url

        req = urllib.request.Request(
            "https://www.youtube.com/iframe_api",
            headers=util.web_headers({"User-Agent": USER_AGENT}),
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            body = response.read().decode("utf-8", "ignore")

        version_match = re.search(r"player\\?/([0-9a-fA-F]{8})\\?/", body)
        if not version_match:
            version_match = re.search(r"([0-9a-fA-F]{8})\\?", body)
        if not version_match:
            return None

        version = version_match.group(1).replace("\\", "")
        self._player_js_url = (
            f"https://www.youtube.com/s/player/{version}/"
            "player_ias.vflset/en_US/base.js"
        )
        return self._player_js_url

    def _get_player_js_code(self) -> Optional[str]:
        """Download and cache player JS source."""
        if self._player_js_code:
            return self._player_js_code

        player_js_url = self._get_player_js_url()
        if not player_js_url:
            return None

        req = urllib.request.Request(
            player_js_url,
            headers=util.web_headers({"User-Agent": USER_AGENT}),
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            self._player_js_code = response.read().decode("utf-8", "ignore")
        return self._player_js_code

    def _build_sig_transform(self, jscode: str):
        funcname = None
        patterns = (
            r"\b(?P<var>[a-zA-Z0-9_$]+)&&\((?P=var)=(?P<sig>[a-zA-Z0-9_$]{2,})\(decodeURIComponent\((?P=var)\)\)",
            r"(?P<sig>[a-zA-Z0-9_$]+)\s*=\s*function\(\s*(?P<arg>[a-zA-Z0-9_$]+)\s*\)\s*\{\s*(?P=arg)\s*=\s*(?P=arg)\.split\(\s*\"\"\s*\)\s*;\s*[^}]+;\s*return\s+(?P=arg)\.join\(\s*\"\"\s*\)",
            r"(?:\b|[^a-zA-Z0-9_$])(?P<sig>[a-zA-Z0-9_$]{2,})\s*=\s*function\(\s*a\s*\)\s*\{\s*a\s*=\s*a\.split\(\s*\"\"\s*\)(?:;[a-zA-Z0-9_$]{2}\.[a-zA-Z0-9_$]{2}\(a,\d+\))?",
            r"\b[cs]\s*&&\s*[adf]\.set\([^,]+\s*,\s*encodeURIComponent\s*\(\s*(?P<sig>[a-zA-Z0-9$]+)\(",
            r"\b[a-zA-Z0-9]+\s*&&\s*[a-zA-Z0-9]+\.set\([^,]+\s*,\s*encodeURIComponent\s*\(\s*(?P<sig>[a-zA-Z0-9$]+)\(",
            r"\bm=(?P<sig>[a-zA-Z0-9$]{2,})\(decodeURIComponent\(h\.s\)\)",
            r"(\"|')signature\\1\s*,\s*(?P<sig>[a-zA-Z0-9$]+)\(",
            r"\.sig\|\|(?P<sig>[a-zA-Z0-9$]+)\(",
            r"yt\.akamaized\.net/\)\s*\|\|\s*.*?\s*[cs]\s*&&\s*[adf]\.set\([^,]+\s*,\s*(?:encodeURIComponent\s*\()?\s*(?P<sig>[a-zA-Z0-9$]+)\(",
            r"\b[cs]\s*&&\s*[adf]\.set\([^,]+\s*,\s*(?P<sig>[a-zA-Z0-9$]+)\(",
            r"\bc\s*&&\s*[a-zA-Z0-9]+\.set\([^,]+\s*,\s*\([^)]*\)\s*\(\s*(?P<sig>[a-zA-Z0-9$]+)\(",
        )
        for pattern in patterns:
            m = re.search(pattern, jscode)
            if m:
                funcname = m.group("sig")
                break
        if not funcname:
            return None

        jsi = JSInterpreter(jscode)
        extracted = jsi.extract_function(funcname)
        return lambda s: extracted([s])

    def _decrypt_signature(self, s: str, video_id: str, player_url: Optional[str] = None) -> str:
        """Old-yt-dlp-style signature decipher helper."""
        cache_key = str(len(s))
        transform = self._sig_func_cache.get(cache_key)
        if not transform:
            jscode = self._get_player_js_code()
            if not jscode:
                raise ValueError("Unable to load player JS for signature decipher")
            transform = self._build_sig_transform(jscode)
            if not transform:
                raise ValueError("Unable to parse signature function")
            self._sig_func_cache[cache_key] = transform
        return transform(s)

    def _extract_n_function_name(self, jscode: str) -> Optional[str]:
        match = re.search(
            r'''(?x)
            (?:
                \.get\("n"\)\)&&\(b=|
                b=String\.fromCharCode\(110\),c=a\.get\(b\)\)&&\(c=|
                [a-zA-Z0-9_$]+\s*&&\s*[a-zA-Z0-9_$]+\.set\([^,]+\s*,\s*encodeURIComponent\(
            )(?P<nfunc>[a-zA-Z0-9_$]+)(?:\[(?P<idx>\d+)\])?\([a-zA-Z]\)
            ''',
            jscode,
        )
        if match:
            funcname = match.group("nfunc")
            idx = match.group("idx")
            if idx:
                arr_match = re.search(
                    rf"var\s+{re.escape(funcname)}\s*=\s*(\[[^\]]+\])\s*[,;]",
                    jscode,
                )
                if arr_match:
                    try:
                        names = json.loads(arr_match.group(1).replace("'", '"'))
                        return names[int(idx)]
                    except Exception:
                        return None
            return funcname

        fallback = re.search(
            r'''(?xs)
            ;\s*(?P<name>[a-zA-Z0-9_$]+)\s*=\s*function\([a-zA-Z0-9_$]+\)
            \s*\{(?:(?!};).)+?return\s*(?P<q>["'])[\w-]+_w8_(?P=q)\s*\+\s*[a-zA-Z0-9_$]+
            ''',
            jscode,
        )
        return fallback.group("name") if fallback else None

    def _decrypt_nsig(self, s: str, video_id: str, player_url: Optional[str] = None) -> str:
        """Old-yt-dlp-style n-parameter decipher helper."""
        if not s:
            return s
        transform = self._nsig_func_cache.get("n")
        if not transform:
            jscode = self._get_player_js_code()
            if not jscode:
                raise ValueError("Unable to load player JS for n decipher")
            funcname = self._extract_n_function_name(jscode)
            if not funcname:
                raise ValueError("Unable to locate n function")
            jsi = JSInterpreter(jscode)
            extracted = jsi.extract_function(funcname)
            transform = lambda n: extracted([n])
            self._nsig_func_cache["n"] = transform
        ret = transform(s)
        if not ret or ret.endswith(s):
            return s
        return ret

    def _decipher_format_url(self, video_id: str, stream_obj: Dict[str, Any]) -> Optional[str]:
        """Resolve direct media URL from signatureCipher/cipher payload."""
        url = stream_obj.get("url")
        if url:
            return url

        cipher = stream_obj.get("signatureCipher") or stream_obj.get("cipher")
        if not cipher:
            return None

        sc = parse_qs(cipher)
        url_list = sc.get("url")
        if not url_list:
            return None
        url = url_list[0]

        sig_list = sc.get("s")
        if sig_list:
            sig = self._decrypt_signature(sig_list[0], video_id, self._get_player_js_url())
            sig_param = (sc.get("sp") or ["signature"])[0]
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{sig_param}={sig}"

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if query.get("n"):
            nval = query["n"][0]
            query["n"] = [self._decrypt_nsig(nval, video_id, self._get_player_js_url())]
            url = urlunparse(parsed._replace(query=qs_urlencode(query, doseq=True)))

        return url

    def _post(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Make a POST request to Innertube API."""
        url = f"{self.base_url}/{endpoint}?{urlencode({'key': API_KEY})}"

        cookies = self._load_youtube_cookies()
        auth_headers = self._build_auth_headers(cookies)
        authenticated = bool(auth_headers)
        payload = self._build_payload(data, authenticated=authenticated)

        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Origin": YOUTUBE_ORIGIN,
            "Referer": YOUTUBE_ORIGIN + "/",
        }
        headers.update(auth_headers)
        if g.visitor_data:
            headers["X-Goog-Visitor-Id"] = g.visitor_data

        if authenticated:
            util.dbg(
                "Innertube authenticated request enabled for endpoint=%s", endpoint
            )

        try:
            with httpx.Client(timeout=self.timeout, cookies=cookies) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            util.dbg(f"Innertube request failed: {e}")
            raise

    def search(self, query: str, limit: int = 20, params: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search using the official Innertube search endpoint with continuation support."""
        results = []
        continuation_token = None
        max_pages = 10
        page_count = 0
        
        while len(results) < limit and page_count < max_pages:
            page_count += 1
            data = {"query": query}
            if params:
                data["params"] = params
            if continuation_token:
                data["continuation"] = continuation_token

            response = self._post("search", data)
            
            # Extract results and next continuation token
            new_results, next_token = self._parse_search_response(response, limit - len(results))
            results.extend(new_results)
            
            if not next_token or not new_results:
                break
            
            continuation_token = next_token
            
        return results

    def _parse_search_response(self, response: Dict[str, Any], limit: int) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Extract results and the next continuation token from a search response."""
        results = []
        
        # Possible paths for search results in the response
        paths = [
            ["contents", "twoColumnSearchResultsRenderer", "primaryContents", "sectionListRenderer", "contents"],
            ["contents", "twoColumnSearchResultsRenderer", "primaryContents", "richGridRenderer", "contents"],
            ["contents", "sectionListRenderer", "contents"],
            ["onResponseReceivedCommands", 0, "appendContinuationItemsAction", "continuationItems"],
        ]
        
        source = None
        for path in paths:
            source = self._getValue(response, path)
            if source:
                break
        
        if not source:
            return results, None

        continuation_token = None
        for element in source:
            if ITEM_SECTION_KEY in element:
                item_source = self._getValue(element, [ITEM_SECTION_KEY, 'contents'])
                if item_source:
                    for item in item_source:
                        self._parse_element(item, results, limit)
            elif CONTINUATION_ITEM_KEY in element:
                continuation_token = self._getValue(element, [CONTINUATION_ITEM_KEY, 'continuationEndpoint', 'continuationCommand', 'token'])
            else:
                self._parse_element(element, results, limit)
            
            if len(results) >= limit:
                break
        
        # If we reached the limit and haven't found a token yet, try to find it in the last element
        if not continuation_token and source:
            last = source[-1]
            if CONTINUATION_ITEM_KEY in last:
                continuation_token = self._getValue(last, [CONTINUATION_ITEM_KEY, 'continuationEndpoint', 'continuationCommand', 'token'])

        return results, continuation_token

    def get_playlist(self, playlist_id: str) -> Any:
        """Retrieve playlist metadata and videos."""
        browse_id = "VL" + playlist_id if not playlist_id.startswith("VL") else playlist_id
        response = self._post("browse", {"browseId": browse_id})
        return self._parse_playlist_response(response)

    def channel_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search specifically for channels."""
        return self.search(query, limit=limit, params=SEARCH_MODE_CHANNELS)

    def get_channel_videos(self, channel_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve videos from a channel's videos tab with continuation support."""
        results = []
        continuation_token = None
        known_channel_name = None
        known_channel_id = None
        max_pages = 10
        page_count = 0
        
        while len(results) < limit and page_count < max_pages:
            page_count += 1
            data = {"browseId": channel_id}
            if continuation_token:
                data["continuation"] = continuation_token
            else:
                data["params"] = "EgZ2aWRlb3PyBgQKAjoA" # Videos tab

            response = self._post("browse", data)
            new_results, next_token = self._parse_browse_response(response, limit - len(results))
            
            # Failsafe: if path-based parsing found nothing on first page but there are videos
            if not new_results and not continuation_token:
                def find_videos_recursive(data, out):
                    if len(out) >= limit: return
                    if isinstance(data, dict):
                        for k in [VIDEO_ELEMENT_KEY, 'compactVideoRenderer', 'gridVideoRenderer']:
                            if k in data:
                                out.append(self._parse_video(data[k]))
                                if len(out) >= limit: return
                        for v in data.values():
                            find_videos_recursive(v, out)
                    elif isinstance(data, list):
                        for i in data:
                            find_videos_recursive(i, out)
                
                find_videos_recursive(response, new_results)

            # Capture channel info
            if not known_channel_name and new_results:
                for v in new_results:
                    if v.get("channel", {}).get("name"):
                        known_channel_name = v["channel"]["name"]
                        known_channel_id = v["channel"]["id"]
                        break
            
            # Inject known channel info
            if known_channel_name:
                for v in new_results:
                    if not v.get("channel", {}).get("name"):
                        v["channel"] = {"name": known_channel_name, "id": known_channel_id}

            results.extend(new_results)
            if not next_token or not new_results:
                break
            continuation_token = next_token
            
        return results

    def _parse_browse_response(self, response: Dict[str, Any], limit: int) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Extract videos and continuation token from a browse response."""
        # Extract channel info for injection
        header = self._getValue(response, ["header", "c4TabbedHeaderRenderer"])
        metadata = self._getValue(response, ["metadata", "channelMetadataRenderer"])
        
        channel_name = (
            self._getValue(header, ["title"]) or 
            self._getValue(metadata, ["title"]) or
            self._getValue(response, ["header", "pageHeaderRenderer", "pageTitle"])
        )
        channel_id = (
            self._getValue(header, ["channelId"]) or
            self._getValue(metadata, ["externalId"])
        )

        videos = []
        continuation_token = None
        
        # Possible paths for items
        items = None
        
        # Path 1: standard tabs
        tabs = (
            self._getValue(response, ["contents", "twoColumnBrowseResultsRenderer", "tabs"]) or
            self._getValue(response, ["contents", "singleColumnBrowseResultsRenderer", "tabs"])
        ) or []
        
        for tab in tabs:
            items = (
                self._getValue(tab, ["tabRenderer", "content", "richGridRenderer", "contents"]) or
                self._getValue(tab, ["tabRenderer", "content", "sectionListRenderer", "contents", 0, "itemSectionRenderer", "contents", 0, "gridRenderer", "items"]) or
                self._getValue(tab, ["tabRenderer", "content", "sectionListRenderer", "contents", 0, "itemSectionRenderer", "contents"])
            )
            if items:
                break
        
        # Path 2: continuation actions
        if not items:
            actions = (
                self._getValue(response, ["onResponseReceivedActions"]) or
                self._getValue(response, ["onResponseReceivedCommands"])
            ) or []
            for action in actions:
                items = (
                    self._getValue(action, ["appendContinuationItemsAction", "continuationItems"]) or
                    self._getValue(action, ["reloadContinuationItemsCommand", "continuationItems"])
                )
                if items:
                    break
        
        # Path 3: direct continuation contents
        if not items:
            items = (
                self._getValue(response, ["continuationContents", "gridContinuation", "items"]) or
                self._getValue(response, ["continuationContents", "sectionListContinuation", "contents"]) or
                self._getValue(response, ["continuationContents", "itemSectionContinuation", "contents"])
            )

        if not items:
            return videos, None

        for item in items:
            video = (
                self._getValue(item, ["richItemRenderer", "content", "videoRenderer"]) or
                self._getValue(item, ["richItemRenderer", "content", "compactVideoRenderer"]) or
                item.get("videoRenderer") or
                item.get("compactVideoRenderer")
            )
            
            if video:
                vdata = self._parse_video(video)
                if not vdata.get("channel", {}).get("name") and channel_name:
                    vdata["channel"] = {"name": channel_name, "id": channel_id}
                videos.append(vdata)
            
            elif CONTINUATION_ITEM_KEY in item:
                continuation_token = self._getValue(item, [CONTINUATION_ITEM_KEY, 'continuationEndpoint', 'continuationCommand', 'token'])
            
            if len(videos) >= limit:
                break
        
        # Check for continuation token at the end of items if not found
        if not continuation_token and items:
            last = items[-1]
            if CONTINUATION_ITEM_KEY in last:
                continuation_token = self._getValue(last, [CONTINUATION_ITEM_KEY, 'continuationEndpoint', 'continuationCommand', 'token'])

        # Path 4: Aggressive Recursive search for nextContinuationData
        if not continuation_token:
            def find_next_token(data):
                if isinstance(data, dict):
                    # Check for token directly
                    token = (
                        self._getValue(data, ["nextContinuationData", "continuation"]) or
                        self._getValue(data, ["continuationEndpoint", "continuationCommand", "token"]) or
                        data.get("continuation") # Sometimes it's just 'continuation'
                    )
                    if token and isinstance(token, str) and len(token) > 20: 
                        return token
                    
                    # Search children
                    for k, v in data.items():
                        if k in ["header", "metadata", "trackingParams", "accessibility"]: continue
                        res = find_next_token(v)
                        if res: return res
                elif isinstance(data, list):
                    for i in data:
                        res = find_next_token(i)
                        if res: return res
                return None
            
            continuation_token = find_next_token(response)

        return videos, continuation_token

    def get_channel_playlists(self, channel_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve playlists from a channel's playlists tab."""
        data = {"browseId": channel_id, "params": "EglwbGF5bGlzdHPyBgQKAjoA"}
        response = self._post("browse", data)
        return self._parse_channel_playlists(response, limit)

    def get_video_info(self, video_id: str) -> Dict[str, Any]:
        """Retrieve raw player response for a video."""
        data = {"videoId": video_id}
        return self._post("player", data)

    def get_video_streams(self, video_id: str) -> List[Dict[str, Any]]:
        """Extract stream metadata from player response."""
        resp = self.get_video_info(video_id)
        streaming_data = resp.get("streamingData", {})
        formats = streaming_data.get("formats", []) + streaming_data.get("adaptiveFormats", [])
        
        results = []
        for f in formats:
            try:
                url = self._decipher_format_url(video_id, f)
            except Exception as e:
                util.dbg("Innertube decipher failed for %s: %s", video_id, str(e))
                url = None
            if not url:
                continue

            quality = f.get("qualityLabel") or f.get("quality")
            results.append({
                "url": url,
                "ext": f.get("mimeType", "").split(";")[0].split("/")[-1],
                "quality": quality,
                "resolution": quality,
                "width": f.get("width"),
                "height": f.get("height"),
                "vcodec": "none" if "audio" in f.get("mimeType", "") else f.get("mimeType", ""),
                "acodec": "none" if "video" in f.get("mimeType", "") and "audio" not in f.get("mimeType", "") else f.get("mimeType", ""),
                "tbr": f.get("bitrate", 0) / 1000,
                "filesize": int(f.get("contentLength", 0)),
            })
        return results

    def _getValue(self, source: Any, path: List[Union[str, int]]) -> Any:
        """Ported from YSP: Robustly extract values from nested dicts."""
        value = source
        for key in path:
            if isinstance(key, str):
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    return None
            elif isinstance(key, int):
                if isinstance(value, (list, tuple)) and len(value) > key:
                    value = value[key]
                else:
                    return None
        return value

    def _parse_element(self, element: Dict[str, Any], results: List[Dict[str, Any]], limit: int) -> None:
        """Helper to parse a single result element."""
        if len(results) >= limit:
            return

        if VIDEO_ELEMENT_KEY in element:
            results.append(self._parse_video(element[VIDEO_ELEMENT_KEY]))
        elif 'compactVideoRenderer' in element:
            results.append(self._parse_video(element['compactVideoRenderer']))
        elif 'gridVideoRenderer' in element:
            results.append(self._parse_video(element['gridVideoRenderer']))
        elif CHANNEL_ELEMENT_KEY in element:
            results.append(self._parse_channel(element[CHANNEL_ELEMENT_KEY]))
        elif 'compactChannelRenderer' in element:
            results.append(self._parse_channel(element['compactChannelRenderer']))
        elif PLAYLIST_ELEMENT_KEY in element:
            results.append(self._parse_playlist_item(element[PLAYLIST_ELEMENT_KEY]))
        elif 'compactPlaylistRenderer' in element:
            results.append(self._parse_playlist_item(element['compactPlaylistRenderer']))
        elif SHELF_ELEMENT_KEY in element:
            shelf_items = (
                self._getValue(element, [SHELF_ELEMENT_KEY, 'content', 'verticalListRenderer', 'items']) or
                self._getValue(element, [SHELF_ELEMENT_KEY, 'content', 'horizontalListRenderer', 'items'])
            )
            if shelf_items:
                for item in shelf_items:
                    self._parse_element(item, results, limit)
        elif RICH_ITEM_KEY in element:
            rich_content = self._getValue(element, [RICH_ITEM_KEY, 'content'])
            if rich_content:
                self._parse_element(rich_content, results, limit)
        elif 'richSectionRenderer' in element:
            rich_content = self._getValue(element, ['richSectionRenderer', 'content', 'richShelfRenderer', 'items'])
            if rich_content:
                for item in rich_content:
                    self._parse_element(item, results, limit)

    def _parse_video(self, video: Dict[str, Any]) -> Dict[str, Any]:
        """Ported from YSP _getVideoComponent with fallbacks for compact renderers."""
        vid_id = video.get('videoId')
        title = (
            self._getValue(video, ['title', 'runs', 0, 'text']) or 
            self._getValue(video, ['title', 'simpleText'])
        )
        published_time = (
            self._getValue(video, ['publishedTimeText', 'simpleText']) or
            self._getValue(video, ['publishedTimeText', 'runs', 0, 'text'])
        )
        duration = (
            self._getValue(video, ['lengthText', 'simpleText']) or
            self._getValue(video, ['lengthText', 'runs', 0, 'text'])
        )
        view_count_text = (
            self._getValue(video, ['viewCountText', 'simpleText']) or
            self._getValue(video, ['viewCountText', 'runs', 0, 'text'])
        )
        short_view_count_text = (
            self._getValue(video, ['shortViewCountText', 'simpleText']) or
            self._getValue(video, ['shortViewCountText', 'runs', 0, 'text'])
        )

        return {
            'type': 'video',
            'id': vid_id,
            'title': title,
            'publishedTime': published_time,
            'duration': duration,
            'viewCount': {
                'text': view_count_text,
                'short': short_view_count_text,
            },
            'channel': {
                'name': (
                    self._getValue(video, ['ownerText', 'runs', 0, 'text']) or 
                    self._getValue(video, ['shortBylineText', 'runs', 0, 'text'])
                ),
                'id': (
                    self._getValue(video, ['ownerText', 'runs', 0, 'navigationEndpoint', 'browseEndpoint', 'browseId']) or
                    self._getValue(video, ['shortBylineText', 'runs', 0, 'navigationEndpoint', 'browseEndpoint', 'browseId'])
                ),
            },
            'link': 'https://www.youtube.com/watch?v=' + vid_id if vid_id else None
        }

    def _parse_channel(self, channel: Dict[str, Any]) -> Dict[str, Any]:
        """Ported from YSP _getChannelComponent with fallbacks."""
        chan_id = (
            channel.get('channelId') or 
            self._getValue(channel, ['navigationEndpoint', 'browseEndpoint', 'browseId'])
        )
        title = (
            self._getValue(channel, ['title', 'runs', 0, 'text']) or 
            self._getValue(channel, ['title', 'simpleText']) or 
            self._getValue(channel, ['displayName', 'runs', 0, 'text'])
        )
        return {
            'type': 'channel',
            'id': chan_id,
            'title': title,
            'videoCount': (
                self._getValue(channel, ['videoCountText', 'runs', 0, 'text']) or
                self._getValue(channel, ['videoCountText', 'simpleText']) or
                self._getValue(channel, ['subscriberCountText', 'simpleText'])
            ),
            'subscribers': (
                self._getValue(channel, ['subscriberCountText', 'runs', 0, 'text']) or
                self._getValue(channel, ['subscriberCountText', 'simpleText'])
            ),
            'link': 'https://www.youtube.com/channel/' + chan_id if chan_id else None
        }

    def _parse_playlist_item(self, playlist: Dict[str, Any]) -> Dict[str, Any]:
        """Ported from YSP _getPlaylistComponent with fallbacks."""
        pl_id = playlist.get('playlistId')
        title = (
            self._getValue(playlist, ['title', 'simpleText']) or 
            self._getValue(playlist, ['title', 'runs', 0, 'text'])
        )
        return {
            'type': 'playlist',
            'id': pl_id,
            'title': title,
            'videoCount': (
                self._getValue(playlist, ['videoCount']) or
                self._getValue(playlist, ['videoCountText', 'runs', 0, 'text']) or
                self._getValue(playlist, ['videoCountShortText', 'simpleText'])
            ),
            'channel': {
                'name': self._getValue(playlist, ['shortBylineText', 'runs', 0, 'text']),
                'id': self._getValue(playlist, ['shortBylineText', 'runs', 0, 'navigationEndpoint', 'browseEndpoint', 'browseId']),
            },
            'link': 'https://www.youtube.com/playlist?list=' + pl_id if pl_id else None
        }

    def _parse_channel_playlists(self, response: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        """Simple extraction for channel playlists."""
        # Extract channel info for injection
        header = self._getValue(response, ["header", "c4TabbedHeaderRenderer"])
        metadata = self._getValue(response, ["metadata", "channelMetadataRenderer"])
        
        channel_name = (
            self._getValue(header, ["title"]) or 
            self._getValue(metadata, ["title"]) or
            self._getValue(response, ["header", "pageHeaderRenderer", "pageTitle"])
        )
        channel_id = (
            self._getValue(header, ["channelId"]) or
            self._getValue(metadata, ["externalId"])
        )

        playlists = []
        tab_renderer = self._getValue(response, ["contents", "twoColumnBrowseResultsRenderer", "tabs"])
        if not tab_renderer:
            return playlists
            
        items = None
        for tab in tab_renderer:
            items = (
                self._getValue(tab, ["tabRenderer", "content", "sectionListRenderer", "contents", 0, "itemSectionRenderer", "contents", 0, "gridRenderer", "items"])
                or self._getValue(tab, ["tabRenderer", "content", "sectionListRenderer", "contents", 0, "itemSectionRenderer", "contents", 0, "shelfRenderer", "content", "horizontalListRenderer", "items"])
            )
            if items:
                break
        
        if not items:
            return playlists

        for item in items:
            pl = (
                item.get("gridPlaylistRenderer") 
                or item.get("playlistRenderer")
                or item.get("compactPlaylistRenderer")
            )
            if pl:
                pdata = self._parse_playlist_item(pl)
                # Inject channel info if missing
                if not pdata.get("channel", {}).get("name") and channel_name:
                    pdata["channel"] = {"name": channel_name, "id": channel_id}
                playlists.append(pdata)
                if len(playlists) >= limit:
                    break
        return playlists

    def _parse_playlist_response(self, response: Dict[str, Any]) -> Any:
        """Ported from YSP PlaylistCore.__getComponents."""
        sidebar = self._getValue(response, ["sidebar", "playlistSidebarRenderer", "items"]) or []
        primary = sidebar[0].get(PLAYLIST_PRIMARY_INFO_KEY) if sidebar else {}
        
        # Path from YSP: contents -> twoColumnBrowseResultsRenderer -> tabs -> 0 -> tabRenderer -> content -> sectionListRenderer -> contents -> 0 -> itemSectionRenderer -> contents -> 0 -> playlistVideoListRenderer -> contents
        video_elements = self._get_first_value(response, ["contents", "twoColumnBrowseResultsRenderer", "tabs", None, "tabRenderer", "content", "sectionListRenderer", "contents", None, "itemSectionRenderer", "contents", None, "playlistVideoListRenderer", "contents"]) or []
        
        videos = []
        for element in video_elements:
            video = element.get(PLAYLIST_VIDEO_KEY)
            if not video:
                continue
            
            vid_id = self._getValue(video, ["videoId"])
            videos.append({
                "id": vid_id,
                "title": self._getValue(video, ["title", "runs", 0, "text"]),
                "duration": self._getValue(video, ["lengthText", "simpleText"]),
                "channel": {
                    "name": self._getValue(video, ["shortBylineText", "runs", 0, "text"]),
                    "id": self._getValue(video, ["shortBylineText", "runs", 0, "navigationEndpoint", "browseEndpoint", "browseId"]),
                },
                "link": "https://www.youtube.com/watch?v=" + vid_id if vid_id else None,
                "isPlayable": video.get("isPlayable", True)
            })

        class MockPlaylist:
            def __init__(self, info, videos):
                self.info = {"info": info}
                self.videos = videos

        info = {
            "title": self._getValue(primary, ["title", "runs", 0, "text"]),
            "id": self._getValue(primary, ["title", "runs", 0, "navigationEndpoint", "watchEndpoint", "playlistId"]),
            "videoCount": self._getValue(primary, ["stats", 0, "runs", 0, "text"]),
        }
        
        return MockPlaylist(info, videos)

    def _get_first_value(self, source: dict, path: List[Any]) -> Any:
        """Helper for wildcards in paths (None in path list)."""
        def get_value_ex(src, p):
            if not p:
                yield src
                return
            key = p[0]
            upcoming = p[1:]
            if key is None:
                # Wildcard: find first list element that has the next key
                if isinstance(src, list):
                    for v in src:
                        yield from get_value_ex(v, upcoming)
                elif isinstance(src, dict):
                    for v in src.values():
                        yield from get_value_ex(v, upcoming)
            else:
                val = self._getValue(src, [key])
                if val is not None:
                    yield from get_value_ex(val, upcoming)

        for val in get_value_ex(source, path):
            if val is not None:
                return val
        return None
