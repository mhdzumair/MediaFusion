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
from db.schemas import SortingOption
from kodi.routes import kodi_router
from metrics.routes import metrics_router
from scrapers.routes import router as scrapers_router
from scrapers.rpdb import update_rpdb_posters, update_rpdb_poster
from streaming_providers import mapper
from streaming_providers.routes import router as streaming_provider_router
from streaming_providers.validator import validate_provider_credentials
from utils import const, crypto, poster, torrent, wrappers
from utils.lock import (
    acquire_scheduler_lock,
    maintain_heartbeat,
    release_scheduler_lock,
)
from utils.network import get_request_namespace, get_user_public_ip, get_user_data
from utils.parser import generate_manifest
from utils.runtime_const import (
    DELETE_ALL_META,
    DELETE_ALL_META_ITEM,
    TEMPLATES,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from utils.validation_helper import (
    validate_mediaflow_proxy_credentials,
    validate_rpdb_token,
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
    start_time = asyncio.get_event_loop().time()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.head("https://www.google.com", timeout=10) as response:
                return {
                    "status": "healthy",
                    "status_code": response.status,
                    "time": asyncio.get_event_loop().time() - start_time,
                }
        except Exception as e:
            logging.error("Health check failed: %s", e)
            raise HTTPException(status_code=503, detail="Health check failed.")


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
):
    response.headers.update(const.NO_CACHE_HEADERS)

    # Remove the password from the streaming provider
    if user_data.streaming_provider:
        user_data.streaming_provider.password = None
        user_data.streaming_provider.token = None

        if user_data.streaming_provider.qbittorrent_config:
            user_data.streaming_provider.qbittorrent_config.qbittorrent_password = None
            user_data.streaming_provider.qbittorrent_config.webdav_password = None

    # Remove the password from the mediaflow proxy
    if user_data.mediaflow_config:
        user_data.mediaflow_config.api_password = None

    user_data.api_password = None

    # Prepare catalogs based on user preferences or default order
    sorted_catalogs = sorted(
        const.CATALOG_DATA.items(),
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
):
    skip, genre = parse_genre_and_skip(genre, skip)
    cache_key, is_watchlist_catalog = get_cache_key(
        catalog_type, catalog_id, skip, genre, user_data
    )

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
        catalog_type, catalog_id, genre, skip, user_data, request, is_watchlist_catalog
    )

    if cache_key:
        await REDIS_ASYNC_CLIENT.set(
            cache_key,
            metas.model_dump_json(exclude_none=True, by_alias=True),
            ex=settings.meta_cache_ttl,
        )

    return await update_rpdb_posters(metas, user_data, catalog_type)


def parse_genre_and_skip(genre: str, skip: int) -> tuple[int, str]:
    if genre and "&" in genre:
        genre, skip = genre.split("&")
        skip = skip.split("=")[1] if "=" in skip else "0"
        skip = int(skip) if skip and skip.isdigit() else 0
    return skip, genre


def get_cache_key(
    catalog_type: str,
    catalog_id: str,
    skip: int,
    genre: str,
    user_data: schemas.UserData,
) -> tuple[str, bool]:
    cache_key = f"{catalog_type}_{catalog_id}_{skip}_{genre}_catalog"
    is_watchlist_catalog = False

    if user_data.streaming_provider and catalog_id.startswith(
        user_data.streaming_provider.service
    ):
        cache_key = None
        is_watchlist_catalog = True
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

        if (
            is_watchlist_catalog
            and catalog_type == "movie"
            and metas.metas
            and mapper.DELETE_ALL_WATCHLIST_FUNCTIONS.get(
                user_data.streaming_provider.service
            )
        ):
            delete_all_meta = DELETE_ALL_META.model_copy()
            delete_all_meta.id = delete_all_meta.id.format(
                user_data.streaming_provider.service
            )
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
    catalog_type: Literal["movie", "series", "tv", "events"],
    meta_id: str,
    user_data: schemas.UserData = Depends(get_user_data),
):
    cache_key = f"{catalog_type}_{meta_id}_meta"

    if catalog_type in ["movie", "series"]:
        cache_key += "_" + "_".join(
            user_data.nudity_filter + user_data.certification_filter
        )

    # Try retrieving the cached data
    cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_data:
        try:
            meta_data = schemas.MetaItem.model_validate_json(cached_data)
            return await update_rpdb_poster(meta_data, user_data, catalog_type)
        except ValidationError:
            pass

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
    secret_str: str = None,
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
            if video_id == f"dl{user_data.streaming_provider.service}":
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
@wrappers.rate_limit(30, 60 * 5, "user_data")
async def encrypt_user_data(user_data: schemas.UserData, request: Request):
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

    validation_result = await _validate_all_config()
    if validation_result["status"] == "error":
        return validation_result

    encrypted_str = crypto.encrypt_user_data(user_data)
    return {"status": "success", "encrypted_str": encrypted_str}


@app.get("/poster/{catalog_type}/{mediafusion_id}.jpg", tags=["poster"])
@wrappers.exclude_rate_limit
async def get_poster(
    catalog_type: Literal["movie", "series", "tv", "events"],
    mediafusion_id: str,
):
    cache_key = f"{catalog_type}_{mediafusion_id}.jpg"

    # Check if the poster is cached in Redis
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
        raise HTTPException(status_code=404, detail="MediaFusion ID not found.")

    if mediafusion_data.is_poster_working is False or not mediafusion_data.poster:
        raise HTTPException(status_code=404, detail="Poster not found.")

    try:
        image_byte_io = await poster.create_poster(mediafusion_data)
        # Convert BytesIO to bytes for Redis
        image_bytes = image_byte_io.getvalue()
        # Save the generated image to Redis. expire in 7 days
        await REDIS_ASYNC_CLIENT.set(cache_key, image_bytes, ex=604800)
        image_byte_io.seek(0)

        return StreamingResponse(image_byte_io, media_type="image/jpeg")
    except asyncio.TimeoutError:
        logging.error("Poster generation timeout.")
        raise HTTPException(status_code=404, detail="Poster generation timeout.")
    except aiohttp.ClientResponseError as e:
        logging.error(f"Failed to create poster: {e}, status: {e.status}")
        if e.status != 404:
            raise HTTPException(status_code=404, detail="Failed to create poster.")
    except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError) as e:
        logging.error(f"Failed to create poster: {e}")
    except Exception as e:
        logging.error(
            f"Unexpected error while creating poster: {mediafusion_data.poster} {e}",
            exc_info=True,
        )
    mediafusion_data.is_poster_working = False
    if catalog_type != "events":
        await mediafusion_data.save()
    raise HTTPException(status_code=404, detail="Failed to create poster.")


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

    context = {
        "title": metadata.title,
        "year": metadata.year,
        "poster": metadata.poster,
        "description": metadata.description,
        "streams": downloadable_streams,
        "catalog_type": catalog_type,
        "season": season,
        "episode": episode,
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
