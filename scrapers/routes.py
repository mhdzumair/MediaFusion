import json
import logging
import random
import re
from datetime import date, datetime, timezone, timedelta
from typing import Literal, Optional
from uuid import uuid4

import PTT
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
    update_meta_stream,
)
from db.enums import TorrentType
from db.models import TorrentStreams, EpisodeFile, MediaFusionMetaData
from db.redis_database import REDIS_ASYNC_CLIENT
from mediafusion_scrapy.task import run_spider
from scrapers.scraper_tasks import meta_fetcher
from scrapers.tmdb_data import get_tmdb_data
from scrapers.tv import add_tv_metadata, parse_m3u_playlist
from utils import const, torrent
from utils.network import get_request_namespace
from utils.parser import calculate_max_similarity_ratio, convert_bytes_to_readable
from utils.runtime_const import TEMPLATES, SPORTS_ARTIFACTS
from utils.telegram_bot import telegram_notifier
from utils.validation_helper import validate_image_url

router = APIRouter()
DATE_STR_REGEX = re.compile(
    r"\d{4}\.\d{2}\.\d{2}|\d{4}-\d{2}-\d{2}|\d{4}_\d{2}_\d{2}|\d{2}\.\d{2}\.\d{4}|\d{2}-\d{2}-\d{4}|\d{2}_\d{2}_\d{4}",
)


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
    mediafusion_id: str = None,
):
    prefill_data = {
        "meta_id": meta_id,
        "meta_type": meta_type,
        "season": season,
        "episode": episode,
        "action": action,
        "info_hash": info_hash,
        "mediafusion_id": mediafusion_id,
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


@router.post("/torrent")
async def add_torrent(
    meta_type: Literal["movie", "series", "sports"] = Form(...),
    created_at: date = Form(...),
    torrent_file: Optional[UploadFile] = File(None),
    magnet_link: Optional[str] = Form(None),
    meta_id: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    poster: Optional[str] = Form(None),
    background: Optional[str] = Form(None),
    logo: Optional[str] = Form(None),
    is_add_title_to_poster: bool = Form(False),
    catalogs: Optional[str] = Form(None),
    languages: Optional[str] = Form(None),
    torrent_type: TorrentType = Form(TorrentType.PUBLIC),
    force_import: bool = Form(False),
    file_data: Optional[str] = Form(None),
    uploader: Optional[str] = Form("Anonymous"),
    resolution: Optional[str] = Form(None),
    quality: Optional[str] = Form(None),
    audio: Optional[str] = Form(None),
    codec: Optional[str] = Form(None),
    hdr: Optional[str] = Form(None),
):
    torrent_data: dict = {}
    info_hash = None

    # Basic validation
    if not magnet_link and not torrent_file:
        raise_error("Either magnet link or torrent file must be provided.")
    if torrent_type != TorrentType.PUBLIC and not torrent_file:
        raise_error(f"Torrent file must be provided for {torrent_type} torrents.")

    # make sure the created_at date is not in the future
    if created_at > (date.today() + timedelta(days=1)):
        raise_error("Created at date cannot be more than one day in the future.")

    # Convert lists from form data
    catalog_list = catalogs.split(",") if catalogs else []
    language_list = languages.split(",") if languages else []
    audio_list = audio.split(",") if audio else []
    hdr_list = hdr.split(",") if hdr else []

    # Process magnet link or torrent file
    if magnet_link:
        info_hash, trackers = torrent.parse_magnet(magnet_link)
        if not info_hash:
            raise_error("Failed to parse magnet link.")
        data = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers)
        if not data:
            raise_error("Failed to fetch torrent metadata.")
        torrent_data = data[0]
        info_hash = info_hash.lower()
    elif torrent_file:
        try:
            torrent_data = torrent.extract_torrent_metadata(
                await torrent_file.read(), is_raise_error=True
            )
        except ValueError as e:
            raise_error(str(e))
        if not torrent_data:
            raise_error("Failed to extract torrent metadata.")
        info_hash = torrent_data.get("info_hash")

    # Check if torrent already exists
    if torrent_stream := await get_stream_by_info_hash(info_hash):
        if meta_id and meta_id != torrent_stream.meta_id:
            await torrent_stream.delete()
        elif meta_type == "movie" and torrent_stream.filename is None:
            await torrent_stream.delete()
        elif meta_type == "series" and (
            not torrent_stream.episode_files
            or not torrent_stream.episode_files[0].filename
        ):
            await torrent_stream.delete()
        else:
            return {
                "status": f"⚠️ Torrent {info_hash} already exists and is attached to meta ID: {torrent_stream.meta_id}. "
                f"Thank you for trying to contribute ✨. If the torrent is not visible, please contact support with the Torrent InfoHash."
            }

    # Add technical specifications to torrent_data
    if resolution:
        torrent_data["resolution"] = torrent_data.get("resolution") or resolution
    if quality:
        torrent_data["quality"] = torrent_data.get("quality") or quality
    if audio_list:
        torrent_data["audio"] = torrent_data.get("audio") or audio_list
    if codec:
        torrent_data["codec"] = torrent_data.get("codec") or codec
    if hdr_list:
        torrent_data["hdr"] = torrent_data.get("hdr") or hdr_list
    if title:
        torrent_data["title"] = title

    # Handle sports content metadata
    if meta_type == "sports":
        if not title or not catalog_list:
            raise_error("Title and sports catalog are required.")
        catalog = catalog_list[0]
        genres = []
        sports_category = {}

        if catalog in ["wwe", "ufc"]:
            genres = [catalog]
            sports_category = SPORTS_ARTIFACTS[catalog.upper()]
            catalog_list = ["fighting"]
            catalog = "fighting"
        elif catalog_mapped := const.CATALOG_DATA.get(catalog):
            sports_category = SPORTS_ARTIFACTS[catalog_mapped]
        else:
            raise_error(
                f"Invalid sports catalog. Must be one of: {', '.join(const.CATALOG_DATA.keys())}"
            )

        # Determine if it's a series type catalog
        is_series_catalog = catalog in ["formula_racing", "motogp_racing"]
        if is_series_catalog:
            meta_type = "series"
        else:
            meta_type = "movie"
            # Check if title has a date and append created_at if not
            created_at_str = created_at.strftime("%d.%m.%Y")
            if created_at_str not in title and not PTT.parse_title(title).get("date"):
                date_str_match = DATE_STR_REGEX.search(title)
                if date_str_match:
                    created_at_str = date_str_match.group()
                    title = title.replace(created_at_str, "").strip()
                title += f" {created_at_str}"

        # Create metadata for sports content
        sports_metadata = {
            "title": title,
            "year": created_at.year,
            "poster": poster or random.choice(sports_category["poster"]),
            "background": background or random.choice(sports_category["background"]),
            "logo": logo or random.choice(sports_category["logo"]),
            "is_add_title_to_poster": is_add_title_to_poster if poster else True,
            "catalogs": catalog_list,
            "created_at": created_at,
            "genres": genres,
        }

        metadata_result = await get_or_create_metadata(
            sports_metadata, meta_type, is_search_imdb_title=False, is_imdb_only=False
        )
        if not metadata_result:
            raise_error("Failed to create metadata for sports content.")
        meta_id = metadata_result["id"]

    elif not meta_id or meta_id.startswith("mf"):
        metadata = {
            "id": meta_id,
            "title": title or torrent_data.get("title"),
            "year": torrent_data.get("year") or created_at.year,
            "poster": poster,
            "background": background,
            "logo": logo,
            "is_add_title_to_poster": is_add_title_to_poster,
            "catalogs": catalog_list,
            "created_at": created_at,
        }
        if meta_id and meta_id.startswith("mftmdb"):
            # Search for metadata from tmdb
            tmdb_data = await get_tmdb_data(meta_id[6:], meta_type)
            if tmdb_data:
                metadata.update(tmdb_data)

        # search for metadata from imdb/tmdb
        metadata_result = await get_or_create_metadata(
            metadata,
            meta_type,
            is_search_imdb_title=meta_id is None,
        )
        meta_id = metadata_result["id"]
    else:
        # Regular IMDb content validation
        if not meta_id or not meta_id.startswith(("tt", "mf")):
            raise_error("Invalid IMDb ID. Must start with 'tt' or 'mf'.")

    # For series, check if we need file annotation
    if meta_type == "series":
        if not file_data:
            return {
                "status": "needs_annotation",
                "files": torrent_data.get("file_data", []),
            }

        annotated_files = json.loads(file_data)
        torrent_data["file_data"] = annotated_files
        # Update seasons and episodes based on annotations
        seasons = {
            file["season_number"] for file in annotated_files if file["season_number"]
        }
        episodes = {
            file["episode_number"] for file in annotated_files if file["episode_number"]
        }
        torrent_data["seasons"] = list(seasons)
        torrent_data["episodes"] = list(episodes)

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

        if validation_errors:
            torrent_data.pop("torrent_file", None)
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
            torrent_data.pop("torrent_file", None)
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
    await REDIS_ASYNC_CLIENT.delete(*stream_cache_keys)

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
            catalogs=catalog_list,
            languages=languages,
        )

    return {
        "status": f"🎉 Successfully added torrent: {torrent_stream.id} for {title} ({torrent_stream.meta_id}). Thanks for your contribution! 🙌"
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
        uploaded_at=datetime.now(tz=timezone.utc),
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
        if file.get("season_number") and file.get("episode_number")
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
        uploaded_at=datetime.now(tz=timezone.utc),
    )
    await store_new_torrent_streams([torrent_stream])

    return torrent_stream


