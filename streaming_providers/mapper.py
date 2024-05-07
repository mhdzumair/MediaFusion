from streaming_providers.alldebrid.utils import (
    update_ad_cache_status,
    fetch_downloaded_info_hashes_from_ad,
    delete_all_torrents_from_ad,
)
from streaming_providers.debridlink.utils import (
    update_dl_cache_status,
    fetch_downloaded_info_hashes_from_dl,
    delete_all_torrents_from_dl,
)
from streaming_providers.offcloud.utils import (
    update_oc_cache_status,
    fetch_downloaded_info_hashes_from_oc,
    delete_all_torrents_from_oc,
)
from streaming_providers.pikpak.utils import (
    update_pikpak_cache_status,
    fetch_downloaded_info_hashes_from_pikpak,
    delete_all_torrents_from_pikpak,
)
from streaming_providers.premiumize.utils import (
    update_pm_cache_status,
    fetch_downloaded_info_hashes_from_premiumize,
    delete_all_torrents_from_pm,
)
from streaming_providers.qbittorrent.utils import (
    update_qbittorrent_cache_status,
    fetch_info_hashes_from_webdav,
    delete_all_torrents_from_qbittorrent,
)
from streaming_providers.realdebrid.utils import (
    update_rd_cache_status,
    fetch_downloaded_info_hashes_from_rd,
    delete_all_watchlist_rd,
)
from streaming_providers.seedr.utils import (
    update_seedr_cache_status,
    fetch_downloaded_info_hashes_from_seedr,
    delete_all_torrents_from_seedr,
)
from streaming_providers.torbox.utils import (
    update_torbox_cache_status,
    fetch_downloaded_info_hashes_from_torbox,
    delete_all_torrents_from_torbox,
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
}
