import asyncio
import json
import logging
import time
from dataclasses import replace

from db.config import settings
from db.schemas import MetadataData
from scrapers.public_indexer_registry import PUBLIC_INDEXER_DEFINITIONS
from scrapers.public_indexers import PublicIndexerScraper


def _configure_logging():
    for logger_name in (
        "scrapling",
        "scrapling.fetchers",
        "scrapling.engines._browsers._stealth",
        "scrapling.engines.toolbelt.custom",
    ):
        logging.getLogger(logger_name).setLevel(logging.ERROR)


def _movie_metadata(indexer_key: str) -> MetadataData:
    return MetadataData(
        id=1,
        external_id=f"mf:test:movie:{indexer_key}",
        type="movie",
        title="Interstellar",
        year=2014,
    )


def _series_metadata(indexer_key: str) -> MetadataData:
    return MetadataData(
        id=2,
        external_id=f"mf:test:series:{indexer_key}",
        type="series",
        title="Breaking Bad",
        year=2008,
    )


def _anime_metadata(indexer_key: str) -> MetadataData:
    return MetadataData(
        id=3,
        external_id=f"mf:test:anime:{indexer_key}",
        type="series",
        title="One Piece",
        year=1999,
        genres=["anime"],
        catalogs=["anime_series"],
    )


async def _probe_indexer(scraper: PublicIndexerScraper, indexer_key: str, timeout_seconds: int = 25) -> dict:
    definition = PUBLIC_INDEXER_DEFINITIONS[indexer_key]
    query = ""
    metadata = None
    catalog_type = "movie"
    season = None
    episode = None
    is_anime = False

    if definition.supports_movie:
        query = "Interstellar 2014"
        metadata = _movie_metadata(indexer_key)
        catalog_type = "movie"
    elif definition.supports_series:
        query = "Breaking Bad S01E01"
        metadata = _series_metadata(indexer_key)
        catalog_type = "series"
        season = 1
        episode = 1
    else:
        query = "One Piece 01"
        metadata = _anime_metadata(indexer_key)
        catalog_type = "series"
        season = 1
        episode = 1
        is_anime = True

    # Keep probe bounded while still covering common URL variants.
    templated_definition = replace(
        definition,
        query_url_templates=definition.query_url_templates[:2],
        search_pages_per_query=1,
    )
    processed_info_hashes: set[str] = set()
    start = time.monotonic()

    async def _collect() -> dict:
        count = 0
        examples: list[str] = []
        async for stream in scraper._search_indexer(
            indexer=templated_definition,
            query=query,
            metadata=metadata,
            catalog_type=catalog_type,
            season=season,
            episode=episode,
            is_anime=is_anime,
            processed_info_hashes=processed_info_hashes,
        ):
            count += 1
            if len(examples) < 2:
                examples.append(stream.name)
            if count >= 3:
                break
        return {"count": count, "examples": examples}

    try:
        result = await asyncio.wait_for(_collect(), timeout=timeout_seconds)
        status = "pass" if result["count"] > 0 else "empty"
        return {
            "indexer": indexer_key,
            "status": status,
            "count": result["count"],
            "examples": result["examples"],
            "elapsed_sec": round(time.monotonic() - start, 2),
        }
    except TimeoutError:
        return {
            "indexer": indexer_key,
            "status": "timeout",
            "count": 0,
            "examples": [],
            "elapsed_sec": round(time.monotonic() - start, 2),
        }
    except Exception as exc:  # noqa: BLE001 - this is a smoke-test harness
        return {
            "indexer": indexer_key,
            "status": "error",
            "count": 0,
            "examples": [],
            "error": str(exc),
            "elapsed_sec": round(time.monotonic() - start, 2),
        }


async def _run_end_to_end(scraper: PublicIndexerScraper) -> dict:
    original_global = settings.public_indexers_live_search_sites
    original_movie = settings.public_indexers_movie_live_search_sites
    original_series = settings.public_indexers_series_live_search_sites
    original_anime = settings.public_indexers_anime_live_search_sites

    # Keep the end-to-end check bounded to the known healthy subset
    # while the per-indexer sweep covers all definitions.
    settings.public_indexers_live_search_sites = ""
    settings.public_indexers_movie_live_search_sites = "uindex,rutor,thepiratebay,yts"
    settings.public_indexers_series_live_search_sites = "uindex,rutor,thepiratebay"
    settings.public_indexers_anime_live_search_sites = "nyaa,uindex,eztv"

    try:

        async def _safe_scrape(
            *, metadata: MetadataData, catalog_type: str, season: int | None = None, episode: int | None = None
        ):
            try:
                return await asyncio.wait_for(
                    scraper._scrape_and_parse(
                        None,
                        metadata,
                        catalog_type,
                        season=season,
                        episode=episode,
                    ),
                    timeout=80,
                )
            except TimeoutError:
                return []

        movie_streams = await _safe_scrape(
            metadata=_movie_metadata("matrix"),
            catalog_type="movie",
        )
        series_streams = await _safe_scrape(
            metadata=_series_metadata("breakingbad"),
            catalog_type="series",
            season=1,
            episode=1,
        )
        anime_streams = await _safe_scrape(
            metadata=_anime_metadata("onepiece"),
            catalog_type="series",
            season=1,
            episode=1,
        )
    finally:
        settings.public_indexers_live_search_sites = original_global
        settings.public_indexers_movie_live_search_sites = original_movie
        settings.public_indexers_series_live_search_sites = original_series
        settings.public_indexers_anime_live_search_sites = original_anime

    return {
        "movie_streams": len(movie_streams),
        "series_streams": len(series_streams),
        "anime_streams": len(anime_streams),
    }


async def main():
    _configure_logging()
    scraper = PublicIndexerScraper()
    all_indexers = sorted(PUBLIC_INDEXER_DEFINITIONS.keys())
    results: list[dict] = []

    for indexer_key in all_indexers:
        result = await _probe_indexer(scraper, indexer_key)
        results.append(result)
        print(f"[{result['status']}] {indexer_key} -> {result['count']}", flush=True)

    end_to_end = await _run_end_to_end(scraper)

    pass_count = sum(1 for item in results if item["status"] == "pass")
    empty_count = sum(1 for item in results if item["status"] == "empty")
    timeout_count = sum(1 for item in results if item["status"] == "timeout")
    error_count = sum(1 for item in results if item["status"] == "error")

    summary = {
        "total_indexers": len(results),
        "pass": pass_count,
        "empty": empty_count,
        "timeout": timeout_count,
        "error": error_count,
        "end_to_end": end_to_end,
        "results": results,
    }
    print("\nRESULT_SUMMARY_JSON_START")
    print(json.dumps(summary, indent=2))
    print("RESULT_SUMMARY_JSON_END")


if __name__ == "__main__":
    asyncio.run(main())
