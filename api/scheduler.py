import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from scrapers import tamil_blasters, tamilmv

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def run_formula_tgx_spider():
    """
    Function to start the formula_tgx spider.
    """
    process = CrawlerProcess(get_project_settings())
    process.crawl("formula_tgx")  # Use the spider name you have defined
    process.start()


scheduler = AsyncIOScheduler()

# Setup tamil blasters scraper
scheduler.add_job(
    tamil_blasters.run_schedule_scrape,
    CronTrigger(hour="*/6"),
    name="tamil_blasters",
)

# Setup tamilmv scraper
scheduler.add_job(tamilmv.run_schedule_scrape, CronTrigger(hour="*/3"), name="tamilmv")

# Setup formula_tgx scraper
scheduler.add_job(run_formula_tgx_spider, CronTrigger(hour="*/12"), name="formula_tgx")

# Start the scheduler
scheduler.start()

try:
    asyncio.get_event_loop().run_forever()
except Exception as e:
    logging.error(f"Error occurred: {e}")
