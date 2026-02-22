"""Stremio meta routes - using new DB architecture."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from db import crud, public_schemas, schemas
from db.config import settings
from db.database import get_read_session
from db.enums import MediaType
from db.redis_database import REDIS_ASYNC_CLIENT
from utils import const, wrappers

META_CACHE_PREFIX = "meta:"

router = APIRouter()


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
    session: AsyncSession = Depends(get_read_session),
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
        raise HTTPException(status_code=404, detail="Metadata not found")

    # Get canonical external_id for Stremio
    canonical_ext_id = await crud.get_canonical_external_id(session, media.id)

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

        # Build episodes from series_metadata.seasons
        episodes = []
        if series_meta and series_meta.seasons:
            for season in series_meta.seasons:
                for episode in season.episodes or []:
                    episodes.append(
                        public_schemas.Video(
                            id=f"{meta_id}:{season.season_number}:{episode.episode_number}",
                            title=episode.title or f"Episode {episode.episode_number}",
                            released=str(episode.air_date) if episode.air_date else None,
                            description=episode.overview,
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
        tv_meta = media.tv_metadata[0] if media.tv_metadata else None

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
