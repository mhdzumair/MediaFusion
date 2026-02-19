"""
Scheduler Management API endpoints for admin control over scheduled jobs.
"""

import asyncio
import json
import logging
from datetime import datetime
from multiprocessing import Process

import humanize
import pytz
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.routers.user.auth import require_role
from db.config import settings
from db.enums import UserRole
from db.models import User
from db.redis_database import REDIS_ASYNC_CLIENT
from utils import const

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin - Scheduler Management"])


# ============================================
# Constants
# ============================================

# Define all scheduler jobs with their metadata
SCHEDULER_JOBS = {
    # Scrapy Spiders
    "tamil_blasters": {
        "display_name": "TamilBlasters",
        "category": "scraper",
        "description": "Scrapes Tamil movie torrents from TamilBlasters",
        "crontab_setting": "tamil_blasters_scheduler_crontab",
        "disable_setting": "disable_tamil_blasters_scheduler",
    },
    "tamilmv": {
        "display_name": "TamilMV",
        "category": "scraper",
        "description": "Scrapes Tamil movie torrents from TamilMV",
        "crontab_setting": "tamilmv_scheduler_crontab",
        "disable_setting": "disable_tamilmv_scheduler",
    },
    "formula_ext": {
        "display_name": "Formula EXT",
        "category": "scraper",
        "description": "Scrapes Formula racing content from ext.to via FlareSolverr",
        "crontab_setting": "formula_ext_scheduler_crontab",
        "disable_setting": "disable_formula_ext_scheduler",
    },
    "motogp_ext": {
        "display_name": "MotoGP EXT",
        "category": "scraper",
        "description": "Scrapes MotoGP racing content from ext.to via FlareSolverr",
        "crontab_setting": "motogp_ext_scheduler_crontab",
        "disable_setting": "disable_motogp_ext_scheduler",
    },
    "wwe_ext": {
        "display_name": "WWE EXT",
        "category": "scraper",
        "description": "Scrapes WWE wrestling content from ext.to via FlareSolverr",
        "crontab_setting": "wwe_ext_scheduler_crontab",
        "disable_setting": "disable_wwe_ext_scheduler",
    },
    "ufc_ext": {
        "display_name": "UFC EXT",
        "category": "scraper",
        "description": "Scrapes UFC fighting content from ext.to via FlareSolverr",
        "crontab_setting": "ufc_ext_scheduler_crontab",
        "disable_setting": "disable_ufc_ext_scheduler",
    },
    "movies_tv_ext": {
        "display_name": "Movies TV EXT",
        "category": "scraper",
        "description": "Scrapes movies and TV series from ext.to via FlareSolverr",
        "crontab_setting": "movies_tv_ext_scheduler_crontab",
        "disable_setting": "disable_movies_tv_ext_scheduler",
    },
    "nowmetv": {
        "display_name": "NowMeTV",
        "category": "scraper",
        "description": "Scrapes TV content from NowMeTV",
        "crontab_setting": "nowmetv_scheduler_crontab",
        "disable_setting": "disable_nowmetv_scheduler",
    },
    "nowsports": {
        "display_name": "NowSports",
        "category": "scraper",
        "description": "Scrapes sports content",
        "crontab_setting": "nowsports_scheduler_crontab",
        "disable_setting": "disable_nowsports_scheduler",
    },
    "tamilultra": {
        "display_name": "Tamil Ultra",
        "category": "scraper",
        "description": "Scrapes Tamil Ultra content",
        "crontab_setting": "tamilultra_scheduler_crontab",
        "disable_setting": "disable_tamilultra_scheduler",
    },
    "sport_video": {
        "display_name": "Sport Video",
        "category": "scraper",
        "description": "Scrapes sports video content",
        "crontab_setting": "sport_video_scheduler_crontab",
        "disable_setting": "disable_sport_video_scheduler",
    },
    "dlhd": {
        "display_name": "DaddyLiveHD",
        "category": "scraper",
        "description": "Scrapes live sports streams",
        "crontab_setting": "dlhd_scheduler_crontab",
        "disable_setting": "disable_dlhd_scheduler",
    },
    "arab_torrents": {
        "display_name": "Arab Torrents",
        "category": "scraper",
        "description": "Scrapes Arabic movie and series torrents",
        "crontab_setting": "arab_torrents_scheduler_crontab",
        "disable_setting": "disable_arab_torrents_scheduler",
    },
    # Feed Scrapers
    "prowlarr_feed_scraper": {
        "display_name": "Prowlarr Feed",
        "category": "feed",
        "description": "Processes torrents from Prowlarr feed",
        "crontab_setting": "prowlarr_feed_scraper_crontab",
        "disable_setting": "disable_prowlarr_feed_scraper",
    },
    "jackett_feed_scraper": {
        "display_name": "Jackett Feed",
        "category": "feed",
        "description": "Processes torrents from Jackett feed",
        "crontab_setting": "jackett_feed_scraper_crontab",
        "disable_setting": "disable_jackett_feed_scraper",
    },
    "rss_feed_scraper": {
        "display_name": "RSS Feed",
        "category": "feed",
        "description": "Processes user RSS feed subscriptions",
        "crontab_setting": "rss_feed_scraper_crontab",
        "disable_setting": "disable_rss_feed_scraper",
    },
    # Maintenance Jobs
    "validate_tv_streams_in_db": {
        "display_name": "TV Stream Validation",
        "category": "maintenance",
        "description": "Validates TV streams in database",
        "crontab_setting": "validate_tv_streams_in_db_crontab",
        "disable_setting": "disable_validate_tv_streams_in_db",
    },
    "update_seeders": {
        "display_name": "Update Seeders",
        "category": "maintenance",
        "description": "Updates seeder counts for torrents",
        "crontab_setting": "update_seeders_crontab",
        "disable_setting": "disable_update_seeders",
    },
    "cleanup_expired_scraper_task": {
        "display_name": "Cleanup Scraper Tasks",
        "category": "maintenance",
        "description": "Cleans up expired scraper task data",
        "crontab_setting": "cleanup_expired_scraper_task_crontab",
        "disable_setting": None,  # Always enabled
    },
    "cleanup_expired_cache_task": {
        "display_name": "Cleanup Expired Cache",
        "category": "maintenance",
        "description": "Cleans up expired cache entries",
        "crontab_setting": "cleanup_expired_cache_task_crontab",
        "disable_setting": None,  # Always enabled
    },
    "background_search": {
        "display_name": "Background Search",
        "category": "background",
        "description": "Runs background searches for missing content",
        "crontab_setting": "background_search_crontab",
        "disable_setting": None,  # Always enabled
    },
}


