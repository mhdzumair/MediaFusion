import gc
import json
import logging
import os
import sys
import time
from multiprocessing import get_context
from typing import Any

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from api.task_queue import (
    TaskCancelledError,
    actor,
    get_current_task_id,
    is_task_cancel_requested,
)
from db.redis_database import REDIS_SYNC_CLIENT

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPIDER_PROCESS_START_METHOD = os.getenv("SCRAPY_PROCESS_START_METHOD", "spawn").strip().lower()
SPIDER_LOOP_JOIN_POLL_SECONDS = 5.0
CHALLENGE_HEAVY_SPIDERS = {"tamilmv", "tamil_blasters"}

try:
    _PROCESS_CONTEXT = get_context(_SPIDER_PROCESS_START_METHOD)
except ValueError:
    logger.warning(
        "Invalid SCRAPY_PROCESS_START_METHOD=%s. Falling back to 'spawn'.",
        _SPIDER_PROCESS_START_METHOD,
    )
    _PROCESS_CONTEXT = get_context("spawn")


def run_spider_in_process(spider_name, *args, **kwargs):
    """
    Function to start a scrapy spider in a new process.
    """
    os.chdir(_PROJECT_ROOT)
    os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "mediafusion_scrapy.settings")
    # Force INFO at process entry to prevent inherited DEBUG handlers from
    # parent worker contexts leaking noisy logs into Scrapy runs.
    logging.getLogger().setLevel(logging.INFO)
    settings = get_project_settings()
    settings.set("LOG_LEVEL", "INFO")
    settings.set("LOG_STDOUT", True)
    if os.getenv("SCRAPY_TEST_LIGHTWEIGHT", "0") == "1":
        # Test mode: disable DB-dependent middleware and heavy pipelines.
        settings.set("SPIDER_MIDDLEWARES", {}, priority="cmdline")
        settings.set("ITEM_PIPELINES", {}, priority="cmdline")

    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("scrapy.core.engine").setLevel(logging.INFO)
    logging.getLogger("scrapy.dupefilters").setLevel(logging.WARNING)

    process = CrawlerProcess(settings)
    process.crawl(spider_name, *args, **kwargs)
    process.start()


def _ensure_project_import_path() -> None:
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_parts = [part for part in existing_pythonpath.split(os.pathsep) if part]
    if _PROJECT_ROOT not in pythonpath_parts:
        pythonpath_parts.insert(0, _PROJECT_ROOT)
        os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)


def _read_runtime_timeouts(spider_name: str | None = None) -> tuple[int, int, int]:
    timeout_seconds = int(os.getenv("SCRAPY_PROCESS_TIMEOUT_SECONDS", "3300"))
    if spider_name:
        timeout_env_suffix = spider_name.upper().replace("-", "_")
        per_spider_timeout = os.getenv(f"SCRAPY_PROCESS_TIMEOUT_SECONDS_{timeout_env_suffix}")
        if per_spider_timeout:
            timeout_seconds = int(per_spider_timeout)
        elif spider_name in CHALLENGE_HEAVY_SPIDERS:
            timeout_seconds = max(timeout_seconds, 5400)
    terminate_grace_seconds = int(os.getenv("SCRAPY_PROCESS_TERMINATE_GRACE_SECONDS", "30"))
    progress_log_interval_seconds = int(os.getenv("SCRAPY_PROCESS_PROGRESS_LOG_INTERVAL_SECONDS", "60"))
    return timeout_seconds, terminate_grace_seconds, progress_log_interval_seconds


def _read_spider_stats(spider_name: str) -> dict[str, Any]:
    raw_stats = REDIS_SYNC_CLIENT.get(f"scrapy_stats:{spider_name}")
    if not raw_stats:
        return {}
    try:
        return json.loads(raw_stats)
    except (TypeError, ValueError):
        return {}


@actor(priority=5, time_limit=60 * 60 * 1000, queue_name="scrapy")
def run_spider(spider_name: str, *args, **kwargs):
    """
    Wrapper function to run the spider in a separate process.

    Uses multiprocessing with a dedicated child process because Scrapy's
    Twisted reactor cannot be restarted within the same process. We pipe the
    child's stdout/stderr
    back to the current process so the worker captures the logs.
    """
    timeout_seconds, terminate_grace_seconds, progress_log_interval_seconds = _read_runtime_timeouts(spider_name)
    task_id = get_current_task_id()
    _ensure_project_import_path()
    logger.info(
        "Starting spider %s in subprocess (timeout=%ss, start_method=%s)",
        spider_name,
        timeout_seconds,
        _PROCESS_CONTEXT.get_start_method(),
    )
    p = None
    try:
        p = _PROCESS_CONTEXT.Process(target=run_spider_in_process, args=(spider_name, *args), kwargs=kwargs)
        p.start()

        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        next_progress_log_at = started_at + max(progress_log_interval_seconds, 15)

        while p.is_alive():
            if task_id and is_task_cancel_requested(task_id):
                logger.warning("Cancellation requested for task %s (spider=%s).", task_id, spider_name)
                p.terminate()
                p.join(timeout=terminate_grace_seconds)
                if p.is_alive():
                    p.kill()
                    p.join(timeout=5)
                raise TaskCancelledError(f"Spider '{spider_name}' cancelled by user request.")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            join_step = min(remaining, SPIDER_LOOP_JOIN_POLL_SECONDS)
            p.join(timeout=join_step)

            if p.is_alive() and time.monotonic() >= next_progress_log_at:
                elapsed = round(time.monotonic() - started_at, 2)
                logger.warning(
                    "Spider %s still running in subprocess pid=%s after %ss.",
                    spider_name,
                    p.pid,
                    elapsed,
                )
                next_progress_log_at = time.monotonic() + max(progress_log_interval_seconds, 15)

        if p.is_alive():
            logger.error(
                "Spider %s exceeded timeout (%ss). Terminating subprocess pid=%s.",
                spider_name,
                timeout_seconds,
                p.pid,
            )
            p.terminate()
            p.join(timeout=terminate_grace_seconds)
            if p.is_alive():
                logger.error(
                    "Spider %s did not terminate gracefully. Killing subprocess pid=%s.",
                    spider_name,
                    p.pid,
                )
                p.kill()
                p.join(timeout=5)
            raise RuntimeError(f"Spider '{spider_name}' timed out after {timeout_seconds} seconds.")

        if p.exitcode != 0:
            logger.error(
                "Spider %s exited with code %s",
                spider_name,
                p.exitcode,
            )
            raise RuntimeError(f"Spider '{spider_name}' exited with code {p.exitcode}.")

        spider_stats = _read_spider_stats(spider_name)
        if (
            spider_stats.get("item_scraped_count", 0) == 0
            and spider_stats.get("close_reason") == "closespider_timeout_no_item"
        ):
            raise RuntimeError(
                f"Spider '{spider_name}' cancelled due to no items for "
                f"{os.getenv('SCRAPY_CLOSESPIDER_TIMEOUT_NO_ITEM', '600')} seconds."
            )

        logger.info("Spider %s finished successfully", spider_name)
    finally:
        if p is not None and p.exitcode is not None:
            p.close()
        gc.collect()


if __name__ == "__main__":
    run_spider_in_process("movies_tv_ext", scrape_all="true", total_pages=5)
