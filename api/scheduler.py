import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db.config import settings
from mediafusion_scrapy.task import run_spider
from scrapers.background_scraper import run_background_search
from scrapers.dmm_hashlist import run_dmm_hashlist_scraper
from scrapers.feed_scraper import run_jackett_feed_scraper, run_prowlarr_feed_scraper
from scrapers.non_torrent_background_scraper import (
    run_acestream_background_scraper,
    run_telegram_background_scraper,
    run_youtube_background_scraper,
)
from scrapers.rss_scraper import run_rss_feed_scraper
from scrapers.scraper_tasks import cleanup_expired_scraper_task
from scrapers.trackers import update_torrent_seeders
from scrapers.tv import validate_tv_streams_in_db
from api.services.sync.tasks import run_all_integration_syncs
from streaming_providers.cache_helpers import cleanup_expired_cache
from utils.telegram_bot import telegram_notifier

logger = logging.getLogger(__name__)


async def async_send(actor_send_method, **kwargs):
    """
    Wrapper to enqueue background jobs with retry.
    """
    max_attempts = 3
    delay_seconds = 1
    actor_name = getattr(actor_send_method, "__qualname__", repr(actor_send_method))

    for attempt in range(1, max_attempts + 1):
        try:
            await actor_send_method(**kwargs)
            return
        except Exception as exc:
            if attempt >= max_attempts:
                logger.warning(
                    "Failed to enqueue scheduler actor %s after %d attempts: %s",
                    actor_name,
                    max_attempts,
                    exc,
                )
                return
            await asyncio.sleep(delay_seconds)
            delay_seconds *= 2


