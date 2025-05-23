CATALOG_DATA = {
    "american_football": "American Football",
    "arabic_movies": "Arabic Movies",
    "arabic_series": "Arabic Series",
    "baseball": "Baseball",
    "basketball": "Basketball",
    "bangla_movies": "Bangla Movies",
    "bangla_series": "Bangla Series",
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
    "punjabi_movies": "Punjabi Movies",
    "punjabi_series": "Punjabi Series",
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
    "fighting": "Fighting (WWE, UFC)",
    "tgx_movie": "TGx Movies",
    "tgx_series": "TGx Series",
    "contribution_movies": "Contribution Streams Movies",
    "contribution_series": "Contribution Streams Series",
}

USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS = [
    "arabic_movies",
    "bangla_movies",
    "english_hdrip",
    "english_tcrip",
    "fighting",
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
    "other_sports",
    "punjabi_movies",
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
    "arabic_series",
    "bangla_series",
    "english_series",
    "hindi_series",
    "kannada_series",
    "malayalam_series",
    "punjabi_series",
    "tamil_series",
    "telugu_series",
]

USER_UPLOAD_SUPPORTED_SPORTS_CATALOG_IDS = [
    "american_football",
    "baseball",
    "basketball",
    "football",
    "formula_racing",
    "hockey",
    "motogp_racing",
    "rugby",
    "other_sports",
    "fighting",
]

RESOLUTIONS = [
    "4k",
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
SUPPORTED_RESOLUTIONS = set(RESOLUTIONS)

DEBRID_SERVER_TIMEOUT = 15


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "*",
}
CACHE_HEADERS = {
    "Cache-Control": "max-age=3600, stale-while-revalidate=3600, stale-if-error=3600, public",
}
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

TORRENT_SORTING_PRIORITY = [
    "language",
    "cached",
    "resolution",
    "quality",
    "size",
    "seeders",
    "created_at",
]
TORRENT_SORTING_PRIORITY_OPTIONS = TORRENT_SORTING_PRIORITY

