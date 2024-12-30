import json
import logging
from datetime import date, datetime
from typing import Literal, Optional
from uuid import uuid4

from fastapi import HTTPException, UploadFile, File, Form, APIRouter, BackgroundTasks
from fastapi.requests import Request
from fastapi.responses import RedirectResponse, Response

from db import schemas
from db.config import settings
from db.crud import (
    get_movie_data_by_id,
    get_series_data_by_id,
    get_stream_by_info_hash,
    store_new_torrent_streams,
    update_metadata,
    get_or_create_metadata,
)
from db.enums import TorrentType
from db.models import TorrentStreams, EpisodeFile
from db.redis_database import REDIS_ASYNC_CLIENT
from mediafusion_scrapy.task import run_spider
from scrapers.tv import add_tv_metadata, parse_m3u_playlist
from utils import const, torrent
from utils.network import get_request_namespace
from utils.parser import calculate_max_similarity_ratio, convert_bytes_to_readable
from utils.runtime_const import TEMPLATES
from utils.telegram_bot import telegram_notifier

router = APIRouter()


def validate_api_password(api_password: str):
    if api_password != settings.api_password:
        raise HTTPException(status_code=401, detail="Invalid API password.")
    return True


def raise_error(error_msg: str):
    error_msg = f"Failed due to: {error_msg}"
    logging.warning(error_msg)
    raise HTTPException(status_code=200, detail=error_msg)


@router.get("/", tags=["scraper"])
async def get_scraper(
    request: Request,
    action: str = None,
    meta_id: str = None,
    meta_type: str = None,
    season: int = None,
    episode: str = None,
    info_hash: str = None,
):
    prefill_data = {
        "meta_id": meta_id,
        "meta_type": meta_type,
        "season": season,
        "episode": episode,
        "action": action,
        "info_hash": info_hash,
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
            "supported_languages": sorted(const.SUPPORTED_LANGUAGES - {None}),
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
        total_pages=task.total_pages,
    )

    return {"status": f"Scraping {task.spider_name} task has been scheduled."}


@router.post("/add_tv_metadata", tags=["scraper"])
async def add_tv_meta_data(data: schemas.TVMetaDataUpload, request: Request):
    validate_api_password(data.api_password)
    await add_tv_metadata(
        [data.tv_metadata.model_dump()], namespace=get_request_namespace(request)
    )
    return {"status": "TV metadata task has been scheduled."}


