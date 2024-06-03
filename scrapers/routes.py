import logging
import re
from datetime import date
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException, UploadFile, File, Form, APIRouter
from fastapi.requests import Request
from fastapi.responses import RedirectResponse, Response

from db import schemas
from db.config import settings
from db.crud import (
    is_torrent_stream_exists,
    get_movie_data_by_id,
    get_series_data_by_id,
)
from mediafusion_scrapy.task import run_spider
from scrapers import imdb_data
from scrapers.prowlarr import handle_series_stream_store, handle_movie_stream_store
from scrapers.tv import add_tv_metadata, parse_m3u_playlist
from utils import const, torrent
from utils.network import get_request_namespace
from utils.parser import calculate_max_similarity_ratio
from utils.runtime_const import TEMPLATES

router = APIRouter()


def validate_api_password(api_password: str):
    if settings.api_password and api_password != settings.api_password:
        raise HTTPException(status_code=200, detail="Invalid API password.")
    return True


def raise_error(error_msg: str):
    error_msg = f"User upload Failed due to: {error_msg}"
    logging.warning(error_msg)
    raise HTTPException(status_code=200, detail=error_msg)


@router.get("/", tags=["scraper"])
async def get_scraper(
    request: Request,
    meta_id: str = None,
    meta_type: str = None,
    season: int = None,
    episode: str = None,
):
    prefill_data = {
        "meta_id": meta_id,
        "meta_type": meta_type,
        "season": season,
        "episode": episode,
    }
    return TEMPLATES.TemplateResponse(
        "html/scraper.html",
        {
            "request": request,
            "api_password_enabled": "true" if settings.api_password else "false",
            "logo_url": settings.logo_url,
            "addon_name": settings.addon_name,
            "scrapy_spiders": const.SCRAPY_SPIDERS.items(),
            "prefill_data": prefill_data,
            "catalog_data": const.CATALOG_DATA,
            "supported_series_catalog_ids": const.USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS,
            "supported_movie_catalog_ids": const.USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS,
        },
    )


@router.post("/run", tags=["scraper"])
async def run_scraper_task(task: schemas.ScraperTask):
    run_spider.send(
        task.spider_name,
        pages=task.pages,
        start_page=task.start_page,
        search_keyword=task.search_keyword,
        scrape_all=str(task.scrape_all),
        scrap_catalog_id=task.scrap_catalog_id,
    )

    return {"status": f"Scraping {task.spider_name} task has been scheduled."}


@router.post("/add_tv_metadata", tags=["scraper"])
async def add_tv_meta_data(data: schemas.TVMetaDataUpload, request: Request):
    validate_api_password(data.api_password)
    add_tv_metadata.send(
        [data.tv_metadata.model_dump()], namespace=get_request_namespace(request)
    )
    return {"status": "TV metadata task has been scheduled."}


@router.post("/m3u_upload", tags=["scraper"])
async def upload_m3u_playlist(
    request: Request,
    scraper_type: str = Form(...),
    m3u_playlist_source: str = Form(...),
    m3u_playlist_url: str = Form(None),
    m3u_playlist_file: UploadFile = File(None),
    api_password: str = Form(...),
):
    validate_api_password(api_password)
    if scraper_type != "add_m3u_playlist":
        raise_error("Invalid scraper type for this endpoint.")

    if m3u_playlist_file:
        content = await m3u_playlist_file.read()
        redis_key = f"m3u_playlist_{uuid4().hex[:10]}"
        await request.app.state.redis.set(redis_key, content)
        parse_m3u_playlist.send(
            namespace=get_request_namespace(request),
            playlist_source=m3u_playlist_source,
            playlist_redis_key=redis_key,
        )
    elif m3u_playlist_url:
        # Process URL submission...
        parse_m3u_playlist.send(
            namespace=get_request_namespace(request),
            playlist_source=m3u_playlist_source,
            playlist_url=m3u_playlist_url,
        )
    else:
        raise_error("Either M3U playlist URL or file must be provided.")

    return {"status": "M3U playlist upload task has been scheduled."}


@router.get("/imdb_data", tags=["scraper"])
async def update_imdb_data(
    response: Response, meta_id: str, redirect_video: bool = False
):
    response.headers.update(const.NO_CACHE_HEADERS)
    if not (meta_id.startswith("tt") and meta_id[2:].isdigit()):
        raise_error("Invalid IMDb ID. Must start with 'tt'.")

    await imdb_data.process_imdb_data([meta_id])

    if redirect_video:
        return RedirectResponse(
            url=f"{settings.host_url}/static/exceptions/update_imdb_data.mp4"
        )

    return {"status": f"Successfully updated IMDb data for {meta_id}."}


