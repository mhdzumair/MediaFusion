import asyncio
import json
import logging
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Literal, Annotated

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    BackgroundTasks,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.responses import HTMLResponse

from api import middleware
from api.scheduler import setup_scheduler
from db import crud, database, schemas
from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import SortingOption
from kodi.routes import kodi_router
from metrics.routes import metrics_router
from api.frontend_api import router as frontend_api_router
from scrapers.routes import router as scrapers_router
from scrapers.rpdb import update_rpdb_posters, update_rpdb_poster
from api.rss_feeds import router as rss_feeds_router
from streaming_providers import mapper
from streaming_providers.routes import router as streaming_provider_router
from streaming_providers.validator import validate_provider_credentials
from utils import const, poster, torrent, wrappers
from utils.crypto import crypto_utils
from utils.lock import (
    acquire_scheduler_lock,
    maintain_heartbeat,
    release_scheduler_lock,
)
from utils.network import get_request_namespace, get_user_public_ip, get_user_data, get_secret_str
from utils.parser import generate_manifest
from utils.runtime_const import (
    DELETE_ALL_META,
    DELETE_ALL_META_ITEM,
    TEMPLATES,
)
from utils.validation_helper import (
    validate_mediaflow_proxy_credentials,
    validate_rpdb_token,
    validate_mdblist_token,
)

