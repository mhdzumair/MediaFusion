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

    For anime rows (catalog/genre/title-based detection), this pipeline also
    performs title-based MAL/Kitsu/AniList resolution and persists those IDs.
    """

    @staticmethod
    def _is_anime_item(item: dict, metadata: dict | None) -> bool:
        catalogs = item.get("catalog", [])
        if isinstance(catalogs, str):
            catalogs = [catalogs]
        if any("anime" in str(catalog).lower() for catalog in catalogs):
            return True

        title = str(item.get("title", "")).lower()
        if any(token in title for token in ("[subsplease]", "erai-raws", "horriblesubs", "anime")):
            return True

        genres = metadata.get("genres", []) if isinstance(metadata, dict) else []
        return any(str(genre).lower() in {"anime", "animation"} for genre in genres)

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

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

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
            metadata = await meta_fetcher.search_metadata(title, year, media_type, created_at)
        except Exception as e:
            logging.warning("Metadata search failed for '%s' (%s): %s", title, year, e)
            return item

        if metadata:
            imdb_id = metadata.get("imdb_id")
            logging.info("Resolved metadata for '%s' (%s) -> %s", title, year, imdb_id)
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
                additional = await self._fetch_additional_providers(imdb_id, media_type, title)
                provider_metadata.update(additional)
            elif self._is_anime_item(item, metadata):
                try:
                    anime_results = await meta_fetcher.search_multiple_results(
                        title=title,
                        limit=1,
                        year=year,
                        media_type=media_type,
                        include_anime=True,
                        anime_source_order=settings.anime_metadata_source_order,
                    )
                except Exception as exc:
                    logging.warning("Anime metadata search failed for '%s': %s", title, exc)
                    anime_results = []

                if anime_results:
                    anime_best = anime_results[0]
                    external_ids: dict[str, str] = {}
                    if anime_best.get("imdb_id"):
                        item["imdb_id"] = anime_best["imdb_id"]
                        external_ids["imdb"] = str(anime_best["imdb_id"])
                    if anime_best.get("tmdb_id"):
                        external_ids["tmdb"] = str(anime_best["tmdb_id"])
                    if anime_best.get("tvdb_id"):
                        external_ids["tvdb"] = str(anime_best["tvdb_id"])
                    if anime_best.get("mal_id"):
                        external_ids["mal"] = str(anime_best["mal_id"])
                    if anime_best.get("kitsu_id"):
                        external_ids["kitsu"] = str(anime_best["kitsu_id"])
                    if anime_best.get("anilist_id"):
                        external_ids["anilist"] = str(anime_best["anilist_id"])

                    if not item.get("poster") and anime_best.get("poster"):
                        item["poster"] = anime_best["poster"]
                    if not item.get("year") and anime_best.get("year"):
                        item["year"] = anime_best["year"]

                    additional = await meta_fetcher.get_metadata_from_all_providers(external_ids, media_type)
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
