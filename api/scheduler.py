import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db.config import settings
from mediafusion_scrapy.task import run_spider
from utils.validation_helper import validate_tv_streams_in_db


def setup_scheduler(scheduler: AsyncIOScheduler):
    """
    Set up the scheduler with the required jobs.
    """
    if settings.disable_all_scheduler:
        logging.info("All Schedulers are disabled. Not setting up any jobs.")
        return

    # Setup tamil blasters scraper
    if settings.disable_tamil_blasters_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.tamil_blasters_scheduler_crontab),
            name="tamil_blasters",
            kwargs={"spider_name": "tamil_blasters"},
        )

    # Setup tamilmv scraper
    if settings.disable_tamilmv_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.tamilmv_scheduler_crontab),
            name="tamilmv",
            kwargs={"spider_name": "tamilmv"},
        )

    # Setup formula_tgx scraper
    if settings.disable_formula_tgx_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.formula_tgx_scheduler_crontab),
            name="formula_tgx",
            kwargs={"spider_name": "formula_tgx", "scrape_all": "false"},
        )

    # Setup mhdtvworld scraper
    if settings.disable_mhdtvworld_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.mhdtvworld_scheduler_crontab),
            name="mhdtvworld",
            kwargs={"spider_name": "mhdtvworld"},
        )

    # Setup mhdtvsports scraper
    if settings.disable_mhdtvsports_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.mhdtvsports_scheduler_crontab),
            name="mhdtvsports",
            kwargs={"spider_name": "mhdtvsports"},
        )

    # Setup tamilultra scraper
    if settings.disable_tamilultra_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.tamilultra_scheduler_crontab),
            name="tamilultra",
            kwargs={"spider_name": "tamilultra"},
        )

    # Schedule validate_tv_streams_in_db
    if settings.disable_validate_tv_streams_in_db:
        scheduler.add_job(
            validate_tv_streams_in_db.send,
            CronTrigger.from_crontab(settings.validate_tv_streams_in_db_crontab),
            name="validate_tv_streams_in_db",
        )

    # Schedule sport_video scraper
    if settings.disable_sport_video_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.sport_video_scheduler_crontab),
            name="sport_video",
            kwargs={"spider_name": "sport_video", "scrape_all": "false"},
        )

    # Schedule streamed scraper
    if settings.disable_streamed_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.streamed_scheduler_crontab),
            name="streamed",
            kwargs={"spider_name": "streamed"},
        )

    # Schedule mrgamingstreams scraper
    if settings.disable_mrgamingstreams_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.mrgamingstreams_scheduler_crontab),
            name="mrgamingstreams",
            kwargs={"spider_name": "mrgamingstreams"},
        )

    # Schedule crictime scraper
    if settings.disable_crictime_scheduler:
        scheduler.add_job(
            run_spider.send,
            CronTrigger.from_crontab(settings.crictime_scheduler_crontab),
            name="crictime",
            kwargs={"spider_name": "crictime"},
        )
