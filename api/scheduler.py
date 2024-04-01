from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db import crud
from db.config import settings
from scrapers import tamil_blasters, tamilmv
from mediafusion_scrapy.task import run_spider
from utils.validation_helper import validate_tv_streams_in_db


def setup_scheduler(scheduler: AsyncIOScheduler):
    """
    Set up the scheduler with the required jobs.
    """

    # Setup tamil blasters scraper
    scheduler.add_job(
        tamil_blasters.run_tamil_blasters_scraper.send,
        CronTrigger.from_crontab(settings.tamil_blasters_scheduler_crontab),
        name="tamil_blasters",
    )

    # Setup tamilmv scraper
    scheduler.add_job(
        tamilmv.run_tamilmv_scraper.send,
        CronTrigger.from_crontab(settings.tamilmv_scheduler_crontab),
        name="tamilmv",
    )

    # Setup formula_tgx scraper
    scheduler.add_job(
        run_spider.send,
        CronTrigger.from_crontab(settings.formula_tgx_scheduler_crontab),
        name="formula_tgx",
        kwargs={"spider_name": "formula_tgx", "scrape_all": "false"},
    )

    # Setup mhdtvworld scraper
    scheduler.add_job(
        run_spider.send,
        CronTrigger.from_crontab(settings.mhdtvworld_scheduler_crontab),
        name="mhdtvworld",
        kwargs={"spider_name": "mhdtvworld"},
    )

    # Setup mhdtvsports scraper
    scheduler.add_job(
        run_spider.send,
        CronTrigger.from_crontab(settings.mhdtvsports_scheduler_crontab),
        name="mhdtvsports",
        kwargs={"spider_name": "mhdtvsports"},
    )

    # Setup tamilultra scraper
    scheduler.add_job(
        run_spider.send,
        CronTrigger.from_crontab(settings.tamilultra_scheduler_crontab),
        name="tamilultra",
        kwargs={"spider_name": "tamilultra"},
    )

    # Schedule validate_tv_streams_in_db
    scheduler.add_job(
        validate_tv_streams_in_db.send,
        CronTrigger.from_crontab(settings.validate_tv_streams_in_db_crontab),
        name="validate_tv_streams_in_db",
    )

    # Schedule sport_video scraper
    scheduler.add_job(
        run_spider.send,
        CronTrigger.from_crontab(settings.sport_video_scheduler_crontab),
        name="sport_video",
        kwargs={"spider_name": "sport_video", "scrape_all": "false"},
    )

    # Schedule streamed scraper
    scheduler.add_job(
        run_spider.send,
        CronTrigger.from_crontab(settings.streamed_scheduler_crontab),
        name="streamed",
        kwargs={"spider_name": "streamed"},
    )

    # Schedule delete_search_history
    scheduler.add_job(
        crud.delete_search_history, CronTrigger(day="*/1"), name="delete_search_history"
    )

    scheduler.add_job(
        run_spider.send,
        CronTrigger.from_crontab(settings.mrgamingstreams_scheduler_crontab),
        name="mrgamingstreams",
        kwargs={"spider_name": "mrgamingstreams"},
    )
