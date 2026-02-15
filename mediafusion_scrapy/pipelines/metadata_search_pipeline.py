import asyncio
import logging

from db.config import settings
from scrapers.scraper_tasks import meta_fetcher
from scrapers.tmdb_data import get_tmdb_data_by_imdb
from scrapers.tvdb_data import get_tvdb_data_by_imdb


class MetadataSearchPipeline:
    """
    Pipeline that searches for metadata from all available providers.
    Should run after torrent parsing (which extracts title/year) and before store pipelines.

    Flow:
    1. Search IMDB/TMDB via meta_fetcher.search_metadata() to get the primary match.
    2. Once we have an IMDB ID, fetch additional providers (TMDB, TVDB) in parallel
       using their IMDB-based cross-lookup functions.
    3. Store all provider data in item['_provider_metadata'] so the store pipeline
       can persist everything in a single pass — no need for "Refresh All".

    Note: MAL and Kitsu don't support IMDB-based lookups and are primarily for anime,
    so they are not included here. They can be added later with title-based search
    if anime detection is implemented.
    """

    async def _fetch_additional_providers(self, imdb_id, media_type, title):
        """Fetch TMDB and TVDB data in parallel using the resolved IMDB ID."""
        provider_metadata = {}
        tasks = {}

        if settings.tmdb_api_key:
            tasks["tmdb"] = get_tmdb_data_by_imdb(imdb_id, media_type)
        if settings.tvdb_api_key:
            tasks["tvdb"] = get_tvdb_data_by_imdb(imdb_id, media_type)

        if not tasks:
            return provider_metadata

        results = await asyncio.gather(
            *tasks.values(), return_exceptions=True
        )

        for provider_name, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logging.warning(
                    "%s lookup by IMDB ID failed for '%s' (%s): %s",
                    provider_name.upper(),
                    title,
                    imdb_id,
                    result,
                )
            elif result:
                provider_metadata[provider_name] = result
                # Log the provider-specific ID
                id_key = f"{provider_name}_id"
                provider_id = result.get(id_key, "unknown")
                logging.info(
                    "Resolved %s data for '%s' (IMDB: %s, %s: %s)",
                    provider_name.upper(),
                    title,
                    imdb_id,
                    provider_name.upper(),
                    provider_id,
                )

        return provider_metadata

    async def process_item(self, item):
        # Skip if no title parsed yet (torrent parsing may have failed)
        if "title" not in item:
            return item

        # Skip if already has an external ID
        if item.get("imdb_id"):
            return item

        title = item["title"]
        year = item.get("year")
        media_type = item.get("type", "movie")
        created_at = item.get("created_at")

        try:
            metadata = await meta_fetcher.search_metadata(
                title, year, media_type, created_at
            )
        except Exception as e:
            logging.warning(
                "Metadata search failed for '%s' (%s): %s", title, year, e
            )
            return item

        if metadata:
            imdb_id = metadata.get("imdb_id")
            logging.info(
                "Resolved metadata for '%s' (%s) -> %s", title, year, imdb_id
            )
            item["imdb_id"] = imdb_id
            # Preserve spider-provided fields, only fill in missing ones
            if not item.get("poster") and metadata.get("poster"):
                item["poster"] = metadata["poster"]
            if not item.get("background") and metadata.get("background"):
                item["background"] = metadata["background"]
            if not item.get("year") and metadata.get("year"):
                item["year"] = metadata["year"]

            # Start with IMDB data, then fetch TMDB + TVDB in parallel
            provider_metadata = {"imdb": metadata}
            if imdb_id:
                additional = await self._fetch_additional_providers(
                    imdb_id, media_type, title
                )
                provider_metadata.update(additional)

            # Store all provider metadata so the store pipeline can
            # persist everything (description, genres, cast, crew, etc.)
            # in a single DB transaction — no need for a separate "Refresh All".
            item["_provider_metadata"] = provider_metadata
        else:
            logging.info(
                "No metadata found for '%s' (%s), will use synthetic ID",
                title,
                year,
            )

        return item
