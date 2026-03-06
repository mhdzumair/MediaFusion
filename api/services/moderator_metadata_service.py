"""Service layer for moderator metadata management workflows."""

import logging
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.admin.admin import (
    _build_metadata_search_condition,
    format_metadata_response,
    get_metadata_with_relations,
    update_metadata_aka_titles,
    update_metadata_cast,
    update_metadata_genres,
    update_metadata_parental_certificates,
)
from api.schemas.metadata_management import (
    ExternalMetadataPreview,
    FetchExternalRequest,
    MetadataListResponse,
    MetadataResponse,
    MigrateIdRequest,
    SearchExternalRequest,
    SearchExternalResponse,
)
from db.crud import add_external_id, get_all_external_ids_dict, get_canonical_external_id
from db.enums import MediaType, NudityStatus
from db.models import Media, MediaCast, MediaExternalID, MediaRating

logger = logging.getLogger(__name__)


def _convert_admin_metadata_response(response) -> MetadataResponse:
    return MetadataResponse.model_validate(response.model_dump())


async def list_metadata(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 20,
    media_type: Literal["movie", "series", "tv"] | None = None,
    search: str | None = None,
    has_streams: bool | None = None,
) -> MetadataListResponse:
    # Base query with eager loading of relationships
    query = select(Media).options(
        selectinload(Media.genres),
        selectinload(Media.catalogs),
        selectinload(Media.aka_titles),
        selectinload(Media.parental_certificates),
        selectinload(Media.cast).selectinload(MediaCast.person),
        selectinload(Media.images),
        selectinload(Media.ratings).selectinload(MediaRating.provider),
    )

    # Apply filters
    if media_type:
        type_map = {
            "movie": MediaType.MOVIE,
            "series": MediaType.SERIES,
            "tv": MediaType.TV,
        }
        query = query.where(Media.type == type_map[media_type])

    if search:
        query = query.where(_build_metadata_search_condition(search))

    if has_streams is not None:
        if has_streams:
            query = query.where(Media.total_streams > 0)
        else:
            query = query.where(Media.total_streams == 0)

    # Count total (without eager loading for efficiency)
    count_query = select(func.count(Media.id))
    if media_type:
        type_map = {
            "movie": MediaType.MOVIE,
            "series": MediaType.SERIES,
            "tv": MediaType.TV,
        }
        count_query = count_query.where(Media.type == type_map[media_type])
    if search:
        count_query = count_query.where(_build_metadata_search_condition(search))
    if has_streams is not None:
        if has_streams:
            count_query = count_query.where(Media.total_streams > 0)
        else:
            count_query = count_query.where(Media.total_streams == 0)
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page).order_by(Media.created_at.desc())

    result = await session.exec(query)
    items = result.all()

    # Build responses
    responses = []
    for base in items:
        responses.append(_convert_admin_metadata_response(format_metadata_response(base)))

    return MetadataListResponse(
        items=responses,
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page if total > 0 else 1,
    )


async def get_metadata(session: AsyncSession, media_id: int) -> MetadataResponse:
    result = await get_metadata_with_relations(session, media_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail="Metadata not found",
        )

    base, specific = result
    ext_ids_dict = await get_all_external_ids_dict(session, media_id)
    return _convert_admin_metadata_response(format_metadata_response(base, specific, ext_ids_dict=ext_ids_dict))


