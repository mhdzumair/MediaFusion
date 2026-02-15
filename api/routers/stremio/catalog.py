"""Stremio catalog routes."""

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from pydantic import ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from db import crud, public_schemas
from db.config import settings
from db.database import get_read_session
from db.enums import MediaType
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData
from scrapers.rpdb import update_rpdb_posters
from streaming_providers import mapper
from utils import const, wrappers
from utils.network import get_request_namespace, get_user_data, get_user_public_ip
from utils.parser import fetch_downloaded_info_hashes
from utils.runtime_const import DELETE_ALL_META

router = APIRouter()


def get_cache_key(
    catalog_type: MediaType,
    catalog_id: str,
    skip: int,
    genre: str | None,
    user_data: UserData,
    is_watchlist: bool,
    namespace: str,
    sort: str | None = None,
    sort_dir: str | None = None,
) -> str | None:
    """Generate cache key for catalog queries.

    Args:
        catalog_type: Type of media catalog
        catalog_id: Catalog identifier
        skip: Pagination offset
        genre: Optional genre filter
        user_data: User preferences and filters
        is_watchlist: Whether this is a watchlist query
        namespace: TV namespace (deprecated)
        sort: Sort field (latest, popular, rating, year, title, release_date)
        sort_dir: Sort direction (asc, desc)

    Returns:
        Cache key string or None if caching should be disabled
    """
    if is_watchlist or catalog_type == MediaType.EVENTS:
        return None

    key_parts = [catalog_type.value, catalog_id, str(skip), genre or ""]

    if catalog_type in [MediaType.MOVIE, MediaType.SERIES]:
        key_parts.extend(user_data.nudity_filter + user_data.certification_filter)
    if catalog_type == MediaType.TV:
        key_parts.append(namespace)

    # Include sort preferences in cache key
    key_parts.append(sort or "latest")
    key_parts.append(sort_dir or "desc")

    return f"catalog:{':'.join(key_parts)}"


@router.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/catalog/{catalog_type}/{catalog_id}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/skip={skip}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/catalog/{catalog_type}/{catalog_id}/skip={skip}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/genre={genre}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/catalog/{catalog_type}/{catalog_id}/genre={genre}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/skip={skip}&genre={genre}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/catalog/{catalog_type}/{catalog_id}/skip={skip}&genre={genre}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/genre={genre}&skip={skip}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@router.get(
    "/catalog/{catalog_type}/{catalog_id}/genre={genre}&skip={skip}.json",
    response_model=public_schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@wrappers.auth_required
@wrappers.rate_limit(150, 300, "catalog")
async def get_catalog(
    response: Response,
    request: Request,
    catalog_type: MediaType,
    catalog_id: str,
    skip: int = 0,
    genre: str = None,
    user_data: UserData = Depends(get_user_data),
    session: AsyncSession = Depends(get_read_session),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> public_schemas.Metas:
    """
    Enhanced catalog endpoint with support for watchlists and external services.

    Supports per-catalog sorting configuration via user profile settings.
    Detects watchlist catalogs for any active provider (multi-provider support).
    """
    # Check if the catalog belongs to any active provider's watchlist
    watchlist_provider = None
    is_watchlist_catalog = False
    for provider in user_data.get_active_providers():
        if catalog_id.startswith(f"{provider.service}_watchlist_"):
            watchlist_provider = provider
            is_watchlist_catalog = True
            break

    # Get catalog configuration from user profile (includes sorting preferences)
    catalog_config = user_data.get_catalog_config(catalog_id)
    sort = catalog_config.sort if catalog_config else None
    sort_dir = catalog_config.order if catalog_config else None

    # Handle watchlist info hashes
    info_hashes = None
    if is_watchlist_catalog:
        info_hashes = await fetch_downloaded_info_hashes(user_data, await get_user_public_ip(request, user_data))
        if not info_hashes:
            return public_schemas.Metas(metas=[])

    namespace = get_request_namespace(request)
    # Cache handling
    cache_key = get_cache_key(
        catalog_type,
        catalog_id,
        skip,
        genre,
        user_data,
        is_watchlist_catalog,
        namespace,
        sort,
        sort_dir,
    )
    if cache_key:
        response.headers.update(const.CACHE_HEADERS)
        cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)
        if cached_data:
            try:
                metas = public_schemas.Metas.model_validate_json(cached_data)
                return await update_rpdb_posters(metas, user_data, catalog_type)
            except ValidationError:
                pass
    else:
        response.headers.update(const.NO_CACHE_HEADERS)

    # Handle MDBList catalogs specially
    if catalog_id.startswith("mdblist_") and user_data.mdblist_config:
        # Parse mdblist ID from catalog_id (format: mdblist_{type}_{id})
        parts = catalog_id.split("_")
        if len(parts) >= 3:
            mdblist_id = int(parts[-1])  # Last part is the list ID
            # Find matching list config
            list_config = next(
                (lst for lst in user_data.mdblist_config.lists if lst.id == mdblist_id),
                None,
            )
            if list_config:
                meta_list = await crud.get_mdblist_meta_list(
                    session=session,
                    user_data=user_data,
                    background_tasks=background_tasks,
                    list_config=list_config,
                    catalog_type=catalog_type.value,
                    genre=genre,
                    skip=skip,
                    limit=50,
                )
                metas = public_schemas.Metas(metas=meta_list)
                # Cache result if applicable
                if cache_key:
                    await REDIS_ASYNC_CLIENT.set(
                        cache_key,
                        metas.model_dump_json(exclude_none=True),
                        ex=settings.meta_cache_ttl,
                    )
                return await update_rpdb_posters(metas, user_data, catalog_type)
        # If parsing failed, return empty
        return public_schemas.Metas(metas=[])

    # Get metadata list with sorting preferences
    metas = await crud.get_catalog_meta_list(
        session=session,
        catalog_type=catalog_type,
        catalog_id=catalog_id,
        user_data=user_data,
        skip=skip,
        genre=genre,
        namespace=namespace,
        is_watchlist_catalog=is_watchlist_catalog,
        info_hashes=info_hashes,
        sort=sort,
        sort_dir=sort_dir,
    )

    # Handle watchlist special case: add "Delete All" option for providers that support it
    if (
        is_watchlist_catalog
        and catalog_type == MediaType.MOVIE
        and metas.metas
        and watchlist_provider
        and mapper.DELETE_ALL_WATCHLIST_FUNCTIONS.get(watchlist_provider.service)
    ):
        delete_all_meta = DELETE_ALL_META.model_copy()
        delete_all_meta.id = delete_all_meta.id.format(watchlist_provider.service)
        metas.metas.insert(0, delete_all_meta)

    # Cache result if applicable
    if cache_key:
        await REDIS_ASYNC_CLIENT.set(
            cache_key,
            metas.model_dump_json(exclude_none=True),
            ex=settings.meta_cache_ttl,
        )

    return await update_rpdb_posters(metas, user_data, catalog_type)