# ============================================
# Pydantic Schemas
# ============================================


class SchedulerJobInfo(BaseModel):
    """Information about a scheduled job"""

    id: str
    display_name: str
    category: str
    description: str
    crontab: str
    is_enabled: bool
    last_run: str | None = None
    last_run_timestamp: float | None = None
    time_since_last_run: str = "Never run"
    next_run_in: str | None = None
    next_run_timestamp: float | None = None
    last_run_state: dict | None = None
    is_running: bool = False


class SchedulerJobsResponse(BaseModel):
    """Response containing all scheduler jobs"""

    jobs: list[SchedulerJobInfo]
    total: int
    active: int
    disabled: int
    running: int
    global_scheduler_disabled: bool


class SchedulerStatsResponse(BaseModel):
    """Statistics about scheduler"""

    total_jobs: int
    active_jobs: int
    disabled_jobs: int
    running_jobs: int
    jobs_by_category: dict
    global_scheduler_disabled: bool


class ManualRunResponse(BaseModel):
    """Response for manual job run"""

    success: bool
    message: str
    job_id: str


class InlineRunResponse(BaseModel):
    """Response for inline job run (direct execution)"""

    success: bool
    message: str
    job_id: str
    execution_time_seconds: float
    result: dict | None = None
    error: str | None = None


class JobHistoryEntry(BaseModel):
    """Single history entry for a job"""

    run_at: str
    duration_seconds: float | None = None
    status: str
    items_scraped: int | None = None
    error: str | None = None


class JobHistoryResponse(BaseModel):
    """Response containing job execution history"""

    job_id: str
    display_name: str
    entries: list[JobHistoryEntry]
    total: int


# ============================================
# Helper Functions
# ============================================


