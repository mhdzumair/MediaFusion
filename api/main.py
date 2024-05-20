import asyncio
import json
import logging
from io import BytesIO
from typing import Literal

import aiohttp
import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import (
    FastAPI,
    Request,
    Response,
    Depends,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api import middleware
from api.scheduler import setup_scheduler
from db import database, crud, schemas
from db.config import settings
from metrics.routes import metrics_router
from scrapers.routes import router as scrapers_router
from streaming_providers import mapper
from streaming_providers.routes import router as streaming_provider_router
from utils import crypto, torrent, poster, const, wrappers, get_json_data
from utils.lock import (
    acquire_scheduler_lock,
    maintain_heartbeat,
    release_scheduler_lock,
)
from utils.network import get_user_public_ip
from utils.parser import generate_manifest
from utils.runtime_const import TEMPLATES, DELETE_ALL_META, DELETE_ALL_META_ITEM

logging.basicConfig(
    format="%(levelname)s::%(asctime)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    level=settings.logging_level,
)
app = FastAPI()

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


app.state.redis = redis.Redis(
    connection_pool=redis.ConnectionPool.from_url(settings.redis_url)
)
app.add_middleware(middleware.RateLimitMiddleware, redis_client=app.state.redis)
app.add_middleware(middleware.TimingMiddleware)
app.add_middleware(middleware.SecureLoggingMiddleware)
app.add_middleware(middleware.UserDataMiddleware)

app.mount("/static", StaticFiles(directory="resources"), name="static")


def get_user_data(request: Request) -> schemas.UserData:
    return request.user


@app.on_event("startup")
async def init_server():
    await database.init()
    await torrent.init_best_trackers()


@app.on_event("startup")
async def start_scheduler():
    if settings.disable_all_scheduler:
        logging.info("All Schedulers are disabled. Not setting up any jobs.")
        return

    acquired, lock = await acquire_scheduler_lock(app.state.redis)
    if acquired:
        try:
            scheduler = AsyncIOScheduler()
            setup_scheduler(scheduler)
            scheduler.start()
            app.state.scheduler = scheduler
            app.state.scheduler_lock = lock
            await asyncio.create_task(maintain_heartbeat(app.state.redis))
        except Exception as e:
            await release_scheduler_lock(app.state.redis, lock)
            raise e


@app.on_event("shutdown")
async def stop_scheduler():
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown(wait=False)

    if hasattr(app.state, "scheduler_lock") and app.state.scheduler_lock:
        await release_scheduler_lock(app.state.redis, app.state.scheduler_lock)


@app.on_event("shutdown")
async def shutdown_event():
    await app.state.redis.aclose()


@app.get("/", tags=["home"])
async def get_home(request: Request):
    manifest = get_json_data("resources/manifest.json")
    return TEMPLATES.TemplateResponse(
        "html/home.html",
        {
            "request": request,
            "addon_name": settings.addon_name,
            "logo_url": settings.logo_url,
            "version": f"{manifest.get('version')}-{settings.git_rev[:7]}",
            "description": manifest.get("description"),
        },
    )


@app.get("/health", tags=["health"])
@wrappers.exclude_rate_limit
async def health(request: Request):
    return {"status": "healthy"}


@app.get("/favicon.ico")
async def get_favicon():
    return RedirectResponse(url=settings.logo_url)


@app.get("/configure", tags=["configure"])
@app.get("/{secret_str}/configure", tags=["configure"])
async def configure(
    response: Response,
    request: Request,
    user_data: schemas.UserData = Depends(get_user_data),
):
    response.headers.update(const.NO_CACHE_HEADERS)

    # Remove the password from the streaming provider
    if user_data.streaming_provider:
        user_data.streaming_provider.password = None
    user_data.api_password = None

    # Prepare catalogs based on user preferences or default order
    sorted_catalogs = sorted(
        const.CATALOG_DATA.items(),
        key=lambda x: user_data.selected_catalogs.index(x[0])
        if x[0] in user_data.selected_catalogs
        else len(user_data.selected_catalogs),
    )

    sorted_sorting_options = user_data.torrent_sorting_priority + [
        option
        for option in const.TORRENT_SORTING_PRIORITY
        if option not in user_data.torrent_sorting_priority
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
            "authentication_required": settings.api_password is not None
            and not settings.is_public_instance,
        },
    )


