from dramatiq.middleware import CurrentMessage
from dramatiq_abort import abort
from scrapy import Spider
from scrapy.extensions.closespider import CloseSpider


class CloseSpiderExtended(CloseSpider):
    def __init__(self, crawler):
        super().__init__(crawler)
        self.shutdown_signal_count = 0

    def _count_items_produced(self, spider: Spider) -> None:
        if self.items_in_period >= 1:
            self.items_in_period = 0
        else:
            spider.logger.info(
                f"Closing spider since no items were produced in the last {self.timeout_no_item} seconds."
            )
            self.shutdown_signal_count += 1
            dramatiq_message = CurrentMessage.get_current_message()
            if dramatiq_message:
                spider.logger.info(f"Aborting message {dramatiq_message.message_id} due to no items produced.")
                abort(dramatiq_message.message_id, abort_ttl=0)
            assert self.crawler.engine
            self.crawler.engine.close_spider(spider, "closespider_timeout_no_item")

        if self.shutdown_signal_count >= 3:
            spider.logger.info(
                f"Closing spider since no items were produced in the last "
                f"{self.timeout_no_item} seconds for 3 consecutive times. Forcefully closing the spider."
            )
            raise SystemExit(0)
