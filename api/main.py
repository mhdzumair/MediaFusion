import json
import logging
from io import BytesIO
from typing import Literal

import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import (
    FastAPI,
    Request,
    Response,
    Depends,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from api import middleware
from db import database, crud, schemas
from db.config import settings
from streaming_providers.routes import router as streaming_provider_router
from utils import crypto, torrent, poster, const, rate_limiter
from utils.parser import generate_manifest

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
app.state.redis = redis.Redis(
    connection_pool=redis.ConnectionPool.from_url(settings.redis_url)
)
app.add_middleware(middleware.RateLimitMiddleware, redis_client=app.state.redis)
app.add_middleware(middleware.SecureLoggingMiddleware)
app.add_middleware(middleware.UserDataMiddleware)

TEMPLATES = Jinja2Templates(directory="resources")


def get_user_data(request: Request) -> schemas.UserData:
    return request.user


@app.on_event("startup")
async def init_server():
    await database.init()
    await torrent.init_best_trackers()


@app.on_event("startup")
async def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        crud.delete_search_history, CronTrigger(day="*/1"), name="delete_search_history"
    )
    app.state.scheduler = scheduler


@app.on_event("shutdown")
async def stop_scheduler():
    try:
        app.state.scheduler.shutdown(wait=False)
    except AttributeError:
        pass


@app.on_event("shutdown")
async def shutdown_event():
    await app.state.redis.aclose()


@app.get("/", tags=["home"])
async def get_home(request: Request):
    with open("resources/manifest.json") as file:
        manifest = json.load(file)
    return TEMPLATES.TemplateResponse(
        "html/home.html",
        {
            "request": request,
            "name": manifest.get("name"),
            "version": f"{manifest.get('version')}-{settings.git_rev[:7]}",
            "description": manifest.get("description"),
            "gives": [
                "Tamil Movies & Series",
                "Malayalam Movies & Series",
                "Telugu Movies & Series",
                "Hindi Movies & Series",
                "Kannada Movies & Series",
                "English Movies & Series",
                "Dubbed Movies & Series",
            ],
            "logo": "static/images/mediafusion_logo.png",
        },
    )


@app.get("/health", tags=["health"])
@rate_limiter.exclude
async def health(request: Request):
    return {"status": "healthy"}


@app.get("/favicon.ico")
async def get_favicon():
    return FileResponse(
        "resources/images/mediafusion_logo.png", media_type="image/x-icon"
    )


@app.get("/static/{file_path:path}")
async def function(file_path: str):
    response = FileResponse(f"resources/{file_path}")
    response.headers.update(const.DEFAULT_HEADERS)
    return response


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

    return TEMPLATES.TemplateResponse(
        "html/configure.html",
        {
            "request": request,
            "user_data": user_data.model_dump(),
            "catalogs": zip(const.CATALOG_ID_DATA, const.CATALOG_NAME_DATA),
            "resolutions": const.RESOLUTIONS,
        },
    )


