import json
import logging

import httpx

from db import schemas
from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT


class MDBListScraper:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.mdblist.com"
        self.client = httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=30.0)
        self.batch_size = 200

    async def close(self):
        await self.client.aclose()

    async def _fetch_list(
        self,
        list_config: schemas.MDBListItem,
        offset: int = 0,
        genre: str | None = None,
    ) -> dict | None:
        """Fetch a list from MDBList API"""
        params = {
            "apikey": self.api_key,
            "limit": self.batch_size,
            "offset": offset,
            "append_to_response": "genre",
            "sort": list_config.sort,
            "order": list_config.order,
        }
        if genre:
            params["filter_genre"] = genre

        # Update cache key to include sort and order
        cache_key = f"mdblist:raw:{list_config.id}:offset_{offset}:{genre or 'all'}:sort_{list_config.sort}:order_{list_config.order}"
        cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)

        if cached_data:
            return json.loads(cached_data)

        try:
            response = await self.client.get(f"{self.base_url}/lists/{list_config.id}/items", params=params)
            if response.status_code == 200:
                # Cache raw API response for 60 minutes
                await REDIS_ASYNC_CLIENT.set(cache_key, response.text, ex=3600)
                return response.json()
            logging.error(f"Failed to fetch MDBList data: {response.status_code}")
            return None
        except Exception as e:
            logging.error(f"Error fetching MDBList data: {e}")
            return None

    @staticmethod
    def _convert_to_meta(item: dict, media_type: str) -> schemas.Meta:
        """Convert MDBList item to Meta object"""
        genres = item.get("genre", [])
        if genres and genres[0] is None:
            genres = None
        return schemas.Meta.model_validate(
            {
                "_id": item["imdb_id"],
                "type": media_type,
                "title": item["title"],
                "year": item["release_year"],
                "genres": genres,
                "poster": f"{settings.poster_host_url}/poster/{media_type}/{item['imdb_id']}.jpg",
            }
        )

    async def get_all_list_items(
        self,
        list_config: schemas.MDBListItem,
        genre: str | None = None,
    ) -> list[str]:
        """
        Fetch all IMDb IDs from a list until no more results are available.
        Used for filtered results to ensure complete dataset.
        """
        cache_key = f"mdblist:all_ids:{list_config.id}:{list_config.catalog_type}:{genre or 'all'}:sort_{list_config.sort}:order_{list_config.order}"
        cached_ids = await REDIS_ASYNC_CLIENT.lrange(cache_key, 0, -1)

        if cached_ids:
            return [cached_id.decode() for cached_id in cached_ids]

        all_imdb_ids = []
        offset = 0

        while True:
            batch = await self._fetch_list(list_config, offset, genre)
            if not batch:
                break

            # Get the correct list based on media type
            items = batch.get("movies" if list_config.catalog_type == "movie" else "shows", [])
            if not items:
                break

            # Filter valid IMDb IDs for the specific media type
            new_ids = [item["imdb_id"] for item in items if item.get("imdb_id", "").startswith("tt")]

            if not new_ids:
                break

            all_imdb_ids.extend(new_ids)
            offset += self.batch_size

            # If we got fewer items than batch_size, we've reached the end
            if len(items) < self.batch_size:
                break

        # Cache the complete list if we got any results
        if all_imdb_ids:
            pipeline = await REDIS_ASYNC_CLIENT.pipeline()
            pipeline.delete(cache_key)
            pipeline.rpush(cache_key, *all_imdb_ids)
            pipeline.expire(cache_key, 3600)  # Cache for 1 hour
            await pipeline.execute()

        return all_imdb_ids

    async def get_list_items(
        self,
        list_config: schemas.MDBListItem,
        skip: int = 0,
        limit: int = 25,
        genre: str | None = None,
        use_filters: bool = True,
    ) -> list[str] | list[schemas.Meta]:
        """
        Get items from a MDBList list.
        For filtered results (use_filters=True), fetches all available items.
        For unfiltered results, uses regular pagination.
        """
        if use_filters:
            return await self.get_all_list_items(list_config, genre)

        # For unfiltered results, use regular pagination
        offset = (skip // self.batch_size) * self.batch_size
        batch = await self._fetch_list(list_config, offset, genre)
        if not batch:
            return []

        items = batch.get("movies" if list_config.catalog_type == "movie" else "shows", [])
        meta_list = [
            self._convert_to_meta(item, list_config.catalog_type)
            for item in items
            if item.get("imdb_id", "").startswith("tt")
        ]

        # Calculate the slice we need from this batch
        start_idx = skip % self.batch_size
        end_idx = min(start_idx + limit, len(meta_list))
        return meta_list[start_idx:end_idx]


async def initialize_mdblist_scraper(api_key: str) -> MDBListScraper:
    """Initialize MDBList scraper with API key"""
    return MDBListScraper(api_key)
