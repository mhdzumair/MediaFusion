import json
import logging
from typing import Optional, Dict, List

import httpx

from db import schemas
from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT


class MDBListScraper:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.mdblist.com"
        self.client = httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def _fetch_list(
        self,
        list_id: str,
        limit: int = 100,
        offset: int = 0,
        genre: Optional[str] = None,
    ) -> Optional[Dict]:
        """Fetch a list from MDBList API"""
        params = {
            "apikey": self.api_key,
            "limit": limit,
            "offset": offset,
            "append_to_response": "genre",
        }
        if genre:
            params["filter_genre"] = genre

        cache_key = (
            f"mdblist:list:{list_id}:limit_{limit}:offset_{offset}:{genre or 'all'}"
        )
        cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)

        if cached_data:
            return json.loads(cached_data)

        try:
            response = await self.client.get(
                f"{self.base_url}/lists/{list_id}/items", params=params
            )
            if response.status_code == 200:
                # Cache for 15 minutes
                await REDIS_ASYNC_CLIENT.set(cache_key, response.text, ex=900)
                return response.json()
            logging.error(f"Failed to fetch MDBList data: {response.status_code}")
            return None
        except Exception as e:
            logging.error(f"Error fetching MDBList data: {e}")
            return None

    def _convert_to_meta(self, item: Dict, media_type: str) -> schemas.Meta:
        """Convert MDBList item to Meta object"""
        genres = item.get("genre", [])
        if genres and genres[0] is None:
            # clean up genre data
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

    async def _fetch_and_process_batch(
        self,
        list_id: str,
        media_type: str,
        offset: int,
        limit: int,
        skip: int,
        genre: Optional[str],
        use_filters: bool = False,
    ) -> List[schemas.Meta] | List[str]:
        """Helper method to fetch and process a batch of results"""
        data = await self._fetch_list(list_id, 100, offset, genre)
        if not data:
            return []

        items = data.get("movies" if media_type == "movie" else "shows", [])

        if not use_filters:
            # Convert directly to Meta objects
            meta_list = [
                self._convert_to_meta(item, media_type)
                for item in items
                if item.get("imdb_id", "").startswith("tt")
            ]
            # Calculate the slice we need from this batch
            start_idx = skip % 100
            end_idx = start_idx + limit
            return meta_list[start_idx:end_idx]

        return [
            item["imdb_id"]
            for item in items
            if item.get("imdb_id", "").startswith("tt")
        ]

    async def get_list_items(
        self,
        list_id: str,
        media_type: str,
        skip: int = 0,
        limit: int = 25,
        genre: Optional[str] = None,
        use_filters: bool = True,
    ) -> List[schemas.Meta] | List[str]:
        """
        Get items from a MDBList list with pagination support.
        For filtered results, keeps fetching until we have enough items after filtering.
        """
        if not use_filters:
            # Direct return for unfiltered results
            fetch_limit = 100
            offset = (skip // fetch_limit) * fetch_limit
            return await self._fetch_and_process_batch(
                list_id, media_type, offset, limit, skip, genre, use_filters=False
            )

        # For filtered results, we need to handle pagination differently
        cache_key = f"mdblist:filtered:{list_id}:genre_{genre or 'all'}"
        cached_filtered_ids = await REDIS_ASYNC_CLIENT.lrange(cache_key, 0, -1)

        if cached_filtered_ids:
            # Use cached filtered results
            start_idx = skip
            end_idx = skip + limit
            return [
                cached_id.decode()
                for cached_id in cached_filtered_ids[start_idx:end_idx]
            ]

        # No cache - need to fetch and filter
        all_imdb_ids = []
        offset = 0
        batch_size = 100

        while len(all_imdb_ids) < skip + limit:
            batch = await self._fetch_list(list_id, batch_size, offset, genre)
            if not batch:
                break

            items = batch.get("movies" if media_type == "movie" else "shows", [])
            if not items:
                break

            new_ids = [
                item["imdb_id"]
                for item in items
                if item.get("imdb_id", "").startswith("tt")
            ]
            all_imdb_ids.extend(new_ids)

            if len(items) < batch_size:  # No more results available
                break

            offset += batch_size

        # Cache the full result for 15 minutes
        if all_imdb_ids:
            pipeline = await REDIS_ASYNC_CLIENT.pipeline()
            pipeline.delete(cache_key)
            pipeline.rpush(cache_key, *all_imdb_ids)
            pipeline.expire(cache_key, 900)  # 15 minutes
            await pipeline.execute()

        return all_imdb_ids[skip : skip + limit]


async def initialize_mdblist_scraper(api_key: str) -> MDBListScraper:
    """Initialize MDBList scraper with API key"""
    return MDBListScraper(api_key)