@app.get("/manifest.json", tags=["manifest"])
@app.get("/{secret_str}/manifest.json", tags=["manifest"])
async def get_manifest(
    response: Response, user_data: schemas.UserData = Depends(get_user_data)
):
    response.headers.update(const.NO_CACHE_HEADERS)

    with open("resources/manifest.json") as file:
        manifest = json.load(file)
    return generate_manifest(manifest, user_data)


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
@rate_limiter.rate_limit(150, 300, "catalog")
async def get_catalog(
    response: Response,
    request: Request,
    catalog_type: Literal["movie", "series", "tv"],
    catalog_id: str,
    skip: int = 0,
    genre: str = None,
    user_data: schemas.UserData = Depends(get_user_data),
):
    response.headers.update(const.DEFAULT_HEADERS)
    if genre and "&" in genre:
        genre, skip = genre.split("&")
        skip = skip.split("=")[1] if "=" in skip else "0"
        skip = int(skip) if skip and skip.isdigit() else 0

    cache_key = f"{catalog_type}_{catalog_id}_{skip}_{genre}_catalog"
    if user_data.streaming_provider and catalog_id.startswith(
        user_data.streaming_provider.service
    ):
        response.headers.update(const.NO_CACHE_HEADERS)
        cache_key = None

    # Try retrieving the cached data
    if cache_key:
        if cached_data := await request.app.state.redis.get(cache_key):
            return json.loads(cached_data)

    metas = schemas.Metas()
    if catalog_type == "tv":
        metas.metas.extend(await crud.get_tv_meta_list(genre, skip))
    else:
        metas.metas.extend(
            await crud.get_meta_list(user_data, catalog_type, catalog_id, skip)
        )

    if cache_key:
        # Cache the data with a TTL of 6 hours
        await request.app.state.redis.set(
            cache_key, metas.model_dump_json(exclude_none=True, by_alias=True), ex=21600
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
async def search_meta(
    response: Response,
    catalog_type: Literal["movie", "series", "tv"],
    catalog_id: Literal[
        "mediafusion_search_movies",
        "mediafusion_search_series",
        "mediafusion_search_tv",
    ],
    search_query: str,
):
    response.headers.update(const.DEFAULT_HEADERS)
    logging.debug("search for catalog_id: %s", catalog_id)

    return await crud.process_search_query(search_query, catalog_type)


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
async def get_meta(
    catalog_type: Literal["movie", "series", "tv"],
    meta_id: str,
    response: Response,
    request: Request,
):
    response.headers.update(const.DEFAULT_HEADERS)

    cache_key = f"{catalog_type}_{meta_id}_meta"
    # Try retrieving the cached data
    cached_data = await request.app.state.redis.get(cache_key)
    if cached_data:
        return json.loads(cached_data)

    if catalog_type == "movie":
        data = await crud.get_movie_meta(meta_id)
    elif catalog_type == "series":
        data = await crud.get_series_meta(meta_id)
    else:
        data = await crud.get_tv_meta(meta_id)

    if not data:
        raise HTTPException(status_code=404, detail="Meta ID not found.")

    # Cache the data with a TTL of 6 hours
    await request.app.state.redis.set(cache_key, json.dumps(data), ex=21600)

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
@rate_limiter.rate_limit(10, 60 * 60, "stream")
async def get_streams(
    catalog_type: Literal["movie", "series", "tv"],
    video_id: str,
    response: Response,
    request: Request,
    secret_str: str = None,
    season: int = None,
    episode: int = None,
    user_data: schemas.UserData = Depends(get_user_data),
):
    response.headers.update(const.DEFAULT_HEADERS)

    if catalog_type == "movie":
        fetched_streams = await crud.get_movie_streams(
            user_data, secret_str, request.app.state.redis, video_id
        )
    elif catalog_type == "series":
        fetched_streams = await crud.get_series_streams(
            user_data, secret_str, request.app.state.redis, video_id, season, episode
        )
    else:
        fetched_streams = await crud.get_tv_streams(video_id)

    return {"streams": fetched_streams}


@app.post("/encrypt-user-data", tags=["user_data"])
async def encrypt_user_data(user_data: schemas.UserData):
    encrypted_str = crypto.encrypt_user_data(user_data)
    return {"encrypted_str": encrypted_str}


@app.get("/poster/{catalog_type}/{mediafusion_id}.jpg", tags=["poster"])
@rate_limiter.exclude
async def get_poster(
    catalog_type: Literal["movie", "series", "tv"],
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
        mediafusion_data = await crud.get_movie_data_by_id(mediafusion_id)
    elif catalog_type == "series":
        mediafusion_data = await crud.get_series_data_by_id(mediafusion_id)
    else:
        mediafusion_data = await crud.get_tv_data_by_id(mediafusion_id)

    if not mediafusion_data:
        raise HTTPException(status_code=404, detail="MediaFusion ID not found.")

    if mediafusion_data.is_poster_working is False:
        raise HTTPException(status_code=404, detail="Poster not found.")

    try:
        image_byte_io = await poster.create_poster(mediafusion_data)
        # Convert BytesIO to bytes for Redis
        image_bytes = image_byte_io.getvalue()
        # Save the generated image to Redis. expire in 7 days
        await request.app.state.redis.set(cache_key, image_bytes, ex=604800)
        image_byte_io.seek(0)

        return StreamingResponse(
            image_byte_io, media_type="image/jpeg", headers=const.DEFAULT_HEADERS
        )
    except Exception as e:
        logging.error(f"Unexpected error while creating poster: {e}")
        mediafusion_data.is_poster_working = False
        await mediafusion_data.save()
        raise HTTPException(status_code=404, detail="Failed to create poster.")


app.include_router(
    streaming_provider_router, prefix="/streaming_provider", tags=["streaming_provider"]
)
