import logging

from scrapy.extensions.closespider import CloseSpider

logger = logging.getLogger(__name__)


class CloseSpiderExtended(CloseSpider):
    def __init__(self, crawler):
        super().__init__(crawler)
        self.shutdown_signal_count = 0

    def _count_items_produced(self) -> None:
        if self.items_in_period >= 1:
            self.items_in_period = 0
        else:
            logger.info(f"Closing spider since no items were produced in the last {self.timeout_no_item} seconds.")
            self.shutdown_signal_count += 1
            assert self.crawler.engine
            spider = self.crawler.engine.spider
            self.crawler.engine.close_spider(spider, "closespider_timeout_no_item")

        if self.shutdown_signal_count >= 3:
            logger.info(
                f"Closing spider since no items were produced in the last "
                f"{self.timeout_no_item} seconds for 3 consecutive times. Forcefully closing the spider."
            )
            raise SystemExit(0)
