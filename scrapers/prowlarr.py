import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Literal

import httpx

from db.config import settings
from db.enums import TorrentType
from db.models import TorrentStreams, MediaFusionMetaData
from scrapers.base_scraper import IndexerBaseScraper
from utils.network import CircuitBreaker
from utils.runtime_const import PROWLARR_SEARCH_TTL


class ProwlarrScraper(IndexerBaseScraper):

    cache_key_prefix = "prowlarr"

    def __init__(self):
        self.headers = {"X-Api-Key": settings.prowlarr_api_key}
        super().__init__(
            cache_key_prefix=self.cache_key_prefix,
            base_url=settings.prowlarr_url,
        )

    @IndexerBaseScraper.cache(ttl=PROWLARR_SEARCH_TTL)
    @IndexerBaseScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        """Scrape and parse Prowlarr indexers for torrent streams"""
        return await super()._scrape_and_parse(
            user_data, metadata, catalog_type, season, episode
        )

    @property
    def live_title_search_enabled(self) -> bool:
        return settings.prowlarr_live_title_search

    @property
    def background_title_search_enabled(self) -> bool:
        return settings.prowlarr_background_title_search

    @property
    def immediate_max_process(self) -> int:
        return settings.prowlarr_immediate_max_process

    @property
    def immediate_max_process_time(self) -> int:
        return settings.prowlarr_immediate_max_process_time

    @property
    def search_query_timeout(self) -> int:
        return settings.prowlarr_search_query_timeout

    def get_info_hash(self, item: dict) -> str:
        return item.get("infoHash")

    def get_guid(self, item: dict) -> str:
        return item.get("guid")

    def get_title(self, item: dict) -> str:
        return item.get("title")

    def get_imdb_id(self, item: dict) -> str | None:
        imdb_id = item.get("imdbId")
        if imdb_id:
            return f"tt{imdb_id}"

    def get_category_ids(self, item: dict) -> List[int]:
        return [category["id"] for category in item["categories"]]

    def get_magent_link(self, item: dict) -> str:
        return item.get("magnetUrl")

    def get_download_link(self, item: dict) -> str:
        return item.get("downloadUrl")

    def get_info_url(self, item: dict) -> str:
        return item.get("infoUrl")

    def get_indexer(self, item: dict) -> str:
        return item.get("indexer")

    def get_torrent_type(self, item: dict) -> TorrentType:
        if item.get("indexerFlags"):
            flag = item["indexerFlags"][0]
        else:
            indexer_id = item.get("indexerId")
            flag = self.indexer_status.get(indexer_id, {}).get("privacy", "public")
            if flag == "semiPrivate":
                return TorrentType.SEMI_PRIVATE
        return TorrentType.PUBLIC if flag == "freeleech" else TorrentType(flag)

    def get_created_at(self, item: dict) -> datetime:
        return datetime.fromisoformat(item.get("publishDate"))

    async def get_healthy_indexers(self) -> List[dict]:
        """Fetch and return list of healthy Prowlarr indexers with their capabilities"""
        try:
            # Fetch both indexer configurations and their current status
            indexers_future = self.http_client.get(
                f"{self.base_url}/api/v1/indexer", headers=self.headers
            )
            statuses_future = self.http_client.get(
                f"{self.base_url}/api/v1/indexerstatus", headers=self.headers
            )

            responses = await asyncio.gather(
                indexers_future, statuses_future, return_exceptions=True
            )

            if any(isinstance(response, Exception) for response in responses):
                self.logger.error("Failed to fetch indexer data or status")
                return []

            indexers_response, statuses_response = responses
            indexers_response.raise_for_status()
            statuses_response.raise_for_status()

            indexers = indexers_response.json()
            status_data = {
                status["indexerId"]: status for status in statuses_response.json()
            }

            healthy_indexers = []
            current_time = datetime.now(timezone.utc)

            for indexer in indexers:
                indexer_id = indexer.get("id")
                if not indexer_id or not indexer.get("enable", False):
                    continue

                # Get status information for this indexer
                status_info = status_data.get(indexer_id, {})
                disabled_till = status_info.get("disabledTill")

                # Convert disabled_till to datetime if it exists
                if disabled_till:
                    try:
                        disabled_till = datetime.fromisoformat(
                            disabled_till.replace("Z", "+00:00")
                        )
                    except ValueError:
                        disabled_till = None

                # Skip if indexer is temporarily disabled
                if disabled_till and disabled_till > current_time:
                    continue

                # Parse capabilities
                caps = indexer.get("capabilities", {})
                search_caps = {}

                # Map Prowlarr search types to our standard types
                cap_mapping = {
                    "searchParams": "search",
                    "tvSearchParams": "tv-search",
                    "movieSearchParams": "movie-search",
                }

                for cap_type, std_type in cap_mapping.items():
                    if caps.get(cap_type):
                        search_caps[std_type] = caps.get(cap_type)

                # Get supported categories
                categories = []
                for cat in caps.get("categories", []):
                    cat_id = cat.get("id")
                    if cat_id:
                        categories.append(cat_id)
                        # Add subcategories if any
                        for subcat in cat.get("subCategories", []):
                            if subcat_id := subcat.get("id"):
                                categories.append(subcat_id)

                indexer_info = {
                    "id": indexer_id,
                    "name": indexer.get("name", "Unknown"),
                    "description": indexer.get("description", ""),
                    "categories": categories,
                    "search_capabilities": search_caps,
                    "protocol": indexer.get("protocol"),
                    "privacy": indexer.get("privacy"),
                    "priority": indexer.get("priority", 25),
                }

                # Initialize circuit breaker
                self.indexer_circuit_breakers[indexer_id] = CircuitBreaker(
                    failure_threshold=3,
                    recovery_timeout=300,
                    half_open_attempts=1,
                )

                # Store indexer status
                self.indexer_status[indexer_id] = {
                    "is_healthy": True,
                    "name": indexer_info["name"],
                    "description": indexer_info["description"],
                    "privacy": indexer_info["privacy"],
                }

                healthy_indexers.append(indexer_info)

            # Sort indexers by priority
            healthy_indexers.sort(key=lambda x: x["priority"])

            self.logger.info(f"Found {len(healthy_indexers)} healthy indexers")
            return healthy_indexers

        except Exception as e:
            self.logger.error(f"Failed to determine healthy indexers: {e}")
            return []

    async def fetch_search_results(
        self,
        params: dict,
        indexer_ids: List[str],
        timeout: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch search results from Prowlarr indexers"""
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
                    search_params = {**params, "indexerIds": [indexer_id]}
                    response = await self.http_client.get(
                        f"{self.base_url}/api/v1/search",
                        params=search_params,
                        headers=self.headers,
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    indexer_results = response.json()

                    # Record success
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
        """Build search parameters for Prowlarr API"""
        if search_type in ["movie", "tvsearch"]:
            search_query = f"{{IMDbId:{video_id}}}"

        return {
            "query": search_query,
            "categories": categories,
            "type": search_type,
        }

    async def parse_indexer_data(
        self, indexer_data: dict, catalog_type: str, parsed_data: dict
    ) -> Optional[dict]:
        """Parse Prowlarr-specific indexer data"""
        download_url = await self.get_download_url(indexer_data)
        if not download_url:
            return None

        torrent_data, is_torrent_downloaded = await self.get_torrent_data(
            download_url, parsed_data
        )

        if not is_torrent_downloaded:
            return None

        info_hash = torrent_data.get("info_hash", "").lower()
        if not info_hash:
            return None

        torrent_data.update(
            {
                "seeders": indexer_data.get("seeders"),
                "created_at": indexer_data.get("publishDate"),
                "source": indexer_data.get("indexer"),
                "catalog": [
                    "prowlarr_streams",
                    f"prowlarr_{catalog_type.rstrip('s')}s",
                ],
                "total_size": torrent_data.get("total_size")
                or indexer_data.get("size"),
                **parsed_data,
            }
        )

        return torrent_data
