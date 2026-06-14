#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import time
from urllib.error import URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


__all__ = ["AddonError", "normalize_page_url", "fetch_stream_url"]


class AddonError(Exception):
    pass


def normalize_page_url(url):
    if not url:
        raise AddonError("Source page URL is empty.")
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url.lstrip("/")
        parsed = urlparse(url)
    if not parsed.path.endswith("/"):
        url = urlunparse(parsed._replace(path=parsed.path + "/"))
    return url


def _http_get(url, referer=None):
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    request = Request(url, headers=headers)
    last_exc = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=15) as response:
                return response.read().decode("utf-8", "replace")
        except URLError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1)
    raise last_exc


def _extract_stream_id(page_html):
    # Matches both JS assignment (streamId = 'x') and JSON property ("streamId":"x")
    match = re.search(r"""streamId['"]\s*[=:]\s*['"]([^'"]+)['"]""", page_html)
    if not match:
        match = re.search(r"""streamId\s*=\s*['"]([^'"]+)['"]""", page_html)
    if not match:
        raise AddonError("Could not find streamId on source page.")
    return match.group(1)


def _build_player_url(stream_id, page_url):
    query = urlencode(
        {
            "video": stream_id,
            "autostart": "false",
            "embedded": "0",
            "mute": "false",
            "sourceUrl": page_url,
        }
    )
    return f"https://player.mediaklikk.hu/playernew/player.php?{query}"


def _extract_stream_url(player_html):
    """Return (stream_url, widevine_license_url_or_None).

    The playlist may contain [bumper, live-DASH-DRM, bumper].  We skip items
    whose file path contains 'Bumper' and prefer the first non-bumper entry.
    """
    playlist_match = re.search(r'"playlist"\s*:\s*(\[.+?\])\s*\}', player_html, re.DOTALL)
    if playlist_match:
        try:
            playlist = json.loads(playlist_match.group(1))
            for item in playlist:
                file_url = item.get("file", "")
                if not file_url or "Bumper" in file_url:
                    continue
                license_url = None
                drm = item.get("drm", {})
                if drm.get("widevine", {}).get("url"):
                    license_url = drm["widevine"]["url"]
                return file_url, license_url
        except (ValueError, KeyError):
            pass

    # Fallback: grab the first mpd/m3u8 that isn't a bumper
    for m in re.finditer(r'"file"\s*:\s*"([^"]+\.(?:m3u8|mpd)[^"]*)"', player_html):
        url = json.loads(f'"{m.group(1)}"')
        if "Bumper" not in url:
            return url, None

    raise AddonError("Could not find a non-bumper stream URL in player output.")


def fetch_stream_url(page_url):
    """Return (stream_url, widevine_license_url_or_None)."""
    page_url = normalize_page_url(page_url)
    page_html = _http_get(page_url)
    stream_id = _extract_stream_id(page_html)
    player_url = _build_player_url(stream_id, page_url)
    player_html = _http_get(player_url, referer=page_url)
    return _extract_stream_url(player_html)