STREAMING_SERVICE_REQUIREMENTS = {
    "pikpak": ["email", "password"],
    "qbittorrent": ["qbittorrent_config"],
    "stremthru": ["url", "token"],
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


IPTV_VALID_CONTENT_TYPES = [
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "video/mp2t",
    "application/octet-stream",
    "application/dash+xml",
]

SCRAPY_SPIDERS = {
    "formula_tgx": "Formula TGX",
    "nowmetv": "NowMeTV",
    "nowsports": "NowSports",
    "tamilultra": "Tamil Ultra",
    "sport_video": "Sport Video",
    "tamil_blasters": "TamilBlasters",
    "tamilmv": "TamilMV",
    "dlhd": "DaddyLiveHD",
    "motogp_tgx": "MotoGP TGX",
    "arab_torrents": "Arab Torrents",
    "wwe_tgx": "WWE TGX",
    "ufc_tgx": "UFC TGX",
    "movies_tv_tgx": "Movies TV TGX",
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
    "stremthru": "ST",
    "easydebrid": "ED",
}

CERTIFICATION_MAPPING = {
    "All Ages": [
        "A.G.", "A/fig", "A/i", "A/i/fig", "AA", "Ai", "AL", "AL/Tous", "ALL", 
        "Alla", "ATP", "Approved", "Btl", "E", "FAM", "G", "General", "Genel İzleyici", 
        "Genel İzleyici Kitlesi", "KT", "L", "Libre", "Livre", "Públicos", 
        "Semua", "SU", "T", "TE", "Tous", "Tous Publics", "TP", "U", "UR", "ZA", 
        "0", "0+", "3", "4+", "5"
    ],
    
    "Children": [
        "6", "6+", "6A", "7", "7+", "7-9 PG", "7i", "8", "8+", "9", "9+", 
        "AP", "Children", "DA", "I", "K", "KN", "LH", "M/4", "M/6", "PG", 
        "PG8", "TV-G", "TV-Y", "TV-Y7", "TV-Y7-FV", "P"
    ],
    
    "Parental Guidance": [
        "10", "10+", "10-12 PG", "10A", "11", "12", "12+", "12A", "12PG", "12i", 
        "B", "BA", "GY", "M/12", "N-7", "P13", "PG-12", "PG12", "Public Averti", 
        "TV-PG", "VM12"
    ],
    
    "Teens": [
        "13", "13+", "14", "14+", "14A", "15", "15+", "15A", "15PG", "16", "16+", 
        "B15", "C", "GA", "I.C.-14", "IIA", "IIB", "M", "M/16", "MA", 
        "MA 15+", "N-13", "N-16", "NC16", "PG-13", "PG-15", "R", "R-12", "R-13", "R-15+", 
        "R-16", "RP13", "RP16", "SAM 13", "SAM 16", "TV-14", "VM14", "VM16", "Y"
    ],
    
    "Adults": [
        "18", "18+", "18A", "18PA", "18PL", "18SG", "18SX", "18TC", "A", 
        "Caution", "D", "I.M.-18", "III", "M/18", "M18", "N-18", "NC-17", 
        "R(A)", "R-18", "R18", "RP18", "SAM 18", "TV-MA", "VM18", "Z",
        "Unrated"
    ],
    
    "Adults+": [
        "20", "20+", "21", "21+", "Banned", "KK", "R21", "R21+", "R-21", "R-21+", "R-21A", 
        "RC", "X", "X18", "X 18+", "XX", "XXX"
    ],
}

LANGUAGES_FILTERS = [
    "English",
    "Tamil",
    "Hindi",
    "Malayalam",
    "Kannada",
    "Telugu",
    "Chinese",
    "Russian",
    "Arabic",
    "Japanese",
    "Korean",
    "Taiwanese",
    "Latino",
    "French",
    "Spanish",
    "Portuguese",
    "Italian",
    "German",
    "Ukrainian",
    "Polish",
    "Czech",
    "Thai",
    "Indonesian",
    "Vietnamese",
    "Dutch",
    "Bengali",
    "Turkish",
    "Greek",
    "Swedish",
    "Romanian",
    "Hungarian",
    "Finnish",
    "Norwegian",
    "Danish",
    "Hebrew",
    "Lithuanian",
    "Punjabi",
    "Marathi",
    "Gujarati",
    "Bhojpuri",
    "Nepali",
    "Urdu",
    "Tagalog",
    "Filipino",
    "Malay",
    "Mongolian",
    "Armenian",
    "Georgian",
    None,
]

SUPPORTED_LANGUAGES = set(LANGUAGES_FILTERS)

QUALITY_GROUPS = {
    "BluRay/UHD": ["BluRay", "BluRay REMUX", "BRRip", "BDRip", "UHDRip"],
    "WEB/HD": ["WEB-DL", "WEB-DLRip", "WEBRip", "HDRip"],
    "DVD/TV/SAT": ["DVD", "DVDRip", "HDTV", "SATRip", "TVRip", "PPVRip"],
    "CAM/Screener": ["CAM", "TeleSync", "TeleCine", "SCR"],
    "Unknown": [None],
}

SUPPORTED_QUALITIES = {
    quality for qualities in QUALITY_GROUPS.values() for quality in qualities
}

QUALITY_RANKING = {
    quality: rank
    for rank, qualities in enumerate(reversed(QUALITY_GROUPS.values()))
    for quality in qualities
}

SUPPORTED_PRIVATE_TRACKER_STREAMING_PROVIDERS = {
    "debridlink",
    "qbittorrent",
    "torbox",
}

LANGUAGE_COUNTRY_FLAGS = {
    "English": "🇬🇧",
    "Tamil": "🇮🇳",
    "Hindi": "🇮🇳",
    "Malayalam": "🇮🇳",
    "Kannada": "🇮🇳",
    "Telugu": "🇮🇳",
    "Chinese": "🇨🇳",
    "Russian": "🇷🇺",
    "Arabic": "🇸🇦",
    "Japanese": "🇯🇵",
    "Korean": "🇰🇷",
    "Taiwanese": "🇹🇼",
    "Latino": "🇲🇽",
    "French": "🇫🇷",
    "Spanish": "🇪🇸",
    "Portuguese": "🇵🇹",
    "Italian": "🇮🇹",
    "German": "🇩🇪",
    "Ukrainian": "🇺🇦",
    "Polish": "🇵🇱",
    "Czech": "🇨🇿",
    "Thai": "🇹🇭",
    "Indonesian": "🇮🇩",
    "Vietnamese": "🇻🇳",
    "Dutch": "🇳🇱",
    "Bengali": "🇧🇩",
    "Turkish": "🇹🇷",
    "Greek": "🇬🇷",
    "Swedish": "🇸🇪",
    "Romanian": "🇷🇴",
    "Hungarian": "🇭🇺",
    "Finnish": "🇫🇮",
    "Norwegian": "🇳🇴",
    "Danish": "🇩🇰",
    "Hebrew": "🇮🇱",
    "Lithuanian": "🇱🇹",
    "Punjabi": "🇮🇳",
    "Marathi": "🇮🇳",
    "Gujarati": "🇮🇳",
    "Bhojpuri": "🇮🇳",
    "Nepali": "🇳🇵",
    "Urdu": "🇵🇰",
    "Tagalog": "🇵🇭",
    "Filipino": "🇵🇭",
    "Malay": "🇲🇾",
    "Mongolian": "🇲🇳",
    "Armenian": "🇦🇲",
    "Georgian": "🇬🇪",
}


CONTENT_TYPE_HEADERS_MAPPING = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv",
    ".m4v": "video/x-m4v",
    ".3gp": "video/3gpp",
    ".3g2": "video/3gpp2",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".mts": "video/mp2t",
    ".vob": "video/x-ms-vob",
    ".ogv": "video/ogg",
    ".divx": "video/divx",
    ".m3u8": "application/x-mpegURL",
    ".mpd": "application/dash+xml",
    ".f4v": "video/x-f4v",
    ".rmvb": "application/vnd.rn-realmedia-vbr",
    ".asf": "video/x-ms-asf",
}