@router.post("/m3u_upload", tags=["scraper"])
async def upload_m3u_playlist(
    request: Request,
    background_tasks: BackgroundTasks,
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
        await REDIS_ASYNC_CLIENT.set(redis_key, content)
        background_tasks.add_task(
            parse_m3u_playlist,
            namespace=get_request_namespace(request),
            playlist_source=m3u_playlist_source,
            playlist_redis_key=redis_key,
            playlist_url=None,
        )

    elif m3u_playlist_url:
        # Process URL submission...
        background_tasks.add_task(
            parse_m3u_playlist,
            namespace=get_request_namespace(request),
            playlist_source=m3u_playlist_source,
            playlist_url=m3u_playlist_url,
            playlist_redis_key=None,
        )
    else:
        raise_error("Either M3U playlist URL or file must be provided.")

    return {"status": "M3U playlist upload task has been scheduled."}


@router.get("/imdb_data", tags=["scraper"])
async def update_imdb_data(
    response: Response,
    meta_id: str,
    media_type: Literal["movie", "series"],
    redirect_video: bool = False,
):
    response.headers.update(const.NO_CACHE_HEADERS)
    if not (meta_id.startswith("tt") and meta_id[2:].isdigit()):
        raise_error("Invalid IMDb ID. Must start with 'tt'.")

    if media_type == "series":
        series = await get_series_data_by_id(meta_id)
        if not series:
            raise_error(f"Series with ID {meta_id} not found.")
    else:
        movie = await get_movie_data_by_id(meta_id)
        if not movie:
            raise_error(f"Movie with ID {meta_id} not found.")

    await update_metadata([meta_id], media_type)

    if redirect_video:
        return RedirectResponse(
            url=f"{settings.host_url}/static/exceptions/update_imdb_data.mp4"
        )

    return {"status": f"Successfully updated IMDb data for {meta_id}."}


@router.post("/torrent", tags=["scraper"])
async def add_torrent(
    meta_type: Literal["movie", "series"] = Form(...),
    meta_id: Optional[str] = Form(None),
    # Sports metadata fields
    title: Optional[str] = Form(None),
    year: Optional[int] = Form(None),
    poster: Optional[str] = Form(None),
    background: Optional[str] = Form(None),
    logo: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    is_add_title_to_poster: bool = Form(False),
    catalogs: Optional[str] = Form(None),
    is_sports_content: bool = Form(False),
    # Common fields
    languages: Optional[str] = Form(None),
    created_at: date = Form(...),
    torrent_type: TorrentType = Form(TorrentType.PUBLIC),
    magnet_link: Optional[str] = Form(None),
    torrent_file: Optional[UploadFile] = File(None),
    force_import: bool = Form(False),
    file_data: Optional[str] = Form(None),
    uploader: Optional[str] = Form("Anonymous"),
):
    torrent_data: dict = {}
    info_hash = None

    # Basic validation
    if not magnet_link and not torrent_file:
        raise_error("Either magnet link or torrent file must be provided.")
    if torrent_type != TorrentType.PUBLIC and not torrent_file:
        raise_error(f"Torrent file must be provided for {torrent_type} torrents.")

    # Convert catalogs string to list if provided
    catalog_list = catalogs.split(",") if catalogs else []

    # Convert languages string to list if provided
    language_list = languages.split(",") if languages else []

    # Process magnet link or torrent file
    if magnet_link:
        info_hash, trackers = torrent.parse_magnet(magnet_link)
        if not info_hash:
            raise_error("Failed to parse magnet link.")
        if torrent_stream := await get_stream_by_info_hash(info_hash):
            return {
                "status": f"Torrent already exists and attached to meta id: {torrent_stream.meta_id}"
            }
        data = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers)
        if not data:
            raise_error("Failed to fetch torrent metadata.")
        torrent_data = data[0]
    elif torrent_file:
        try:
            torrent_data = torrent.extract_torrent_metadata(
                await torrent_file.read(), is_raise_error=True
            )
        except ValueError as e:
            raise_error(str(e))
        if not torrent_data:
            raise_error("Failed to extract torrent metadata.")
        if settings.adult_content_filter_in_torrent_title and torrent_data.get("adult"):
            raise_error(
                f"Torrent name contains 18+ keywords: {torrent_data['torrent_name']}"
            )
        info_hash = torrent_data.get("info_hash")
        if torrent_stream := await get_stream_by_info_hash(info_hash):
            return {
                "status": f"Torrent already exists and attached to meta id: {torrent_stream.meta_id}"
            }

    # Handle sports content metadata
    if is_sports_content:
        if not title or not poster or not catalog_list:
            raise_error("Title, poster, and sports catalog are required.")

        # Validate catalog is in CATALOG_DATA
        if catalog_list[0] not in const.CATALOG_DATA:
            raise_error(
                f"Invalid sports catalog. Must be one of: {', '.join(const.CATALOG_DATA.keys())}"
            )

        # Determine if it's a series type catalog
        is_series_catalog = catalog_list[0] in ["formula_racing", "motogp_racing"]
        if is_series_catalog:
            meta_type = "series"
        else:
            meta_type = "movie"

        # Create metadata for sports content
        sports_metadata = {
            "title": title,
            "year": year,
            "poster": poster,
            "background": background or poster,  # Use poster as fallback
            "logo": logo,
            "description": description,
            "website": website,
            "is_add_title_to_poster": is_add_title_to_poster,
            "catalogs": catalog_list,
            "created_at": created_at,
        }

        # Get or create metadata
        metadata_result = await get_or_create_metadata(
            sports_metadata, meta_type, is_search_imdb_title=False, is_imdb_only=False
        )

        if not metadata_result:
            raise_error("Failed to create metadata for sports content.")

        meta_id = metadata_result["id"]
    else:
        # Regular IMDb content validation
        if not meta_id or not meta_id.startswith("tt") or not meta_id[2:].isdigit():
            raise_error("Invalid IMDb ID. Must start with 'tt'.")

    # For series, check if we need file annotation
    if meta_type == "series":
        has_all_metadata = all(
            file.get("season_number") and file.get("episode_number")
            for file in torrent_data.get("file_data", [])
        )

        # If we received annotated file data, update the torrent_data
        if file_data:
            annotated_files = json.loads(file_data)
            torrent_data["file_data"] = annotated_files
            # Update seasons and episodes based on annotations
            seasons = {
                file["season_number"]
                for file in annotated_files
                if file["season_number"]
            }
            episodes = {
                file["episode_number"]
                for file in annotated_files
                if file["episode_number"]
            }
            torrent_data["seasons"] = list(seasons)
            torrent_data["episodes"] = list(episodes)

        # If automatic detection failed and we don't have annotations yet
        elif not has_all_metadata:
            return {
                "status": "needs_annotation",
                "files": torrent_data.get("file_data", []),
            }

    source = "Contribution Stream"
    languages = set(language_list).union(torrent_data.get("languages", []))
    torrent_data.update(
        {
            "source": source,
            "created_at": created_at,
            "languages": languages,
            "torrent_type": torrent_type,
            "uploader": uploader,
        }
    )
    if torrent_type == TorrentType.PUBLIC:
        torrent_data.pop("torrent_file", None)

    # Add contribution_stream to catalogs if provided
    if catalog_list:
        catalog_list.append("contribution_stream")
        torrent_data["catalog"] = catalog_list
    else:
        torrent_data["catalog"] = ["contribution_stream"]

    validation_errors = []
    seasons_and_episodes = {}
    stream_cache_keys = []

    if meta_type == "movie":
        movie_data = await get_movie_data_by_id(meta_id)
        if not movie_data:
            raise_error(f"Movie with ID {meta_id} not found.")
        title = movie_data.title

        # Title similarity check
        max_similarity_ratio = calculate_max_similarity_ratio(
            torrent_data.get("title"), movie_data.title, movie_data.aka_titles
        )
        if max_similarity_ratio < 75 and not force_import:
            validation_errors.append(
                {
                    "type": "title_mismatch",
                    "message": f"Title mismatch: '{movie_data.title}' != '{torrent_data.get('title')}' ratio: {max_similarity_ratio}",
                }
            )

        # Year validation
        if torrent_data.get("year") != movie_data.year and not force_import:
            validation_errors.append(
                {
                    "type": "year_mismatch",
                    "message": f"Year mismatch: '{movie_data.year}' != '{torrent_data.get('year')}'",
                }
            )

        if validation_errors:
            return {
                "status": "validation_failed",
                "errors": validation_errors,
                "info_hash": info_hash,
                "torrent_data": torrent_data,
            }

        torrent_stream = await handle_movie_stream_store(
            info_hash, torrent_data, meta_id
        )
        stream_cache_keys = [f"torrent_streams:{meta_id}"]

    else:  # Series
        series_data = await get_series_data_by_id(meta_id)
        if not series_data:
            raise_error(f"Series with ID {meta_id} not found.")
        title = series_data.title

        # Title similarity check
        max_similarity_ratio = calculate_max_similarity_ratio(
            torrent_data.get("title"), series_data.title, series_data.aka_titles
        )
        if max_similarity_ratio < 75 and not force_import:
            validation_errors.append(
                {
                    "type": "title_mismatch",
                    "message": f"Title mismatch: '{series_data.title}' != '{torrent_data.get('title')}' ratio: {max_similarity_ratio}",
                }
            )

        # Episode validation
        if not torrent_data.get("episodes"):
            validation_errors.append(
                {
                    "type": "episodes_not_found",
                    "message": "No episode found in torrent data",
                }
            )

        # Season validation
        if not torrent_data.get("seasons", []):
            validation_errors.append(
                {
                    "type": "seasons_not_found",
                    "message": "No season found in torrent data",
                }
            )

        if validation_errors:
            return {
                "status": "validation_failed",
                "errors": validation_errors,
                "info_hash": info_hash,
                "torrent_data": torrent_data,
            }

        torrent_stream = await handle_series_stream_store(
            info_hash, torrent_data, meta_id
        )

        # Prepare seasons and episodes for cache cleanup
        for ep in torrent_stream.episode_files:
            if ep.season_number not in seasons_and_episodes:
                seasons_and_episodes[ep.season_number] = []
            seasons_and_episodes[ep.season_number].append(ep.episode_number)
            stream_cache_keys.append(
                f"torrent_streams:{meta_id}:{ep.season_number}:{ep.episode_number}"
            )

    if not torrent_stream:
        raise_error("Failed to store torrent data. Contact support.")

    # Cleanup redis caching for quick access
    for key in stream_cache_keys:
        await REDIS_ASYNC_CLIENT.delete(key)

    # Send Telegram notification
    if settings.telegram_bot_token:
        await telegram_notifier.send_contribution_notification(
            meta_id=meta_id,
            title=title,
            meta_type=meta_type,
            poster=f"{settings.poster_host_url}/poster/{meta_type}/{meta_id}.jpg",
            uploader=uploader,
            info_hash=info_hash,
            torrent_type=torrent_type,
            size=convert_bytes_to_readable(torrent_stream.size),
            torrent_name=torrent_stream.torrent_name,
            seasons_and_episodes=seasons_and_episodes,
        )

    return {
        "status": f"Successfully added torrent: {torrent_stream.id} for {title}. Thanks for your contribution."
    }


