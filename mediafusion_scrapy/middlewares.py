# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html
import asyncio
import random
from urllib.parse import urlparse

import httpx
from scrapy import Request, signals
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.utils.response import response_status_message
from twisted.internet import defer, reactor

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

    DEFAULT_DELAY = 30  # Default initial delay in seconds.
    MAX_DELAY = 180  # Max delay between retries.
    BACKOFF_FACTOR = 3  # Exponential backoff factor.

    def __init__(self, settings):
        super().__init__(settings)
        self.max_retry_times = settings.getint("RETRY_TIMES", 5)  # Get max retries from settings.

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
                spider.logger.error(f"Gave up retrying {request.url} after {retries} attempts.")
                return response

            # Calculate the delay with exponential backoff
            retry_after = response.headers.get("retry-after")
            try:
                retry_after = int(retry_after) + random.randint(1, 10)
            except (ValueError, TypeError):
                delay = min(self.MAX_DELAY, self.DEFAULT_DELAY * (self.BACKOFF_FACTOR**retries))
            else:
                delay = min(self.MAX_DELAY, retry_after + random.randint(5, 120))  # Add some jitter

            spider.logger.info(f"Retrying {request.url} in {delay} seconds (retry {retries}/{self.max_retry_times}).")

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


class FlaresolverrMiddleware:
    def __init__(self, flaresolverr_url, cache_duration, max_timeout, max_attempts):
        self.flaresolverr_url = flaresolverr_url
        self.cache_duration = cache_duration
        self.max_timeout = max_timeout
        self.max_attempts = max_attempts
        self.solved_domains = {}
        self.client = httpx.AsyncClient()

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            flaresolverr_url=crawler.settings.get("FLARESOLVERR_URL", "http://localhost:8191/v1"),
            cache_duration=crawler.settings.get("FLARESOLVERR_CACHE_DURATION", 3600),
            max_timeout=crawler.settings.get("FLARESOLVERR_MAX_TIMEOUT", 60000),
            max_attempts=crawler.settings.get("FLARESOLVERR_MAX_ATTEMPTS", 3),
        )

    async def process_request(self, request, spider):
        return None

    async def process_response(self, request, response, spider):
        if not hasattr(spider, "use_flaresolverr") or not spider.use_flaresolverr:
            return response

        if response.status == 403 or (response.status == 503 and "cloudflare" in response.text.lower()):
            return await self._handle_cloudflare(request, spider)

        return response

    async def _handle_cloudflare(self, request, spider):
        domain = urlparse(request.url).netloc
        current_time = reactor.seconds()

        if domain in self.solved_domains:
            last_solved_time, solution = self.solved_domains[domain]
            if current_time - last_solved_time < self.cache_duration:
                return self._apply_solution(request, solution)

        for attempt in range(self.max_attempts):
            try:
                timeout = min(self.max_timeout, 30000 * (2**attempt))  # Exponential backoff
                flaresolverr_response = await self.client.post(
                    self.flaresolverr_url,
                    headers={"Content-Type": "application/json"},
                    json={
                        "cmd": "request.get",
                        "url": request.url,
                        "maxTimeout": timeout,
                    },
                    timeout=timeout / 1000 + 5,
                )

                if flaresolverr_response.status_code == 200:
                    solution = flaresolverr_response.json()
                    if solution.get("status") == "ok":
                        self.solved_domains[domain] = (current_time, solution)
                        return self._apply_solution(request, solution)

                spider.logger.error(f"FlareSolverr attempt {attempt + 1} failed: {flaresolverr_response.text}")
                await asyncio.sleep(2**attempt)  # Wait before next attempt

            except httpx.RequestError as e:
                spider.logger.error(f"FlareSolverr request error on attempt {attempt + 1}: {e}")
                await asyncio.sleep(2**attempt)

        spider.logger.error(
            f"Failed to solve Cloudflare challenge for {request.url} after {self.max_attempts} attempts"
        )
        raise IgnoreRequest()

    def _apply_solution(self, original_request, solution):
        solution_response = solution.get("solution", {}).get("response", {})
        return Request(
            url=original_request.url,
            headers=solution_response.get("headers", {}),
            cookies={cookie["name"]: cookie["value"] for cookie in solution_response.get("cookies", [])},
            dont_filter=True,
            meta={"flaresolverr_solved": True, **original_request.meta},
        )

    async def close_spider(self, spider):
        await self.client.aclose()
