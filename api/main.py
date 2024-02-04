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
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from api.middleware import SecureLoggingMiddleware
from db import database, crud, schemas
from db.config import settings
from scrappers import tamil_blasters, tamilmv
from streaming_providers.alldebrid.utils import get_direct_link_from_alldebrid
from streaming_providers.debridlink.api import router as debridlink_router
from streaming_providers.debridlink.utils import get_direct_link_from_debridlink
from streaming_providers.exceptions import ProviderException
from streaming_providers.offcloud.utils import get_direct_link_from_offcloud
from streaming_providers.pikpak.utils import get_direct_link_from_pikpak
from streaming_providers.premiumize.api import router as premiumize_router
from streaming_providers.premiumize.utils import get_direct_link_from_premiumize
from streaming_providers.realdebrid.api import router as realdebrid_router
from streaming_providers.realdebrid.utils import get_direct_link_from_realdebrid
from streaming_providers.seedr.api import router as seedr_router
from streaming_providers.seedr.utils import get_direct_link_from_seedr
from streaming_providers.torbox.utils import get_direct_link_from_torbox
from utils import crypto, torrent, poster, validation_helper, const
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
TEMPLATES = Jinja2Templates(directory="resources")
headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Cache-Control": "max-age=3600, stale-while-revalidate=3600, stale-if-error=604800, public",
}
no_cache_headers = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.middleware("http")
async def add_custom_logging(request: Request, call_next):
    return await SecureLoggingMiddleware()(request, call_next)


@app.on_event("startup")
async def init_server():
    await database.init()
    await torrent.init_best_trackers()
    app.state.redis = redis.Redis(
        connection_pool=redis.ConnectionPool.from_url(settings.redis_url)
    )


@app.on_event("startup")
async def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        crud.delete_search_history, CronTrigger(day="*/1"), name="delete_search_history"
    )

    if settings.enable_scrapper:
        scheduler.add_job(
            tamil_blasters.run_schedule_scrape,
            CronTrigger(hour="*/6"),
            name="tamil_blasters",
        )
        scheduler.add_job(
            tamilmv.run_schedule_scrape, CronTrigger(hour="*/3"), name="tamilmv"
        )
        scheduler.start()
    app.state.scheduler = scheduler


@app.on_event("shutdown")
async def stop_scheduler():
    if settings.enable_scrapper:
        app.state.scheduler.shutdown(wait=False)


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
async def health():
    return {"status": "healthy"}


@app.get("/favicon.ico")
async def get_favicon():
    return FileResponse(
        "resources/images/mediafusion_logo.png", media_type="image/x-icon"
    )


@app.get("/static/{file_path:path}")
async def function(file_path: str):
    response = FileResponse(f"resources/{file_path}")
    response.headers.update(headers)
    return response


@app.get("/configure", tags=["configure"])
@app.get("/{secret_str}/configure", tags=["configure"])
async def configure(
    response: Response,
    request: Request,
    user_data: schemas.UserData = Depends(crypto.decrypt_user_data),
):
    response.headers.update(headers)
    response.headers.update(no_cache_headers)

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
    response: Response, user_data: schemas.UserData = Depends(crypto.decrypt_user_data)
):
    response.headers.update({**headers, **no_cache_headers})

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
async def get_catalog(
    response: Response,
    request: Request,
    catalog_type: Literal["movie", "series", "tv"],
    catalog_id: str,
    skip: int = 0,
    genre: str = None,
    user_data: schemas.UserData = Depends(crypto.decrypt_user_data),
):
    response.headers.update(headers)
    if genre and "&" in genre:
        genre, skip = genre.split("&")
        skip = skip.split("=")[1] if "=" in skip else "0"
        skip = int(skip) if skip and skip.isdigit() else 0

    cache_key = f"{catalog_type}_{catalog_id}_{skip}_{genre}_catalog"
    if user_data.streaming_provider and catalog_id.startswith(
        user_data.streaming_provider.service
    ):
        response.headers.update(no_cache_headers)
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
    response.headers.update(headers)
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
    response.headers.update(headers)

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
async def get_streams(
    catalog_type: Literal["movie", "series", "tv"],
    video_id: str,
    response: Response,
    request: Request,
    secret_str: str = None,
    season: int = None,
    episode: int = None,
    user_data: schemas.UserData = Depends(crypto.decrypt_user_data),
):
    response.headers.update(headers)

    if catalog_type == "movie":
        fetched_streams = await crud.get_movie_streams(
            user_data, secret_str, request.app.state.redis, video_id
        )
    elif catalog_type == "series":
        fetched_streams = await crud.get_series_streams(
            user_data, secret_str, request.app.state.redis, video_id, season, episode
        )
    else:
        response.headers.update(no_cache_headers)
        fetched_streams = await crud.get_tv_streams(video_id)

    return {"streams": fetched_streams}


