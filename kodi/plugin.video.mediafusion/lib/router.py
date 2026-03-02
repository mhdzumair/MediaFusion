import json
import sys
from urllib import parse

import xbmc
import xbmcgui
import xbmcplugin

from .parser import parse_stream_entry
from .source_select_window import open_source_select_window
from .utils import (
    ADDON_HANDLE,
    BASE_URL,
    SECRET_STR,
    build_url,
    convert_info_hash_to_magnet,
    fetch_data,
    is_elementum_installed_and_enabled,
    log,
)


def list_categories():
    manifest_data = fetch_data(parse.urljoin(BASE_URL, f"/{SECRET_STR}/manifest.json"))
    if not manifest_data:
        return

    catalogs = manifest_data.get("catalogs", [])
    if not catalogs:
        log(
            "No MediaFusion native catalogs enabled in the config. Use MediaFusion with TMDB Helper",
            xbmc.LOGERROR,
        )
        xbmcgui.Dialog().notification(
            "MediaFusion",
            "No MediaFusion native catalogs enabled in the config. Use MediaFusion with TMDB Helper",
            xbmcgui.NOTIFICATION_ERROR,
        )
        return

    for catalog in catalogs:
        if any(extra["name"] == "search" for extra in catalog.get("extra", [])):
            action = "search_catalog"
            label = f"Search {catalog['name']}"
        elif "Watchlist" in catalog["name"]:
            action = "list_catalog"
            label = f"{catalog['name']} {catalog['type'].capitalize()}"
        else:
            action = "list_catalog"
            label = catalog["name"]

        url = build_url(action, catalog_type=catalog["type"], catalog_id=catalog["id"])
        li = xbmcgui.ListItem(label=label)
        xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=url, listitem=li, isFolder=True)
    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def process_videos(videos, action, catalog_type, catalog_id):
    content_type = "movies" if catalog_type == "movie" else "tvshows"
    xbmcplugin.setContent(ADDON_HANDLE, content_type)

    for video in videos:
        if catalog_type == "series":
            url = build_url(
                "list_seasons",
                catalog_type=catalog_type,
                video_id=video["id"],
            )
        else:
            url = build_url("get_streams", video_id=video["id"], catalog_type=catalog_type)
        li = xbmcgui.ListItem(label=video["name"])
        tags = li.getVideoInfoTag()
        tags.setUniqueID(video["id"], type="imdb" if video["id"].startswith("tt") else "mf")
        if video["id"].startswith("tt"):
            tags.setIMDBNumber(video["id"])
            tags.setUniqueID(video["id"], type="imdb")
        else:
            tags.setUniqueID(video["id"], type="mf")

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

        xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=url, listitem=li, isFolder=True)


