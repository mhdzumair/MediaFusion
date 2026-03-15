# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html
import asyncio
import random
from urllib.parse import urlparse

from scrapy import signals
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.http import TextResponse
from scrapy.utils.response import response_status_message
from twisted.internet import defer, reactor

from db import database
from mediafusion_scrapy.scrapling_adapter import solve_protected_page


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
        scheme = (urlparse(request.url).scheme or "").lower()
        if scheme not in {"http", "https"}:
            spider.logger.warning(
                "Skipping request with unsupported URL scheme '%s': %s", scheme or "unknown", request.url
            )
            raise IgnoreRequest(f"Unsupported URL scheme: {scheme or 'unknown'}")
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

    def __init__(self, settings, crawler):
        super().__init__(settings)
        self.crawler = crawler
        self.max_retry_times = settings.getint("RETRY_TIMES", 5)  # Get max retries from settings.

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings, crawler)

    def process_response(self, request, response):
        """
        Handle response with 429 status by retrying the request with an exponential backoff delay.
        """
        spider = self.crawler.spider
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
                return self._retry(request, reason) or response

            deferred.addCallback(retry_request)
            return deferred

        return response


class ScraplingAntiBotMiddleware:
    def __init__(self, cache_duration, max_timeout, max_attempts, crawler, scraper_options):
        self.cache_duration = cache_duration
        self.max_timeout = max_timeout
        self.max_attempts = max_attempts
        self.crawler = crawler
        self.scraper_options = scraper_options
        self.solved_domains = {}

    @classmethod
    def from_crawler(cls, crawler):
        scraper_options = {
            "headless": crawler.settings.get("SCRAPLING_HEADLESS", True),
            "disable_resources": crawler.settings.get("SCRAPLING_DISABLE_RESOURCES", False),
            "network_idle": crawler.settings.get("SCRAPLING_NETWORK_IDLE", True),
            "wait_time_ms": crawler.settings.get("SCRAPLING_WAIT_TIME_MS", 0),
            "google_search_referer": crawler.settings.get("SCRAPLING_GOOGLE_SEARCH_REFERER", False),
            "proxy_url": crawler.settings.get("SCRAPLING_PROXY_URL"),
            "cdp_url": crawler.settings.get("SCRAPLING_CDP_URL"),
            "fetcher_mode": crawler.settings.get("SCRAPLING_FETCHER_MODE", "stealthy"),
            "solve_cloudflare": crawler.settings.get("SCRAPLING_SOLVE_CLOUDFLARE", False),
            "real_chrome": crawler.settings.get("SCRAPLING_REAL_CHROME", False),
        }
        return cls(
            cache_duration=crawler.settings.get("SCRAPLING_CLOUDFLARE_CACHE_DURATION", 3600),
            max_timeout=crawler.settings.get("SCRAPLING_MAX_TIMEOUT", 60000),
            max_attempts=crawler.settings.get("SCRAPLING_CLOUDFLARE_MAX_ATTEMPTS", 3),
            crawler=crawler,
            scraper_options=scraper_options,
        )

    @staticmethod
    def _is_solver_enabled(spider):
        return getattr(spider, "use_anti_bot_solver", False)

    def _apply_cached_solution(self, request, solution):
        cookies = solution.get("cookies", {})
        if cookies:
            request.cookies.update(cookies)
        user_agent = solution.get("user_agent")
        if user_agent:
            request.headers[b"User-Agent"] = user_agent.encode()

    async def process_request(self, request):
        spider = self.crawler.spider
        if not self._is_solver_enabled(spider):
            return None

        domain = urlparse(request.url).netloc
        current_time = reactor.seconds()
        cached = self.solved_domains.get(domain)
        if not cached:
            return None

        last_solved_time, solution = cached
        if current_time - last_solved_time < self.cache_duration:
            spider.logger.debug("Applying cached anti-bot cookies for %s", domain)
            self._apply_cached_solution(request, solution)
        return None

    async def process_response(self, request, response):
        spider = self.crawler.spider
        if not self._is_solver_enabled(spider):
            return response

        if request.meta.get("anti_bot_solved"):
            return response

        response_text = getattr(response, "text", "") or ""
        if response.status == 403 or (response.status == 503 and "cloudflare" in response_text.lower()):
            return await self._handle_cloudflare(request)
        return response

    async def _handle_cloudflare(self, request):
        spider = self.crawler.spider
        domain = urlparse(request.url).netloc
        current_time = reactor.seconds()

        cached = self.solved_domains.get(domain)
        if cached:
            last_solved_time, solution = cached
            if current_time - last_solved_time < self.cache_duration:
                return self._build_solved_response(request, solution)

        for attempt in range(self.max_attempts):
            timeout_ms = min(self.max_timeout, 30000 * (2**attempt))
            try:
                solution = await solve_protected_page(
                    request.url,
                    timeout_ms=timeout_ms,
                    **self.scraper_options,
                )
            except Exception as exc:
                spider.logger.error("Scrapling anti-bot attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(2**attempt)
                continue

            if solution.get("status", 0) >= 400 or not solution.get("html"):
                spider.logger.error(
                    "Scrapling anti-bot attempt %d returned status=%s for %s",
                    attempt + 1,
                    solution.get("status"),
                    request.url,
                )
                await asyncio.sleep(2**attempt)
                continue

            self.solved_domains[domain] = (current_time, solution)
            return self._build_solved_response(request, solution)

        spider.logger.error(
            "Failed to solve Cloudflare challenge for %s after %d attempts", request.url, self.max_attempts
        )
        raise IgnoreRequest()

    def _build_solved_response(self, original_request, solution):
        html_body = solution.get("html", "")
        solved_url = solution.get("url", original_request.url)
        cookies = solution.get("cookies", {})
        user_agent = solution.get("user_agent", "")

        headers = {}
        if user_agent:
            headers["User-Agent"] = user_agent
        if cookies:
            headers["Set-Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

        original_request.meta["anti_bot_solved"] = True
        return TextResponse(
            url=solved_url,
            body=html_body,
            encoding="utf-8",
            headers=headers,
            request=original_request,
        )
