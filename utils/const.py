CATALOG_ID_DATA = [
    "american_football",
    "baseball",
    "basketball",
    "english_hdrip",
    "english_series",
    "english_tcrip",
    "football",
    "formula_racing",
    "hindi_dubbed",
    "hindi_hdrip",
    "hindi_old",
    "hindi_series",
    "hindi_tcrip",
    "hockey",
    "kannada_dubbed",
    "kannada_hdrip",
    "kannada_old",
    "kannada_series",
    "kannada_tcrip",
    "live_sport_events",
    "live_tv",
    "malayalam_dubbed",
    "malayalam_hdrip",
    "malayalam_old",
    "malayalam_series",
    "malayalam_tcrip",
    "mediafusion_search_movies",
    "mediafusion_search_series",
    "mediafusion_search_tv",
    "other_sports",
    "prowlarr_streams",
    "rugby",
    "tamil_dubbed",
    "tamil_hdrip",
    "tamil_old",
    "tamil_series",
    "tamil_tcrip",
    "telugu_dubbed",
    "telugu_hdrip",
    "telugu_old",
    "telugu_series",
    "telugu_tcrip",
    "torrentio_streams",
]

CATALOG_NAME_DATA = [
    "American Football",
    "Baseball",
    "Basketball",
    "English HD Movies",
    "English Series",
    "English TCRip Movies",
    "Football",
    "Formula Racing",
    "Hindi Dubbed Movies",
    "Hindi HD Movies",
    "Hindi Old Movies",
    "Hindi Series",
    "Hindi TCRip Movies",
    "Hockey",
    "Kannada Dubbed Movies",
    "Kannada HD Movies",
    "Kannada Old Movies",
    "Kannada Series",
    "Kannada TCRip Movies",
    "Live Sport Events",
    "Live TV",
    "Malayalam Dubbed Movies",
    "Malayalam HD Movies",
    "Malayalam Old Movies",
    "Malayalam Series",
    "Malayalam TCRip Movies",
    "MediaFusion Search Movies",
    "MediaFusion Search Series",
    "MediaFusion Search TV",
    "Other Sports",
    "Prowlarr Streams",
    "Rugby/AFL",
    "Tamil Dubbed Movies",
    "Tamil HD Movies",
    "Tamil Old Movies",
    "Tamil Series",
    "Tamil TCRip Movies",
    "Telugu Dubbed Movies",
    "Telugu HD Movies",
    "Telugu Old Movies",
    "Telugu Series",
    "Telugu TCRip Movies",
    "Torrentio Streams",
]

RESOLUTIONS = [
    "4K",
    "2160p",
    "1440p",
    "1080p",
    "720p",
    "576p",
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

DELETE_ALL_WATCHLIST_META = {
    "_id": "dl{}",
    "title": "🗑️💩 Delete all files",
    "type": "movie",
    "description": "🚨💀⚠ Delete all files in streaming provider",
}

UA_HEADER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
}


M3U8_VALID_CONTENT_TYPES = [
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "video/mp2t",
    "application/octet-stream",
]

SCRAPY_SPIDERS = {
    "formula_tgx": "Formula TGX",
    "mhdtvworld": "MHDTV World",
    "mhdtvsports": "MHDTV Sports",
    "tamilultra": "Tamil Ultra",
    "sport_video": "Sport Video",
    "streamed": "Streamed Sport Events",
    "mrgamingstreams": "MrGamingStreams Sport Events",
    "tamil_blasters": "TamilBlasters",
    "tamilmv": "TamilMV",
    "crictime": "CricTime",
    "streambtw": "StreamBTW",
    "dlhd": "DaddyLiveHD",
}