@router.post("/block_torrent", tags=["scraper"])
async def block_torrent(block_data: schemas.BlockTorrent):
    validate_api_password(block_data.api_password)
    torrent_stream = await TorrentStreams.get(block_data.info_hash)
    if not torrent_stream:
        return {
            "status": f"Torrent {block_data.info_hash} is already deleted / not found."
        }
    if block_data.action == "block" and torrent_stream.is_blocked:
        return {"status": f"Torrent {block_data.info_hash} is already blocked."}
    try:
        if block_data.action == "delete":
            await torrent_stream.delete()
        else:
            torrent_stream.is_blocked = True
            await torrent_stream.save()
        # Send Telegram notification
        if settings.telegram_bot_token:
            metadata = await MediaFusionMetaData.get_motor_collection().find_one(
                {"_id": torrent_stream.meta_id}, projection={"title": 1, "type": 1}
            )
            title = metadata.get("title")
            meta_type = metadata.get("type")
            poster = f"{settings.poster_host_url}/poster/{meta_type}/{torrent_stream.meta_id}.jpg"
            await telegram_notifier.send_block_notification(
                info_hash=block_data.info_hash,
                action=block_data.action,
                meta_id=torrent_stream.meta_id,
                title=title,
                meta_type=meta_type,
                poster=poster,
                torrent_name=torrent_stream.torrent_name,
            )

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to block torrent: {str(e)}"
        )

    return {
        "status": f"Torrent {block_data.info_hash} has been successfully {'blocked' if block_data.action == 'block' else 'deleted'}."
    }


