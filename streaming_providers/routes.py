import logging

from fastapi import (
    Request,
    Response,
    HTTPException,
    APIRouter,
)
from fastapi.responses import RedirectResponse

from db import crud
from db.config import settings
from streaming_providers.alldebrid.utils import get_direct_link_from_alldebrid
from streaming_providers.debridlink.api import router as debridlink_router
from streaming_providers.debridlink.utils import get_direct_link_from_debridlink
from streaming_providers.exceptions import ProviderException
from streaming_providers.offcloud.utils import get_direct_link_from_offcloud
from streaming_providers.pikpak.utils import get_direct_link_from_pikpak
from streaming_providers.premiumize.api import router as premiumize_router
from streaming_providers.premiumize.utils import get_direct_link_from_premiumize
from streaming_providers.realdebrid.api import router as realdebrid_router
from streaming_providers.realdebrid.utils import get_direct_link_from_realdebrid
from streaming_providers.seedr.api import router as seedr_router
from streaming_providers.seedr.utils import get_direct_link_from_seedr
from streaming_providers.torbox.utils import get_direct_link_from_torbox
from streaming_providers.qbittorrent.utils import get_direct_link_from_qbittorrent
from utils import crypto, torrent, wrappers, const

router = APIRouter()


@router.get("/{secret_str}/stream", tags=["streaming_provider"])
@wrappers.exclude_rate_limit
@wrappers.auth_required
async def streaming_provider_endpoint(
    secret_str: str,
    info_hash: str,
    response: Response,
    request: Request,
    season: int = None,
    episode: int = None,
):
    response.headers.update(const.NO_CACHE_HEADERS)

    user_data = request.scope.get("user", crypto.decrypt_user_data(secret_str))
    if not user_data.streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    stream = await crud.get_stream_by_info_hash(info_hash)
    if not stream:
        raise HTTPException(status_code=400, detail="Stream not found.")

    magnet_link = await torrent.convert_info_hash_to_magnet(
        info_hash, stream.announce_list
    )

    episode_data = stream.get_episode(season, episode)
    filename = episode_data.filename if episode_data else stream.filename

    try:
        if user_data.streaming_provider.service == "seedr":
            video_url = await get_direct_link_from_seedr(
                info_hash, magnet_link, user_data, stream, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "realdebrid":
            video_url = get_direct_link_from_realdebrid(
                info_hash, magnet_link, user_data, filename, stream.file_index, 1, 0
            )
        elif user_data.streaming_provider.service == "alldebrid":
            video_url = get_direct_link_from_alldebrid(
                info_hash, magnet_link, user_data, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "offcloud":
            video_url = get_direct_link_from_offcloud(
                info_hash, magnet_link, user_data, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "pikpak":
            video_url = await get_direct_link_from_pikpak(
                info_hash, magnet_link, user_data, stream, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "torbox":
            video_url = get_direct_link_from_torbox(
                info_hash, magnet_link, user_data, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "premiumize":
            video_url = get_direct_link_from_premiumize(
                info_hash, magnet_link, user_data, stream.torrent_name, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "qbittorrent":
            video_url = await get_direct_link_from_qbittorrent(
                info_hash, magnet_link, user_data, stream, filename, 1, 0
            )
        else:
            video_url = get_direct_link_from_debridlink(
                info_hash, magnet_link, user_data, filename, stream.file_index, 1, 0
            )
    except ProviderException as error:
        logging.error(
            "Exception occurred for %s: %s",
            info_hash,
            error.message,
            exc_info=True if error.video_file_name == "api_error.mp4" else False,
        )
        video_url = f"{settings.host_url}/static/exceptions/{error.video_file_name}"
    except Exception as e:
        logging.error("Exception occurred for %s: %s", info_hash, e, exc_info=True)
        video_url = f"{settings.host_url}/static/exceptions/api_error.mp4"

    return RedirectResponse(url=video_url, headers=response.headers)


router.include_router(seedr_router, prefix="/seedr", tags=["seedr"])
router.include_router(realdebrid_router, prefix="/realdebrid", tags=["realdebrid"])
router.include_router(debridlink_router, prefix="/debridlink", tags=["debridlink"])
router.include_router(premiumize_router, prefix="/premiumize", tags=["premiumize"])
