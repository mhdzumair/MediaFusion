from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db.config import settings
from mediafusion_scrapy.task import run_spider
from scrapers.imdb_data import fetch_movie_ids_to_update
from scrapers.prowlarr_feed import run_prowlarr_feed_scraper
from scrapers.trackers import update_torrent_seeders
from scrapers.tv import validate_tv_streams_in_db
from scrapers.utils import cleanup_expired_scraper_task
from streaming_providers.cache_helpers import cleanup_expired_cache


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

    # Setup tamilmv scraper
    if not settings.disable_tamilmv_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.tamilmv_scheduler_crontab),
            name="tamilmv",
            kwargs={
                "spider_name": "tamilmv",
                "crontab_expression": settings.tamilmv_scheduler_crontab,
            },
        )

    # Setup formula_tgx scraper
    if not settings.disable_formula_tgx_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.formula_tgx_scheduler_crontab),
            name="formula_tgx",
            kwargs={
                "spider_name": "formula_tgx",
                "scrape_all": "false",
                "crontab_expression": settings.formula_tgx_scheduler_crontab,
            },
        )

    # Setup nowmetv scraper
    if not settings.disable_nowmetv_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.nowmetv_scheduler_crontab),
            name="nowmetv",
            kwargs={
                "spider_name": "nowmetv",
                "crontab_expression": settings.nowmetv_scheduler_crontab,
            },
        )

    # Setup nowsports scraper
    if not settings.disable_nowsports_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.nowsports_scheduler_crontab),
            name="nowsports",
            kwargs={
                "spider_name": "nowsports",
                "crontab_expression": settings.nowsports_scheduler_crontab,
            },
        )

    # Setup tamilultra scraper
    if not settings.disable_tamilultra_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.tamilultra_scheduler_crontab),
            name="tamilultra",
            kwargs={
                "spider_name": "tamilultra",
                "crontab_expression": settings.tamilultra_scheduler_crontab,
            },
        )

    # Schedule validate_tv_streams_in_db
    if not settings.disable_validate_tv_streams_in_db:
        scheduler.add_job(
            validate_tv_streams_in_db.send,
            CronTrigger.from_crontab(settings.validate_tv_streams_in_db_crontab),
            name="validate_tv_streams_in_db",
            kwargs={"crontab_expression": settings.validate_tv_streams_in_db_crontab},
        )

    # Schedule sport_video scraper
    if not settings.disable_sport_video_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.sport_video_scheduler_crontab),
            name="sport_video",
            kwargs={
                "spider_name": "sport_video",
                "scrape_all": "false",
                "crontab_expression": settings.sport_video_scheduler_crontab,
            },
        )

    # Schedule dlhd scraper
    if not settings.disable_dlhd_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.dlhd_scheduler_crontab),
            name="dlhd",
            kwargs={
                "spider_name": "dlhd",
                "crontab_expression": settings.dlhd_scheduler_crontab,
            },
        )

    scheduler.add_job(
        fetch_movie_ids_to_update.send,
        CronTrigger.from_crontab(settings.update_imdb_data_crontab),
        name="update_imdb_data",
        kwargs={
            "crontab_expression": settings.update_imdb_data_crontab,
        },
    )

    if not settings.disable_motogp_tgx_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.motogp_tgx_scheduler_crontab),
            name="motogp_tgx",
            kwargs={
                "spider_name": "motogp_tgx",
                "crontab_expression": settings.motogp_tgx_scheduler_crontab,
                "scrape_all": "false",
            },
        )

    scheduler.add_job(
        update_torrent_seeders.send,
        CronTrigger.from_crontab(settings.update_seeders_crontab),
        name="update_seeders",
        kwargs={
            "crontab_expression": settings.update_seeders_crontab,
        },
    )

    if not settings.disable_arab_torrents_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.arab_torrents_scheduler_crontab),
            name="arab_torrents",
            kwargs={
                "spider_name": "arab_torrents",
                "crontab_expression": settings.arab_torrents_scheduler_crontab,
            },
        )

    if not settings.disable_wwe_tgx_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.wwe_tgx_scheduler_crontab),
            name="wwe_tgx",
            kwargs={
                "spider_name": "wwe_tgx",
                "crontab_expression": settings.wwe_tgx_scheduler_crontab,
                "scrape_all": "false",
            },
        )

    if not settings.disable_ufc_tgx_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.ufc_tgx_scheduler_crontab),
            name="ufc_tgx",
            kwargs={
                "spider_name": "ufc_tgx",
                "crontab_expression": settings.ufc_tgx_scheduler_crontab,
                "scrape_all": "false",
            },
        )

    if not settings.disable_movies_tv_tgx_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.movies_tv_tgx_scheduler_crontab),
            name="movies_tv_tgx",
            kwargs={
                "spider_name": "movies_tv_tgx",
                "crontab_expression": settings.movies_tv_tgx_scheduler_crontab,
                "scrape_all": "false",
            },
        )

    # Schedule the feed scraper
    if not settings.disable_prowlarr_feed_scraper:
        scheduler.add_job(
            run_prowlarr_feed_scraper.send,
            CronTrigger.from_crontab(settings.prowlarr_feed_scraper_crontab),
            name="prowlarr_feed_scraper",
            kwargs={
                "crontab_expression": settings.prowlarr_feed_scraper_crontab,
            },
        )

    scheduler.add_job(
        cleanup_expired_scraper_task.send,
        CronTrigger.from_crontab(settings.cleanup_expired_scraper_task_crontab),
        name="cleanup_expired_scraper_task",
        kwargs={
            "crontab_expression": settings.cleanup_expired_scraper_task_crontab,
        },
    )

    scheduler.add_job(
        cleanup_expired_cache.send,
        CronTrigger.from_crontab(settings.cleanup_expired_cache_task_crontab),
        name="cleanup_expired_cache_task",
        kwargs={
            "crontab_expression": settings.cleanup_expired_cache_task_crontab,
        },
    )
