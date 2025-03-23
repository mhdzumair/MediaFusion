import math
from typing import Dict, List, Optional, Any, Literal

from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel

from db import crud, schemas
from db.config import settings
from utils import const
from utils.network import get_user_data, get_user_public_ip

# Create a router with more appropriate naming for the frontend
router = APIRouter(prefix="/api/v1", tags=["frontend"])


# ---- Data Models ----

class AppConfig(BaseModel):
    """Base configuration data for the application"""
    addon_name: str
    logo_url: str
    host_url: str
    poster_host_url: Optional[str]
    version: str
    description: str
    branding_description: str
    is_public_instance: bool
    disabled_providers: List[str]
    authentication_required: bool


class UserConfig(BaseModel):
    """User-specific configuration data"""
    user_data: Dict[str, Any]
    configured_fields: List[str]


class ValidationResult(BaseModel):
    """Data model for validation results"""
    status: str
    message: Optional[str] = None


class StreamData(BaseModel):
    """Data model for stream information"""
    title: str
    year: int
    poster: str
    background: str
    description: str
    streams: List[Dict[str, Any]]
    catalog_type: str
    season: Optional[int] = None
    episode: Optional[int] = None
    video_id: str
    secret_str: str
    series_data: Optional[str] = None


# ---- API Endpoints ----

@router.get("/app-config", response_model=AppConfig)
async def get_app_config():
    """
    Get basic application configuration.
    Replaces the former home endpoint with more specific naming.
    """
    return {
        "addon_name": settings.addon_name,
        "logo_url": settings.logo_url,
        "host_url": settings.host_url,
        "poster_host_url": settings.poster_host_url,
        "version": settings.version,
        "description": settings.description,
        "branding_description": settings.branding_description,
        "is_public_instance": settings.is_public_instance,
        "disabled_providers": settings.disabled_providers,
        "authentication_required": settings.api_password is not None and not settings.is_public_instance,
    }


@router.get("/user-config", response_model=UserConfig)
async def get_user_config(
    user_data: schemas.UserData = Depends(get_user_data),
    secret_str: Optional[str] = None,
):
    """
    Get user-specific configuration.
    Returns only user configuration without default values from constants.
    """
    configured_fields = []

    # Handle sensitive data masking
    if user_data.streaming_provider:
        user_data.streaming_provider.password = "••••••••"
        user_data.streaming_provider.token = "••••••••"
        configured_fields.extend(["provider_token", "password"])

        if user_data.streaming_provider.qbittorrent_config:
            user_data.streaming_provider.qbittorrent_config.qbittorrent_password = "••••••••"
            user_data.streaming_provider.qbittorrent_config.webdav_password = "••••••••"
            configured_fields.extend(["qbittorrent_password", "webdav_password"])

    if user_data.mediaflow_config:
        user_data.mediaflow_config.api_password = "••••••••"
        configured_fields.append("mediaflow_api_password")

    if user_data.rpdb_config:
        user_data.rpdb_config.api_key = "••••••••"
        configured_fields.append("rpdb_api_key")

    user_data.api_password = None

    return {
        "user_data": user_data.model_dump(),
        "configured_fields": configured_fields,
    }


@router.get("/system-constants")
async def get_system_constants():
    """
    Get all system constants needed by the frontend.
    Renamed from 'constants' to be more descriptive.
    """
    return {
        "CATALOG_DATA": const.CATALOG_DATA,
        "RESOLUTIONS": const.RESOLUTIONS,
        "TORRENT_SORTING_PRIORITY_OPTIONS": const.TORRENT_SORTING_PRIORITY_OPTIONS,
        "SUPPORTED_LANGUAGES": const.SUPPORTED_LANGUAGES,
        "QUALITY_GROUPS": const.QUALITY_GROUPS,
    }


@router.get("/download/{secret_str}/{catalog_type}/{video_id}", response_model=StreamData)
@router.get("/download/{secret_str}/{catalog_type}/{video_id}/{season}/{episode}", response_model=StreamData)
async def get_download_info(
    request: Request,
    secret_str: str,
    catalog_type: Literal["movie", "series"],
    video_id: str,
    user_data: schemas.UserData = Depends(get_user_data),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    season: int = None,
    episode: int = None,
):
    """
    Get download information for the React frontend.
    Replaces the Jinja template rendering for the download page.
    """
    if (
        not user_data.streaming_provider
        or not user_data.streaming_provider.download_via_browser
    ):
        raise HTTPException(
            status_code=403,
            detail="Download option is not enabled or no streaming provider configured",
        )

    metadata = (
        await crud.get_movie_data_by_id(video_id)
        if catalog_type == "movie"
        else await crud.get_series_data_by_id(video_id)
    )
    if not metadata:
        raise HTTPException(status_code=404, detail="Metadata not found")

    user_ip = await get_user_public_ip(request, user_data)

    if catalog_type == "movie":
        streams = await crud.get_movie_streams(
            user_data, secret_str, video_id, user_ip, background_tasks
        )
    else:
        streams = await crud.get_series_streams(
            user_data, secret_str, video_id, season, episode, user_ip, background_tasks
        )

    streaming_provider_path = f"{settings.host_url}/streaming_provider/"
    downloadable_streams = [
        stream.model_dump()
        for stream in streams
        if stream.url and stream.url.startswith(streaming_provider_path)
    ]

    if video_id.startswith("tt") and user_data.rpdb_config:
        _poster = f"https://api.ratingposterdb.com/{user_data.rpdb_config.api_key}/imdb/poster-default/{video_id}.jpg?fallback=true"
    else:
        _poster = f"{settings.poster_host_url}/poster/{catalog_type}/{video_id}.jpg"

    background = (
        metadata.background
        if metadata.background
        else f"{settings.host_url}/static/images/background.jpg"
    )

    return {
        "title": metadata.title,
        "year": metadata.year,
        "poster": _poster,
        "background": background,
        "description": metadata.description,
        "streams": downloadable_streams,
        "catalog_type": catalog_type,
        "season": season,
        "episode": episode,
        "video_id": video_id,
        "secret_str": secret_str,
        "series_data": (
            metadata.model_dump_json(
                include={"episodes": {"__all__": {"season_number", "episode_number"}}}
            )
            if catalog_type == "series"
            else None
        ),
    }
