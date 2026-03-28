"""Stremio meta routes - using new DB architecture."""

import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import ValidationError

from db import crud, public_schemas, schemas
from db.config import settings
from db.database import get_async_session_context, get_read_session_context
from db.enums import MediaType
from db.redis_database import REDIS_ASYNC_CLIENT
from db.retry_utils import run_db_read_with_primary_fallback
from utils import const, wrappers

META_CACHE_PREFIX = "meta:"

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_image_url(media, image_type: str) -> str | None:
    """Get image URL from MediaImage relationship by type."""
    if not media.images:
        return None
    for img in media.images:
        if img.image_type == image_type:
            return img.url
    return None


def _get_stars(media) -> list[str]:
    """Get star names from MediaCast relationship."""
    if not media.cast:
        return []
    return [c.person.name for c in media.cast[:10] if c.person]


def _episode_still_thumbnail_url(episode) -> str | None:
    """Pick primary still/thumbnail for Stremio ``Video.thumbnail`` (episode poster)."""
    images = getattr(episode, "images", None) or []
    if not images:
        return None
    stills = [img for img in images if (img.image_type or "") in ("still", "thumbnail")]
    if not stills:
        return None
    primary = next((img for img in stills if img.is_primary), None)
    return (primary or stills[0]).url


def _stremio_video_released(air_date: object | None, *, fallback_year: int) -> str:
    """Stremio requires each episode ``released`` as ISO 8601 (see addon-sdk meta.md Video object)."""
    if air_date is not None:
        s = str(air_date).strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return f"{s[:10]}T00:00:00.000Z"
        if "T" in s or s.endswith("Z"):
            return s
        if s:
            return f"{s}T00:00:00.000Z"
    y = fallback_year if fallback_year and fallback_year > 0 else 2000
    return f"{y}-01-01T00:00:00.000Z"


@router.get(
    "/{secret_str}/meta/{catalog_type}/{meta_id}.json",
    tags=["meta"],
    response_model=public_schemas.MetaItem,
    response_model_exclude_none=True,
    response_model_by_alias=False,
)
@router.get(
    "/meta/{catalog_type}/{meta_id}.json",
    tags=["meta"],
    response_model=public_schemas.MetaItem,
    response_model_exclude_none=True,
    response_model_by_alias=False,
)
@wrappers.auth_required
async def get_meta(
    response: Response,
    catalog_type: MediaType,
    meta_id: str,
) -> schemas.MetaItem:
    """Get metadata for a specific item."""
    response.headers.update(const.CACHE_HEADERS)

    # Check Redis cache first -- meta data is user-independent
    cache_key = f"{META_CACHE_PREFIX}{catalog_type.value}:{meta_id}"
    cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_data:
        try:
            return public_schemas.MetaItem.model_validate_json(cached_data)
        except ValidationError:
            pass

    async def _fetch_meta_and_canonical_id_with_session(session):
        # Use the appropriate CRUD function that loads relationships
        if catalog_type == MediaType.MOVIE:
            media = await crud.get_movie_data_by_id(session, meta_id)
        elif catalog_type == MediaType.SERIES:
            media = await crud.get_series_data_by_id(session, meta_id)
        elif catalog_type == MediaType.TV:
            media = await crud.get_tv_data_by_id(session, meta_id)
        else:
            media = await crud.get_metadata_by_id(session, meta_id, load_relations=True)

        if not media:
            return None, None

        # Get canonical external_id for Stremio
        canonical_ext_id = await crud.get_canonical_external_id(session, media.id)
        return media, canonical_ext_id

    async def _fetch_meta_from_read_replica():
        async with get_read_session_context() as session:
            return await _fetch_meta_and_canonical_id_with_session(session)

    async def _fetch_meta_from_primary():
        async with get_async_session_context() as session:
            return await _fetch_meta_and_canonical_id_with_session(session)

    media, canonical_ext_id = await run_db_read_with_primary_fallback(
        _fetch_meta_from_read_replica,
        _fetch_meta_from_primary,
        operation_name=f"stremio meta fetch {catalog_type.value}:{meta_id}",
        on_fallback=lambda exc: logger.warning(
            "Read replica conflict for stremio meta %s:%s, retrying on primary: %s",
            catalog_type.value,
            meta_id,
            exc,
        ),
    )

    if not media:
        raise HTTPException(status_code=404, detail="Metadata not found")

    # Get IMDb rating from MediaRating table
    imdb_rating = None
    if media.ratings:
        for media_rating in media.ratings:
            if media_rating.provider and media_rating.provider.name.lower() == "imdb":
                imdb_rating = media_rating.rating
                break

    # Get images from MediaImage relationship
    poster = _get_image_url(media, "poster")
    background = _get_image_url(media, "background")
    logo = _get_image_url(media, "logo")

    # Build base meta from Media model
    base_meta = {
        "id": canonical_ext_id,
        "title": media.title,
        "type": media.type.value,
        "poster": poster,
        "background": background,
        "description": media.description,
        "year": media.year,
        "genres": [g.name for g in media.genres if g.name.lower() not in const.ADULT_GENRE_NAMES]
        if media.genres
        else [],
        "stars": _get_stars(media),
        "runtime": f"{media.runtime_minutes} min" if media.runtime_minutes else None,
    }

    if catalog_type == MediaType.SERIES:
        # Get series-specific metadata
        series_meta = media.series_metadata
        fallback_year = media.year if media.year else 2000

        # Build episodes from series_metadata.seasons
        episodes = []
        if series_meta and series_meta.seasons:
            seasons_sorted = sorted(
                series_meta.seasons,
                key=lambda s: (s.season_number is None, s.season_number or 0),
            )
            for season in seasons_sorted:
                eps = season.episodes or []
                for episode in sorted(eps, key=lambda e: (e.episode_number is None, e.episode_number or 0)):
                    episodes.append(
                        public_schemas.Video(
                            id=f"{meta_id}:{season.season_number}:{episode.episode_number}",
                            title=episode.title or f"Episode {episode.episode_number}",
                            released=_stremio_video_released(episode.air_date, fallback_year=fallback_year),
                            overview=episode.overview,
                            thumbnail=_episode_still_thumbnail_url(episode),
                            season=season.season_number,
                            episode=episode.episode_number,
                        )
                    )

        meta_response = public_schemas.Meta(
            imdbRating=imdb_rating,
            end_year=media.end_date.year if media.end_date else None,
            videos=episodes,
            **base_meta,
        )
    elif catalog_type == MediaType.TV:
        # Get TV-specific metadata
        tv_meta = media.tv_metadata

        meta_response = public_schemas.Meta(
            language=tv_meta.tv_language if tv_meta else None,
            country=tv_meta.country if tv_meta else None,
            logo=logo,
            **base_meta,
        )
    elif catalog_type == MediaType.MOVIE:
        meta_response = public_schemas.Meta(
            imdbRating=imdb_rating,
            **base_meta,
        )
    else:
        meta_response = public_schemas.Meta(**base_meta)

    meta_item = public_schemas.MetaItem(meta=meta_response)

    # Cache the result in Redis
    await REDIS_ASYNC_CLIENT.set(
        cache_key,
        meta_item.model_dump_json(exclude_none=True),
        ex=settings.meta_cache_ttl,
    )

    return meta_item
