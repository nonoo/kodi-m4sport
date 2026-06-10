#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from urllib.parse import parse_qs, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
LIB_PATH = os.path.join(ADDON_PATH, "resources", "lib")
if LIB_PATH not in sys.path:
    sys.path.append(LIB_PATH)

from m4sport_core import fetch_stream_url, normalize_page_url  # noqa: E402


def log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def plugin_url(base_url, query):
    return f"{base_url}?{urlencode(query)}"


def list_root(handle, base_url, channel_name):
    item = xbmcgui.ListItem(label=f"{channel_name} (Live)")
    item.setInfo("video", {"title": f"{channel_name} (Live)"})
    item.setProperty("IsPlayable", "true")
    play_url = plugin_url(base_url, {"action": "play"})
    xbmcplugin.addDirectoryItem(handle=handle, url=play_url, listitem=item, isFolder=False)
    xbmcplugin.endOfDirectory(handle)


def resolve_play(handle, page_url, channel_name):
    stream_url = fetch_stream_url(page_url)
    list_item = xbmcgui.ListItem(label=channel_name, path=stream_url)
    list_item.setInfo("video", {"title": channel_name})
    xbmcplugin.setResolvedUrl(handle, True, list_item)
    log(f"Resolved stream URL: {stream_url}")


def run():
    handle = int(sys.argv[1])
    base_url = sys.argv[0]
    params = parse_qs(sys.argv[2][1:]) if len(sys.argv) > 2 and sys.argv[2] else {}

    action = params.get("action", [""])[0]
    page_url = normalize_page_url(
        ADDON.getSetting("source_page_url") or "https://mediaklikk.hu/elo/mtv4live/"
    )
    channel_name = ADDON.getSetting("channel_name") or "M4 Sport"

    if action == "play":
        resolve_play(handle, page_url, channel_name)
    else:
        list_root(handle, base_url, channel_name)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        log(f"Plugin failed: {exc}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            ADDON.getAddonInfo("name"),
            f"Failed to resolve stream: {exc}",
            xbmcgui.NOTIFICATION_ERROR,
            4000,
        )
        xbmcplugin.endOfDirectory(int(sys.argv[1]), succeeded=False)