@router.post("/migrate_id", tags=["scraper"])
async def migrate_id(migrate_data: schemas.MigrateID):
    # Validate ID formats
    if not (
        migrate_data.mediafusion_id.startswith("mf")
        and migrate_data.imdb_id.startswith("tt")
    ):
        raise_error("Invalid mediafusion or IMDb ID.")

    # First verify the IMDb metadata exists
    if migrate_data.media_type == "series":
        metadata = await get_series_data_by_id(migrate_data.imdb_id)
    else:
        metadata = await get_movie_data_by_id(migrate_data.imdb_id)
    if not metadata:
        raise_error(f"Metadata with ID {migrate_data.imdb_id} not found.")

    # Get old metadata for notification before deleting
    old_metadata = await MediaFusionMetaData.get_motor_collection().find_one(
        {"_id": migrate_data.mediafusion_id}, projection={"title": 1}
    )
    if not old_metadata:
        raise_error(f"Metadata with ID {migrate_data.mediafusion_id} not found.")
    old_title = old_metadata.get("title")

    # Perform the migration
    await TorrentStreams.find({"meta_id": migrate_data.mediafusion_id}).update(
        {"$set": {"meta_id": migrate_data.imdb_id}}
    )
    await MediaFusionMetaData.get_motor_collection().delete_one(
        {"_id": migrate_data.mediafusion_id}
    )
    await update_meta_stream(migrate_data.imdb_id)

    # Send notification
    if settings.telegram_bot_token:
        new_poster = f"{settings.poster_host_url}/poster/{migrate_data.media_type}/{migrate_data.imdb_id}.jpg"
        await telegram_notifier.send_migration_notification(
            old_id=migrate_data.mediafusion_id,
            new_id=migrate_data.imdb_id,
            title=old_title,
            meta_type=migrate_data.media_type,
            poster=new_poster,
        )

    return {
        "status": f"Successfully migrated {migrate_data.mediafusion_id} to {migrate_data.imdb_id}."
    }