@app.get("/manifest.json", tags=["manifest"])
@app.get("/{secret_str}/manifest.json", tags=["manifest"])
@wrappers.auth_required
async def get_manifest(
    response: Response,
    request: Request,
    user_data: schemas.UserData = Depends(get_user_data),
):
    response.headers.update(const.NO_CACHE_HEADERS)

    manifest = get_json_data("resources/manifest.json")
    return await generate_manifest(manifest, user_data, request.app.state.redis)


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
    if genre and "&" in genre:
        genre, skip = genre.split("&")
        skip = skip.split("=")[1] if "=" in skip else "0"
        skip = int(skip) if skip and skip.isdigit() else 0

    cache_key = f"{catalog_type}_{catalog_id}_{skip}_{genre}_catalog"
    is_watchlist_catalog = False
    if user_data.streaming_provider and catalog_id.startswith(
        user_data.streaming_provider.service
    ):
        response.headers.update(const.NO_CACHE_HEADERS)
        cache_key = None
        is_watchlist_catalog = True
    elif catalog_type == "events":
        response.headers.update(const.NO_CACHE_HEADERS)
        cache_key = None

    # Try retrieving the cached data
    if cache_key:
        if cached_data := await request.app.state.redis.get(cache_key):
            return json.loads(cached_data)

    metas = schemas.Metas()
    if catalog_type == "tv":
        metas.metas.extend(await crud.get_tv_meta_list(genre, skip))
    elif catalog_type == "events":
        metas.metas.extend(
            await crud.get_events_meta_list(request.app.state.redis, genre, skip)
        )
    else:
        user_ip = get_user_public_ip(request)
        metas.metas.extend(
            await crud.get_meta_list(
                user_data,
                catalog_type,
                catalog_id,
                is_watchlist_catalog,
                skip,
                user_ip=user_ip,
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

    if cache_key:
        await request.app.state.redis.set(
            cache_key,
            metas.model_dump_json(exclude_none=True, by_alias=True),
            ex=settings.meta_cache_ttl,
        )

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
    response: Response,
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
        return await crud.process_tv_search_query(search_query)

    return await crud.process_search_query(search_query, catalog_type, user_data)


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
    response: Response,
    request: Request,
):
    cache_key = f"{catalog_type}_{meta_id}_meta"
    # Try retrieving the cached data
    cached_data = await request.app.state.redis.get(cache_key)
    if cached_data:
        meta_data = json.loads(cached_data)
        if not meta_data:
            raise HTTPException(status_code=404, detail="Meta ID not found.")
        return meta_data

    if catalog_type == "movie":
        if meta_id.startswith("dl"):
            delete_all_meta_item = DELETE_ALL_META_ITEM.copy()
            delete_all_meta_item["meta"]["_id"] = meta_id
            data = delete_all_meta_item
        else:
            data = await crud.get_movie_meta(meta_id, request.app.state.redis)
    elif catalog_type == "series":
        data = await crud.get_series_meta(meta_id)
    elif catalog_type == "events":
        data = await crud.get_event_meta(request.app.state.redis, meta_id)
    else:
        data = await crud.get_tv_meta(meta_id)

    # Cache the data with a TTL of 30 minutes
    # If the data is not found, cached the empty data to avoid db query.
    await request.app.state.redis.set(cache_key, json.dumps(data, default=str), ex=1800)

    if not data:
        raise HTTPException(status_code=404, detail="Meta ID not found.")

    return data


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
):
    user_ip = get_user_public_ip(request)
    user_feeds = []
    if season is None or episode is None:
        season = episode = 1
    if "contribution_streams" in user_data.selected_catalogs and video_id.startswith(
        "tt"
    ):
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
                url=f"{settings.host_url}/scraper/imdb_data?meta_id={video_id}&redirect_video=true",
            ),
            schemas.Stream(
                name=settings.addon_name,
                description=f"📤 Upload torrent for {video_id}",
                externalUrl=upload_url,
            ),
        ]

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
                user_data, secret_str, request.app.state.redis, video_id, user_ip
            )
            fetched_streams.extend(user_feeds)
    elif catalog_type == "series":
        fetched_streams = await crud.get_series_streams(
            user_data,
            secret_str,
            request.app.state.redis,
            video_id,
            season,
            episode,
            user_ip,
        )
        fetched_streams.extend(user_feeds)
    elif catalog_type == "events":
        fetched_streams = await crud.get_event_streams(
            request.app.state.redis, video_id
        )
        response.headers.update(const.NO_CACHE_HEADERS)
    else:
        response.headers.update(const.NO_CACHE_HEADERS)
        fetched_streams = await crud.get_tv_streams(request.app.state.redis, video_id)

    return {"streams": fetched_streams}


