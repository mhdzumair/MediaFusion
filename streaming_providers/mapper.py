from streaming_providers.alldebrid.utils import (
    delete_all_torrents_from_ad,
    delete_torrent_from_ad,
    fetch_downloaded_info_hashes_from_ad,
    fetch_torrent_details_from_ad,
    get_video_url_from_alldebrid,
    update_ad_cache_status,
    validate_alldebrid_credentials,
)
from streaming_providers.debrider.utils import (
    get_video_url_from_debrider,
    get_video_url_from_usenet_debrider,
    update_debrider_cache_status,
    update_debrider_usenet_cache_status,
    validate_debrider_credentials,
)
from streaming_providers.debridlink.utils import (
    delete_all_torrents_from_dl,
    delete_torrent_from_dl,
    fetch_downloaded_info_hashes_from_dl,
    fetch_torrent_details_from_dl,
    get_video_url_from_debridlink,
    update_dl_cache_status,
    validate_debridlink_credentials,
)
from streaming_providers.easydebrid.utils import (
    get_video_url_from_easydebrid,
    update_easydebrid_cache_status,
    validate_easydebrid_credentials,
)
from streaming_providers.easynews.utils import (
    delete_all_usenet_from_easynews,
    delete_usenet_from_easynews,
    fetch_downloaded_usenet_hashes_from_easynews,
    get_video_url_from_easynews,
    update_easynews_cache_status,
    validate_easynews_credentials,
)
from streaming_providers.nzbdav.utils import (
    delete_all_usenet_from_nzbdav,
    delete_usenet_from_nzbdav,
    fetch_downloaded_usenet_hashes_from_nzbdav,
    get_video_url_from_nzbdav,
    update_nzbdav_cache_status,
    validate_nzbdav_credentials,
)
from streaming_providers.nzbget.utils import (
    delete_all_usenet_from_nzbget,
    delete_usenet_from_nzbget,
    fetch_downloaded_usenet_hashes_from_nzbget,
    get_video_url_from_nzbget,
    update_nzbget_cache_status,
    validate_nzbget_credentials,
)
from streaming_providers.offcloud.utils import (
    delete_all_torrents_from_oc,
    delete_torrent_from_oc,
    fetch_downloaded_info_hashes_from_oc,
    fetch_torrent_details_from_oc,
    get_video_url_from_offcloud,
    update_oc_cache_status,
    validate_offcloud_credentials,
)
from streaming_providers.pikpak.utils import (
    delete_all_torrents_from_pikpak,
    delete_torrent_from_pikpak,
    fetch_downloaded_info_hashes_from_pikpak,
    fetch_torrent_details_from_pikpak,
    get_video_url_from_pikpak,
    update_pikpak_cache_status,
    validate_pikpak_credentials,
)
from streaming_providers.premiumize.utils import (
    delete_all_torrents_from_pm,
    delete_torrent_from_pm,
    fetch_downloaded_info_hashes_from_premiumize,
    fetch_torrent_details_from_premiumize,
    get_video_url_from_premiumize,
    update_pm_cache_status,
    validate_premiumize_credentials,
)
from streaming_providers.qbittorrent.utils import (
    delete_all_torrents_from_qbittorrent,
    fetch_info_hashes_from_webdav,
    get_video_url_from_qbittorrent,
    update_qbittorrent_cache_status,
    validate_qbittorrent_credentials,
)
from streaming_providers.realdebrid.utils import (
    delete_all_watchlist_rd,
    delete_torrent_from_rd,
    fetch_downloaded_info_hashes_from_rd,
    fetch_torrent_details_from_rd,
    get_video_url_from_realdebrid,
    update_rd_cache_status,
    validate_realdebrid_credentials,
)
from streaming_providers.sabnzbd.utils import (
    delete_all_usenet_from_sabnzbd,
    delete_usenet_from_sabnzbd,
    fetch_downloaded_usenet_hashes_from_sabnzbd,
    get_video_url_from_sabnzbd,
    update_sabnzbd_cache_status,
    validate_sabnzbd_credentials,
)
from streaming_providers.seedr.utils import (
    delete_all_torrents_from_seedr,
    delete_torrent_from_seedr,
    fetch_downloaded_info_hashes_from_seedr,
    fetch_torrent_details_from_seedr,
    get_video_url_from_seedr,
    update_seedr_cache_status,
    validate_seedr_credentials,
)
from streaming_providers.stremthru.utils import (
    delete_all_torrents_from_st,
    fetch_downloaded_info_hashes_from_st,
    get_video_url_from_stremthru,
    update_st_cache_status,
    validate_stremthru_credentials,
)
from streaming_providers.torbox.utils import (
    delete_all_torrents_from_torbox,
    delete_all_usenet_from_torbox,
    delete_torrent_from_torbox,
    delete_usenet_from_torbox,
    fetch_downloaded_info_hashes_from_torbox,
    fetch_downloaded_usenet_hashes_from_torbox,
    fetch_torrent_details_from_torbox,
    get_video_url_from_torbox,
    get_video_url_from_usenet_torbox,
    update_torbox_cache_status,
    update_torbox_usenet_cache_status,
    validate_torbox_credentials,
)

