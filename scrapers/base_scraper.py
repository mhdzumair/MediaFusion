import abc
import logging
from typing import List, Any, Dict
from datetime import timedelta
from functools import wraps

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from ratelimit import limits, sleep_and_retry

from db.models import TorrentStreams
from utils.runtime_const import REDIS_ASYNC_CLIENT


class ScraperError(Exception):
    pass


class BaseScraper(abc.ABC):
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.http_client = httpx.AsyncClient(timeout=30)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.http_client.aclose()

    @abc.abstractmethod
    async def scrape_and_parse(self, *args, **kwargs) -> List[TorrentStreams]:
        """
        Scrape data and parse it into TorrentStreams objects.
        This method should be implemented by each specific scraper.
        """
        pass

    @classmethod
    async def get_streams(cls, *args, **kwargs) -> List[TorrentStreams]:
        """
        Common method to get streams for all scrapers.
        This method calls the scrape_and_parse method and returns the result.
        """
        async with cls() as instance:
            return await instance.scrape_and_parse(*args, **kwargs)

    @staticmethod
    def cache(ttl: int = 3600):
        """
        Decorator for caching the results of a method.
        :param ttl: Time to live for the cache in seconds
        """

        def decorator(func):
            @wraps(func)
            async def wrapper(self, *args, **kwargs):
                cache_key = self.get_cache_key(*args, **kwargs)
                cached_result = await REDIS_ASYNC_CLIENT.get(cache_key)
                if cached_result:
                    return []

                result = await func(self, *args, **kwargs)
                await REDIS_ASYNC_CLIENT.set(cache_key, "True", ex=ttl)
                return result

            return wrapper

        return decorator

    @staticmethod
    def rate_limit(calls: int, period: timedelta):
        """
        Decorator for rate limiting method calls.
        :param calls: Number of calls allowed in the period
        :param period: Time period for the rate limit
        """

        def decorator(func):
            @sleep_and_retry
            @limits(calls=calls, period=period.total_seconds())
            @wraps(func)
            async def wrapper(self, *args, **kwargs):
                return await func(self, *args, **kwargs)

            return wrapper

        return decorator

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def make_request(
        self, url: str, method: str = "GET", **kwargs
    ) -> httpx.Response:
        """
        Make an HTTP request with retry logic.
        :param url: URL to request
        :param method: HTTP method (GET, POST, etc.)
        :param kwargs: Additional arguments to pass to the request
        :return: Response object
        :raises ScraperError: If an error occurs while making the request
        """
        try:
            response = await self.http_client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP error occurred: {e}")
            raise ScraperError(f"HTTP error occurred: {e}")
        except httpx.RequestError as e:
            self.logger.error(f"An error occurred while requesting {e.request.url!r}.")
            raise ScraperError(f"An error occurred while requesting {e.request.url!r}.")

    @abc.abstractmethod
    def validate_response(self, response: Dict[str, Any]) -> bool:
        """
        Validate the response from the scraper.
        :param response: Response dictionary
        :return: True if valid, False otherwise
        """
        pass

    @abc.abstractmethod
    def parse_response(
        self,
        response: Dict[str, Any],
        video_id: str,
        title: str,
        aka_titles: list[str],
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        """
        Parse the response into TorrentStreams objects.
        :param response: Response dictionary
        :param video_id: Video ID
        :param title: Video title
        :param aka_titles: List of aka titles
        :param catalog_type: Catalog type (movie, series)
        :param season: Season number (for series)
        :param episode: Episode number (for series)
        :return: List of TorrentStreams objects
        """
        pass

    def get_cache_key(self, *args, **kwargs) -> str:
        """
        Generate a cache key for the given arguments.
        :return: Cache key string
        """
        return f"{self.__class__.__name__}:{args}:{kwargs}"