def setup_scheduler(scheduler: AsyncIOScheduler):
    """
    Set up the scheduler with the required jobs.
    All jobs use async_send wrapper with retry for Redis transient failures.
    """
    # Setup tamil blasters scraper
    if not settings.disable_tamil_blasters_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.tamil_blasters_scheduler_crontab),
            name="tamil_blasters",
            kwargs={
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": validate_tv_streams_in_db.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": run_spider.async_send,
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
                "actor_send_method": update_torrent_seeders.async_send,
                "crontab_expression": settings.update_seeders_crontab,
            },
        )

    if not settings.disable_arab_torrents_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.arab_torrents_scheduler_crontab),
            name="arab_torrents",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "arab_torrents",
                "crontab_expression": settings.arab_torrents_scheduler_crontab,
            },
        )

    if not settings.disable_x1337_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.x1337_scheduler_crontab),
            name="x1337",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "x1337",
                "scrape_all": "false",
                "crontab_expression": settings.x1337_scheduler_crontab,
            },
        )

    if not settings.disable_thepiratebay_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.thepiratebay_scheduler_crontab),
            name="thepiratebay",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "thepiratebay",
                "scrape_all": "false",
                "crontab_expression": settings.thepiratebay_scheduler_crontab,
            },
        )

    if not settings.disable_rutor_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.rutor_scheduler_crontab),
            name="rutor",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "rutor",
                "scrape_all": "false",
                "crontab_expression": settings.rutor_scheduler_crontab,
            },
        )

    if not settings.disable_limetorrents_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.limetorrents_scheduler_crontab),
            name="limetorrents",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "limetorrents",
                "scrape_all": "false",
                "crontab_expression": settings.limetorrents_scheduler_crontab,
            },
        )

    if not settings.disable_yts_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.yts_scheduler_crontab),
            name="yts",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "yts",
                "scrape_all": "false",
                "crontab_expression": settings.yts_scheduler_crontab,
            },
        )

    if not settings.disable_bt4g_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.bt4g_scheduler_crontab),
            name="bt4g",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "bt4g",
                "scrape_all": "false",
                "crontab_expression": settings.bt4g_scheduler_crontab,
            },
        )

    if not settings.disable_nyaa_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.nyaa_scheduler_crontab),
            name="nyaa",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "nyaa",
                "scrape_all": "false",
                "crontab_expression": settings.nyaa_scheduler_crontab,
            },
        )

    if not settings.disable_animetosho_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.animetosho_scheduler_crontab),
            name="animetosho",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "animetosho",
                "scrape_all": "false",
                "crontab_expression": settings.animetosho_scheduler_crontab,
            },
        )

    if not settings.disable_subsplease_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.subsplease_scheduler_crontab),
            name="subsplease",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "subsplease",
                "scrape_all": "false",
                "crontab_expression": settings.subsplease_scheduler_crontab,
            },
        )

    if not settings.disable_animepahe_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.animepahe_scheduler_crontab),
            name="animepahe",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "animepahe",
                "scrape_all": "false",
                "crontab_expression": settings.animepahe_scheduler_crontab,
            },
        )

    if not settings.disable_bt52_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.bt52_scheduler_crontab),
            name="bt52",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "bt52",
                "scrape_all": "false",
                "crontab_expression": settings.bt52_scheduler_crontab,
            },
        )

    if not settings.disable_uindex_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.uindex_scheduler_crontab),
            name="uindex",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "uindex",
                "scrape_all": "false",
                "crontab_expression": settings.uindex_scheduler_crontab,
            },
        )

    if not settings.disable_eztv_rss_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.eztv_rss_scheduler_crontab),
            name="eztv_rss",
            kwargs={
                "actor_send_method": run_spider.async_send,
                "spider_name": "eztv_rss",
                "crontab_expression": settings.eztv_rss_scheduler_crontab,
            },
        )

    # Schedule the feed scraper
    if not settings.disable_prowlarr_feed_scraper:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.prowlarr_feed_scraper_crontab),
            name="prowlarr_feed_scraper",
            kwargs={
                "actor_send_method": run_prowlarr_feed_scraper.async_send,
                "crontab_expression": settings.prowlarr_feed_scraper_crontab,
            },
        )

    if not settings.disable_jackett_feed_scraper:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.jackett_feed_scraper_crontab),
            name="jackett_feed_scraper",
            kwargs={
                "actor_send_method": run_jackett_feed_scraper.async_send,
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
                "actor_send_method": run_rss_feed_scraper.async_send,
                "crontab_expression": settings.rss_feed_scraper_crontab,
            },
        )

    # Schedule DMM hashlist scraper
    if not settings.disable_dmm_hashlist_scraper:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.dmm_hashlist_scraper_crontab),
            name="dmm_hashlist_scraper",
            kwargs={
                "actor_send_method": run_dmm_hashlist_scraper.async_send,
                "crontab_expression": settings.dmm_hashlist_scraper_crontab,
            },
        )

    if not settings.disable_youtube_background_scraper and settings.is_scrap_from_youtube_background:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.youtube_background_scraper_crontab),
            name="youtube_background_scraper",
            kwargs={
                "actor_send_method": run_youtube_background_scraper.async_send,
                "crontab_expression": settings.youtube_background_scraper_crontab,
            },
        )

    if not settings.disable_acestream_background_scraper and settings.is_scrap_from_acestream_background:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.acestream_background_scraper_crontab),
            name="acestream_background_scraper",
            kwargs={
                "actor_send_method": run_acestream_background_scraper.async_send,
                "crontab_expression": settings.acestream_background_scraper_crontab,
            },
        )

    if not settings.disable_telegram_background_scraper and settings.is_scrap_from_telegram_background:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.telegram_background_scraper_crontab),
            name="telegram_background_scraper",
            kwargs={
                "actor_send_method": run_telegram_background_scraper.async_send,
                "crontab_expression": settings.telegram_background_scraper_crontab,
            },
        )

    scheduler.add_job(
        async_send,
        CronTrigger.from_crontab(settings.cleanup_expired_scraper_task_crontab),
        name="cleanup_expired_scraper_task",
        kwargs={
            "actor_send_method": cleanup_expired_scraper_task.async_send,
            "crontab_expression": settings.cleanup_expired_scraper_task_crontab,
        },
    )

    scheduler.add_job(
        async_send,
        CronTrigger.from_crontab(settings.cleanup_expired_cache_task_crontab),
        name="cleanup_expired_cache_task",
        kwargs={
            "actor_send_method": cleanup_expired_cache.async_send,
            "crontab_expression": settings.cleanup_expired_cache_task_crontab,
        },
    )

    scheduler.add_job(
        async_send,
        CronTrigger.from_crontab(settings.background_search_crontab),
        name="background_search",
        kwargs={
            "actor_send_method": run_background_search.async_send,
            "crontab_expression": settings.background_search_crontab,
        },
    )

    if (
        not settings.disable_pending_moderation_reminder_scheduler
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        scheduler.add_job(
            telegram_notifier.send_pending_moderation_reminder,
            CronTrigger.from_crontab(settings.pending_moderation_reminder_crontab),
            name="pending_moderation_reminder",
        )

    if not settings.disable_integration_sync_scheduler:
        scheduler.add_job(
            async_send,
            CronTrigger.from_crontab(settings.integration_sync_crontab),
            name="integration_sync",
            kwargs={
                "actor_send_method": run_all_integration_syncs.async_send,
            },
        )
