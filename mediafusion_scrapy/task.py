import logging
import os
from multiprocessing import Process

import dramatiq
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_spider_in_process(spider_name, *args, **kwargs):
    """
    Function to start a scrapy spider in a new process.
    """
    os.chdir(_PROJECT_ROOT)
    os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "mediafusion_scrapy.settings")
    settings = get_project_settings()
    settings.set("LOG_LEVEL", "INFO")
    settings.set("LOG_STDOUT", True)

    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("scrapy.core.engine").setLevel(logging.INFO)
    logging.getLogger("scrapy.dupefilters").setLevel(logging.WARNING)

    process = CrawlerProcess(settings)
    process.crawl(spider_name, *args, **kwargs)
    process.start()


@dramatiq.actor(priority=5, time_limit=60 * 60 * 1000, queue_name="scrapy")
def run_spider(spider_name: str, *args, **kwargs):
    """
    Wrapper function to run the spider in a separate process.

    Uses multiprocessing.Process because Scrapy's Twisted reactor cannot be
    restarted within the same process.  We pipe the child's stdout/stderr
    back to the current process so Dramatiq captures the logs.
    """
    logger.info("Starting spider %s in subprocess", spider_name)
    p = Process(target=run_spider_in_process, args=(spider_name, *args), kwargs=kwargs)
    p.start()
    p.join()
    if p.exitcode != 0:
        logger.error("Spider %s exited with code %s", spider_name, p.exitcode)
    else:
        logger.info("Spider %s finished successfully", spider_name)


if __name__ == "__main__":
    run_spider_in_process("movies_tv_ext", scrape_all="true", total_pages=5)
