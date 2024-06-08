import json
import logging

import redis
from scrapy import signals
from scrapy.statscollectors import StatsCollector

from db.config import settings


class RedisStatsCollector(StatsCollector):
    def __init__(self, crawler):
        super().__init__(crawler)
        crawler.signals.connect(self.spider_closed, signal=signals.spider_closed)
        self.redis_client = redis.Redis.from_url(settings.redis_url)

    def spider_closed(self, spider, reason):
        # Access the stats dictionary
        stats = self.get_stats()

        # Extract the required stats
        item_dropped_count = stats.get("item_dropped_count", 0)
        item_scraped_count = stats.get("item_scraped_count", 0)
        log_count_error = stats.get("log_count/ERROR", 0)
        log_count_info = stats.get("log_count/INFO", 0)
        log_count_warning = stats.get("log_count/WARNING", 0)

        # Prepare stats data
        stats_data = {
            "item_dropped_count": item_dropped_count,
            "item_scraped_count": item_scraped_count,
            "log_count_error": log_count_error,
            "log_count_info": log_count_info,
            "log_count_warning": log_count_warning,
        }

        # Save the stats to Redis
        self.save_stats(spider.name, stats_data)

    def save_stats(self, spider_name, stats):
        key = f"scrapy_stats:{spider_name}"
        self.redis_client.set(key, json.dumps(stats))
        logging.info(f"Stats saved to Redis for '{key}': {stats}")
