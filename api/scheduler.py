import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scrapers import tamil_blasters, tamilmv

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

scheduler = AsyncIOScheduler()

# Setup tamil blasters scraper
scheduler.add_job(
    tamil_blasters.run_schedule_scrape,
    CronTrigger(hour="*/6"),
    name="tamil_blasters",
)

# Setup tamilmv scraper
scheduler.add_job(tamilmv.run_schedule_scrape, CronTrigger(hour="*/3"), name="tamilmv")

# Start the scheduler
scheduler.start()

try:
    asyncio.get_event_loop().run_forever()
except Exception as e:
    logging.error(f"Error occurred: {e}")
