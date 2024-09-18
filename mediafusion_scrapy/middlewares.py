# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html
import random

# useful for handling different item types with a single interface

from scrapy import signals
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.utils.response import response_status_message
from twisted.internet import reactor, defer

from db import database


class MediafusionScrapySpiderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the spider middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response, spider):
        # Called for each response that goes through the spider
        # middleware and into the spider.

        # Should return None or raise an exception.
        return None

    def process_spider_output(self, response, result, spider):
        # Called with the results returned from the Spider, after
        # it has processed the response.

        # Must return an iterable of Request, or item objects.
        for i in result:
            yield i

    def process_spider_exception(self, response, exception, spider):
        # Called when a spider or process_spider_input() method
        # (from other spider middleware) raises an exception.

        # Should return either None or an iterable of Request or item objects.
        pass

    def process_start_requests(self, start_requests, spider):
        # Called with the start requests of the spider, and works
        # similarly to the process_spider_output() method, except
        # that it doesn’t have a response associated.

        # Must return only requests (not items).
        for r in start_requests:
            yield r

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)


class MediafusionScrapyDownloaderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the downloader middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        # Called for each request that goes through the downloader
        # middleware.

        # Must either:
        # - return None: continue processing this request
        # - or return a Response object
        # - or return a Request object
        # - or raise IgnoreRequest: process_exception() methods of
        #   installed downloader middleware will be called
        return None

    def process_response(self, request, response, spider):
        # Called with the response returned from the downloader.

        # Must either;
        # - return a Response object
        # - return a Request object
        # - or raise IgnoreRequest
        return response

    def process_exception(self, request, exception, spider):
        # Called when a download handler or a process_request()
        # (from other downloader middleware) raises an exception.

        # Must either:
        # - return None: continue processing this exception
        # - return a Response object: stops process_exception() chain
        # - return a Request object: stops process_exception() chain
        pass

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)


class DatabaseInitializationMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your middleware instance
        middleware = cls()
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        return middleware

    async def spider_opened(self, spider):
        # Initialize your database here
        await database.init()
        spider.logger.info("Database initialized successfully.")


class TooManyRequestsRetryMiddleware(RetryMiddleware):
    """
    Middleware to handle 429 Too Many Requests with a backoff retry mechanism.
    """

    DEFAULT_DELAY = 6  # Default initial delay in seconds.
    MAX_DELAY = 300  # Max delay between retries.
    BACKOFF_FACTOR = 2  # Exponential backoff factor.

    def __init__(self, settings):
        super().__init__(settings)
        self.max_retry_times = settings.getint(
            "RETRY_TIMES", 5
        )  # Get max retries from settings.

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def process_response(self, request, response, spider):
        """
        Handle response with 429 status by retrying the request with an exponential backoff delay.
        """
        if request.meta.get("dont_retry", False):
            return response

        # If we get a 429 Too Many Requests response
        if response.status == 429:
            retries = request.meta.get("retry_times", 0) + 1

            # If max retries exceeded, give up
            if retries > self.max_retry_times:
                spider.logger.error(
                    f"Gave up retrying {request.url} after {retries} attempts."
                )
                return response

            # Calculate the delay with exponential backoff
            retry_after = response.headers.get("retry-after")
            try:
                retry_after = int(retry_after)
            except (ValueError, TypeError):
                delay = min(
                    self.MAX_DELAY, self.DEFAULT_DELAY * (self.BACKOFF_FACTOR**retries)
                )
            else:
                delay = min(
                    self.MAX_DELAY, retry_after + random.randint(0, 10)
                )  # Add some jitter

            spider.logger.info(
                f"Retrying {request.url} in {delay} seconds (retry {retries}/{self.max_retry_times})."
            )

            # Update retry meta information
            request.meta["retry_times"] = retries

            # Defer the retry with the calculated delay
            deferred = defer.Deferred()
            reactor.callLater(delay, deferred.callback, None)

            def retry_request(_):
                reason = response_status_message(response.status)
                return self._retry(request, reason, spider) or response

            deferred.addCallback(retry_request)
            return deferred

        return response
