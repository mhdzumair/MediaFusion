"""Stremio search routes."""

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from db import crud, public_schemas
from db.database import get_read_session
from db.enums import MediaType
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData
from scrapers.rpdb import update_rpdb_posters
from utils import wrappers
from utils.network import get_request_namespace, get_user_data

router = APIRouter()


async def get_search_cache_key(
    catalog_type: MediaType,
    catalog_id: str,
    search_query: str,
    user_data: UserData,
    namespace: str,
) -> str:
    """Generate cache key for search results."""
    key_parts = [catalog_type.value, catalog_id, search_query]
    if catalog_type in [MediaType.MOVIE, MediaType.SERIES]:
        key_parts.extend(user_data.nudity_filter + user_data.certification_filter)
    if catalog_type == MediaType.TV:
        key_parts.append(namespace)
    return f"search:{':'.join(key_parts)}"


@router.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/search={search_query}.json",
    tags=["search"],
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
)
@router.get(
    "/catalog/{catalog_type}/{catalog_id}/search={search_query}.json",
    tags=["search"],
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
)
@wrappers.auth_required
async def search_meta(
    request: Request,
    catalog_type: MediaType,
    catalog_id: str,
    search_query: str,
    user_data: UserData = Depends(get_user_data),
    session: AsyncSession = Depends(get_read_session),
) -> public_schemas.Metas:
    """
    Enhanced search endpoint with caching and efficient text search.
    """
    if not search_query.strip():
        return public_schemas.Metas(metas=[])

    namespace = get_request_namespace(request)
    # Generate cache key
    cache_key = await get_search_cache_key(catalog_type, catalog_id, search_query, user_data, namespace)

    # Try to get from cache
    cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_data:
        try:
            metas = public_schemas.Metas.model_validate_json(cached_data)
            return await update_rpdb_posters(metas, user_data, catalog_type)
        except ValidationError:
            pass

    # Perform search
    metas = await crud.search_metadata(
        session=session,
        catalog_type=catalog_type,
        search_query=search_query,
        user_data=user_data,
        namespace=namespace,
    )

    # Cache the results (5 minutes for search results)
    await REDIS_ASYNC_CLIENT.set(
        cache_key,
        metas.model_dump_json(exclude_none=True),
        ex=300,  # 5 minutes cache
    )

    return await update_rpdb_posters(metas, user_data, catalog_type)