async def handle_movie_stream_store(info_hash, parsed_data, video_id):
    """
    Handles the store logic for a single torrent stream.
    """
    # Create new stream
    torrent_stream = TorrentStreams(
        id=info_hash,
        torrent_name=parsed_data.get("torrent_name"),
        announce_list=parsed_data.get("announce_list"),
        size=parsed_data.get("total_size"),
        filename=parsed_data.get("largest_file", {}).get("filename"),
        file_index=parsed_data.get("largest_file", {}).get("index"),
        languages=parsed_data.get("languages"),
        resolution=parsed_data.get("resolution"),
        codec=parsed_data.get("codec"),
        quality=parsed_data.get("quality"),
        audio=parsed_data.get("audio"),
        hdr=parsed_data.get("hdr"),
        source=parsed_data.get("source"),
        uploader=parsed_data.get("uploader"),
        catalog=parsed_data.get("catalog"),
        updated_at=datetime.now(),
        seeders=parsed_data.get("seeders"),
        created_at=parsed_data.get("created_at"),
        meta_id=video_id,
        torrent_type=parsed_data.get("torrent_type"),
        torrent_file=parsed_data.get("torrent_file"),
    )

    await store_new_torrent_streams([torrent_stream])
    logging.info(f"Created movies stream {info_hash} for {video_id}")
    return torrent_stream


