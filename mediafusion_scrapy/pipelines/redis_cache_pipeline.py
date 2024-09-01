from scrapy import signals
from scrapy.exceptions import DropItem

from utils.runtime_const import REDIS_ASYNC_CLIENT


class RedisCacheURLPipeline:
    def __init__(self):
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await self.redis.aclose()

    @classmethod
    def from_crawler(cls, crawler):
        p = cls()
        crawler.signals.connect(p.close, signal=signals.spider_closed)
        return p

    async def process_item(self, item, spider):
        if "webpage_url" not in item:
            raise DropItem(f"webpage_url not found in item: {item}")

        await self.redis.sadd(item["scraped_url_key"], item["webpage_url"])
        return item
