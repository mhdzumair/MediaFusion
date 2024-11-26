from streaming_providers.alldebrid.utils import (
    update_ad_cache_status,
    fetch_downloaded_info_hashes_from_ad,
    delete_all_torrents_from_ad,
    get_video_url_from_alldebrid,
    validate_alldebrid_credentials,
)
from streaming_providers.debridlink.utils import (
    update_dl_cache_status,
    fetch_downloaded_info_hashes_from_dl,
    delete_all_torrents_from_dl,
    get_video_url_from_debridlink,
    validate_debridlink_credentials,
)
from streaming_providers.offcloud.utils import (
    update_oc_cache_status,
    fetch_downloaded_info_hashes_from_oc,
    delete_all_torrents_from_oc,
    get_video_url_from_offcloud,
    validate_offcloud_credentials,
)
from streaming_providers.pikpak.utils import (
    update_pikpak_cache_status,
    fetch_downloaded_info_hashes_from_pikpak,
    delete_all_torrents_from_pikpak,
    get_video_url_from_pikpak,
    validate_pikpak_credentials,
)
from streaming_providers.premiumize.utils import (
    update_pm_cache_status,
    fetch_downloaded_info_hashes_from_premiumize,
    delete_all_torrents_from_pm,
    get_video_url_from_premiumize,
    validate_premiumize_credentials,
)
from streaming_providers.qbittorrent.utils import (
    update_qbittorrent_cache_status,
    fetch_info_hashes_from_webdav,
    delete_all_torrents_from_qbittorrent,
    get_video_url_from_qbittorrent,
    validate_qbittorrent_credentials,
)
from streaming_providers.realdebrid.utils import (
    update_rd_cache_status,
    fetch_downloaded_info_hashes_from_rd,
    delete_all_watchlist_rd,
    get_video_url_from_realdebrid,
    validate_realdebrid_credentials,
)
from streaming_providers.seedr.utils import (
    update_seedr_cache_status,
    fetch_downloaded_info_hashes_from_seedr,
    delete_all_torrents_from_seedr,
    get_video_url_from_seedr,
    validate_seedr_credentials,
)
from streaming_providers.torbox.utils import (
    update_torbox_cache_status,
    fetch_downloaded_info_hashes_from_torbox,
    delete_all_torrents_from_torbox,
    get_video_url_from_torbox,
    validate_torbox_credentials,
)
from streaming_providers.stremthru.utils import (
    update_st_cache_status,
    fetch_downloaded_info_hashes_from_st,
    delete_all_torrents_from_st,
    get_video_url_from_stremthru,
    validate_stremthru_credentials,
)
from streaming_providers.easydebrid.utils import (
    update_easydebrid_cache_status,
    fetch_downloaded_info_hashes_from_easydebrid,
    delete_all_torrents_from_easydebrid,
    get_video_url_from_easydebrid,
    validate_easydebrid_credentials,
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
    "easydebrid": fetch_downloaded_info_hashes_from_easydebrid,
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
    "easydebrid": delete_all_torrents_from_easydebrid,
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
}
