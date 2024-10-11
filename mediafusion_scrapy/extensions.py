from datetime import datetime, timedelta

from scrapy import signals
from scrapy.exceptions import NotConfigured, CloseSpider
from scrapy.utils.log import logger
from twisted.internet import task


class InactivityMonitor:
    """Monitor spider for inactivity and close if inactive for too long"""

    def __init__(self, crawler, interval=60.0, inactivity_timeout=15):
        self.crawler = crawler
        self.stats = crawler.stats
        self.interval = interval
        self.inactivity_timeout = inactivity_timeout
        self.task = None
        self.last_scraped_time = datetime.now()

    @classmethod
    def from_crawler(cls, crawler):
        interval = crawler.settings.getfloat("INACTIVITY_CHECK_INTERVAL", 60.0)
        inactivity_timeout = crawler.settings.getint("INACTIVITY_TIMEOUT_MINUTES", 3)

        if not interval or not inactivity_timeout:
            raise NotConfigured

        ext = cls(crawler, interval, inactivity_timeout)

        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        crawler.signals.connect(ext.item_scraped, signal=signals.item_scraped)

        return ext

    def spider_opened(self, spider):
        self.last_scraped_time = datetime.now()
        self.task = task.LoopingCall(self.check_inactivity, spider)
        self.task.start(self.interval)

    def check_inactivity(self, spider):
        time_since_last_scrape = datetime.now() - self.last_scraped_time
        logger.debug(
            "Checking inactivity for spider %s, last scraped %s ago",
            spider.name,
            time_since_last_scrape,
        )
        if time_since_last_scrape > timedelta(minutes=self.inactivity_timeout):
            msg = f"No items scraped in the last {self.inactivity_timeout} minutes. Closing spider."
            logger.info(msg, extra={"spider": spider})
            self.crawler.engine.close_spider(
                spider, reason=f"Inactivity timeout: {msg}"
            )
            raise CloseSpider(f"Inactivity timeout: {msg}")

    def item_scraped(self, item, response, spider):
        self.last_scraped_time = datetime.now()

    def spider_closed(self, spider, reason):
        if self.task and self.task.running:
            self.task.stop()
