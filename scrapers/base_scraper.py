import abc
import logging
from datetime import timedelta
from functools import wraps
from typing import List, Any, Dict

import httpx
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential

from db.models import TorrentStreams, MediaFusionMetaData
from utils.parser import calculate_max_similarity_ratio
from utils.runtime_const import REDIS_ASYNC_CLIENT


class ScraperError(Exception):
    pass


class BaseScraper(abc.ABC):
    def __init__(self, cache_key_prefix: str, logger_name: str):
        self.logger = logging.getLogger(logger_name)
        self.http_client = httpx.AsyncClient(timeout=30)
        self.cache_key_prefix = cache_key_prefix

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

    def validate_response(self, response: Dict[str, Any]) -> bool:
        """
        Validate the response from the scraper.
        :param response: Response dictionary
        :return: True if valid, False otherwise
        """
        pass

    def parse_response(
        self,
        response: Dict[str, Any],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        """
        Parse the response into TorrentStreams objects.
        :param response: Response dictionary
        :param metadata: MediaFusionMetaData object
        :param catalog_type: Catalog type (movie, series)
        :param season: Season number (for series)
        :param episode: Episode number (for series)
        :return: List of TorrentStreams objects
        """
        pass

    def get_cache_key(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: str = None,
        episode: str = None,
        *_args,
        **_kwargs,
    ) -> str:
        """
        Generate a cache key for the given arguments.
        :return: Cache key string
        """
        if catalog_type == "movie":
            return f"{self.cache_key_prefix}:{catalog_type}:{metadata.id}"

        return (
            f"{self.cache_key_prefix}:{catalog_type}:{metadata.id}:{season}:{episode}"
        )

    def validate_title_and_year(
        self,
        parsed_data: dict,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        torrent_title: str,
        expected_ratio: int = 85,
    ) -> bool:
        """
        Validate the title and year of the parsed data against the metadata.
        :param parsed_data: Parsed data dictionary
        :param metadata: MediaFusionMetaData object
        :param catalog_type: Catalog type (movie, series)
        :param torrent_title: Torrent title
        :param expected_ratio: Expected similarity ratio

        :return: True if valid, False otherwise
        """
        # Check similarity ratios
        max_similarity_ratio = calculate_max_similarity_ratio(
            parsed_data["title"], metadata.title, metadata.aka_titles
        )

        # Log and return False if similarity ratios is below the expected threshold
        if max_similarity_ratio < expected_ratio:
            self.logger.debug(
                f"Title mismatch: '{parsed_data['title']}' vs. '{metadata.title}'. Torrent title: '{torrent_title}'"
            )
            return False

        # Validate year based on a catalog type
        if catalog_type == "movie":
            if parsed_data.get("year") != metadata.year:
                self.logger.debug(
                    f"Year mismatch for movie: {parsed_data['title']} ({parsed_data.get('year')}) vs. {metadata.title} ({metadata.year}). Torrent title: '{torrent_title}'"
                )
                return False
            if parsed_data.get("season"):
                self.logger.debug(
                    f"Season found for movie: {parsed_data['title']} ({parsed_data.get('season')}). Torrent title: '{torrent_title}'"
                )
                return False

        if (
            catalog_type == "series"
            and parsed_data.get("year")
            and (
                (
                    metadata.end_year
                    and not (
                        metadata.year <= parsed_data.get("year") <= metadata.end_year
                    )
                )
                or (not metadata.end_year and parsed_data.get("year") < metadata.year)
            )
        ):
            self.logger.debug(
                f"Year mismatch for series: {parsed_data['title']} ({parsed_data.get('year')}) vs. {metadata.title} ({metadata.year} - {metadata.end_year}). Torrent title: '{torrent_title}'"
            )
            return False

        return True

    @staticmethod
    async def store_streams(streams: List[TorrentStreams]):
        """
        Store the parsed streams in the database.
        :param streams: List of TorrentStreams objects
        """
        from db.crud import store_new_torrent_streams

        await store_new_torrent_streams(streams)