async def fetch_external_metadata(
    session: AsyncSession,
    media_id: int,
    request: FetchExternalRequest,
) -> ExternalMetadataPreview:
    # Verify metadata exists
    base = await session.get(Media, media_id)

    if not base:
        raise HTTPException(status_code=404, detail="Metadata not found")

    media_type = "movie" if base.type == MediaType.MOVIE else "series"

    try:
        if request.provider == "imdb":
            from scrapers.imdb_data import get_imdb_title_data

            data = await get_imdb_title_data(request.external_id, media_type)
        else:  # tmdb
            from scrapers.tmdb_data import get_tmdb_data

            data = await get_tmdb_data(request.external_id, media_type, load_episodes=False)

        if not data:
            raise HTTPException(
                status_code=404,
                detail=f"No data found on {request.provider.upper()} for ID {request.external_id}",
            )

        return ExternalMetadataPreview(
            provider=request.provider,
            external_id=request.external_id,
            title=data.get("title", ""),
            year=data.get("year"),
            description=data.get("description"),
            poster=data.get("poster"),
            background=data.get("background"),
            genres=data.get("genres", []),
            imdb_rating=data.get("imdb_rating"),
            tmdb_rating=data.get("tmdb_rating"),
            nudity_status=data.get("parent_guide_nudity_status"),
            parental_certificates=data.get("parent_guide_certificates", []),
            stars=data.get("stars", []),
            aka_titles=data.get("aka_titles", []),
            runtime=data.get("runtime"),
            imdb_id=data.get("imdb_id"),
            tmdb_id=data.get("tmdb_id"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error fetching external metadata: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch metadata: {str(exc)}")


async def apply_external_metadata(
    session: AsyncSession,
    media_id: int,
    request: FetchExternalRequest,
) -> MetadataResponse:
    # Verify metadata exists
    base = await session.get(Media, media_id)

    if not base:
        raise HTTPException(status_code=404, detail="Metadata not found")

    media_type = "movie" if base.type == MediaType.MOVIE else "series"

    try:
        if request.provider == "imdb":
            from scrapers.imdb_data import get_imdb_title_data

            data = await get_imdb_title_data(request.external_id, media_type)
        else:  # tmdb
            from scrapers.tmdb_data import get_tmdb_data

            data = await get_tmdb_data(request.external_id, media_type, load_episodes=False)

        if not data:
            raise HTTPException(
                status_code=404,
                detail=f"No data found on {request.provider.upper()} for ID {request.external_id}",
            )

        # Update base metadata
        if data.get("title"):
            base.title = data["title"]
        if data.get("year"):
            base.year = data["year"]
        if data.get("description"):
            base.description = data["description"]
        if data.get("runtime"):
            # Parse runtime to minutes
            runtime_str = data["runtime"]
            if runtime_str:
                try:
                    if "min" in runtime_str.lower():
                        base.runtime_minutes = int(runtime_str.lower().replace("min", "").strip())
                except (ValueError, TypeError):
                    pass

        # Update nudity status on Media
        nudity = data.get("parent_guide_nudity_status")
        if nudity:
            try:
                base.nudity_status = NudityStatus(nudity)
            except ValueError:
                pass

        if request.provider == "imdb" and data.get("imdb_id"):
            await add_external_id(session, base.id, "imdb", data["imdb_id"])
        elif request.provider == "tmdb" and data.get("tmdb_id"):
            await add_external_id(session, base.id, "tmdb", str(data["tmdb_id"]))

        session.add(base)

        # Update relationships
        if data.get("genres"):
            await update_metadata_genres(session, base.id, data["genres"])

        if data.get("stars"):
            await update_metadata_cast(session, base.id, data["stars"])

        if data.get("parent_guide_certificates"):
            await update_metadata_parental_certificates(session, base.id, data["parent_guide_certificates"])

        if data.get("aka_titles"):
            await update_metadata_aka_titles(session, base.id, data["aka_titles"])

        await session.commit()

        # Re-fetch with all relationships for response
        result = await get_metadata_with_relations(session, base.id)
        base, specific = result
        ext_ids_dict = await get_all_external_ids_dict(session, base.id)

        logger.info(f"Applied external metadata from {request.provider} to {media_id}")
        return _convert_admin_metadata_response(format_metadata_response(base, specific, ext_ids_dict=ext_ids_dict))

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error applying external metadata: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to apply metadata: {str(exc)}")


async def migrate_metadata_id(
    session: AsyncSession,
    media_id: int,
    request: MigrateIdRequest,
) -> MetadataResponse:
    # Verify metadata exists
    base = await session.get(Media, media_id)

    if not base:
        raise HTTPException(status_code=404, detail="Metadata not found")

    # Get current canonical ID for logging
    old_id = await get_canonical_external_id(session, base.id)
    new_id = request.new_external_id.strip()

    # Validate new ID format and determine provider
    if new_id.startswith("tt"):
        provider = "imdb"
        provider_id = new_id
    elif new_id.startswith("tmdb:"):
        provider = "tmdb"
        provider_id = new_id[5:]  # Remove 'tmdb:' prefix
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid external ID format. Use 'tt1234567' for IMDb or 'tmdb:12345' for TMDB",
        )

    # Check if new ID already exists for another media
    existing_check = await session.exec(
        select(MediaExternalID).where(
            MediaExternalID.provider == provider,
            MediaExternalID.external_id == provider_id,
            MediaExternalID.media_id != base.id,
        )
    )
    if existing_check.first():
        raise HTTPException(
            status_code=409,
            detail=f"External ID {new_id} is already in use by another media item",
        )

    # Add or update the external ID in MediaExternalID table
    await add_external_id(session, base.id, provider, provider_id)
    await session.commit()

    # Re-fetch with all relationships for response
    result = await get_metadata_with_relations(session, base.id)
    base, specific = result
    ext_ids_dict = await get_all_external_ids_dict(session, base.id)

    logger.info(f"Migrated metadata ID from {old_id} to {new_id}")
    return _convert_admin_metadata_response(format_metadata_response(base, specific, ext_ids_dict=ext_ids_dict))


async def search_external_metadata(request: SearchExternalRequest) -> SearchExternalResponse:
    try:
        if request.provider == "imdb":
            from scrapers.imdb_data import search_multiple_imdb

            results = await search_multiple_imdb(
                title=request.title,
                year=request.year,
                media_type=request.media_type,
                limit=10,
            )
        else:  # tmdb
            from scrapers.tmdb_data import search_multiple_tmdb

            results = await search_multiple_tmdb(
                title=request.title,
                year=request.year,
                media_type=request.media_type,
                limit=10,
            )

        previews = []
        for data in results:
            previews.append(
                ExternalMetadataPreview(
                    provider=request.provider,
                    external_id=data.get("imdb_id") or data.get("tmdb_id") or "",
                    title=data.get("title", ""),
                    year=data.get("year"),
                    description=data.get("description"),
                    poster=data.get("poster"),
                    background=data.get("background"),
                    genres=data.get("genres", []),
                    imdb_rating=data.get("imdb_rating"),
                    tmdb_rating=data.get("tmdb_rating"),
                    nudity_status=data.get("parent_guide_nudity_status"),
                    parental_certificates=data.get("parent_guide_certificates", []),
                    stars=data.get("stars", [])[:5],  # Limit stars for preview
                    aka_titles=data.get("aka_titles", [])[:5],  # Limit aka titles
                    runtime=data.get("runtime"),
                    imdb_id=data.get("imdb_id"),
                    tmdb_id=data.get("tmdb_id"),
                )
            )

        return SearchExternalResponse(results=previews)

    except Exception as exc:
        logger.error(f"Error searching external metadata: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to search: {str(exc)}")