# Define provider-specific cache update functions
CACHE_UPDATE_FUNCTIONS = {
    "alldebrid": update_ad_cache_status,
    "debridlink": update_dl_cache_status,
    "offcloud": update_oc_cache_status,
    "pikpak": update_pikpak_cache_status,
    "realdebrid": update_rd_cache_status,
    "seedr": update_seedr_cache_status,
    "torbox": update_torbox_cache_status,
    "premiumize": update_pm_cache_status,
    "qbittorrent": update_qbittorrent_cache_status,
    "stremthru": update_st_cache_status,
    "easydebrid": update_easydebrid_cache_status,
    "debrider": update_debrider_cache_status,
}

# Define provider-specific downloaded info hashes fetch functions
FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS = {
    "alldebrid": fetch_downloaded_info_hashes_from_ad,
    "debridlink": fetch_downloaded_info_hashes_from_dl,
    "offcloud": fetch_downloaded_info_hashes_from_oc,
    "pikpak": fetch_downloaded_info_hashes_from_pikpak,
    "realdebrid": fetch_downloaded_info_hashes_from_rd,
    "seedr": fetch_downloaded_info_hashes_from_seedr,
    "torbox": fetch_downloaded_info_hashes_from_torbox,
    "premiumize": fetch_downloaded_info_hashes_from_premiumize,
    "qbittorrent": fetch_info_hashes_from_webdav,
    "stremthru": fetch_downloaded_info_hashes_from_st,
}

# Define provider-specific torrent details fetch functions (for watchlist import)
FETCH_TORRENT_DETAILS_FUNCTIONS = {
    "alldebrid": fetch_torrent_details_from_ad,
    "debridlink": fetch_torrent_details_from_dl,
    "offcloud": fetch_torrent_details_from_oc,
    "pikpak": fetch_torrent_details_from_pikpak,
    "premiumize": fetch_torrent_details_from_premiumize,
    "realdebrid": fetch_torrent_details_from_rd,
    "seedr": fetch_torrent_details_from_seedr,
    "torbox": fetch_torrent_details_from_torbox,
}


DELETE_ALL_WATCHLIST_FUNCTIONS = {
    "alldebrid": delete_all_torrents_from_ad,
    "debridlink": delete_all_torrents_from_dl,
    "pikpak": delete_all_torrents_from_pikpak,
    "premiumize": delete_all_torrents_from_pm,
    "qbittorrent": delete_all_torrents_from_qbittorrent,
    "realdebrid": delete_all_watchlist_rd,
    "seedr": delete_all_torrents_from_seedr,
    "offcloud": delete_all_torrents_from_oc,
    "torbox": delete_all_torrents_from_torbox,
    "stremthru": delete_all_torrents_from_st,
}

# Define provider-specific single torrent delete functions
DELETE_TORRENT_FUNCTIONS = {
    "alldebrid": delete_torrent_from_ad,
    "debridlink": delete_torrent_from_dl,
    "offcloud": delete_torrent_from_oc,
    "pikpak": delete_torrent_from_pikpak,
    "premiumize": delete_torrent_from_pm,
    "realdebrid": delete_torrent_from_rd,
    "seedr": delete_torrent_from_seedr,
    "torbox": delete_torrent_from_torbox,
}


