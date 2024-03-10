import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from db.config import settings
from scrapers import tamil_blasters, tamilmv

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def run_formula_tgx_spider(spider_name: str):
    """
    Function to start the formula_tgx spider.
    """
    process = CrawlerProcess(get_project_settings())
    process.crawl(spider_name)
    process.start()


scheduler = AsyncIOScheduler()

# Setup tamil blasters scraper
scheduler.add_job(
    tamil_blasters.run_schedule_scrape,
    CronTrigger.from_crontab(settings.tamil_blasters_scheduler_crontab),
    name="tamil_blasters",
)

# Setup tamilmv scraper
scheduler.add_job(
    tamilmv.run_schedule_scrape,
    CronTrigger.from_crontab(settings.tamilmv_scheduler_crontab),
    name="tamilmv",
)

# Setup formula_tgx scraper
scheduler.add_job(
    run_formula_tgx_spider,
    CronTrigger.from_crontab(settings.formula_tgx_scheduler_crontab),
    name="formula_tgx",
    kwargs={"spider_name": "formula_tgx"},
)

# Setup mhdtvworld scraper
scheduler.add_job(
    run_formula_tgx_spider,
    CronTrigger.from_crontab(settings.mhdtvworld_scheduler_crontab),
    name="mhdtvworld",
    kwargs={"spider_name": "mhdtvworld"},
)

# Setup mhdtvsports scraper
scheduler.add_job(
    run_formula_tgx_spider,
    CronTrigger.from_crontab(settings.mhdtvsports_scheduler_crontab),
    name="mhdtvsports",
    kwargs={"spider_name": "mhdtvsports"},
)

# Start the scheduler
scheduler.start()

try:
    asyncio.get_event_loop().run_forever()
except Exception as e:
    logging.error(f"Error occurred: {e}")
