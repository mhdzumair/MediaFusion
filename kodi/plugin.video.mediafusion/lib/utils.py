import sys
from urllib import parse

import requests
import xbmc
import xbmcaddon
import xbmcgui

ADDON_HANDLE = int(sys.argv[1])
ADDON = xbmcaddon.Addon()
ADDON_PATH = sys.argv[0]
ADDON_ID = ADDON.getAddonInfo("id")


BASE_URL = ADDON.getSetting("base_url")
SECRET_STR = ADDON.getSetting("secret_string")

if not SECRET_STR:
    xbmcgui.Dialog().notification(
        "MediaFusion",
        "MediaFusion is not configured. Please configure the addon",
        xbmcgui.NOTIFICATION_INFO,
    )
    xbmc.executebuiltin(
        f"RunScript(special://home/addons/{ADDON_ID}/lib/custom_settings_window.py)"
    )
    sys.exit(0)


def fetch_data(url):
    session = requests.session()
    try:
        response = session.get(url)
        response.raise_for_status()
        return response.json()
    except requests.ConnectionError as e:
        xbmc.log(f"Connection failed: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            "MediaFusion", "Connection failed", xbmcgui.NOTIFICATION_ERROR
        )
    except requests.Timeout as e:
        xbmc.log(f"Request timed out: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            "MediaFusion", "Request timed out", xbmcgui.NOTIFICATION_ERROR
        )
    except requests.RequestException as e:
        if e.response is None:
            xbmc.log(f"Request failed: {e}", xbmc.LOGERROR)
            xbmcgui.Dialog().notification(
                "MediaFusion", "Request failed", xbmcgui.NOTIFICATION_ERROR
            )
        elif e.response.status_code == 401:
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
    except Exception as e:
        xbmc.log(f"Failed to fetch data: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            "MediaFusion", "Failed to fetch data", xbmcgui.NOTIFICATION_ERROR
        )


def build_url(action, **params):
    query = parse.urlencode(params)
    return f"{ADDON_PATH}?action={action}&{query}"


def log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[MediaFusion] {message}", level)


def convert_info_hash_to_magnet(info_hash: str, trackers: list):
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    for tracker in trackers:
        if tracker.startswith("tracker:"):
            magnet += f"&tr={tracker.replace('tracker:', '')}"
        elif tracker.startswith("dht:"):
            magnet += f"&dht={tracker.replace('dht:', '')}"

    return magnet


def is_elementum_installed_and_enabled():
    try:
        addon = xbmcaddon.Addon("plugin.video.elementum")
        return True
    except Exception:
        return False
