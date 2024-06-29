import os
import sys
from urllib import parse

import requests
import xbmc
import xbmcaddon
import xbmcgui

ADDON_HANDLE = int(sys.argv[1])
ADDON = xbmcaddon.Addon()
ADDON_PATH = sys.argv[0]
MANIFEST_URL = ADDON.getSetting("manifest_url")
ADDON_ID = ADDON.getAddonInfo("id")

if not MANIFEST_URL:
    xbmcgui.Dialog().notification(
        "MediaFusion",
        "Manifest URL is not set. Please configure the addon",
        xbmcgui.NOTIFICATION_INFO,
    )
    xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    sys.exit(0)


parsed_url = parse.urlparse(MANIFEST_URL)
BASE_URL = f"{parsed_url.scheme}://{parsed_url.netloc}"
try:
    SECRET_STR = parsed_url.path.split("/")[1]
except IndexError:
    xbmcgui.Dialog().notification(
        "MediaFusion",
        "Invalid manifest URL. Please configure the addon",
        xbmcgui.NOTIFICATION_ERROR,
    )
    xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
    sys.exit(0)


def fetch_data(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        if e.response.status_code == 401:
            xbmc.log("Unauthorized request", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(
                "MediaFusion", "Unauthorized request", xbmcgui.NOTIFICATION_ERROR
            )
        elif e.response.status_code == 429:
            xbmc.log("Too many requests, Try again in few seconds", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(
                "MediaFusion",
                "Too many requests, Try again in few seconds",
                xbmcgui.NOTIFICATION_ERROR,
            )
        else:
            xbmc.log(f"Request failed: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(
                "MediaFusion", "Request failed", xbmcgui.NOTIFICATION_ERROR
            )
        return None


def build_url(action, **params):
    query = parse.urlencode(params)
    return f"{ADDON_PATH}?action={action}&{query}"


def add_context_menu_items(li, video_id, catalog_type, catalog_id=None):
    context_menu = [
        (
            "Mark as Watched",
            f"RunPlugin({build_url('mark_watched', video_id=video_id)})",
        ),
        (
            "Mark as Unwatched",
            f"RunPlugin({build_url('mark_unwatched', video_id=video_id)})",
        ),
    ]
    if catalog_id:
        context_menu.insert(
            0,
            (
                "Refresh",
                f"Container.Refresh({build_url('list_catalog', catalog_type=catalog_type, catalog_id=catalog_id)})",
            ),
        )
    li.addContextMenuItems(context_menu)


def log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[MediaFusion] {message}", level)