@router.post("/analyze_torrent")
async def analyze_torrent(
    torrent_file: Optional[UploadFile] = File(None),
    magnet_link: Optional[str] = Form(None),
    meta_type: Literal["movie", "series", "sports"] = Form(...),
):
    """Analyze torrent file or magnet link and search for matching content"""
    try:
        if torrent_file:
            # Extract metadata from torrent
            torrent_data = torrent.extract_torrent_metadata(
                await torrent_file.read(), is_raise_error=True
            )
            # Remove torrent_file bytes data from response
            torrent_data.pop("torrent_file", None)
        elif magnet_link:
            info_hash, trackers = torrent.parse_magnet(magnet_link)
            if not info_hash:
                raise HTTPException(
                    status_code=400, detail="Failed to parse magnet link."
                )
            data = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers)
            if not data:
                raise HTTPException(
                    status_code=400, detail="Failed to fetch torrent metadata."
                )
            torrent_data = data[0]
            torrent_data.pop("torrent_file", None)
        else:
            raise HTTPException(
                status_code=400,
                detail="Either torrent file or magnet link must be provided.",
            )

        if not torrent_data or not torrent_data.get("title"):
            raise HTTPException(
                status_code=400, detail="Could not extract title from torrent"
            )

        torrent_data["type"] = meta_type
        if meta_type == "sports":
            # parse title for sports content
            title = torrent_data["torrent_name"]
            # remove resolution, quality, codec, audio, hdr from title
            for key in [
                "resolution",
                "quality",
                "codec",
                "hdr",
                "group",
                "extension",
                "container",
            ]:
                replacer = torrent_data.get(key, "")
                if replacer and isinstance(replacer, str):
                    title = title.replace(replacer, "")

            # extract date from title. ex: 2021.05.01 | 2021-05-01 | 2021_05_01 | 01.05.2021 | 01-05-2021 | 01_05_2021
            date_str = ""
            date_str_match = DATE_STR_REGEX.search(title)
            if date_str_match:
                date_str = date_str_match.group()
                title = title.replace(date_str, "").strip()

            # cleanup title
            title = re.sub(r"h26[45]|.torrent", "", title, flags=re.IGNORECASE)
            title = (
                title.replace(".", " ")
                .replace("-", " ")
                .replace("_", " ")
                .replace(" at ", " vs ")
            )
            title = re.sub(r"\s+", " ", title).strip()
            title += f" {date_str}"
            torrent_data["title"] = title
            return {
                "torrent_data": torrent_data,
                "matches": [],
            }

        # Search for match using meta_fetcher
        matches = await meta_fetcher.search_multiple_results(
            title=torrent_data["title"],
            year=torrent_data.get("year"),
            media_type=meta_type,
        )

        return {
            "torrent_data": torrent_data,
            "matches": matches,
        }

    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to analyze torrent: {str(e)}"
        )


@router.post("/update_images")
async def update_images(
    meta_id: str = Form(...),
    poster: Optional[str] = Form(None),
    background: Optional[str] = Form(None),
    logo: Optional[str] = Form(None),
):
    """Update poster, background, and/or logo for existing content"""
    # Validate that at least one image URL is provided
    if not any([poster, background, logo]):
        raise_error(
            "At least one image URL (poster, background, or logo) must be provided"
        )

    # Get metadata to validate it exists and get current values
    metadata = await MediaFusionMetaData.get_motor_collection().find_one(
        {"_id": meta_id},
        projection={"title": 1, "type": 1, "poster": 1, "background": 1, "logo": 1},
    )

    if not metadata:
        raise_error(f"Content with ID {meta_id} not found")

    # Build update fields
    update_fields = {}
    if poster:
        update_fields["poster"] = poster
        update_fields["is_poster_working"] = True
        if not await validate_image_url(poster):
            raise_error("Invalid poster image URL, Not able to fetch image")
    if background:
        update_fields["background"] = background
        if not await validate_image_url(background):
            raise_error("Invalid background image URL, Not able to fetch image")
    if logo:
        update_fields["logo"] = logo
        if not await validate_image_url(logo):
            raise_error("Invalid logo image URL, Not able to fetch image")

    # Update metadata
    await MediaFusionMetaData.get_motor_collection().update_one(
        {"_id": meta_id}, {"$set": update_fields}
    )

    # Send Telegram notification
    if settings.telegram_bot_token:
        await telegram_notifier.send_image_update_notification(
            meta_id=meta_id,
            title=metadata.get("title"),
            meta_type=metadata.get("type"),
            poster=f"{settings.poster_host_url}/poster/{metadata.get('type')}/{meta_id}.jpg",
            old_poster=metadata.get("poster"),
            new_poster=poster,
            old_background=metadata.get("background"),
            new_background=background,
            old_logo=metadata.get("logo"),
            new_logo=logo,
        )

    # cleanup redis cache
    cache_key = f"{metadata.get('type')}_{meta_id}.jpg"
    await REDIS_ASYNC_CLIENT.delete(cache_key)

    return {"status": f"Successfully updated images for {meta_id}"}