async def get_job_info(job_id: str, job_meta: dict) -> SchedulerJobInfo:
    """Get detailed information about a single job"""
    # Get crontab
    crontab_setting = job_meta.get("crontab_setting")
    crontab = getattr(settings, crontab_setting, "0 0 * * *") if crontab_setting else "0 0 * * *"

    # Check if enabled
    disable_setting = job_meta.get("disable_setting")
    if settings.disable_all_scheduler:
        is_enabled = False
    elif disable_setting:
        is_enabled = not getattr(settings, disable_setting, False)
    else:
        is_enabled = True

    # Get last run info from Redis
    task_key = f"background_tasks:run_spider:spider_name={job_id}"
    state_key = f"scrapy_stats:{job_id}"

    # Also check other task key patterns
    if job_id not in const.SCRAPY_SPIDERS:
        task_key = f"background_tasks:{job_id}"

    last_run_timestamp = await REDIS_ASYNC_CLIENT.get(task_key)
    last_run_state_raw = await REDIS_ASYNC_CLIENT.get(state_key)

    # Check if job is currently running
    running_key = f"scheduler:running:{job_id}"
    is_running = await REDIS_ASYNC_CLIENT.exists(running_key) > 0

    # Parse last run info
    last_run = None
    last_run_ts = None
    time_since_last_run = "Never run"

    if last_run_timestamp:
        try:
            last_run_ts = float(last_run_timestamp)
            last_run_dt = datetime.fromtimestamp(last_run_ts, tz=pytz.UTC)
            last_run = last_run_dt.isoformat()
            delta = datetime.now(tz=pytz.UTC) - last_run_dt
            time_since_last_run = humanize.precisedelta(delta, minimum_unit="minutes")
        except (ValueError, TypeError):
            pass

    # Calculate next run time
    next_run_in = None
    next_run_ts = None
    if is_enabled and crontab:
        try:
            cron_trigger = CronTrigger.from_crontab(crontab)
            next_time = cron_trigger.get_next_fire_time(None, datetime.now(tz=cron_trigger.timezone))
            if next_time:
                next_run_ts = next_time.timestamp()
                next_run_in = humanize.naturaldelta(next_time - datetime.now(tz=cron_trigger.timezone))
        except Exception:
            pass

    # Parse last run state
    last_run_state = None
    if last_run_state_raw:
        try:
            last_run_state = json.loads(last_run_state_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    return SchedulerJobInfo(
        id=job_id,
        display_name=job_meta["display_name"],
        category=job_meta["category"],
        description=job_meta["description"],
        crontab=crontab,
        is_enabled=is_enabled,
        last_run=last_run,
        last_run_timestamp=last_run_ts,
        time_since_last_run=time_since_last_run,
        next_run_in=next_run_in,
        next_run_timestamp=next_run_ts,
        last_run_state=last_run_state,
        is_running=is_running,
    )


# ============================================
# API Endpoints
# ============================================


@router.get("/schedulers", response_model=SchedulerJobsResponse)
async def list_scheduler_jobs(
    category: str | None = Query(None, description="Filter by category (scraper, feed, maintenance, background)"),
    enabled_only: bool = Query(False, description="Only show enabled jobs"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    List all scheduled jobs with their status and configuration.
    """
    jobs = []
    for job_id, job_meta in SCHEDULER_JOBS.items():
        # Filter by category
        if category and job_meta["category"] != category:
            continue

        job_info = await get_job_info(job_id, job_meta)

        # Filter by enabled status
        if enabled_only and not job_info.is_enabled:
            continue

        jobs.append(job_info)

    # Sort by category, then by display_name
    jobs.sort(key=lambda j: (j.category, j.display_name))

    active = sum(1 for j in jobs if j.is_enabled)
    disabled = sum(1 for j in jobs if not j.is_enabled)
    running = sum(1 for j in jobs if j.is_running)

    return SchedulerJobsResponse(
        jobs=jobs,
        total=len(jobs),
        active=active,
        disabled=disabled,
        running=running,
        global_scheduler_disabled=settings.disable_all_scheduler,
    )


@router.get("/schedulers/stats", response_model=SchedulerStatsResponse)
async def get_scheduler_stats(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Get scheduler statistics summary.
    """
    active = 0
    disabled = 0
    running = 0
    jobs_by_category: dict = {}

    for job_id, job_meta in SCHEDULER_JOBS.items():
        category = job_meta["category"]
        if category not in jobs_by_category:
            jobs_by_category[category] = {"total": 0, "active": 0}

        jobs_by_category[category]["total"] += 1

        job_info = await get_job_info(job_id, job_meta)
        if job_info.is_enabled:
            active += 1
            jobs_by_category[category]["active"] += 1
        else:
            disabled += 1

        if job_info.is_running:
            running += 1

    return SchedulerStatsResponse(
        total_jobs=len(SCHEDULER_JOBS),
        active_jobs=active,
        disabled_jobs=disabled,
        running_jobs=running,
        jobs_by_category=jobs_by_category,
        global_scheduler_disabled=settings.disable_all_scheduler,
    )


@router.get("/schedulers/{job_id}", response_model=SchedulerJobInfo)
async def get_scheduler_job(
    job_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Get detailed information about a specific scheduled job.
    """
    if job_id not in SCHEDULER_JOBS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduler job '{job_id}' not found",
        )

    return await get_job_info(job_id, SCHEDULER_JOBS[job_id])


@router.post("/schedulers/{job_id}/run", response_model=ManualRunResponse)
async def run_scheduler_job(
    job_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Manually trigger a scheduled job to run immediately.
    Note: This sends the task to the background worker queue.
    """
    if job_id not in SCHEDULER_JOBS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduler job '{job_id}' not found",
        )

    job_meta = SCHEDULER_JOBS[job_id]

    # Check if already running
    running_key = f"scheduler:running:{job_id}"
    if await REDIS_ASYNC_CLIENT.exists(running_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job '{job_id}' is already running",
        )

    # Import task functions based on category
    # Use asyncio.to_thread to avoid blocking the event loop with synchronous Redis calls
    try:
        if job_meta["category"] == "scraper":
            from mediafusion_scrapy.task import run_spider

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(run_spider.send, spider_name=job_id, crontab_expression=crontab)
        elif job_id == "prowlarr_feed_scraper":
            from scrapers.feed_scraper import run_prowlarr_feed_scraper

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(run_prowlarr_feed_scraper.send, crontab_expression=crontab)
        elif job_id == "jackett_feed_scraper":
            from scrapers.feed_scraper import run_jackett_feed_scraper

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(run_jackett_feed_scraper.send, crontab_expression=crontab)
        elif job_id == "rss_feed_scraper":
            from scrapers.rss_scraper import run_rss_feed_scraper

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(run_rss_feed_scraper.send, crontab_expression=crontab)
        elif job_id == "validate_tv_streams_in_db":
            from scrapers.tv import validate_tv_streams_in_db

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(validate_tv_streams_in_db.send, crontab_expression=crontab)
        elif job_id == "update_seeders":
            from scrapers.trackers import update_torrent_seeders

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(update_torrent_seeders.send, crontab_expression=crontab)
        elif job_id == "cleanup_expired_scraper_task":
            from scrapers.scraper_tasks import cleanup_expired_scraper_task

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(cleanup_expired_scraper_task.send, crontab_expression=crontab)
        elif job_id == "cleanup_expired_cache_task":
            from streaming_providers.cache_helpers import cleanup_expired_cache

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(cleanup_expired_cache.send, crontab_expression=crontab)
        elif job_id == "background_search":
            from scrapers.background_scraper import run_background_search

            crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")
            await asyncio.to_thread(run_background_search.send, crontab_expression=crontab)
        else:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=f"Manual run not implemented for job '{job_id}'",
            )

        logger.info(f"Manual run triggered for scheduler job: {job_id} by user: {current_user.username}")

        return ManualRunResponse(
            success=True,
            message=f"Job '{job_meta['display_name']}' has been queued for execution",
            job_id=job_id,
        )

    except Exception as e:
        logger.error(f"Failed to trigger job {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger job: {str(e)}",
        )


@router.post("/schedulers/{job_id}/run-inline", response_model=InlineRunResponse)
async def run_scheduler_job_inline(
    job_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Run a scheduled job directly (inline) within FastAPI for testing purposes.

    WARNING: This runs the job synchronously in the FastAPI process, which may:
    - Block the current request for the duration of the job
    - Use server resources directly
    - Take a long time for heavy jobs

    Use this only for testing purposes. For production, use the regular /run endpoint.
    """
    import time

    if job_id not in SCHEDULER_JOBS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduler job '{job_id}' not found",
        )

    job_meta = SCHEDULER_JOBS[job_id]

    # Check if already running
    running_key = f"scheduler:running:{job_id}"
    if await REDIS_ASYNC_CLIENT.exists(running_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job '{job_id}' is already running",
        )

    # Set running flag with expiry (safety measure)
    await REDIS_ASYNC_CLIENT.setex(running_key, 3600, "1")  # 1 hour max

    start_time = time.time()
    result_data = None
    error_message = None

    try:
        crontab = getattr(settings, job_meta["crontab_setting"], "0 0 * * *")

        if job_meta["category"] == "scraper":
            from mediafusion_scrapy.task import run_spider_in_process

            def _run():
                p = Process(target=run_spider_in_process, args=(job_id,), kwargs={"crontab_expression": crontab})
                p.start()
                p.join()
                return p.exitcode

            exitcode = await asyncio.to_thread(_run)
            result_data = {
                "status": "completed" if exitcode == 0 else "failed",
                "spider_name": job_id,
                "exitcode": exitcode,
            }

        elif job_id == "prowlarr_feed_scraper":
            from scrapers.feed_scraper import ProwlarrFeedScraper

            if not settings.is_scrap_from_prowlarr:
                result_data = {
                    "status": "skipped",
                    "reason": "Prowlarr scraping disabled",
                }
            else:
                scraper = ProwlarrFeedScraper()
                await scraper.scrape_feed()
                result_data = {"status": "completed"}

        elif job_id == "jackett_feed_scraper":
            from scrapers.feed_scraper import JackettFeedScraper

            if not settings.is_scrap_from_jackett:
                result_data = {
                    "status": "skipped",
                    "reason": "Jackett scraping disabled",
                }
            else:
                scraper = JackettFeedScraper()
                await scraper.scrape_feed()
                result_data = {"status": "completed"}

        elif job_id == "rss_feed_scraper":
            from scrapers.rss_scraper import RssScraper

            scraper = RssScraper()
            result = await scraper.process_all_feeds()
            total_processed = sum(r.get("processed", 0) for r in result.get("results", {}).values())
            total_errors = sum(r.get("errors", 0) for r in result.get("results", {}).values())
            result_data = {
                "status": "completed",
                "processed": total_processed,
                "errors": total_errors,
                "feeds_count": len(result.get("results", {})),
            }

        elif job_id == "validate_tv_streams_in_db":
            from scrapers.tv import validate_tv_streams_in_db

            # Call the underlying async function directly
            await validate_tv_streams_in_db.fn(crontab_expression=crontab)
            result_data = {"status": "completed"}

        elif job_id == "update_seeders":
            from scrapers.trackers import update_torrent_seeders

            await update_torrent_seeders.fn(crontab_expression=crontab)
            result_data = {"status": "completed"}

        elif job_id == "cleanup_expired_scraper_task":
            from scrapers.scraper_tasks import cleanup_expired_scraper_task

            await cleanup_expired_scraper_task.fn(crontab_expression=crontab)
            result_data = {"status": "completed"}

        elif job_id == "cleanup_expired_cache_task":
            from streaming_providers.cache_helpers import cleanup_expired_cache

            await cleanup_expired_cache.fn(crontab_expression=crontab)
            result_data = {"status": "completed"}

        elif job_id == "background_search":
            from scrapers.background_scraper import BackgroundSearchWorker

            # Don't call database.init() - FastAPI already has it initialized
            worker = BackgroundSearchWorker()
            await worker.manager.cleanup_stale_processing()
            # Process movies and series concurrently
            await asyncio.gather(worker.process_movie_batch(), worker.process_series_batch())
            result_data = {"status": "completed"}

        else:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=f"Inline run not implemented for job '{job_id}'",
            )

        execution_time = time.time() - start_time
        logger.info(
            f"Inline run completed for scheduler job: {job_id} "
            f"by user: {current_user.username} in {execution_time:.2f}s"
        )

        return InlineRunResponse(
            success=True,
            message=f"Job '{job_meta['display_name']}' completed successfully",
            job_id=job_id,
            execution_time_seconds=round(execution_time, 2),
            result=result_data,
        )

    except HTTPException:
        raise
    except Exception as e:
        execution_time = time.time() - start_time
        error_message = str(e)
        logger.error(f"Inline run failed for job {job_id}: {e}")

        return InlineRunResponse(
            success=False,
            message=f"Job '{job_meta['display_name']}' failed",
            job_id=job_id,
            execution_time_seconds=round(execution_time, 2),
            error=error_message,
        )

    finally:
        # Remove running flag
        await REDIS_ASYNC_CLIENT.delete(running_key)


@router.get("/schedulers/{job_id}/history", response_model=JobHistoryResponse)
async def get_job_history(
    job_id: str,
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Get execution history for a scheduled job.
    History is stored in Redis and may be limited.
    """
    if job_id not in SCHEDULER_JOBS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduler job '{job_id}' not found",
        )

    job_meta = SCHEDULER_JOBS[job_id]
    history_key = f"scheduler:history:{job_id}"

    # Get history from Redis list
    history_raw = await REDIS_ASYNC_CLIENT.lrange(history_key, 0, limit - 1)

    entries = []
    for entry_raw in history_raw:
        try:
            entry_data = json.loads(entry_raw)
            entries.append(
                JobHistoryEntry(
                    run_at=entry_data.get("run_at", "Unknown"),
                    duration_seconds=entry_data.get("duration_seconds"),
                    status=entry_data.get("status", "unknown"),
                    items_scraped=entry_data.get("items_scraped"),
                    error=entry_data.get("error"),
                )
            )
        except (json.JSONDecodeError, TypeError):
            continue

    return JobHistoryResponse(
        job_id=job_id,
        display_name=job_meta["display_name"],
        entries=entries,
        total=len(entries),
    )
