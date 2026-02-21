import gc
import logging
import os
from multiprocessing import Process

import dramatiq
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPIDER_PROCESS_TIMEOUT_SECONDS = int(os.getenv("SCRAPY_PROCESS_TIMEOUT_SECONDS", "3300"))
_SPIDER_TERMINATE_GRACE_SECONDS = int(os.getenv("SCRAPY_PROCESS_TERMINATE_GRACE_SECONDS", "30"))


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
    logger.info(
        "Starting spider %s in subprocess (timeout=%ss)",
        spider_name,
        _SPIDER_PROCESS_TIMEOUT_SECONDS,
    )
    p = None
    try:
        p = Process(target=run_spider_in_process, args=(spider_name, *args), kwargs=kwargs)
        p.start()

        p.join(timeout=_SPIDER_PROCESS_TIMEOUT_SECONDS)
        if p.is_alive():
            logger.error(
                "Spider %s exceeded timeout (%ss). Terminating subprocess pid=%s.",
                spider_name,
                _SPIDER_PROCESS_TIMEOUT_SECONDS,
                p.pid,
            )
            p.terminate()
            p.join(timeout=_SPIDER_TERMINATE_GRACE_SECONDS)
            if p.is_alive():
                logger.error(
                    "Spider %s did not terminate gracefully. Killing subprocess pid=%s.",
                    spider_name,
                    p.pid,
                )
                p.kill()
                p.join(timeout=5)
            raise RuntimeError(f"Spider '{spider_name}' timed out after {_SPIDER_PROCESS_TIMEOUT_SECONDS} seconds.")

        if p.exitcode != 0:
            logger.error(
                "Spider %s exited with code %s",
                spider_name,
                p.exitcode,
            )
            raise RuntimeError(f"Spider '{spider_name}' exited with code {p.exitcode}.")

        logger.info("Spider %s finished successfully", spider_name)
    finally:
        if p is not None and p.exitcode is not None:
            p.close()
        gc.collect()


if __name__ == "__main__":
    run_spider_in_process("movies_tv_ext", scrape_all="true", total_pages=5)