async def handle_series_stream_store(info_hash, parsed_data, video_id):
    """
    Handles the storage logic for a single series torrent stream, including updating
    or creating records for all episodes contained within the torrent.
    """
    # Prepare episode data based on detailed file data or basic episode numbers
    episode_data = [
        EpisodeFile(
            season_number=file["season_number"],
            episode_number=file["episode_number"],
            filename=file.get("filename"),
            size=file.get("size"),
            file_index=file.get("index"),
            released=file.get("release_date"),
            title=file.get("title"),
            overview=file.get("overview"),
            thumbnail=file.get("thumbnail"),
        )
        for file in parsed_data["file_data"]
    ]

    # Skip the torrent if no episode data is available
    if not episode_data:
        return None

    # Create new stream, initially without episodes
    torrent_stream = TorrentStreams(
        id=info_hash,
        torrent_name=parsed_data.get("torrent_name"),
        announce_list=parsed_data.get("announce_list"),
        size=parsed_data.get("total_size"),
        filename=None,
        languages=parsed_data.get("languages"),
        resolution=parsed_data.get("resolution"),
        codec=parsed_data.get("codec"),
        quality=parsed_data.get("quality"),
        audio=parsed_data.get("audio"),
        hdr=parsed_data.get("hdr"),
        source=parsed_data.get("source"),
        uploader=parsed_data.get("uploader"),
        catalog=parsed_data.get("catalog"),
        updated_at=datetime.now(),
        seeders=parsed_data.get("seeders"),
        created_at=parsed_data.get("created_at"),
        meta_id=video_id,
        episode_files=episode_data,
        torrent_type=parsed_data.get("torrent_type"),
        torrent_file=parsed_data.get("torrent_file"),
    )
    await store_new_torrent_streams([torrent_stream])

    return torrent_stream


@router.post("/block_torrent", tags=["scraper"])
async def block_torrent(block_data: schemas.BlockTorrent):
    validate_api_password(block_data.api_password)
    torrent_stream = await TorrentStreams.get(block_data.info_hash)
    if not torrent_stream:
        raise HTTPException(status_code=404, detail="Torrent not found.")
    if torrent_stream.is_blocked:
        return {"status": f"Torrent {block_data.info_hash} is already blocked."}
    torrent_stream.is_blocked = True
    try:
        await torrent_stream.save()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to block torrent: {str(e)}"
        )
    return {"status": f"Torrent {block_data.info_hash} has been successfully blocked."}
