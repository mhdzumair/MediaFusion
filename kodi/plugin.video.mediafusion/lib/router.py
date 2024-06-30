import sys
from urllib import parse

import xbmc
import xbmcgui
import xbmcplugin

from .utils import (
    fetch_data,
    build_url,
    ADDON_HANDLE,
    BASE_URL,
    SECRET_STR,
    add_context_menu_items,
    log,
    convert_info_hash_to_magnet,
    is_elementum_installed_and_enabled,
)


def list_categories():
    manifest_data = fetch_data(f"{BASE_URL}/{SECRET_STR}/manifest.json")
    if not manifest_data:
        return

    catalogs = manifest_data.get("catalogs", [])
    if not catalogs:
        log("No catalogs enabled. Make sure to configure correctly", xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            "MediaFusion",
            "No catalogs enabled. Make sure to configure correctly",
            xbmcgui.NOTIFICATION_ERROR,
        )
        return

    for catalog in catalogs:
        if any(extra["name"] == "search" for extra in catalog.get("extra", [])):
            continue

        url = build_url(
            "list_catalog", catalog_type=catalog["type"], catalog_id=catalog["id"]
        )
        li = xbmcgui.ListItem(label=catalog["name"])
        xbmcplugin.addDirectoryItem(
            handle=ADDON_HANDLE, url=url, listitem=li, isFolder=True
        )
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_catalogs(params):
    log(f"Loading {params['catalog_type']} videos...", xbmc.LOGINFO)
    skip = int(params.get("skip", 0))
    url = f"{BASE_URL}/{SECRET_STR}/catalog/{params['catalog_type']}/{params['catalog_id']}/skip={skip}.json"
    response = fetch_data(url)
    if not response:
        return

    videos = response.get("metas", [])
    if not videos:
        xbmcgui.Dialog().notification(
            "MediaFusion", "No videos available", xbmcgui.NOTIFICATION_ERROR
        )
        return

    content_type = "movies" if params["catalog_type"] == "movie" else "tvshows"
    xbmcplugin.setContent(ADDON_HANDLE, content_type)

    for video in videos:
        if params["catalog_type"] == "series":
            url = build_url(
                "list_seasons",
                catalog_type=params["catalog_type"],
                video_id=video["id"],
            )
        else:
            url = build_url(
                "get_streams", video_id=video["id"], catalog_type=params["catalog_type"]
            )
        li = xbmcgui.ListItem(label=video["name"])
        tags = li.getVideoInfoTag()
        tags.setUniqueID(
            video["id"], type="imdb" if video["id"].startswith("tt") else "mf"
        )
        tags.setTitle(video["name"])
        tags.setPlot(video.get("description", ""))
        tags.setRating(float(video.get("imdbRating", 0)))
        if video.get("releaseInfo"):
            tags.setYear(int(video.get("releaseInfo").strip("--")))
        tags.setGenres(video.get("genres", []))

        li.setArt(
            {
                "thumb": video.get("poster", ""),
                "poster": video.get("poster", ""),
                "fanart": video.get("poster", ""),
                "icon": video.get("poster", ""),
                "banner": video.get("background", ""),
                "landscape": video.get("background", ""),
            }
        )

        add_context_menu_items(
            li, video["id"], params["catalog_type"], params["catalog_id"]
        )
        xbmcplugin.addDirectoryItem(
            handle=ADDON_HANDLE, url=url, listitem=li, isFolder=True
        )

    next_url = build_url(
        "list_catalog",
        catalog_type=params["catalog_type"],
        catalog_id=params["catalog_id"],
        skip=skip + len(videos),
    )
    li = xbmcgui.ListItem(label="Next Page")
    xbmcplugin.addDirectoryItem(
        handle=ADDON_HANDLE, url=next_url, listitem=li, isFolder=True
    )

    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_seasons(params):
    url = f"{BASE_URL}/{SECRET_STR}/meta/{params['catalog_type']}/{params['video_id']}.json"
    response = fetch_data(url)
    if not response:
        return

    meta_data = response.get("meta", {})
    videos = meta_data.get("videos", [])
    if not videos:
        xbmcgui.Dialog().notification(
            "MediaFusion", "No seasons available", xbmcgui.NOTIFICATION_ERROR
        )
        return

    available_seasons = set(video["season"] for video in videos)

    for season in available_seasons:
        url = build_url(
            "list_episodes",
            catalog_type=params["catalog_type"],
            video_id=params["video_id"],
            season=season,
        )
        li = xbmcgui.ListItem(label=f"Season {season}")
        tags = li.getVideoInfoTag()
        tags.setUniqueID(
            meta_data["id"], type="imdb" if meta_data["id"].startswith("tt") else "mf"
        )
        tags.setTitle(meta_data["name"])
        tags.setPlot(meta_data.get("description", ""))
        tags.setRating(float(meta_data.get("imdbRating", 0)))
        if meta_data.get("releaseInfo"):
            tags.setYear(int(meta_data.get("releaseInfo").strip("--")))
        tags.setGenres(meta_data.get("genres", []))
        tags.setTvShowTitle(meta_data["name"])
        tags.setSeason(season)

        li.setArt(
            {
                "thumb": meta_data.get("poster", ""),
                "poster": meta_data.get("poster", ""),
                "fanart": meta_data.get("poster", ""),
                "icon": meta_data.get("poster", ""),
                "banner": meta_data.get("background", ""),
                "landscape": meta_data.get("background", ""),
            }
        )

        add_context_menu_items(li, params["video_id"], params["catalog_type"])
        xbmcplugin.addDirectoryItem(
            handle=ADDON_HANDLE, url=url, listitem=li, isFolder=True
        )

    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_episodes(params):
    url = f"{BASE_URL}/{SECRET_STR}/meta/{params['catalog_type']}/{params['video_id']}.json"
    response = fetch_data(url)
    if not response:
        return

    meta_data = response.get("meta", {})
    videos = meta_data.get("videos", [])
    if not videos:
        xbmcgui.Dialog().notification(
            "MediaFusion", "No episodes available", xbmcgui.NOTIFICATION_ERROR
        )
        return

    for video in videos:
        if video["season"] != int(params["season"]):
            continue

        url = build_url(
            "get_streams",
            video_id=f"{params['video_id']}:{video['season']}:{video['episode']}",
            catalog_type=params["catalog_type"],
        )
        li = xbmcgui.ListItem(label=video["title"])
        tags = li.getVideoInfoTag()
        tags.setUniqueID(
            meta_data["id"], type="imdb" if meta_data["id"].startswith("tt") else "mf"
        )
        tags.setTitle(video["title"])
        tags.setPlot(meta_data.get("description", ""))
        tags.setRating(float(meta_data.get("imdbRating", 0)))
        if meta_data.get("releaseInfo"):
            tags.setYear(int(meta_data.get("releaseInfo").strip("--")))
        tags.setGenres(meta_data.get("genres", []))
        tags.setTvShowTitle(video["title"])
        tags.setSeason(int(video["season"]))
        tags.setEpisode(int(video["episode"]))

        li.setArt(
            {
                "thumb": meta_data.get("poster", ""),
                "poster": meta_data.get("poster", ""),
                "fanart": meta_data.get("poster", ""),
                "icon": meta_data.get("poster", ""),
                "banner": meta_data.get("background", ""),
                "landscape": meta_data.get("background", ""),
            }
        )

        add_context_menu_items(li, params["video_id"], params["catalog_type"])
        xbmcplugin.addDirectoryItem(
            handle=ADDON_HANDLE, url=url, listitem=li, isFolder=True
        )

    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def get_streams(params):
    url = f"{BASE_URL}/{SECRET_STR}/stream/{params['catalog_type']}/{params['video_id']}.json"
    response = fetch_data(url)
    if not response:
        return

    streams = response.get("streams", [])
    if not streams:
        xbmcgui.Dialog().notification(
            "MediaFusion", "No streams available", xbmcgui.NOTIFICATION_ERROR
        )
        return

    for stream in streams:
        li = xbmcgui.ListItem(label=stream["name"], offscreen=True)
        tags = li.getVideoInfoTag()
        tags.setTitle(stream["name"])
        tags.setPlot(stream.get("description", ""))

        li.setProperty("IsPlayable", "true")

        if "url" in stream:
            video_url = stream.get("url")
        elif "infoHash" in stream:
            if not is_elementum_installed_and_enabled():
                xbmcgui.Dialog().notification(
                    "MediaFusion",
                    "Elementum is not installed. Please install Elementum to play p2p torrents.",
                    xbmcgui.NOTIFICATION_ERROR,
                )
                return
            magnet_link = convert_info_hash_to_magnet(
                stream.get("infoHash"), stream.get("sources", [])
            )
            video_url = f"plugin://plugin.video.elementum/play?uri={parse.quote_plus(magnet_link)}"
        else:
            continue

        xbmcplugin.addDirectoryItem(
            handle=ADDON_HANDLE,
            url=build_url(
                "play_video",
                video_url=video_url,
                headers=parse.urlencode(
                    stream.get("behaviorHints", {})
                    .get("proxyHeaders", {})
                    .get("request", {})
                ),
            ),
            listitem=li,
            isFolder=False,
            totalItems=len(streams),
        )
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def play_video(params):
    video_url = params["video_url"]
    li = xbmcgui.ListItem(path=video_url)

    # If headers are present, append them to the URL for ffmpegdirect inputstream
    if "headers" in params:
        headers = parse.parse_qs(params["headers"])
        formatted_headers = "&".join([f"{k}={v[0]}" for k, v in headers.items()])
        li.setPath(f"{video_url}|{formatted_headers}")

        li.setProperty("inputstream", "inputstream.ffmpegdirect")
        if video_url.endswith(".ts"):
            li.setMimeType("video/mp2t")
        elif video_url.endswith(".mpd"):
            li.setMimeType("application/dash+xml")
        elif video_url.endswith(".m3u8"):
            li.setMimeType("application/vnd.apple.mpegurl")

    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, li)


def addon_router():
    param_string = sys.argv[2][1:]
    if param_string:
        params = dict(parse.parse_qsl(param_string))
        action = params.get("action")

        if action == "list_catalog":
            list_catalogs(params)
        elif action == "get_streams":
            get_streams(params)
        elif action == "list_seasons":
            list_seasons(params)
        elif action == "list_episodes":
            list_episodes(params)
        elif action == "play_video":
            play_video(params)
        else:
            list_categories()
    else:
        list_categories()
