"""Stremio poster routes."""

import json
import logging
import random
from io import BytesIO
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db import crud, schemas
from db.database import get_read_session
from db.models.links import MediaGenreLink
from db.models.reference import Genre
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas.media import MediaFusionEventsMetaData
from utils import poster
from utils.runtime_const import SPORTS_ARTIFACTS

router = APIRouter()
logger = logging.getLogger(__name__)


def raise_poster_error(error_message: str):
    """Raise a 404 error on poster error."""
    raise HTTPException(status_code=404, detail=error_message)


async def _get_sports_poster_url(session: AsyncSession, media_id: int) -> str | None:
    """Pick a random poster URL from SPORTS_ARTIFACTS based on media genres.

    Looks up the media's genres and tries to find a matching key in
    SPORTS_ARTIFACTS. Falls back to "Other Sports" if no match is found.
    """
    # Fetch genre names for this media
    query = (
        select(Genre.name)
        .join(MediaGenreLink, MediaGenreLink.genre_id == Genre.id)
        .where(MediaGenreLink.media_id == media_id)
    )
    result = await session.exec(query)
    genre_names = result.all()

    # Try to match a genre against SPORTS_ARTIFACTS keys
    for genre_name in genre_names:
        # Try exact match first
        if genre_name in SPORTS_ARTIFACTS:
            posters = SPORTS_ARTIFACTS[genre_name].get("poster", [])
            if posters:
                return random.choice(posters)

        # Try case-insensitive match
        for artifact_key, artifacts in SPORTS_ARTIFACTS.items():
            if genre_name.lower() == artifact_key.lower():
                posters = artifacts.get("poster", [])
                if posters:
                    return random.choice(posters)

    # Fallback to "Other Sports" or first available
    fallback = SPORTS_ARTIFACTS.get("Other Sports") or SPORTS_ARTIFACTS.get("Sports")
    if fallback:
        posters = fallback.get("poster", [])
        if posters:
            return random.choice(posters)

    return None


@router.get(
    "/poster/{catalog_type}/{mediafusion_id}.jpg",
    tags=["poster"],
    response_class=StreamingResponse,
)
async def get_poster(
    catalog_type: str,
    mediafusion_id: str,
    session: AsyncSession = Depends(get_read_session),
):
    """Get poster image for a media item."""
    # Ensure the mediafusion_id is URL-decoded (e.g. mf%3A955480 -> mf:955480)
    mediafusion_id = unquote(mediafusion_id)

    # Check Redis cache first
    cache_key = f"{catalog_type}_{mediafusion_id}.jpg"
    cached_image = await REDIS_ASYNC_CLIENT.get(cache_key)

    if cached_image:
        return StreamingResponse(
            BytesIO(cached_image),
            media_type="image/jpeg",
        )

    # Get metadata based on catalog type
    is_add_title_to_poster = False

    if catalog_type == "events":
        # Events are stored in Redis, not database
        mediafusion_data = await REDIS_ASYNC_CLIENT.get(f"events:{mediafusion_id}")
        if not mediafusion_data:
            return raise_poster_error("Event not found.")

        mediafusion_data = MediaFusionEventsMetaData.model_validate(json.loads(mediafusion_data))
        poster_url = mediafusion_data.poster
        meta_id = mediafusion_data.id
        title = mediafusion_data.title
        imdb_rating = None
        is_add_title_to_poster = mediafusion_data.is_add_title_to_poster
    else:
        # Get from database - returns Media model directly (new schema)
        media = await crud.get_metadata_by_id(session, mediafusion_id)
        if not media:
            return raise_poster_error("MediaFusion ID not found.")

        # Get poster from MediaImage table (v5 schema)
        poster_image = await crud.get_primary_image(session, media.id, "poster")
        poster_url = poster_image.url if poster_image else None
        meta_id = await crud.get_canonical_external_id(session, media.id)
        title = media.title
        imdb_rating = None  # Now in MediaRating table
        is_add_title_to_poster = media.is_add_title_to_poster

        # For sports content with no stored poster, pick a random one from sports artifacts
        if not poster_url and is_add_title_to_poster:
            poster_url = await _get_sports_poster_url(session, media.id)

    if not poster_url:
        return raise_poster_error("Poster not found.")

    try:
        # Create poster data using Pydantic model
        poster_data = schemas.PosterData(
            id=meta_id,
            poster=poster_url,
            title=title,
            imdb_rating=imdb_rating,
            is_add_title_to_poster=is_add_title_to_poster,
        )
        image_byte_io = await poster.create_poster(poster_data)
        # Convert BytesIO to bytes for Redis
        image_bytes = image_byte_io.getvalue()
        # Save the generated image to Redis. expire in 7 days
        await REDIS_ASYNC_CLIENT.set(cache_key, image_bytes, ex=604800)
        image_byte_io.seek(0)
        return StreamingResponse(
            image_byte_io,
            media_type="image/jpeg",
        )

    except Exception as e:
        logger.exception(f"Error creating poster for {mediafusion_id}: {e}")
        return raise_poster_error("Error creating poster.")