GET_VIDEO_URL_FUNCTIONS = {
    "alldebrid": get_video_url_from_alldebrid,
    "debridlink": get_video_url_from_debridlink,
    "offcloud": get_video_url_from_offcloud,
    "pikpak": get_video_url_from_pikpak,
    "premiumize": get_video_url_from_premiumize,
    "qbittorrent": get_video_url_from_qbittorrent,
    "realdebrid": get_video_url_from_realdebrid,
    "seedr": get_video_url_from_seedr,
    "torbox": get_video_url_from_torbox,
    "stremthru": get_video_url_from_stremthru,
    "easydebrid": get_video_url_from_easydebrid,
    "debrider": get_video_url_from_debrider,
}


VALIDATE_CREDENTIALS_FUNCTIONS = {
    "alldebrid": validate_alldebrid_credentials,
    "debridlink": validate_debridlink_credentials,
    "offcloud": validate_offcloud_credentials,
    "pikpak": validate_pikpak_credentials,
    "premiumize": validate_premiumize_credentials,
    "qbittorrent": validate_qbittorrent_credentials,
    "realdebrid": validate_realdebrid_credentials,
    "seedr": validate_seedr_credentials,
    "torbox": validate_torbox_credentials,
    "stremthru": validate_stremthru_credentials,
    "easydebrid": validate_easydebrid_credentials,
    "debrider": validate_debrider_credentials,
    # Usenet-only providers
    "sabnzbd": validate_sabnzbd_credentials,
    "nzbget": validate_nzbget_credentials,
    "nzbdav": validate_nzbdav_credentials,
    "easynews": validate_easynews_credentials,
}


# =========================================================================
# Usenet/NZB Provider Mappings
# =========================================================================

# Providers that support Usenet content
USENET_CAPABLE_PROVIDERS = {"torbox", "debrider", "sabnzbd", "nzbget", "nzbdav", "easynews"}

# Define provider-specific Usenet cache update functions
USENET_CACHE_UPDATE_FUNCTIONS = {
    "torbox": update_torbox_usenet_cache_status,
    "debrider": update_debrider_usenet_cache_status,
    "sabnzbd": update_sabnzbd_cache_status,
    "nzbget": update_nzbget_cache_status,
    "nzbdav": update_nzbdav_cache_status,
    "easynews": update_easynews_cache_status,
}

# Define provider-specific Usenet video URL functions
USENET_GET_VIDEO_URL_FUNCTIONS = {
    "torbox": get_video_url_from_usenet_torbox,
    "debrider": get_video_url_from_usenet_debrider,
    "sabnzbd": get_video_url_from_sabnzbd,
    "nzbget": get_video_url_from_nzbget,
    "nzbdav": get_video_url_from_nzbdav,
    "easynews": get_video_url_from_easynews,
}

# Define provider-specific Usenet downloaded hashes fetch functions
USENET_FETCH_DOWNLOADED_HASHES_FUNCTIONS = {
    "torbox": fetch_downloaded_usenet_hashes_from_torbox,
    "sabnzbd": fetch_downloaded_usenet_hashes_from_sabnzbd,
    "nzbget": fetch_downloaded_usenet_hashes_from_nzbget,
    "nzbdav": fetch_downloaded_usenet_hashes_from_nzbdav,
    "easynews": fetch_downloaded_usenet_hashes_from_easynews,
}

# Define provider-specific Usenet delete all functions
USENET_DELETE_ALL_FUNCTIONS = {
    "torbox": delete_all_usenet_from_torbox,
    "sabnzbd": delete_all_usenet_from_sabnzbd,
    "nzbget": delete_all_usenet_from_nzbget,
    "nzbdav": delete_all_usenet_from_nzbdav,
    "easynews": delete_all_usenet_from_easynews,
}

# Define provider-specific single Usenet delete functions
USENET_DELETE_FUNCTIONS = {
    "torbox": delete_usenet_from_torbox,
    "sabnzbd": delete_usenet_from_sabnzbd,
    "nzbget": delete_usenet_from_nzbget,
    "nzbdav": delete_usenet_from_nzbdav,
    "easynews": delete_usenet_from_easynews,
}
