CATALOG_ID_DATA = [
    "mediafusion_search_movies",
    "mediafusion_search_series",
    "tamil_hdrip",
    "tamil_tcrip",
    "tamil_old",
    "tamil_dubbed",
    "tamil_series",
    "malayalam_tcrip",
    "malayalam_hdrip",
    "malayalam_old",
    "malayalam_dubbed",
    "malayalam_series",
    "telugu_tcrip",
    "telugu_hdrip",
    "telugu_old",
    "telugu_dubbed",
    "telugu_series",
    "hindi_tcrip",
    "hindi_hdrip",
    "hindi_old",
    "hindi_dubbed",
    "hindi_series",
    "kannada_tcrip",
    "kannada_hdrip",
    "kannada_old",
    "kannada_dubbed",
    "kannada_series",
    "english_hdrip",
    "english_tcrip",
    "english_series",
    "live_tv",
    "mediafusion_search_tv",
    "torrentio_streams",
    "prowlarr_streams",
    "formula_racing",
]

CATALOG_NAME_DATA = [
    "MediaFusion Search Movies",
    "MediaFusion Search Series",
    "Tamil HD Movies",
    "Tamil TCRip Movies",
    "Tamil Old Movies",
    "Tamil Dubbed Movies",
    "Tamil Series",
    "Malayalam TCRip Movies",
    "Malayalam HD Movies",
    "Malayalam Old Movies",
    "Malayalam Dubbed Movies",
    "Malayalam Series",
    "Telugu TCRip Movies",
    "Telugu HD Movies",
    "Telugu Old Movies",
    "Telugu Dubbed Movies",
    "Telugu Series",
    "Hindi TCRip Movies",
    "Hindi HD Movies",
    "Hindi Old Movies",
    "Hindi Dubbed Movies",
    "Hindi Series",
    "Kannada TCRip Movies",
    "Kannada HD Movies",
    "Kannada Old Movies",
    "Kannada Dubbed Movies",
    "Kannada Series",
    "English HD Movies",
    "English TCRip Movies",
    "English Series",
    "Live TV",
    "MediaFusion Search TV",
    "Torrentio Streams",
    "Prowlarr Streams",
    "Formula Racing",
]

RESOLUTIONS = [
    "4K",
    "2160p",
    "1440p",
    "1080p",
    "720p",
    "480p",
    "360p",
    "240p",
    None,
]

RESOLUTION_RANKING = {res: rank for rank, res in enumerate(reversed(RESOLUTIONS))}


DEBRID_SERVER_TIMEOUT = 15


DEFAULT_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Cache-Control": "max-age=3600, stale-while-revalidate=3600, stale-if-error=604800, public",
}
NO_CACHE_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

TORRENT_SORTING_PRIORITY = ["cached", "resolution", "size", "seeders", "created_at"]

STREAMING_SERVICE_REQUIREMENTS = {
    "pikpak": ["username", "password"],
    "qbittorrent": ["qbittorrent_config"],
    "default": ["token"],
}