@router.post("/torrent", tags=["scraper"])
async def add_torrent(
    request: Request,
    meta_id: str = Form(...),
    meta_type: Literal["movie", "series"] = Form(...),
    source: str = Form(...),
    catalogs: str = Form(...),
    created_at: date = Form(...),
    season: int | None = Form(None),
    episodes: str | None = Form(None),
    magnet_link: str = Form(None),
    torrent_file: UploadFile = File(None),
):
    torrent_data: dict = {}
    error_msg = None
    info_hash = None
    title = None
    catalogs = catalogs.split(",")

    if not magnet_link and not torrent_file:
        raise_error("Either magnet link or torrent file must be provided.")
    if not meta_id.startswith("tt") or not meta_id[2:].isdigit():
        raise_error("Invalid IMDb ID. Must start with 'tt'.")
    if meta_type == "movie":
        # check if any catalog is not in const.USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS
        if not set(catalogs).issubset(const.USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS):
            raise_error("Invalid catalogs selected.")
    else:
        # check if any catalog is not in const.USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS
        if not set(catalogs).issubset(const.USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS):
            raise_error("Invalid catalogs selected.")
        if not season or not episodes:
            raise_error("Season and episode number must be provided.")
        episodes = [int(ep) for ep in episodes.split(",")]

    if error_msg:
        raise HTTPException(status_code=200, detail=error_msg)

    if magnet_link:
        info_hash, trackers = torrent.parse_magnet(magnet_link)
        if not info_hash:
            raise_error("Failed to parse magnet link.")
        if await is_torrent_stream_exists(info_hash):
            return {"status": "Torrent already exists."}
        data = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers)
        if not data:
            raise_error("Failed to fetch torrent metadata.")
        torrent_data = data[0]
    elif torrent_file:
        torrent_data = torrent.extract_torrent_metadata(await torrent_file.read())
        if not torrent_data:
            raise_error("Failed to extract torrent metadata.")
        info_hash = torrent_data.get("info_hash")
        if await is_torrent_stream_exists(info_hash):
            return {"status": "Torrent already exists."}

    # if the source is url then only take the domain name
    if re.match(r"^https?://", source):
        source = urlparse(source).netloc.replace("www.", "")

    torrent_data.update(
        {
            "source": source,
            "created_at": created_at,
        }
    )
    catalogs.append("user_upload")

    if meta_type == "movie":
        movie_data = await get_movie_data_by_id(meta_id, request.app.state.redis)
        title = movie_data.title

        max_similarity_ratio = calculate_max_similarity_ratio(
            torrent_data.get("title"), movie_data.title, movie_data.aka_titles
        )
        if max_similarity_ratio < 85:
            raise_error(
                f"Title mismatch: '{movie_data.title}' != '{torrent_data.get('title')}' ratio: {max_similarity_ratio}"
            )

        if torrent_data.get("year") != movie_data.year:
            raise_error(
                f"Year mismatch: '{movie_data.year}' != '{torrent_data.get('year')}'"
            )

        catalogs.extend(
            ["user_upload_movies", f"{torrent_data.get('source', '').lower()}_movies"]
        )
        torrent_data["catalog"] = catalogs

        torrent_stream, _ = await handle_movie_stream_store(
            info_hash, torrent_data, meta_id
        )

    else:
        series_data = await get_series_data_by_id(meta_id, request.app.state.redis)
        title = series_data.title

        max_similarity_ratio = calculate_max_similarity_ratio(
            torrent_data.get("title"), series_data.title, series_data.aka_titles
        )
        if max_similarity_ratio < 85:
            raise_error(
                f"Title mismatch: '{series_data.title}' != '{torrent_data.get('title')}' ratio: {max_similarity_ratio}"
            )

        if isinstance(torrent_data.get("episode"), list):
            if not set(torrent_data.get("episode")).issubset(set(episodes)):
                raise_error(
                    f"Episode mismatch: '{torrent_data.get('episode')}' != '{episodes}'"
                )
        elif torrent_data.get("episode") != episodes[0]:
            raise_error(
                f"Episode mismatch: '{torrent_data.get('episode')}' != '{episodes[0]}'"
            )
        else:
            raise_error("No episode found in torrent data")

        if torrent_data.get("season") != season:
            if torrent_data.get("season") is None and season == 1:
                pass  # If torrent doesn't mention season and it's season 1, it's okay
            else:
                raise_error(
                    f"Season mismatch: '{torrent_data.get('season')}' != '{season}'"
                )

        catalogs.extend(
            ["user_upload_series", f"{torrent_data.get('source', '').lower()}_series"]
        )
        torrent_data["catalog"] = catalogs

        torrent_stream, _ = await handle_series_stream_store(
            info_hash, torrent_data, meta_id, season
        )

    if not torrent_stream:
        raise_error("Failed to store torrent data. Contact support.")
    return {
        "status": f"Successfully added torrent: {torrent_stream.id} for {title}. Thanks for your contribution."
    }
