from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db.config import settings
# Removed imports for stream-related tasks
from mediafusion_scrapy.task import run_spider # Keep for potential future catalog spiders
from scrapers.scraper_tasks import cleanup_expired_scraper_task # Keep for general task cleanup


def setup_scheduler(scheduler: AsyncIOScheduler):
    """
    Set up the scheduler with the required jobs.
    """
    # Setup tamil blasters scraper
    if not settings.disable_tamil_blasters_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.tamil_blasters_scheduler_crontab),
            name="tamil_blasters",
            kwargs={
                "spider_name": "tamil_blasters",
                "crontab_expression": settings.tamil_blasters_scheduler_crontab,
            },
        )

    # Removed scheduler jobs for stream-related spiders and tasks:
    # tamilmv, formula_tgx, nowmetv, nowsports, tamilultra, validate_tv_streams_in_db,
    # sport_video, dlhd, motogp_tgx, update_seeders, arab_torrents, wwe_tgx, ufc_tgx,
    # movies_tv_tgx, run_prowlarr_feed_scraper, run_jackett_feed_scraper,
    # cleanup_expired_cache, run_background_search

    # Keep general task cleanup
    scheduler.add_job(
        cleanup_expired_scraper_task.send,
        CronTrigger.from_crontab(settings.cleanup_expired_scraper_task_crontab),
        name="cleanup_expired_scraper_task",
        kwargs={
            "crontab_expression": settings.cleanup_expired_scraper_task_crontab,
        },
    )
