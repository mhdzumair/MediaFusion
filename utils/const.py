CATALOG_DATA = {
    "american_football": "American Football",
    "baseball": "Baseball",
    "basketball": "Basketball",
    "english_hdrip": "English HD Movies",
    "english_series": "English Series",
    "english_tcrip": "English TCRip Movies",
    "football": "Football",
    "formula_racing": "Formula Racing",
    "hindi_dubbed": "Hindi Dubbed Movies",
    "hindi_hdrip": "Hindi HD Movies",
    "hindi_old": "Hindi Old Movies",
    "hindi_series": "Hindi Series",
    "hindi_tcrip": "Hindi TCRip Movies",
    "hockey": "Hockey",
    "kannada_dubbed": "Kannada Dubbed Movies",
    "kannada_hdrip": "Kannada HD Movies",
    "kannada_old": "Kannada Old Movies",
    "kannada_series": "Kannada Series",
    "kannada_tcrip": "Kannada TCRip Movies",
    "live_sport_events": "Live Sport Events",
    "live_tv": "Live TV",
    "malayalam_dubbed": "Malayalam Dubbed Movies",
    "malayalam_hdrip": "Malayalam HD Movies",
    "malayalam_old": "Malayalam Old Movies",
    "malayalam_series": "Malayalam Series",
    "malayalam_tcrip": "Malayalam TCRip Movies",
    "mediafusion_search_movies": "MediaFusion Search Movies",
    "mediafusion_search_series": "MediaFusion Search Series",
    "mediafusion_search_tv": "MediaFusion Search TV",
    "motogp_racing": "MotoGP Racing",
    "other_sports": "Other Sports",
    "prowlarr_movies": "Prowlarr Scraped Movies",
    "prowlarr_series": "Prowlarr Scraped Series",
    "prowlarr_streams": "Prowlarr Streams",
    "rugby": "Rugby/AFL",
    "tamil_dubbed": "Tamil Dubbed Movies",
    "tamil_hdrip": "Tamil HD Movies",
    "tamil_old": "Tamil Old Movies",
    "tamil_series": "Tamil Series",
    "tamil_tcrip": "Tamil TCRip Movies",
    "telugu_dubbed": "Telugu Dubbed Movies",
    "telugu_hdrip": "Telugu HD Movies",
    "telugu_old": "Telugu Old Movies",
    "telugu_series": "Telugu Series",
    "telugu_tcrip": "Telugu TCRip Movies",
    "torrentio_streams": "Torrentio Streams",
    "contribution_streams": "Contribution Streams",
}

USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS = [
    "english_hdrip",
    "english_tcrip",
    "hindi_dubbed",
    "hindi_hdrip",
    "hindi_old",
    "hindi_tcrip",
    "kannada_dubbed",
    "kannada_hdrip",
    "kannada_old",
    "kannada_tcrip",
    "malayalam_dubbed",
    "malayalam_hdrip",
    "malayalam_old",
    "malayalam_tcrip",
    "tamil_dubbed",
    "tamil_hdrip",
    "tamil_old",
    "tamil_tcrip",
    "telugu_dubbed",
    "telugu_hdrip",
    "telugu_old",
    "telugu_tcrip",
]

USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS = [
    "english_series",
    "hindi_series",
    "kannada_series",
    "malayalam_series",
    "tamil_series",
    "telugu_series",
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


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "*",
}
CACHE_HEADERS = {
    "Cache-Control": "max-age=3600, stale-while-revalidate=3600, stale-if-error=604800, public",
}
NO_CACHE_HEADERS = {
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
    "motogp_tgx": "MotoGP TGX",
}


STREAMING_PROVIDERS_SHORT_NAMES = {
    "alldebrid": "AD",
    "debridlink": "DL",
    "offcloud": "OC",
    "pikpak": "PKP",
    "premiumize": "PM",
    "qbittorrent": "QB-WD",
    "realdebrid": "RD",
    "seedr": "SDR",
    "torbox": "TRB",
}