def list_catalog(params):
    log(f"Loading {params['catalog_type']} videos...", xbmc.LOGINFO)
    skip = int(params.get("skip", 0))
    url = parse.urljoin(
        BASE_URL,
        f"/{SECRET_STR}/catalog/{params['catalog_type']}/{params['catalog_id']}/skip={skip}.json",
    )
    response = fetch_data(url)
    if not response:
        return

    videos = response.get("metas", [])
    if not videos:
        xbmcgui.Dialog().notification("MediaFusion", "No videos available", xbmcgui.NOTIFICATION_ERROR)
        return

    content_type = "movies" if params["catalog_type"] == "movie" else "tvshows"
    xbmcplugin.setContent(ADDON_HANDLE, content_type)

    process_videos(videos, "list_catalog", params["catalog_type"], params["catalog_id"])

    if len(videos) >= 25:
        next_url = build_url(
            "list_catalog",
            catalog_type=params["catalog_type"],
            catalog_id=params["catalog_id"],
            skip=skip + len(videos),
        )
        li = xbmcgui.ListItem(label="Next Page")
        xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=next_url, listitem=li, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def search_catalog(params):
    search_query = xbmcgui.Dialog().input("Search", type=xbmcgui.INPUT_ALPHANUM)
    if not search_query:
        return

    url = parse.urljoin(
        BASE_URL,
        f"/{SECRET_STR}/catalog/{params['catalog_type']}/{params['catalog_id']}/search={search_query}.json",
    )
    response = fetch_data(url)
    if not response:
        return

    videos = response.get("metas", [])
    if not videos:
        xbmcgui.Dialog().notification("MediaFusion", "No results found", xbmcgui.NOTIFICATION_ERROR)
        return

    content_type = "movies" if params["catalog_type"] == "movie" else "tvshows"
    xbmcplugin.setContent(ADDON_HANDLE, content_type)
    process_videos(videos, "search_catalog", params["catalog_type"], params["catalog_id"])

    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_seasons(params):
    url = parse.urljoin(
        BASE_URL,
        f"/{SECRET_STR}/meta/{params['catalog_type']}/{params['video_id']}.json",
    )
    response = fetch_data(url)
    if not response:
        return

    meta_data = response.get("meta", {})
    videos = meta_data.get("videos", [])
    if not videos:
        xbmcgui.Dialog().notification("MediaFusion", "No seasons available", xbmcgui.NOTIFICATION_ERROR)
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
        if meta_data["id"].startswith("tt"):
            tags.setIMDBNumber(meta_data["id"])
            tags.setUniqueID(meta_data["id"], type="imdb")
        else:
            tags.setUniqueID(meta_data["id"], type="mf")

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

        xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=url, listitem=li, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def list_episodes(params):
    url = parse.urljoin(
        BASE_URL,
        f"/{SECRET_STR}/meta/{params['catalog_type']}/{params['video_id']}.json",
    )
    response = fetch_data(url)
    if not response:
        return

    meta_data = response.get("meta", {})
    videos = meta_data.get("videos", [])
    if not videos:
        xbmcgui.Dialog().notification("MediaFusion", "No episodes available", xbmcgui.NOTIFICATION_ERROR)
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
        if meta_data["id"].startswith("tt"):
            tags.setIMDBNumber(meta_data["id"])
            tags.setUniqueID(meta_data["id"], type="imdb")
        else:
            tags.setUniqueID(meta_data["id"], type="mf")
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

        xbmcplugin.addDirectoryItem(handle=ADDON_HANDLE, url=url, listitem=li, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE)


def _build_kodi_stream_url(catalog_type, video_id, page, page_size):
    base_path = f"/{SECRET_STR}/kodi/stream/{catalog_type}/{video_id}.json"
    query = parse.urlencode({"page": page, "page_size": page_size})
    return parse.urljoin(BASE_URL, f"{base_path}?{query}")


def _build_stream_options(stream_entries, video_id, season, episode, is_imdb):
    stream_options = []
    elementum_available = is_elementum_installed_and_enabled()
    elementum_warning_shown = False

    for stream_entry in stream_entries:
        if "stream" not in stream_entry or "metadata" not in stream_entry:
            log("Received non-structured stream payload from Kodi endpoint", xbmc.LOGERROR)
            continue

        parsed_stream = parse_stream_entry(stream_entry)
        stream = parsed_stream["stream"]
        main_label = parsed_stream["main_label"]
        detail_label = parsed_stream["detail_label"]
        video_info = parsed_stream["video_info"]
        plot = parsed_stream["plot"]

        if "url" in stream:
            video_url = stream.get("url")
        elif "infoHash" in stream:
            if not elementum_available:
                if not elementum_warning_shown:
                    xbmcgui.Dialog().notification(
                        "MediaFusion",
                        "Elementum is not installed. Torrent streams were skipped.",
                        xbmcgui.NOTIFICATION_WARNING,
                    )
                    elementum_warning_shown = True
                continue
            magnet_link = convert_info_hash_to_magnet(stream.get("infoHash"), stream.get("sources", []))
            video_url = f"plugin://plugin.video.elementum/play?uri={parse.quote_plus(magnet_link)}"
        else:
            continue

        stream_options.append(
            {
                "main_label": main_label,
                "detail_label": detail_label,
                "list_primary": parsed_stream.get("list_primary", main_label),
                "list_secondary": parsed_stream.get("list_secondary", detail_label),
                "detail_title": parsed_stream.get("detail_title", detail_label),
                "detail_badges": parsed_stream.get("detail_badges", ""),
                "plot": plot,
                "sort_cached": parsed_stream.get("sort_cached", 1),
                "sort_resolution": parsed_stream.get("sort_resolution", 0),
                "sort_seeders": parsed_stream.get("sort_seeders", 0),
                "sort_size": parsed_stream.get("sort_size", 0),
                "stream_type_raw": parsed_stream.get("stream_type_raw", ""),
                "video_info": video_info,
                "video_url": video_url,
                "headers": parse.urlencode(stream.get("behaviorHints", {}).get("proxyHeaders", {}).get("request", {})),
                "imdb": video_id if is_imdb else None,
                "season": season,
                "episode": episode,
            }
        )

    return stream_options