@app.post("/encrypt-user-data", tags=["user_data"])
@wrappers.rate_limit(30, 60 * 5, "user_data")
async def encrypt_user_data(user_data: schemas.UserData):
    encrypted_str = crypto.encrypt_user_data(user_data)
    return {"encrypted_str": encrypted_str}


@app.get("/poster/{catalog_type}/{mediafusion_id}.jpg", tags=["poster"])
@wrappers.exclude_rate_limit
async def get_poster(
    catalog_type: Literal["movie", "series", "tv", "events"],
    mediafusion_id: str,
    request: Request,
):
    cache_key = f"{catalog_type}_{mediafusion_id}.jpg"

    # Check if the poster is cached in Redis
    cached_image = await request.app.state.redis.get(cache_key)
    if cached_image:
        image_byte_io = BytesIO(cached_image)
        return StreamingResponse(image_byte_io, media_type="image/jpeg")

    # Query the MediaFusion data
    if catalog_type == "movie":
        mediafusion_data = await crud.get_movie_data_by_id(
            mediafusion_id, request.app.state.redis
        )
    elif catalog_type == "series":
        mediafusion_data = await crud.get_series_data_by_id(mediafusion_id)
    elif catalog_type == "events":
        mediafusion_data = await crud.get_event_data_by_id(
            request.app.state.redis, mediafusion_id
        )
    else:
        mediafusion_data = await crud.get_tv_data_by_id(mediafusion_id)

    if not mediafusion_data:
        raise HTTPException(status_code=404, detail="MediaFusion ID not found.")

    if mediafusion_data.is_poster_working is False or not mediafusion_data.poster:
        raise HTTPException(status_code=404, detail="Poster not found.")

    try:
        image_byte_io = await poster.create_poster(
            mediafusion_data, request.app.state.redis
        )
        # Convert BytesIO to bytes for Redis
        image_bytes = image_byte_io.getvalue()
        # Save the generated image to Redis. expire in 7 days
        await request.app.state.redis.set(cache_key, image_bytes, ex=604800)
        image_byte_io.seek(0)

        return StreamingResponse(image_byte_io, media_type="image/jpeg")
    except asyncio.TimeoutError:
        logging.error("Poster generation timeout.")
        raise HTTPException(status_code=404, detail="Poster generation timeout.")
    except aiohttp.ClientResponseError as e:
        logging.error(f"Failed to create poster: {e}, status: {e.status}")
        if e.status != 404:
            raise HTTPException(status_code=404, detail="Failed to create poster.")
    except aiohttp.ClientConnectorError as e:
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


app.include_router(
    streaming_provider_router, prefix="/streaming_provider", tags=["streaming_provider"]
)

app.include_router(scrapers_router, prefix="/scraper", tags=["scraper"])

app.include_router(metrics_router, prefix="/metrics", tags=["metrics"])
