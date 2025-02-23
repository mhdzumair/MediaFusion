import sqlite3
import sys
from urllib import parse

import requests
import requests_cache
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

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


def remove_cache(url):
    session = get_request_session()
    session.cache.delete_url(url)


def fetch_data(url, force_refresh=False):
    session = get_request_session()
    try:
        if force_refresh:
            with session.cache_disabled():
                response = session.get(url)
        else:
            response = session.get(url)
        xbmc.log(f"Response from Caching: {response.from_cache}.", xbmc.LOGINFO)

        response.raise_for_status()
        if "Cache-Control" in response.headers:
            cache_control = response.headers["Cache-Control"]
            if "no-store" in cache_control or "no-cache" in cache_control:
                remove_cache(response.url)
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


def get_request_session():
    # Initialize requests_cache with CachedSession
    cache_file = xbmcvfs.translatePath(
        f"special://profile/addon_data/{ADDON_ID}/cache.sqlite"
    )
    try:
        session = requests_cache.CachedSession(
            cache_name=cache_file, backend="sqlite", cache_control=True
        )
    except sqlite3.OperationalError as e:
        xbmc.log(f"Failed to setup cache: {e}", xbmc.LOGERROR)
        try:
            fallback_cache_file = xbmcvfs.translatePath(
                f"special://temp/{ADDON_ID}_cache.sqlite"
            )
            session = requests_cache.CachedSession(
                cache_name=fallback_cache_file, backend="sqlite", cache_control=True
            )
            xbmc.log(
                f"Using fallback cache location: {fallback_cache_file}", xbmc.LOGINFO
            )
        except sqlite3.OperationalError:
            xbmc.log("Fallback to in-memory cache", xbmc.LOGWARNING)
            session = requests_cache.CachedSession(
                cache_name="mediafusion_request", backend="memory", cache_control=True
            )
        xbmcgui.Dialog().notification(
            "MediaFusion", f"Cache setup issue: {str(e)}", xbmcgui.NOTIFICATION_WARNING
        )
    return session