def get_streams(params):
    page_size = 25
    current_page = 1
    response = fetch_data(
        _build_kodi_stream_url(
            params["catalog_type"],
            params["video_id"],
            current_page,
            page_size,
        )
    )
    if not response:
        return

    streams = response.get("streams", [])
    has_more = bool(response.get("has_more"))
    if not streams:
        xbmcgui.Dialog().notification("MediaFusion", "No streams available", xbmcgui.NOTIFICATION_ERROR)
        return

    is_imdb = params["video_id"].startswith("tt")
    if ":" in params["video_id"]:
        video_id, season, episode = params["video_id"].split(":")
    else:
        video_id, season, episode = params["video_id"], None, None

    stream_options = _build_stream_options(streams, video_id, season, episode, is_imdb)

    if not stream_options:
        xbmcgui.Dialog().notification("MediaFusion", "No playable streams available", xbmcgui.NOTIFICATION_ERROR)
        return

    def _load_more_streams():
        nonlocal current_page, has_more
        if not has_more:
            return [], False

        next_page = current_page + 1
        next_response = fetch_data(
            _build_kodi_stream_url(
                params["catalog_type"],
                params["video_id"],
                next_page,
                page_size,
            )
        )
        if not next_response:
            return [], has_more

        next_stream_entries = next_response.get("streams", [])
        has_more = bool(next_response.get("has_more"))
        current_page = next_page
        next_stream_options = _build_stream_options(next_stream_entries, video_id, season, episode, is_imdb)
        return next_stream_options, has_more

    selected_option = open_source_select_window(
        stream_options,
        has_more=has_more,
        on_load_more=_load_more_streams,
    )
    if not selected_option:
        xbmcplugin.setResolvedUrl(ADDON_HANDLE, False, xbmcgui.ListItem())
        return

    _resolve_playback(
        video_url=selected_option["video_url"],
        headers=selected_option.get("headers", ""),
        imdb=selected_option.get("imdb"),
        season=selected_option.get("season"),
        episode=selected_option.get("episode"),
        direct_play=True,
    )


def _resolve_playback(video_url, headers="", imdb=None, season=None, episode=None, direct_play=False):
    li = xbmcgui.ListItem(path=video_url)
    li.setProperty("IsPlayable", "true")

    is_http_stream = video_url.startswith(("http://", "https://"))
    if headers and is_http_stream:
        parsed_headers = parse.parse_qs(headers)
        formatted_headers = "&".join([f"{k}={v[0]}" for k, v in parsed_headers.items()])
        if formatted_headers:
            li.setPath(f"{video_url}|{formatted_headers}")
            li.setProperty("inputstream", "inputstream.ffmpegdirect")
            if video_url.endswith(".ts"):
                li.setMimeType("video/mp2t")
            elif video_url.endswith(".mpd"):
                li.setMimeType("application/dash+xml")
            elif video_url.endswith(".m3u8"):
                li.setMimeType("application/vnd.apple.mpegurl")

    if season and episode:
        try:
            li.setInfo("video", {"season": int(season), "episode": int(episode)})
        except (TypeError, ValueError):
            li.setInfo("video", {"season": season, "episode": episode})
    if imdb:
        li.setInfo("video", {"imdbnumber": imdb})
        ids = json.dumps({"imdb": imdb})
        xbmcgui.Window(10000).setProperty("script.trakt.ids", ids)

    if direct_play:
        xbmc.Player().play(li.getPath(), li)
        return

    xbmcplugin.setResolvedUrl(ADDON_HANDLE, True, li)


def play_video(params):
    video_url = params["video_url"]
    imdb = params.get("imdb", None)
    season = params.get("season", None)
    episode = params.get("episode", None)
    _resolve_playback(
        video_url=video_url,
        headers=params.get("headers", ""),
        imdb=imdb,
        season=season,
        episode=episode,
    )


def addon_router():
    param_string = sys.argv[2][1:]
    actions = {
        "list_catalog": list_catalog,
        "search_catalog": search_catalog,
        "get_streams": get_streams,
        "list_seasons": list_seasons,
        "list_episodes": list_episodes,
        "play_video": play_video,
    }

    if param_string:
        params = dict(parse.parse_qsl(param_string))
        action = params.get("action")

        action_func = actions.get(action)
        if action_func:
            action_func(params)
            return

    list_categories()
