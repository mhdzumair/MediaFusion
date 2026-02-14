"""
Xtream Codes Import API endpoints.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytz
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.content.m3u_import import (
    _import_movie_entry,
    _import_series_entry,
    _import_tv_entry,
)
from api.routers.content.torrent_import import ImportResponse
from api.routers.user.auth import require_auth
from db.config import settings
from db.database import get_async_session
from db.enums import IPTVSourceType
from db.models import IPTVSource, User
from db.redis_database import REDIS_ASYNC_CLIENT
from scrapers.import_tasks import create_import_job, run_xtream_import
from utils.profile_crypto import profile_crypto
from utils.xtream_client import XtreamAuthError, XtreamClient, XtreamError

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# Xtream Codes Import Schemas
# ============================================


class XtreamCredentials(BaseModel):
    """Xtream Codes server credentials."""

    server_url: str
    username: str
    password: str


class XtreamCategory(BaseModel):
    """Xtream category info."""

    id: str
    name: str
    count: int = 0


class XtreamAnalyzeResponse(BaseModel):
    """Response from Xtream server analysis."""

    status: str
    account_info: dict[str, Any] | None = None  # exp_date, max_connections, etc.
    summary: dict[str, int] = Field(default_factory=dict)  # {live: X, vod: Y, series: Z}
    live_categories: list[XtreamCategory] = Field(default_factory=list)
    vod_categories: list[XtreamCategory] = Field(default_factory=list)
    series_categories: list[XtreamCategory] = Field(default_factory=list)
    redis_key: str = ""
    error: str | None = None


class XtreamImportRequest(BaseModel):
    """Request to import from Xtream server."""

    redis_key: str
    source_name: str
    save_source: bool = True
    is_public: bool = False
    import_live: bool = True
    import_vod: bool = True
    import_series: bool = True
    # Category filters (None = all)
    live_category_ids: list[str] | None = None
    vod_category_ids: list[str] | None = None
    series_category_ids: list[str] | None = None


# ============================================
# Xtream Codes Endpoints
# ============================================


@router.post("/xtream/analyze", response_model=XtreamAnalyzeResponse)
async def analyze_xtream(
    credentials: XtreamCredentials,
    user: User = Depends(require_auth),
):
    """
    Analyze an Xtream Codes server and return available content.

    Tests the credentials and fetches categories and content counts
    for Live TV, VOD (movies), and Series.
    """
    # Check if IPTV import feature is enabled
    if not settings.enable_iptv_import:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IPTV import feature is disabled on this server.",
        )

    try:
        client = XtreamClient(
            server_url=credentials.server_url,
            username=credentials.username,
            password=credentials.password,
        )

        # Authenticate and get account info
        account_data = await client.authenticate()
        user_info = account_data.get("user_info", {})

        account_info = {
            "status": user_info.get("status"),
            "exp_date": user_info.get("exp_date"),
            "max_connections": user_info.get("max_connections"),
            "active_cons": user_info.get("active_cons"),
            "is_trial": user_info.get("is_trial") == "1",
        }

        # Fetch all categories and count items
        live_cats = await client.get_live_categories()
        vod_cats = await client.get_vod_categories()
        series_cats = await client.get_series_categories()

        # Get counts per category and total
        live_streams = await client.get_live_streams()
        vod_streams = await client.get_vod_streams()
        series_list = await client.get_series()

        # Build category count maps
        live_count_map = {}
        for stream in live_streams:
            cat_id = str(stream.get("category_id", ""))
            live_count_map[cat_id] = live_count_map.get(cat_id, 0) + 1

        vod_count_map = {}
        for stream in vod_streams:
            cat_id = str(stream.get("category_id", ""))
            vod_count_map[cat_id] = vod_count_map.get(cat_id, 0) + 1

        series_count_map = {}
        for series in series_list:
            cat_id = str(series.get("category_id", ""))
            series_count_map[cat_id] = series_count_map.get(cat_id, 0) + 1

        # Build response categories with counts
        live_categories = [
            XtreamCategory(
                id=str(cat.get("category_id", "")),
                name=cat.get("category_name", "Unknown"),
                count=live_count_map.get(str(cat.get("category_id", "")), 0),
            )
            for cat in live_cats
        ]

        vod_categories = [
            XtreamCategory(
                id=str(cat.get("category_id", "")),
                name=cat.get("category_name", "Unknown"),
                count=vod_count_map.get(str(cat.get("category_id", "")), 0),
            )
            for cat in vod_cats
        ]

        series_categories = [
            XtreamCategory(
                id=str(cat.get("category_id", "")),
                name=cat.get("category_name", "Unknown"),
                count=series_count_map.get(str(cat.get("category_id", "")), 0),
            )
            for cat in series_cats
        ]

        summary = {
            "live": len(live_streams),
            "vod": len(vod_streams),
            "series": len(series_list),
        }

        # Cache data for import
        redis_key = f"xtream_analyze_{uuid4().hex[:12]}"
        cache_data = {
            "credentials": {
                "server_url": credentials.server_url,
                "username": credentials.username,
                "password": credentials.password,
            },
            "live_streams": live_streams,
            "vod_streams": vod_streams,
            "series_list": series_list,
            "summary": summary,
            "user_id": user.id,
        }

        await REDIS_ASYNC_CLIENT.set(
            redis_key,
            json.dumps(cache_data),
            ex=3600,  # 1 hour
        )

        return XtreamAnalyzeResponse(
            status="success",
            account_info=account_info,
            summary=summary,
            live_categories=live_categories,
            vod_categories=vod_categories,
            series_categories=series_categories,
            redis_key=redis_key,
        )

    except XtreamAuthError as e:
        logger.warning(f"Xtream auth error: {e}")
        return XtreamAnalyzeResponse(
            status="error",
            error=f"Authentication failed: {str(e)}",
        )
    except XtreamError as e:
        logger.warning(f"Xtream error: {e}")
        return XtreamAnalyzeResponse(
            status="error",
            error=str(e),
        )
    except Exception as e:
        logger.exception(f"Failed to analyze Xtream server: {e}")
        return XtreamAnalyzeResponse(
            status="error",
            error=f"Failed to connect to server: {str(e)}",
        )


@router.post("/xtream", response_model=ImportResponse)
async def import_xtream(
    xtream_import_request: XtreamImportRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import content from an Xtream Codes server.

    Uses cached data from the analyze step. Imports selected content
    types (Live TV, VOD, Series) and optionally saves credentials for re-sync.

    For large imports (>100 items total), the import is processed in the background.
    The response will include a job_id that can be used to poll for status.
    """
    # Check if IPTV import feature is enabled
    if not settings.enable_iptv_import:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IPTV import feature is disabled on this server.",
        )

    # Enforce private-only if public sharing is disabled
    is_public = xtream_import_request.is_public
    if not settings.allow_public_iptv_sharing:
        is_public = False

    import_id = uuid4().hex[:10]
    stats = {"tv": 0, "movie": 0, "series": 0, "failed": 0, "skipped": 0}

    try:
        # Load cached data
        cached_data = await REDIS_ASYNC_CLIENT.get(xtream_import_request.redis_key)
        if not cached_data:
            return ImportResponse(
                status="error",
                message="Session expired. Please analyze the server again.",
            )

        cache = json.loads(cached_data)
        credentials = cache.get("credentials", {})
        live_streams = cache.get("live_streams", [])
        vod_streams = cache.get("vod_streams", [])
        series_list = cache.get("series_list", [])

        # Filter by selected categories to calculate total
        filtered_live = []
        filtered_vod = []
        filtered_series = []

        if xtream_import_request.import_live:
            if xtream_import_request.live_category_ids:
                filtered_live = [
                    s for s in live_streams if str(s.get("category_id", "")) in xtream_import_request.live_category_ids
                ]
            else:
                filtered_live = live_streams

        if xtream_import_request.import_vod:
            if xtream_import_request.vod_category_ids:
                filtered_vod = [
                    s for s in vod_streams if str(s.get("category_id", "")) in xtream_import_request.vod_category_ids
                ]
            else:
                filtered_vod = vod_streams

        if xtream_import_request.import_series:
            if xtream_import_request.series_category_ids:
                filtered_series = [
                    s for s in series_list if str(s.get("category_id", "")) in xtream_import_request.series_category_ids
                ]
            else:
                filtered_series = series_list

        total_items = len(filtered_live) + len(filtered_vod) + len(filtered_series)

        # For large imports, use background processing
        BACKGROUND_THRESHOLD = 100
        if total_items > BACKGROUND_THRESHOLD:
            job_id = f"xtream_{import_id}"
            await create_import_job(
                job_id=job_id,
                user_id=user.id,
                source_type="xtream",
                total_items=total_items,
            )

            # Queue background task
            await asyncio.to_thread(
                run_xtream_import.send,
                job_id=job_id,
                user_id=user.id,
                server_url=credentials["server_url"],
                username=credentials["username"],
                password=credentials["password"],
                source_name=xtream_import_request.source_name,
                is_public=is_public,
                save_source=xtream_import_request.save_source,
                import_live=xtream_import_request.import_live,
                import_vod=xtream_import_request.import_vod,
                import_series=xtream_import_request.import_series,
                live_category_ids=xtream_import_request.live_category_ids,
                vod_category_ids=xtream_import_request.vod_category_ids,
                series_category_ids=xtream_import_request.series_category_ids,
            )

            # Delete cache after queueing
            await REDIS_ASYNC_CLIENT.delete(xtream_import_request.redis_key)

            return ImportResponse(
                status="processing",
                message=f"Import of {total_items} items started in background.",
                import_id=import_id,
                details={
                    "job_id": job_id,
                    "total_items": total_items,
                    "background": True,
                },
            )

        # Delete cache for sync processing
        await REDIS_ASYNC_CLIENT.delete(xtream_import_request.redis_key)

        # Build Xtream client for URL construction
        client = XtreamClient(
            server_url=credentials["server_url"],
            username=credentials["username"],
            password=credentials["password"],
        )

        # Process live streams
        for stream in filtered_live:
            try:
                stream_url = client.build_live_url(str(stream.get("stream_id", "")))
                entry = {
                    "name": stream.get("name", "Unknown"),
                    "url": stream_url,
                    "logo": stream.get("stream_icon"),
                    "genres": [],
                    "index": stream.get("num", 0),
                }

                result = await _import_tv_entry(
                    session=session,
                    entry=entry,
                    source="xtream",
                    user_id=user.id,
                    is_public=is_public,
                )

                if result.get("stream_created"):
                    stats["tv"] += 1
                elif result.get("stream_existed"):
                    stats["skipped"] += 1

            except Exception as e:
                logger.warning(f"Failed to import live stream: {e}")
                stats["failed"] += 1

        # Process VOD streams
        for stream in filtered_vod:
            try:
                ext = stream.get("container_extension", "mkv")
                stream_url = client.build_vod_url(str(stream.get("stream_id", "")), ext)
                entry = {
                    "name": stream.get("name", "Unknown"),
                    "url": stream_url,
                    "logo": stream.get("stream_icon"),
                    "parsed_title": stream.get("name"),
                    "parsed_year": None,
                    "index": stream.get("num", 0),
                }

                # Try to extract year from name
                year_match = re.search(r"\((\d{4})\)", entry["name"])
                if year_match:
                    entry["parsed_year"] = int(year_match.group(1))
                    entry["parsed_title"] = entry["name"][: year_match.start()].strip()

                await _import_movie_entry(
                    session=session,
                    entry=entry,
                    source="xtream",
                    user_id=user.id,
                    is_public=is_public,
                )
                stats["movie"] += 1

            except Exception as e:
                logger.warning(f"Failed to import VOD: {e}")
                stats["failed"] += 1

        # Process series
        for series in filtered_series:
            try:
                # Get series details with episodes
                series_id = str(series.get("series_id", ""))
                series_info = await client.get_series_info(series_id)

                episodes = series_info.get("episodes", {})
                for season_num, eps in episodes.items():
                    for ep in eps:
                        try:
                            ext = ep.get("container_extension", "mkv")
                            stream_url = client.build_series_url(str(ep.get("id", "")), ext)
                            entry = {
                                "name": f"{series.get('name', 'Unknown')} S{season_num}E{ep.get('episode_num', 0)}",
                                "url": stream_url,
                                "logo": series.get("cover"),
                                "parsed_title": series.get("name"),
                                "parsed_year": None,
                                "season": int(season_num),
                                "episode": int(ep.get("episode_num", 0)),
                                "index": 0,
                            }

                            await _import_series_entry(
                                session=session,
                                entry=entry,
                                source="xtream",
                                user_id=user.id,
                                is_public=is_public,
                            )
                            stats["series"] += 1

                        except Exception as e:
                            logger.warning(f"Failed to import episode: {e}")
                            stats["failed"] += 1

            except Exception as e:
                logger.warning(f"Failed to import series {series.get('name')}: {e}")
                stats["failed"] += 1

        await session.commit()

        # Save IPTV source if requested
        source_id = None
        if xtream_import_request.save_source:
            # Encrypt credentials
            encrypted_creds = profile_crypto.encrypt_secrets(
                {
                    "username": credentials["username"],
                    "password": credentials["password"],
                }
            )

            iptv_source = IPTVSource(
                user_id=user.id,
                source_type=IPTVSourceType.XTREAM,
                name=xtream_import_request.source_name,
                server_url=credentials["server_url"],
                encrypted_credentials=encrypted_creds,
                is_public=is_public,
                import_live=xtream_import_request.import_live,
                import_vod=xtream_import_request.import_vod,
                import_series=xtream_import_request.import_series,
                live_category_ids=xtream_import_request.live_category_ids,
                vod_category_ids=xtream_import_request.vod_category_ids,
                series_category_ids=xtream_import_request.series_category_ids,
                last_synced_at=datetime.now(pytz.UTC),
                last_sync_stats=stats,
                is_active=True,
            )
            session.add(iptv_source)
            await session.commit()
            await session.refresh(iptv_source)
            source_id = iptv_source.id
            logger.info(f"Saved Xtream source '{xtream_import_request.source_name}' for user {user.id}")

        total_imported = stats["tv"] + stats["movie"] + stats["series"]
        return ImportResponse(
            status="success",
            message=f"Successfully imported {total_imported} items from Xtream server.",
            import_id=import_id,
            details={
                "source": "xtream",
                "is_public": is_public,
                "stats": stats,
                "source_saved": xtream_import_request.save_source,
                "source_id": source_id,
            },
        )

    except Exception as e:
        logger.exception(f"Failed to import from Xtream: {e}")
        return ImportResponse(
            status="error",
            message=f"Failed to import: {str(e)}",
        )
