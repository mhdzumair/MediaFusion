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
MANIFEST_URL = ADDON.getSetting("manifest_url")
ADDON_ID = ADDON.getAddonInfo("id")

# Initialize requests_cache with CachedSession
cache_file = xbmcvfs.translatePath(
    f"special://profile/addon_data/{ADDON_ID}/cache.sqlite"
)
session = requests_cache.CachedSession(
    cache_name=cache_file, backend="sqlite", cache_control=True
)


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


def remove_cache(url):
    session.cache.delete_url(url)


def fetch_data(url, force_refresh=False):
    if force_refresh:
        with session.cache_disabled():
            response = session.get(url)
    else:
        response = session.get(url)

    try:
        response.raise_for_status()
        if "Cache-Control" in response.headers:
            cache_control = response.headers["Cache-Control"]
            if "no-store" in cache_control or "no-cache" in cache_control:
                remove_cache(response.url)
        return response.json()
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
        return None


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
