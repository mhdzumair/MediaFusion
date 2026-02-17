import asyncio
from functools import partial

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db.config import settings
from mediafusion_scrapy.task import run_spider
from scrapers.background_scraper import run_background_search
from scrapers.feed_scraper import run_jackett_feed_scraper, run_prowlarr_feed_scraper
from scrapers.rss_scraper import run_rss_feed_scraper
from scrapers.scraper_tasks import cleanup_expired_scraper_task
from scrapers.trackers import update_torrent_seeders
from scrapers.tv import validate_tv_streams_in_db
from streaming_providers.cache_helpers import cleanup_expired_cache


async def async_send(actor_send_method, **kwargs):
    """
    Wrapper to run Dramatiq's synchronous .send() method in a thread pool
    to avoid blocking the asyncio event loop.
    """
    # Run the synchronous .send() call in a thread pool
    await asyncio.to_thread(partial(actor_send_method, **kwargs))


def setup_scheduler(scheduler: AsyncIOScheduler):
    """
    Set up the scheduler with the required jobs.
    All jobs use async_send wrapper to avoid blocking the event loop
    when enqueuing tasks to Dramatiq via synchronous Redis calls.
    """
    # Setup tamil blasters scraper
    if not settings.disable_tamil_blasters_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.tamil_blasters_scheduler_crontab),
            name="tamil_blasters",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "tamil_blasters",
                "crontab_expression": settings.tamil_blasters_scheduler_crontab,
            },
        )

    # Setup tamilmv scraper
    if not settings.disable_tamilmv_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.tamilmv_scheduler_crontab),
            name="tamilmv",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "tamilmv",
                "crontab_expression": settings.tamilmv_scheduler_crontab,
            },
        )

    # Setup formula_ext scraper
    if not settings.disable_formula_ext_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.formula_ext_scheduler_crontab),
            name="formula_ext",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "formula_ext",
                "scrape_all": "false",
                "crontab_expression": settings.formula_ext_scheduler_crontab,
            },
        )

    # Setup motogp_ext scraper
    if not settings.disable_motogp_ext_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.motogp_ext_scheduler_crontab),
            name="motogp_ext",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "motogp_ext",
                "scrape_all": "false",
                "crontab_expression": settings.motogp_ext_scheduler_crontab,
            },
        )

    # Setup wwe_ext scraper
    if not settings.disable_wwe_ext_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.wwe_ext_scheduler_crontab),
            name="wwe_ext",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "wwe_ext",
                "scrape_all": "false",
                "crontab_expression": settings.wwe_ext_scheduler_crontab,
            },
        )

    # Setup ufc_ext scraper
    if not settings.disable_ufc_ext_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.ufc_ext_scheduler_crontab),
            name="ufc_ext",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "ufc_ext",
                "scrape_all": "false",
                "crontab_expression": settings.ufc_ext_scheduler_crontab,
            },
        )

    # Setup movies_tv_ext scraper
    if not settings.disable_movies_tv_ext_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.movies_tv_ext_scheduler_crontab),
            name="movies_tv_ext",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "movies_tv_ext",
                "scrape_all": "false",
                "crontab_expression": settings.movies_tv_ext_scheduler_crontab,
            },
        )

    # Setup nowmetv scraper
    if not settings.disable_nowmetv_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.nowmetv_scheduler_crontab),
            name="nowmetv",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "nowmetv",
                "crontab_expression": settings.nowmetv_scheduler_crontab,
            },
        )

    # Setup nowsports scraper
    if not settings.disable_nowsports_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.nowsports_scheduler_crontab),
            name="nowsports",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "nowsports",
                "crontab_expression": settings.nowsports_scheduler_crontab,
            },
        )

    # Setup tamilultra scraper
    if not settings.disable_tamilultra_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.tamilultra_scheduler_crontab),
            name="tamilultra",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "tamilultra",
                "crontab_expression": settings.tamilultra_scheduler_crontab,
            },
        )

    # Schedule validate_tv_streams_in_db
    if not settings.disable_validate_tv_streams_in_db:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.validate_tv_streams_in_db_crontab),
            name="validate_tv_streams_in_db",
            kwargs={
                "actor_send_method": validate_tv_streams_in_db.send,
                "crontab_expression": settings.validate_tv_streams_in_db_crontab,
            },
        )

    # Schedule sport_video scraper
    if not settings.disable_sport_video_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.sport_video_scheduler_crontab),
            name="sport_video",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "sport_video",
                "scrape_all": "false",
                "crontab_expression": settings.sport_video_scheduler_crontab,
            },
        )

    # Schedule dlhd scraper
    if not settings.disable_dlhd_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.dlhd_scheduler_crontab),
            name="dlhd",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "dlhd",
                "crontab_expression": settings.dlhd_scheduler_crontab,
            },
        )

    if not settings.disable_update_seeders:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.update_seeders_crontab),
            name="update_seeders",
            kwargs={
                "actor_send_method": update_torrent_seeders.send,
                "crontab_expression": settings.update_seeders_crontab,
            },
        )

    if not settings.disable_arab_torrents_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.arab_torrents_scheduler_crontab),
            name="arab_torrents",
            kwargs={
                "actor_send_method": run_spider.send,
                "spider_name": "arab_torrents",
                "crontab_expression": settings.arab_torrents_scheduler_crontab,
            },
        )

    # Schedule the feed scraper
    if not settings.disable_prowlarr_feed_scraper:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.prowlarr_feed_scraper_crontab),
            name="prowlarr_feed_scraper",
            kwargs={
                "actor_send_method": run_prowlarr_feed_scraper.send,
                "crontab_expression": settings.prowlarr_feed_scraper_crontab,
            },
        )

    if not settings.disable_jackett_feed_scraper:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.jackett_feed_scraper_crontab),
            name="jackett_feed_scraper",
            kwargs={
                "actor_send_method": run_jackett_feed_scraper.send,
                "crontab_expression": settings.jackett_feed_scraper_crontab,
            },
        )

    # Schedule RSS feed scraper
    if not settings.disable_rss_feed_scraper:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.rss_feed_scraper_crontab),
            name="rss_feed_scraper",
            kwargs={
                "actor_send_method": run_rss_feed_scraper.send,
                "crontab_expression": settings.rss_feed_scraper_crontab,
            },
        )

    scheduler.add_job(
        async_send,
        CronTrigger.from_crontab(settings.cleanup_expired_scraper_task_crontab),
        name="cleanup_expired_scraper_task",
        kwargs={
            "actor_send_method": cleanup_expired_scraper_task.send,
            "crontab_expression": settings.cleanup_expired_scraper_task_crontab,
        },
    )

    scheduler.add_job(
        async_send,
        CronTrigger.from_crontab(settings.cleanup_expired_cache_task_crontab),
        name="cleanup_expired_cache_task",
        kwargs={
            "actor_send_method": cleanup_expired_cache.send,
            "crontab_expression": settings.cleanup_expired_cache_task_crontab,
        },
    )

    scheduler.add_job(
        async_send,
        CronTrigger.from_crontab(settings.background_search_crontab),
        name="background_search",
        kwargs={
            "actor_send_method": run_background_search.send,
            "crontab_expression": settings.background_search_crontab,
        },
    )
