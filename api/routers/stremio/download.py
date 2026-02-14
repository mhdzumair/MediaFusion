"""Stremio download info routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import HTMLResponse

from db import crud, schemas
from db.config import settings
from db.database import get_read_session
from utils import wrappers
from utils.network import get_user_data, get_user_public_ip
from utils.runtime_const import TEMPLATES

router = APIRouter()


@router.get(
    "/download/{secret_str}/{catalog_type}/{video_id}",
    response_class=HTMLResponse,
    tags=["download"],
)
@router.get(
    "/download/{secret_str}/{catalog_type}/{video_id}/{season}/{episode}",
    response_class=HTMLResponse,
    tags=["download"],
)
@wrappers.auth_required
async def download_info(
    request: Request,
    secret_str: str,
    catalog_type: Literal["movie", "series"],
    video_id: str,
    user_data: Annotated[schemas.UserData, Depends(get_user_data)],
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_read_session),
    season: int = None,
    episode: int = None,
):
    """Get download information for a video."""
    primary_provider = user_data.get_primary_provider()
    if not primary_provider or not primary_provider.download_via_browser:
        raise HTTPException(
            status_code=403,
            detail="Download option is not enabled or no streaming provider configured",
        )

    # Get metadata from PostgreSQL - returns Media model directly (new schema)
    if catalog_type == "movie":
        media = await crud.get_movie_data_by_id(session, video_id)
    else:
        media = await crud.get_series_data_by_id(session, video_id)

    if not media:
        raise HTTPException(status_code=404, detail="Metadata not found")

    user_ip = await get_user_public_ip(request, user_data)

    # Get streams from PostgreSQL
    if catalog_type == "movie":
        streams = await crud.get_movie_streams(session, video_id, user_data, secret_str, user_ip, background_tasks)
    else:
        streams = await crud.get_series_streams(
            session,
            video_id,
            season,
            episode,
            user_data,
            secret_str,
            user_ip,
            background_tasks,
        )

    streaming_provider_path = f"{settings.host_url}/streaming_provider/"
    downloadable_streams = [
        stream for stream in streams if stream.url and stream.url.startswith(streaming_provider_path)
    ]

    if video_id.startswith("tt") and user_data.rpdb_config:
        _poster = f"https://api.ratingposterdb.com/{user_data.rpdb_config.api_key}/imdb/poster-default/{video_id}.jpg?fallback=true"
    else:
        _poster = f"{settings.poster_host_url}/poster/{catalog_type}/{video_id}.jpg"

    # Get background from MediaImage table (v5 schema)
    background_image = await crud.get_primary_image(session, media.id, "background")
    background = background_image.url if background_image else f"{settings.host_url}/static/images/background.jpg"
    title = media.title
    year = media.year
    description = media.description or ""

    # Prepare context with all necessary data
    context = {
        "title": title,
        "year": year,
        "logo_url": settings.logo_url,
        "poster": _poster,
        "background": background,
        "description": description,
        "streams": downloadable_streams,
        "catalog_type": catalog_type,
        "season": season,
        "episode": episode,
        "video_id": video_id,
        "secret_str": secret_str,
        "settings": settings,
        "series_data": None,  # TODO: Update for new schema if needed
    }

    return TEMPLATES.TemplateResponse("html/download_info.html", {"request": request, **context})
