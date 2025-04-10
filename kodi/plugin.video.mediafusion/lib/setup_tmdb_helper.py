import os.path

import xbmcaddon
import xbmcgui
import xbmc
import xbmcvfs

addon = xbmcaddon.Addon("plugin.video.mediafusion")
addon_path = addon.getAddonInfo("path")

# Check if TMDB Helper is installed
try:
    xbmcaddon.Addon("plugin.video.themoviedb.helper")
except Exception:
    xbmc.log("TMDB Helper is not installed", xbmc.LOGERROR)
    xbmcgui.Dialog().notification(
        "MediaFusion", "TMDB Helper is not installed", xbmcgui.NOTIFICATION_ERROR
    )
    exit()

# Check if MediaFusion player is installed "mediafusion.select.json" in
home_path = xbmcvfs.translatePath("special://home")
player_path = os.path.join(
    home_path, "userdata/addon_data/plugin.video.themoviedb.helper/players"
)
player_file = os.path.join(player_path, "mediafusion.select.json")
if os.path.exists(player_path):
    if os.path.exists(player_file):
        xbmc.log("MediaFusion player is already installed", xbmc.LOGINFO)
        xbmcgui.Dialog().notification(
            "MediaFusion",
            "MediaFusion player is already installed",
            xbmcgui.NOTIFICATION_INFO,
        )
        exit()

xbmcvfs.mkdir(player_path)
xbmcvfs.copy(
    os.path.join(addon_path, "resources/player", "mediafusion.select.json"),
    player_file,
)
xbmc.log("MediaFusion player is installed", xbmc.LOGINFO)
xbmcgui.Dialog().notification(
    "MediaFusion",
    "Successfully Copied the MediaFusion player to TMDB Helper",
    xbmcgui.NOTIFICATION_INFO,
)
