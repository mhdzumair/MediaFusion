HOMEPAGE = "https://www.1tamilblasters.lat"

TAMIL_BLASTER_LINKS = {
    "tamil": {
        "hdrip": f"{HOMEPAGE}/index.php?/forums/forum/7-tamil-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "tcrip": f"{HOMEPAGE}/index.php?/forums/forum/8-tamil-new-movies-tcrip-dvdscr-hdcam-predvd",
        "dubbed": f"{HOMEPAGE}/index.php?/forums/forum/9-tamil-dubbed-movies-bdrips-hdrips-dvdscr-hdcam-in-multi-audios",
        "series": f"{HOMEPAGE}/index.php?/forums/forum/63-tamil-new-web-series-tv-shows",
    },
    "malayalam": {
        "tcrip": f"{HOMEPAGE}/index.php?/forums/forum/75-malayalam-new-movies-tcrip-dvdscr-hdcam-predvd",
        "hdrip": f"{HOMEPAGE}/index.php?/forums/forum/74-malayalam-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "dubbed": f"{HOMEPAGE}/index.php?/forums/forum/76-malayalam-dubbed-movies-bdrips-hdrips-dvdscr-hdcam",
        "series": f"{HOMEPAGE}/index.php?/forums/forum/98-malayalam-new-web-series-tv-shows",
    },
    "telugu": {
        "tcrip": f"{HOMEPAGE}/index.php?/forums/forum/79-telugu-new-movies-tcrip-dvdscr-hdcam-predvd",
        "hdrip": f"{HOMEPAGE}/index.php?/forums/forum/78-telugu-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "dubbed": f"{HOMEPAGE}/index.php?/forums/forum/80-telugu-dubbed-movies-bdrips-hdrips-dvdscr-hdcam",
        "series": f"{HOMEPAGE}/index.php?/forums/forum/96-telugu-new-web-series-tv-shows",
    },
    "hindi": {
        "tcrip": f"{HOMEPAGE}/index.php?/forums/forum/87-hindi-new-movies-tcrip-dvdscr-hdcam-predvd",
        "hdrip": f"{HOMEPAGE}/index.php?/forums/forum/86-hindi-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "dubbed": f"{HOMEPAGE}/index.php?/forums/forum/88-hindi-dubbed-movies-bdrips-hdrips-dvdscr-hdcam",
        "series": f"{HOMEPAGE}/index.php?/forums/forum/89-hindi-new-web-series-tv-shows",
    },
    "kannada": {
        "tcrip": f"{HOMEPAGE}/index.php?/forums/forum/83-kannada-new-movies-tcrip-dvdscr-hdcam-predvd",
        "hdrip": f"{HOMEPAGE}/index.php?/forums/forum/82-kannada-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "dubbed": f"{HOMEPAGE}/index.php?/forums/forum/84-kannada-dubbed-movies-bdrips-hdrips-dvdscr-hdcam",
        "series": f"{HOMEPAGE}/index.php?/forums/forum/103-kannada-new-web-series-tv-shows",
    },
    "english": {
        "tcrip": f"{HOMEPAGE}/index.php?/forums/forum/52-english-movies-hdcam-dvdscr-predvd",
        "hdrip": f"{HOMEPAGE}/index.php?/forums/forum/53-english-movies-hdrips-bdrips-dvdrips",
        "series": f"{HOMEPAGE}/index.php?/forums/forum/92-english-web-series-tv-shows",
    },
}

LANGUAGE_CATALOGS = {
    "Tamil": {"movie": ["tamil_hdrip", "tamil_tcrip", "tamil_dubbed"], "series": ["tamil_series"]},
    "Malayalam": {"movie": ["malayalam_hdrip", "malayalam_tcrip", "malayalam_dubbed"], "series": ["malayalam_series"]},
    "Telugu": {"movie": ["telugu_hdrip", "telugu_tcrip", "telugu_dubbed"], "series": ["telugu_series"]},
    "Hindi": {"movie": ["hindi_hdrip", "hindi_tcrip", "hindi_dubbed"], "series": ["hindi_series"]},
    "Kannada": {"movie": ["kannada_hdrip", "kannada_tcrip", "kannada_dubbed"], "series": ["kannada_series"]},
    "English": {"movie": ["english_hdrip", "english_tcrip"], "series": ["english_series"]},
}

ALWAYS_ENABLED = ["tamil_blasters", "tamil_blasters", "any_any"]

TRACKERS = [
    "udp://tracker.openbittorrent.com:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.pomf.se:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.internetwarriors.net:1337/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://9.rarbg.to:2710/announce",
    "udp://9.rarbg.me:2710/announce",
    "http://tracker3.itzmx.com:8080/announce",
    "udp://ipv4.tracker.harry.lu:80/announce",
    "http://125.227.35.196:6969/announce",
    "http://210.244.71.25:6969/announce",
    "http://210.244.71.26:6969/announce",
    "http://213.159.215.198:6970/announce",
    "http://37.19.5.139:6969/announce",
    "http://37.19.5.155:6881/announce",
    "http://46.4.109.148:6969/announce",
    "http://87.248.186.252:8080/announce",
]
