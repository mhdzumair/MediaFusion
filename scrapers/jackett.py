from datetime import timedelta, datetime
from typing import List, Dict, Any, Literal, Optional
from xml.etree import ElementTree

import httpx

from db.config import settings
from db.enums import TorrentType
from db.models import (
    TorrentStreams,
    MediaFusionMetaData,
)
from scrapers.base_scraper import IndexerBaseScraper
from utils.network import CircuitBreaker
from utils.runtime_const import JACKETT_SEARCH_TTL


class JackettScraper(IndexerBaseScraper):
    cache_key_prefix = "jackett"
    search_url = "/api/v2.0/indexers/all/results"

    def __init__(self):
        super().__init__(
            cache_key_prefix=self.cache_key_prefix,
            base_url=settings.jackett_url,
        )

    @property
    def live_title_search_enabled(self) -> bool:
        return settings.jackett_live_title_search

    @property
    def background_title_search_enabled(self) -> bool:
        return settings.jackett_background_title_search

    @property
    def immediate_max_process(self) -> int:
        return settings.jackett_immediate_max_process

    @property
    def immediate_max_process_time(self) -> int:
        return settings.jackett_immediate_max_process_time

    @property
    def search_query_timeout(self) -> int:
        return settings.jackett_search_query_timeout

    def get_info_hash(self, item: dict) -> str:
        return item.get("InfoHash")

    def get_guid(self, item: dict) -> str:
        return item.get("Guid")

    def get_title(self, item: dict) -> str:
        return item.get("Title")

    def get_imdb_id(self, item: dict) -> str:
        return item.get("Imdb")

    def get_category_ids(self, item: dict) -> List[int]:
        return item["Category"]

    def get_magent_link(self, item: dict) -> str:
        return item.get("MagnetUri")

    def get_download_link(self, item: dict) -> str:
        return item.get("Link")

    def get_info_url(self, item: dict) -> str:
        return item.get("Details")

    def get_indexer(self, item: dict) -> str:
        return item.get("Tracker")

    def get_torrent_type(self, item: dict) -> TorrentType:
        return TorrentType(item.get("TrackerType"))

    def get_created_at(self, item: dict) -> datetime:
        return datetime.fromisoformat(item.get("PublishDate"))

    @IndexerBaseScraper.cache(ttl=JACKETT_SEARCH_TTL)
    @IndexerBaseScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        """Scrape and parse Jackett indexers for torrent streams"""
        return await super()._scrape_and_parse(metadata, catalog_type, season, episode)

    async def get_healthy_indexers(self) -> List[dict]:
        """Fetch and return list of healthy Jackett indexers with their capabilities"""
        try:
            response = await self.http_client.get(
                f"{self.base_url}/api/v2.0/indexers/!status:failing/results/torznab/api",
                params={
                    "apikey": settings.jackett_api_key,
                    "t": "indexers",
                    "configured": "true",
                },
                timeout=15,
            )
            response.raise_for_status()

            # Parse XML response

            root = ElementTree.fromstring(response.text)

            healthy_indexers = []
            for indexer in root.findall("indexer"):
                indexer_id = indexer.get("id")
                if not indexer_id:
                    continue

                # Get basic indexer info
                title = indexer.find("title").text
                description = (
                    indexer.find("description").text
                    if indexer.find("description") is not None
                    else ""
                )

                # Get searching capabilities
                caps = indexer.find("caps")
                if caps is None:
                    continue

                searching = caps.find("searching")
                if searching is None:
                    continue

                # Get category information
                categories = []
                cats_elem = caps.find("categories")
                if cats_elem is not None:
                    for cat in cats_elem.findall(".//category"):
                        cat_id = int(cat.get("id"))
                        categories.append(cat_id)
                        # Add subcategories
                        for subcat in cat.findall("subcat"):
                            categories.append(int(subcat.get("id")))

                # Parse search capabilities
                search_caps = {}
                for search_type in ["search", "tv-search", "movie-search"]:
                    search_elem = searching.find(search_type)
                    if (
                        search_elem is not None
                        and search_elem.get("available") == "yes"
                    ):
                        supported_params = search_elem.get("supportedParams", "").split(
                            ","
                        )
                        search_caps[search_type] = supported_params

                indexer_info = {
                    "id": indexer_id,
                    "name": title,
                    "description": description,
                    "categories": categories,
                    "search_capabilities": search_caps,
                }

                # Initialize circuit breaker
                self.indexer_circuit_breakers[indexer_id] = CircuitBreaker(
                    failure_threshold=3,
                    recovery_timeout=300,
                    half_open_attempts=1,
                )

                # Store indexer status
                self.indexer_status[indexer_id] = {
                    "is_healthy": True,  # If it's in the list, it's configured and healthy
                    "name": title,
                    "description": description,
                }

                healthy_indexers.append(indexer_info)

            self.logger.info(f"Found {len(healthy_indexers)} healthy indexers")
            return healthy_indexers

        except Exception as e:
            self.logger.error(f"Failed to determine healthy indexers: {e}")
            return []

    async def fetch_search_results(
        self,
        params: dict,
        indexer_ids: List[int],
        timeout: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch search results from Jackett indexers with circuit breaker handling"""
        results = []
        timeout = timeout or self.search_query_timeout

        for indexer_id in indexer_ids:
            indexer_status = self.indexer_status.get(indexer_id, {})
            if not indexer_status.get("is_healthy", False):
                continue

            circuit_breaker = self.indexer_circuit_breakers.get(indexer_id)
            if not circuit_breaker:
                continue

            indexer_name = indexer_status.get("name", f"ID:{indexer_id}")

            if circuit_breaker.is_closed():
                try:
                    search_params = {
                        **params,
                        "Tracker[]": [indexer_id],
                        "apikey": settings.jackett_api_key,
                    }
                    response = await self.http_client.get(
                        f"{self.base_url}{self.search_url}",
                        params=search_params,
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    indexer_results = response.json().get("Results", [])

                    circuit_breaker.record_success()
                    self.metrics.record_indexer_success(
                        indexer_name, len(indexer_results)
                    )
                    results.extend(indexer_results)

                except Exception as e:
                    error_msg = f"Error searching indexer {indexer_name}: {str(e)}"
                    self.logger.error(error_msg)

                    circuit_breaker.record_failure()
                    self.metrics.record_indexer_error(indexer_name, str(e))

                    if not circuit_breaker.is_closed():
                        self.logger.warning(
                            f"Circuit breaker opened for indexer {indexer_name}. "
                            f"Status: {circuit_breaker.get_status()}"
                        )
                        indexer_status["is_healthy"] = False
                        self.indexer_status[indexer_id] = indexer_status
            else:
                self.logger.debug(
                    f"Skipping indexer {indexer_name} - circuit breaker is {circuit_breaker.state}"
                )
                self.metrics.record_indexer_error(
                    indexer_name, f"Circuit breaker {circuit_breaker.state}"
                )

        return results

    async def build_search_params(
        self,
        video_id: str,
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        search_query: str = None,
    ) -> dict:
        """Build search parameters for Jackett API"""
        params = {
            "Category[]": categories,
        }

        if search_type in ["movie", "tvsearch"]:
            params["imdbid"] = video_id
        else:
            params["Query"] = search_query

        return params

    async def parse_indexer_data(
        self, indexer_data: dict, catalog_type: str, parsed_data: dict
    ) -> dict | None:
        """Parse Jackett-specific indexer data"""
        if not self.validate_category_with_title(indexer_data):
            return None

        download_url = indexer_data.get("MagnetUri") or indexer_data.get("Link")
        if not download_url:
            return None
        download_url = await self.get_download_url(indexer_data)
        if not download_url:
            return None

        try:
            torrent_data, is_torrent_downloaded = await self.get_torrent_data(
                download_url, indexer_data.get("Tracker"), parsed_data
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in [429, 500]:
                raise error
            self.logger.error(
                f"HTTP Error getting torrent data: {download_url}, status code: {error.response.status_code}"
            )
            return None
        except httpx.TimeoutException as error:
            self.logger.warning("Timeout while getting torrent data")
            raise error
        except httpx.RequestError as error:
            self.logger.error(f"Request error getting torrent data: {error}")
            raise error
        except Exception as e:
            self.logger.exception(f"Error getting torrent data: {e}")
            return None

        info_hash = torrent_data.get("info_hash", "").lower()
        if not info_hash:
            return None

        torrent_data.update(
            {
                "info_hash": info_hash,
                "seeders": indexer_data.get("Seeders"),
                "created_at": indexer_data.get("PublishDate"),
                "source": indexer_data.get("Tracker"),
                "catalog": [
                    "jackett_streams",
                    f"jackett_{catalog_type.rstrip('s')}s",
                ],
                "total_size": torrent_data.get("total_size")
                or indexer_data.get("Size"),
                **parsed_data,
            }
        )

        return torrent_data