logging.basicConfig(
    format="%(levelname)s::%(asctime)s::%(pathname)s::%(lineno)d - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    level=settings.logging_level,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Startup logic
    await database.init()
    await torrent.init_best_trackers()
    scheduler = None
    scheduler_lock = None

    if not settings.disable_all_scheduler:
        acquired, scheduler_lock = await acquire_scheduler_lock()
        if acquired:
            try:
                scheduler = AsyncIOScheduler()
                setup_scheduler(scheduler)
                scheduler.start()
                await asyncio.create_task(maintain_heartbeat())
            except Exception as e:
                await release_scheduler_lock(scheduler_lock)
                raise e

    yield

    # Shutdown logic
    if scheduler:
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:
            logging.exception("Error shutting down scheduler, %s", e)
        finally:
            await release_scheduler_lock(scheduler_lock)

    await REDIS_ASYNC_CLIENT.aclose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# set CORS headers
@app.middleware("http")
async def add_cors_header(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(const.CORS_HEADERS)
    if "cache-control" not in response.headers:
        response.headers.update(const.CACHE_HEADERS)
    return response


app.add_middleware(middleware.RateLimitMiddleware)
app.add_middleware(middleware.UserDataMiddleware)
app.add_middleware(middleware.TimingMiddleware)
app.add_middleware(middleware.SecureLoggingMiddleware)

app.mount("/static", StaticFiles(directory="resources"), name="static")


# Keep the existing template-based routes for backward compatibility
@app.get("/", tags=["home"])
async def get_home(request: Request):
    return TEMPLATES.TemplateResponse(
        "html/home.html",
        {
            "request": request,
            "addon_name": settings.addon_name,
            "logo_url": settings.logo_url,
            "version": f"{settings.version}",
            "description": settings.description,
            "branding_description": settings.branding_description,
        },
    )


@app.get("/health", tags=["health"])
@wrappers.exclude_rate_limit
async def health():
    return {"status": "ok"}


@app.get("/favicon.ico")
async def get_favicon():
    return RedirectResponse(url=settings.logo_url)


@app.get("/configure", tags=["configure"])
@app.get("/{secret_str}/configure", tags=["configure"])
async def configure(
    response: Response,
    request: Request,
    user_data: schemas.UserData = Depends(get_user_data),
    kodi_code: str = None,
    secret_str: str = None,
):
    response.headers.update(const.NO_CACHE_HEADERS)

    configured_fields = []
    mdblist_configured_lists = []
    catalogs_data = const.CATALOG_DATA.copy()
    # Remove the password from the streaming provider
    if user_data.streaming_provider:
        user_data.streaming_provider.password = "••••••••"
        user_data.streaming_provider.token = "••••••••"
        configured_fields.extend(["provider_token", "password"])

        if user_data.streaming_provider.qbittorrent_config:
            user_data.streaming_provider.qbittorrent_config.qbittorrent_password = (
                "••••••••"
            )
            user_data.streaming_provider.qbittorrent_config.webdav_password = "••••••••"
            configured_fields.extend(["qbittorrent_password", "webdav_password"])

    # Remove the password from the mediaflow proxy
    if user_data.mediaflow_config:
        user_data.mediaflow_config.api_password = "••••••••"
        configured_fields.append("mediaflow_api_password")

    # Check RPDB configuration
    if user_data.rpdb_config:
        user_data.rpdb_config.api_key = "••••••••"
        configured_fields.append("rpdb_api_key")

    # Check MDBList configuration
    if user_data.mdblist_config:
        # user_data.mdblist_config.api_key = "••••••••"
        # configured_fields.append("mdblist_api_key")
        for mdblist in user_data.mdblist_config.lists:
            mdblist_configured_lists.append(mdblist.model_dump())
            catalogs_data[f"mdblist_{mdblist.catalog_type}_{mdblist.id}"] = (
                f"MDBList: {mdblist.title}"
            )

    user_data.api_password = None

    # Prepare catalogs based on user preferences or default order
    sorted_catalogs = sorted(
        catalogs_data.items(),
        key=lambda x: (
            user_data.selected_catalogs.index(x[0])
            if x[0] in user_data.selected_catalogs
            else len(user_data.selected_catalogs)
        ),
    )

    sorted_sorting_options = user_data.torrent_sorting_priority + [
        SortingOption(key=option)
        for option in const.TORRENT_SORTING_PRIORITY_OPTIONS
        if not user_data.is_sorting_option_present(option)
    ]

    # Sort languages based on user preference
    sorted_languages = user_data.language_sorting + [
        lang
        for lang in const.SUPPORTED_LANGUAGES
        if lang not in user_data.language_sorting
    ]

    return TEMPLATES.TemplateResponse(
        "html/configure.html",
        {
            "request": request,
            "user_data": user_data.model_dump(),
            "logo_url": settings.logo_url,
            "addon_name": settings.addon_name,
            "catalogs": sorted_catalogs,
            "resolutions": const.RESOLUTIONS,
            "sorting_options": sorted_sorting_options,
            "sorted_languages": sorted_languages,
            "quality_groups": const.QUALITY_GROUPS,
            "authentication_required": settings.api_password is not None
            and not settings.is_public_instance,
            "kodi_code": kodi_code,
            "disabled_providers": settings.disabled_providers,
            "configured_fields": configured_fields,
            "secret_str": secret_str,
            "mdblist_configured_lists": mdblist_configured_lists,
        },
    )


@app.get("/manifest.json", tags=["manifest"])
@app.get("/{secret_str}/manifest.json", tags=["manifest"])
@wrappers.auth_required
async def get_manifest(
    response: Response,
    user_data: schemas.UserData = Depends(get_user_data),
):
    response.headers.update(const.NO_CACHE_HEADERS)
    catalog_types = ["movie", "series", "tv"]
    genre_tasks = [crud.get_genres(catalog_type) for catalog_type in catalog_types]
    try:
        genres_list = await asyncio.gather(*genre_tasks)
    except Exception as e:
        logging.exception("Error gathering genres: %s", e)
        genres_list = [[] for _ in catalog_types]  # Provide default empty list
    genres = dict(zip(catalog_types, genres_list))

    return await generate_manifest(user_data, genres)


@app.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}.json",
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@app.get(
    "/catalog/{catalog_type}/{catalog_id}.json",
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@app.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/skip={skip}.json",
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@app.get(
    "/catalog/{catalog_type}/{catalog_id}/skip={skip}.json",
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@app.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/genre={genre}.json",
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@app.get(
    "/catalog/{catalog_type}/{catalog_id}/genre={genre}.json",
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@app.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/skip={skip}&genre={genre}.json",
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@app.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/genre={genre}&skip={skip}.json",
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
    tags=["catalog"],
)
@wrappers.auth_required
@wrappers.rate_limit(150, 300, "catalog")
async def get_catalog(
    response: Response,
    request: Request,
    catalog_type: Literal["movie", "series", "tv", "events"],
    catalog_id: str,
    skip: int = 0,
    genre: str = None,
    user_data: schemas.UserData = Depends(get_user_data),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    if genre and genre.lower() == "adult":
        raise HTTPException(404, "Adult genre is not allowed.")

    cache_key, is_watchlist_catalog = get_cache_key(
        catalog_type, catalog_id, skip, genre, user_data
    )

    list_config = None
    if catalog_id.startswith("mdblist"):
        _, media_type, list_id = catalog_id.split("_", 2)
        list_config = next(
            (
                list_item
                for list_item in user_data.mdblist_config.lists
                if str(list_item.id) == list_id and list_item.catalog_type == media_type
            ),
            None,
        )
        if not list_config:
            raise HTTPException(404, "MDBList ID not found.")
        cache_key += f"_{list_config.sort}_{list_config.order}"

    if cache_key:
        response.headers.update(const.CACHE_HEADERS)
        if cached_data := await REDIS_ASYNC_CLIENT.get(cache_key):
            try:
                metas = schemas.Metas.model_validate_json(cached_data)
                return await update_rpdb_posters(metas, user_data, catalog_type)
            except ValidationError:
                pass
    else:
        response.headers.update(const.NO_CACHE_HEADERS)

    metas = await fetch_metas(
        catalog_type,
        catalog_id,
        genre,
        skip,
        user_data,
        request,
        is_watchlist_catalog,
        list_config,
        background_tasks,
    )

    if cache_key:
        await REDIS_ASYNC_CLIENT.set(
            cache_key,
            metas.model_dump_json(exclude_none=True, by_alias=True),
            ex=settings.meta_cache_ttl,
        )

    return await update_rpdb_posters(metas, user_data, catalog_type)


def get_cache_key(
    catalog_type: str,
    catalog_id: str,
    skip: int,
    genre: str,
    user_data: schemas.UserData,
) -> tuple[str, bool]:
    cache_key = f"{catalog_type}_{catalog_id}_{skip}_{genre}_catalog"
    is_watchlist_catalog = False

    if user_data.streaming_provider and "_watchlist_" in catalog_id:
        cache_key = None
        is_watchlist_catalog = True
    elif catalog_id.startswith("contribution_") and user_data.contribution_streams:
        cache_key = None
    elif catalog_type == "events":
        cache_key = None
    elif catalog_type in ["movie", "series"]:
        cache_key += "_" + "_".join(
            user_data.nudity_filter + user_data.certification_filter
        )

    return cache_key, is_watchlist_catalog


async def fetch_metas(
    catalog_type: str,
    catalog_id: str,
    genre: str,
    skip: int,
    user_data: schemas.UserData,
    request: Request,
    is_watchlist_catalog: bool,
    list_config: schemas.MDBListItem,
    background_tasks: BackgroundTasks,
) -> schemas.Metas:
    metas = schemas.Metas()

    if catalog_type == "tv":
        metas.metas.extend(
            await crud.get_tv_meta_list(
                namespace=get_request_namespace(request), genre=genre, skip=skip
            )
        )
    elif catalog_type == "events":
        metas.metas.extend(await crud.get_events_meta_list(genre, skip))
    elif catalog_id.startswith("mdblist"):
        metas.metas.extend(
            await crud.get_mdblist_meta_list(
                user_data, background_tasks, list_config, catalog_type, genre, skip
            )
        )
    else:
        user_ip = await get_user_public_ip(request, user_data)
        metas.metas.extend(
            await crud.get_meta_list(
                user_data,
                catalog_type,
                catalog_id,
                is_watchlist_catalog,
                skip,
                user_ip=user_ip,
                genre=genre,
            )
        )

        watchlist_service = catalog_id.split("_")[0]
        if (
            is_watchlist_catalog
            and catalog_type == "movie"
            and metas.metas
            and mapper.DELETE_ALL_WATCHLIST_FUNCTIONS.get(watchlist_service)
        ):
            delete_all_meta = DELETE_ALL_META.model_copy()
            delete_all_meta.id = delete_all_meta.id.format(watchlist_service)
            metas.metas.insert(0, delete_all_meta)

    return metas


@app.get(
    "/{secret_str}/catalog/{catalog_type}/{catalog_id}/search={search_query}.json",
    tags=["search"],
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
)
@app.get(
    "/catalog/{catalog_type}/{catalog_id}/search={search_query}.json",
    tags=["search"],
    response_model=schemas.Metas,
    response_model_exclude_none=True,
    response_model_by_alias=False,
)
@wrappers.auth_required
async def search_meta(
    request: Request,
    catalog_type: Literal["movie", "series", "tv"],
    catalog_id: Literal[
        "mediafusion_search_movies",
        "mediafusion_search_series",
        "mediafusion_search_tv",
    ],
    search_query: str,
    user_data: schemas.UserData = Depends(get_user_data),
):
    logging.debug("search for catalog_id: %s", catalog_id)

    if catalog_type == "tv":
        return await crud.process_tv_search_query(
            search_query, namespace=get_request_namespace(request)
        )

    metadata = await crud.process_search_query(search_query, catalog_type, user_data)
    return await update_rpdb_posters(
        schemas.Metas.model_validate(metadata), user_data, catalog_type
    )


@app.get(
    "/{secret_str}/meta/{catalog_type}/{meta_id}.json",
    tags=["meta"],
    response_model=schemas.MetaItem,
    response_model_exclude_none=True,
    response_model_by_alias=False,
)
@app.get(
    "/meta/{catalog_type}/{meta_id}.json",
    tags=["meta"],
    response_model=schemas.MetaItem,
    response_model_exclude_none=True,
    response_model_by_alias=False,
)
@wrappers.auth_required
async def get_meta(
    response: Response,
    catalog_type: Literal["movie", "series", "tv", "events"],
    meta_id: str,
    user_data: schemas.UserData = Depends(get_user_data),
):
    cache_key = f"{catalog_type}_{meta_id}_meta" if catalog_type != "events" else None

    if catalog_type in ["movie", "series"]:
        cache_key += "_" + "_".join(
            user_data.nudity_filter + user_data.certification_filter
        )

    # Try retrieving the cached data
    if cache_key:
        cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)
        if cached_data:
            try:
                meta_data = schemas.MetaItem.model_validate_json(cached_data)
                return await update_rpdb_poster(meta_data, user_data, catalog_type)
            except ValidationError:
                pass
    else:
        response.headers.update(const.NO_CACHE_HEADERS)

    if catalog_type == "movie":
        if meta_id.startswith("dl"):
            delete_all_meta_item = DELETE_ALL_META_ITEM.copy()
            delete_all_meta_item["meta"]["_id"] = meta_id
            data = delete_all_meta_item
        else:
            data = await crud.get_movie_meta(meta_id, user_data)
    elif catalog_type == "series":
        data = await crud.get_series_meta(meta_id, user_data)
    elif catalog_type == "events":
        data = await crud.get_event_meta(meta_id)
    else:
        data = await crud.get_tv_meta(meta_id)

    # Cache the data with a TTL of 30 minutes
    # If the data is not found, cached the empty data to avoid db query.
    if cache_key:
        await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(data, default=str), ex=1800)

    if not data:
        raise HTTPException(status_code=404, detail="Meta ID not found.")

    return await update_rpdb_poster(
        schemas.MetaItem.model_validate(data), user_data, catalog_type
    )


@app.get(
    "/{secret_str}/stream/{catalog_type}/{video_id}.json",
    response_model=schemas.Streams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@app.get(
    "/stream/{catalog_type}/{video_id}.json",
    response_model=schemas.Streams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@app.get(
    "/{secret_str}/stream/{catalog_type}/{video_id}:{season}:{episode}.json",
    response_model=schemas.Streams,
    response_model_exclude_none=True,
    tags=["stream"],
)
@app.get(
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
    if "p2p" in settings.disabled_providers and not user_data.streaming_provider:
        return {"streams": []}

    user_ip = await get_user_public_ip(request, user_data)
    user_feeds = []
    if season is None or episode is None:
        season = episode = 1
    if user_data.contribution_streams and video_id.startswith("tt"):
        upload_url = (
            f"{settings.host_url}/scraper/?meta_id={video_id}&meta_type={catalog_type}"
        )
        if catalog_type == "series":
            upload_url += f"&season={season}&episode={episode}"
        user_feeds = [
            schemas.Stream(
                name=settings.addon_name,
                description=f"🔄 Update IMDb metadata for {video_id}\n"
                f"This will fetch the latest IMDb data for this {catalog_type},\n Once after you make contribution to IMDb.",
                url=f"{settings.host_url}/scraper/imdb_data?meta_id={video_id}&media_type={catalog_type}&redirect_video=true",
            ),
            schemas.Stream(
                name=settings.addon_name,
                description=f"📤 Upload torrent for {video_id}",
                externalUrl=upload_url,
            ),
        ]
        response.headers.update(const.NO_CACHE_HEADERS)

    if catalog_type == "movie":
        if video_id.startswith("dl"):
            service_name = video_id[2:]
            if (
                user_data.streaming_provider
                and service_name == user_data.streaming_provider.service
            ):
                fetched_streams = [
                    schemas.Stream(
                        name=f"{settings.addon_name} {user_data.streaming_provider.service.title()} 🗑️💩🚨",
                        description=f"🚨💀⚠ Delete all files in {user_data.streaming_provider.service} watchlist.",
                        url=f"{settings.host_url}/streaming_provider/{secret_str}/delete_all_watchlist",
                    )
                ]
            else:
                raise HTTPException(status_code=404, detail="Meta ID not found.")
        else:
            fetched_streams = await crud.get_movie_streams(
                user_data, secret_str, video_id, user_ip, background_tasks
            )
            fetched_streams.extend(user_feeds)
    elif catalog_type == "series":
        fetched_streams = await crud.get_series_streams(
            user_data,
            secret_str,
            video_id,
            season,
            episode,
            user_ip,
            background_tasks,
        )
        fetched_streams.extend(user_feeds)
    elif catalog_type == "events":
        fetched_streams = await crud.get_event_streams(video_id, user_data)
        response.headers.update(const.NO_CACHE_HEADERS)
    else:
        response.headers.update(const.NO_CACHE_HEADERS)
        fetched_streams = await crud.get_tv_streams(
            video_id, get_request_namespace(request), user_data
        )

    return {"streams": fetched_streams}


@app.post("/encrypt-user-data", tags=["user_data"])
@app.post("/encrypt-user-data/{existing_secret_str}", tags=["user_data"])
@wrappers.rate_limit(30, 60 * 5, "user_data")
async def encrypt_user_data(
    user_data: schemas.UserData,
    request: Request,
    existing_secret_str: str | None = None,
):
    async def _validate_all_config() -> dict:
        if "p2p" in settings.disabled_providers and not user_data.streaming_provider:
            return {
                "status": "error",
                "message": "Direct torrent has been disabled by the administrator. You must select a streaming provider.",
            }

        if not settings.is_public_instance and (
            not user_data.api_password
            or user_data.api_password != settings.api_password
        ):
            return {
                "status": "error",
                "message": "Invalid MediaFusion API Password. Make sure to enter the correct password which is configured in environment variables.",
            }
        try:
            validation_tasks = [
                validate_provider_credentials(request, user_data),
                validate_mediaflow_proxy_credentials(user_data),
                validate_rpdb_token(user_data),
                validate_mdblist_token(user_data),
            ]

            results = await asyncio.gather(*validation_tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    return {
                        "status": "error",
                        "message": f"Validation failed: {str(result)}",
                    }
                if isinstance(result, dict) and result["status"] == "error":
                    return result

            return {"status": "success"}
        except Exception as e:
            return {
                "status": "error",
                "message": f"Unexpected error during validation: {str(e)}",
            }

    if existing_secret_str:
        try:
            existing_config = await crypto_utils.decrypt_user_data(existing_secret_str)
        except ValueError:
            existing_config = schemas.UserData()

        if user_data.streaming_provider and existing_config.streaming_provider:
            if user_data.streaming_provider.password == "••••••••":
                user_data.streaming_provider.password = (
                    existing_config.streaming_provider.password
                )
            if user_data.streaming_provider.token == "••••••••":
                user_data.streaming_provider.token = (
                    existing_config.streaming_provider.token
                )

            if user_data.streaming_provider.qbittorrent_config:
                if (
                    user_data.streaming_provider.qbittorrent_config.qbittorrent_password
                    == "••••••••"
                ):
                    user_data.streaming_provider.qbittorrent_config.qbittorrent_password = (
                        existing_config.streaming_provider.qbittorrent_config.qbittorrent_password
                    )
                if (
                    user_data.streaming_provider.qbittorrent_config.webdav_password
                    == "••••••••"
                ):
                    user_data.streaming_provider.qbittorrent_config.webdav_password = (
                        existing_config.streaming_provider.qbittorrent_config.webdav_password
                    )

        if user_data.mediaflow_config and existing_config.mediaflow_config:
            if user_data.mediaflow_config.api_password == "••••••••":
                user_data.mediaflow_config.api_password = (
                    existing_config.mediaflow_config.api_password
                )

        if user_data.rpdb_config and existing_config.rpdb_config:
            if user_data.rpdb_config.api_key == "••••••••":
                user_data.rpdb_config.api_key = existing_config.rpdb_config.api_key

    validation_result = await _validate_all_config()
    if validation_result["status"] == "error":
        return validation_result

    encrypted_str = await crypto_utils.process_user_data(user_data)
    return {"status": "success", "encrypted_str": encrypted_str}


def raise_poster_error(meta_id: str, error_message: str):
    if meta_id.startswith("tt"):
        # Fall back to Cinemeta poster
        return RedirectResponse(
            f"https://live.metahub.space/poster/small/{meta_id}/img", status_code=302
        )
    raise HTTPException(
        status_code=404,
        detail=f"Failed to create poster for {meta_id}: {error_message}",
    )


@app.get("/poster/{catalog_type}/{mediafusion_id}.jpg", tags=["poster"])
@wrappers.exclude_rate_limit
async def get_poster(
    catalog_type: Literal["movie", "series", "tv", "events"],
    mediafusion_id: str,
):
    cache_key = f"{catalog_type}_{mediafusion_id}.jpg"

    cached_image = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_image:
        image_byte_io = BytesIO(cached_image)
        return StreamingResponse(image_byte_io, media_type="image/jpeg")

    # Query the MediaFusion data
    if catalog_type == "movie":
        mediafusion_data = await crud.get_movie_data_by_id(mediafusion_id)
    elif catalog_type == "series":
        mediafusion_data = await crud.get_series_data_by_id(mediafusion_id)
    elif catalog_type == "events":
        mediafusion_data = await crud.get_event_data_by_id(mediafusion_id)
    else:
        mediafusion_data = await crud.get_tv_data_by_id(mediafusion_id)

    if not mediafusion_data:
        return raise_poster_error(mediafusion_id, "MediaFusion ID not found.")

    if mediafusion_data.is_poster_working is False or not mediafusion_data.poster:
        return raise_poster_error(mediafusion_id, "Poster not found.")

    try:
        image_byte_io = await poster.create_poster(mediafusion_data)
        # Convert BytesIO to bytes for Redis
        image_bytes = image_byte_io.getvalue()
        # Save the generated image to Redis. expire in 7 days
        await REDIS_ASYNC_CLIENT.set(cache_key, image_bytes, ex=604800)
        image_byte_io.seek(0)

        return StreamingResponse(image_byte_io, media_type="image/jpeg")
    except asyncio.TimeoutError:
        return raise_poster_error(mediafusion_id, "Poster generation timeout.")
    except aiohttp.ClientResponseError as e:
        if e.status == 404 and catalog_type != "events":
            mediafusion_data.is_poster_working = False
            await mediafusion_data.save()
        return raise_poster_error(mediafusion_id, f"Poster generation failed: {e}")
    except (ConnectionResetError, ValueError):
        mediafusion_data.is_poster_working = False
        await mediafusion_data.save()
        return raise_poster_error(mediafusion_id, "Poster generation failed}")
    except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError) as e:
        return raise_poster_error(mediafusion_id, f"Poster generation failed: {e}")
    except Exception as e:
        logging.error(
            f"Unexpected error while creating poster: {mediafusion_data.poster} {e}",
            exc_info=True,
        )
        return raise_poster_error(mediafusion_id, f"Unexpected error: {e}")


@app.get(
    "/download/{secret_str}/{catalog_type}/{video_id}",
    response_class=HTMLResponse,
    tags=["download"],
)
@app.get(
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
    season: int = None,
    episode: int = None,
):
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
        stream
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

    # Prepare context with all necessary data
    context = {
        "title": metadata.title,
        "year": metadata.year,
        "logo_url": settings.logo_url,
        "poster": _poster,
        "background": background,
        "description": metadata.description,
        "streams": downloadable_streams,
        "catalog_type": catalog_type,
        "season": season,
        "episode": episode,
        "video_id": video_id,
        "secret_str": secret_str,
        "settings": settings,
        "series_data": (
            metadata.model_dump_json(
                include={"episodes": {"__all__": {"season_number", "episode_number"}}}
            )
            if catalog_type == "series"
            else None
        ),
    }

    return TEMPLATES.TemplateResponse(
        "html/download_info.html", {"request": request, **context}
    )


app.include_router(
    streaming_provider_router, prefix="/streaming_provider", tags=["streaming_provider"]
)

app.include_router(scrapers_router, prefix="/scraper", tags=["scraper"])

app.include_router(metrics_router, prefix="/metrics", tags=["metrics"])

app.include_router(kodi_router, prefix="/kodi", tags=["kodi"])


app.include_router(frontend_api_router, prefix="/api/v1", tags=["frontend"])
app.include_router(rss_feeds_router, prefix="/rss", tags=["rss"])
