"""Stremio stream routes."""

from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)

from db import crud, schemas
from db.config import settings
from utils import const, wrappers
from utils.network import (
    get_request_namespace,
    get_secret_str,
    get_user_data,
    get_user_public_ip,
)

router = APIRouter()


@router.get(
    "/{secret_str}/stream/{catalog_type}/{video_id}.json",
    response_model=schemas.Streams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@router.get(
    "/stream/{catalog_type}/{video_id}.json",
    response_model=schemas.Streams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@router.get(
    "/{secret_str}/stream/{catalog_type}/{video_id}:{season}:{episode}.json",
    response_model=schemas.Streams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@router.get(
    "/stream/{catalog_type}/{video_id}:{season}:{episode}.json",
    response_model=schemas.Streams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@wrappers.auth_required
@wrappers.rate_limit(20, 60 * 60, "stream")
async def get_streams(
    catalog_type: Literal["movie", "series", "tv", "events"],
    video_id: str,
    response: Response,
    request: Request,
    secret_str: str = Depends(get_secret_str),
    season: int = None,
    episode: int = None,
    user_data: schemas.UserData = Depends(get_user_data),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Get streams for a specific video."""
    if "p2p" in settings.disabled_providers and not user_data.has_any_provider():
        return {"streams": []}

    user_ip = await get_user_public_ip(request, user_data)
    user_feeds = []
    if season is None or episode is None:
        season = episode = 1

    if catalog_type == "movie":
        if video_id.startswith("dl"):
            service_name = video_id[2:]
            primary_provider = user_data.get_primary_provider()
            if primary_provider and service_name == primary_provider.service:
                fetched_streams = [
                    schemas.Stream(
                        name=f"{settings.addon_name} {primary_provider.service.title()} 🗑️💩🚨",
                        description=f"🚨💀⚠ Delete all files in {primary_provider.service} watchlist.",
                        url=f"{settings.host_url}/streaming_provider/{secret_str}/delete_all_watchlist",
                    )
                ]
            else:
                raise HTTPException(status_code=404, detail="Meta ID not found.")
        else:
            fetched_streams = await crud.get_movie_streams(
                video_id,
                user_data,
                secret_str,
                user_ip,
                background_tasks,
                user_data.user_id,
            )
            fetched_streams.extend(user_feeds)
    elif catalog_type == "series":
        fetched_streams = await crud.get_series_streams(
            video_id,
            season,
            episode,
            user_data,
            secret_str,
            user_ip,
            background_tasks,
            user_data.user_id,
        )
        fetched_streams.extend(user_feeds)
    elif catalog_type == "events":
        fetched_streams = await crud.get_event_streams(video_id, user_data)
        response.headers.update(const.NO_CACHE_HEADERS)
    else:
        response.headers.update(const.NO_CACHE_HEADERS)
        fetched_streams = await crud.get_tv_streams_formatted(video_id, get_request_namespace(request), user_data)

    return {"streams": fetched_streams}


@router.get(
    "/{secret_str}/kodi/stream/{catalog_type}/{video_id}.json",
    response_model=schemas.RichStreams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@router.get(
    "/kodi/stream/{catalog_type}/{video_id}.json",
    response_model=schemas.RichStreams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@router.get(
    "/{secret_str}/kodi/stream/{catalog_type}/{video_id}:{season}:{episode}.json",
    response_model=schemas.RichStreams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@router.get(
    "/kodi/stream/{catalog_type}/{video_id}:{season}:{episode}.json",
    response_model=schemas.RichStreams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@wrappers.auth_required
@wrappers.rate_limit(20, 60 * 60, "kodi_stream")
async def get_kodi_streams(
    catalog_type: Literal["movie", "series"],
    video_id: str,
    request: Request,
    secret_str: str = Depends(get_secret_str),
    season: int = None,
    episode: int = None,
    user_data: schemas.UserData = Depends(get_user_data),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Get Kodi-focused rich stream payloads with structured metadata."""
    if "p2p" in settings.disabled_providers and not user_data.has_any_provider():
        return {"streams": []}

    user_ip = await get_user_public_ip(request, user_data)
    if season is None or episode is None:
        season = episode = 1

    if catalog_type == "movie":
        if video_id.startswith("dl"):
            raise HTTPException(status_code=404, detail="Meta ID not found.")
        fetched_streams = await crud.get_movie_streams(
            video_id,
            user_data,
            secret_str,
            user_ip,
            background_tasks,
            user_data.user_id,
            return_rich=True,
        )
    else:
        fetched_streams = await crud.get_series_streams(
            video_id,
            season,
            episode,
            user_data,
            secret_str,
            user_ip,
            background_tasks,
            user_data.user_id,
            return_rich=True,
        )

    return {"streams": fetched_streams}