@app.post("/encrypt-user-data", tags=["user_data"])
async def encrypt_user_data(user_data: schemas.UserData):
    encrypted_str = crypto.encrypt_user_data(user_data)
    return {"encrypted_str": encrypted_str}


@app.get("/{secret_str}/streaming_provider", tags=["streaming_provider"])
async def streaming_provider_endpoint(
    secret_str: str,
    info_hash: str,
    response: Response,
    season: int = None,
    episode: int = None,
):
    response.headers.update(headers)
    response.headers.update(no_cache_headers)

    user_data = crypto.decrypt_user_data(secret_str)
    if not user_data.streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    stream = await crud.get_stream_by_info_hash(info_hash)
    if not stream:
        raise HTTPException(status_code=400, detail="Stream not found.")

    magnet_link = await torrent.convert_info_hash_to_magnet(
        info_hash, stream.announce_list
    )

    episode_data = stream.get_episode(season, episode)
    filename = episode_data.filename if episode_data else stream.filename

    try:
        if user_data.streaming_provider.service == "seedr":
            video_url = await get_direct_link_from_seedr(
                info_hash, magnet_link, user_data, stream, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "realdebrid":
            video_url = get_direct_link_from_realdebrid(
                info_hash, magnet_link, user_data, filename, stream.file_index, 1, 0
            )
        elif user_data.streaming_provider.service == "alldebrid":
            video_url = get_direct_link_from_alldebrid(
                info_hash, magnet_link, user_data, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "offcloud":
            video_url = get_direct_link_from_offcloud(
                info_hash, magnet_link, user_data, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "pikpak":
            video_url = await get_direct_link_from_pikpak(
                info_hash, magnet_link, user_data, stream, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "torbox":
            video_url = get_direct_link_from_torbox(
                info_hash, magnet_link, user_data, filename, 1, 0
            )
        elif user_data.streaming_provider.service == "premiumize":
            video_url = get_direct_link_from_premiumize(
                info_hash, magnet_link, user_data, stream.torrent_name, filename, 1, 0
            )
        else:
            video_url = get_direct_link_from_debridlink(
                info_hash, magnet_link, user_data, filename, stream.file_index, 1, 0
            )
    except ProviderException as error:
        logging.error(
            "Exception occurred for %s: %s",
            info_hash,
            error.message,
            exc_info=True if error.video_file_name == "api_error.mp4" else False,
        )
        video_url = f"{settings.host_url}/static/exceptions/{error.video_file_name}"
    except Exception as e:
        logging.error("Exception occurred for %s: %s", info_hash, e, exc_info=True)
        video_url = f"{settings.host_url}/static/exceptions/api_error.mp4"

    return RedirectResponse(url=video_url, headers=response.headers)


@app.get("/poster/{catalog_type}/{mediafusion_id}.jpg", tags=["poster"])
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
            image_byte_io, media_type="image/jpeg", headers=headers
        )
    except Exception as e:
        logging.error(f"Unexpected error while creating poster: {e}")
        mediafusion_data.is_poster_working = False
        await mediafusion_data.save()
        raise HTTPException(status_code=404, detail="Failed to create poster.")


@app.post("/tv-metadata", status_code=status.HTTP_201_CREATED, tags=["tv"])
async def add_tv_metadata(metadata: schemas.TVMetaData):
    try:
        metadata.streams = validation_helper.validate_tv_metadata(metadata)
    except validation_helper.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    tv_channel_id, is_new = await crud.save_tv_channel_metadata(metadata)

    if is_new:
        return {
            "status": f"Metadata with ID {tv_channel_id} has been created and is pending approval. Thanks for your contribution."
        }

    return {
        "status": f"Tv Channel with ID {tv_channel_id} Streams has been updated. Thanks for your contribution."
    }


app.include_router(seedr_router, prefix="/seedr", tags=["seedr"])
app.include_router(realdebrid_router, prefix="/realdebrid", tags=["realdebrid"])
app.include_router(debridlink_router, prefix="/debridlink", tags=["debridlink"])
app.include_router(premiumize_router, prefix="/premiumize", tags=["premiumize"])
