#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class AddonError(Exception):
    pass


def normalize_page_url(url):
    if not url:
        raise AddonError("Source page URL is empty.")
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url.lstrip("/")
    if not url.endswith("/"):
        url += "/"
    return url


def http_get(url, referer=None):
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    request = Request(url, headers=headers)
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", "replace")


def extract_stream_id(page_html):
    match = re.search(r'"streamId"\s*:\s*"([^"]+)"', page_html)
    if not match:
        raise AddonError("Could not find streamId on source page.")
    return match.group(1)


def build_player_url(stream_id, page_url):
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


def extract_stream_url(player_html):
    direct = re.search(r'"file"\s*:\s*"([^"]+\.(?:m3u8|mpd)[^"]*)"', player_html)
    if direct:
        return json.loads(f'"{direct.group(1)}"')

    fallback = re.search(r"https?://[^\"'\s<>]+\.(?:m3u8|mpd)[^\"'\s<>]*", player_html)
    if fallback:
        return fallback.group(0)

    raise AddonError("Could not find .m3u8/.mpd stream URL in player output.")


def fetch_stream_url(page_url):
    page_url = normalize_page_url(page_url)
    page_html = http_get(page_url)
    stream_id = extract_stream_id(page_html)
    player_url = build_player_url(stream_id, page_url)
    player_html = http_get(player_url, referer=page_url)
    return extract_stream_url(player_html)
