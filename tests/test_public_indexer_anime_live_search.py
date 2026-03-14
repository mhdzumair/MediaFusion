import asyncio
import time
from types import SimpleNamespace

import pytest

from db.schemas import MetadataData
from db.config import settings
from scrapers.public_indexer_registry import PUBLIC_INDEXER_DEFINITIONS
from scrapers.public_indexers import PublicIndexerScraper


def test_is_anime_metadata_detects_japanese_series_without_anime_genre():
    scraper = PublicIndexerScraper()
    metadata = MetadataData(
        id=101,
        external_id="tt2560140",
        type="series",
        title="Attack on Titan",
        original_title="進撃の巨人",
        year=2013,
        genres=["Action & Adventure"],
        catalogs=["series"],
        original_language="ja",
        country="JP",
    )

    assert scraper._is_anime_metadata(metadata) is True


def test_is_anime_metadata_ignores_non_anime_series_without_hints():
    scraper = PublicIndexerScraper()
    metadata = MetadataData(
        id=102,
        external_id="tt0944947",
        type="series",
        title="Game of Thrones",
        year=2011,
        genres=["Drama", "Action & Adventure"],
        catalogs=["series"],
        original_language="en",
        country="US",
    )

    assert scraper._is_anime_metadata(metadata) is False


@pytest.mark.asyncio
async def test_select_indexers_uses_env_anime_order(monkeypatch):
    scraper = PublicIndexerScraper()

    monkeypatch.setattr(settings, "public_indexers_live_search_sites", "")
    monkeypatch.setattr(settings, "public_indexers_anime_live_search_sites", "nyaa,uindex,eztv")
    monkeypatch.setattr(settings, "public_indexers_source_health_gates_enabled", False)

    selected = await scraper._select_indexers(user_data=None, catalog_type="series", is_anime=True)
    keys = [definition.key for definition in selected]

    assert keys[:3] == ["nyaa", "uindex", "eztv"]


@pytest.mark.asyncio
async def test_select_indexers_applies_failure_budget_gate(monkeypatch):
    scraper = PublicIndexerScraper()

    def _mock_is_snapshot_within_budget(snapshot, **_kwargs):
        return snapshot.source_key != "subsplease"

    monkeypatch.setattr(settings, "public_indexers_live_search_sites", "")
    monkeypatch.setattr(settings, "public_indexers_source_health_gates_enabled", True)
    monkeypatch.setattr(settings, "public_indexers_source_health_probation_enabled", False)
    monkeypatch.setattr(scraper, "_is_snapshot_within_budget", _mock_is_snapshot_within_budget)

    selected = await scraper._select_indexers(user_data=None, catalog_type="series", is_anime=True)
    keys = [definition.key for definition in selected]

    assert "subsplease" not in keys
    assert "nyaa" in keys


@pytest.mark.asyncio
async def test_select_indexers_applies_public_failure_budget_gate(monkeypatch):
    scraper = PublicIndexerScraper()

    def _mock_is_snapshot_within_budget(snapshot, **_kwargs):
        return snapshot.source_key != "uindex"

    monkeypatch.setattr(settings, "public_indexers_live_search_sites", "")
    monkeypatch.setattr(settings, "public_indexers_movie_live_search_sites", "uindex,thepiratebay")
    monkeypatch.setattr(settings, "public_indexers_source_health_gates_enabled", True)
    monkeypatch.setattr(settings, "public_indexers_source_health_probation_enabled", False)
    monkeypatch.setattr(scraper, "_is_snapshot_within_budget", _mock_is_snapshot_within_budget)

    selected = await scraper._select_indexers(user_data=None, catalog_type="movie", is_anime=False)
    keys = [definition.key for definition in selected]

    assert "uindex" not in keys
    assert "thepiratebay" in keys


@pytest.mark.asyncio
async def test_select_indexers_probation_sampler_retests_blocked_sources(monkeypatch):
    scraper = PublicIndexerScraper()

    def _mock_is_snapshot_within_budget(snapshot, **_kwargs):
        return snapshot.source_key != "uindex"

    monkeypatch.setattr(settings, "public_indexers_live_search_sites", "")
    monkeypatch.setattr(settings, "public_indexers_movie_live_search_sites", "uindex,thepiratebay")
    monkeypatch.setattr(settings, "public_indexers_source_health_gates_enabled", True)
    monkeypatch.setattr(settings, "public_indexers_source_health_probation_enabled", True)
    monkeypatch.setattr(settings, "public_indexers_source_health_probation_ratio", 1.0)
    monkeypatch.setattr(settings, "public_indexers_source_health_probation_max_sources_per_query", 1)
    monkeypatch.setattr(scraper, "_is_snapshot_within_budget", _mock_is_snapshot_within_budget)

    selected = await scraper._select_indexers(user_data=None, catalog_type="movie", is_anime=False)
    keys = [definition.key for definition in selected]

    assert "uindex" in keys
    assert "thepiratebay" in keys


def test_rank_anime_results_prefers_release_group_and_seeders():
    scraper = PublicIndexerScraper()
    streams = [
        SimpleNamespace(release_group="random", source="Nyaa", quality="WEB", seeders=450),
        SimpleNamespace(release_group="SubsPlease", source="SubsPlease", quality="WEB", seeders=120),
        SimpleNamespace(release_group="Erai-Raws", source="Mirror", quality="WEB", seeders=90),
    ]

    ranked = scraper._rank_anime_results(streams)

    assert ranked[0].release_group.lower() == "subsplease"
    assert ranked[1].release_group.lower() == "erai-raws"


@pytest.mark.asyncio
async def test_subsplease_api_parser_returns_streams(monkeypatch):
    scraper = PublicIndexerScraper()
    definition = PUBLIC_INDEXER_DEFINITIONS["subsplease"]
    metadata = MetadataData(
        id=10,
        external_id="mal:21",
        type="series",
        title="One Piece",
        year=1999,
        genres=["anime"],
        catalogs=["anime_series"],
    )

    async def _mock_fetch_with_http(_url: str):
        return (
            {
                "status": 200,
                "url": "https://subsplease.org/api/?f=search&tz=UTC&s=one+piece",
                "html": (
                    '{"One Piece - 01":{"show":"One Piece","episode":"01","downloads":[{"res":"1080",'
                    '"magnet":"magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&xl=1048576"}]}}'
                ),
            },
            False,
        )

    monkeypatch.setattr(scraper, "_fetch_with_http", _mock_fetch_with_http)

    results = []
    async for stream in scraper._search_subsplease_api(
        indexer=definition,
        search_url="https://subsplease.org/api/?f=search&tz=UTC&s=one+piece",
        metadata=metadata,
        catalog_type="series",
        season=1,
        episode=1,
        processed_info_hashes=set(),
    ):
        results.append(stream)

    assert len(results) == 1
    assert results[0].source == "SubsPlease"
    assert results[0].size == 1048576


@pytest.mark.asyncio
async def test_scrape_and_parse_runs_indexers_in_parallel(monkeypatch):
    scraper = PublicIndexerScraper()
    metadata = MetadataData(
        id=11,
        external_id="tt0114709",
        type="series",
        title="Toy Story Toons",
        year=2011,
        genres=[],
        catalogs=["series"],
    )
    selected_indexers = [
        PUBLIC_INDEXER_DEFINITIONS["uindex"],
        PUBLIC_INDEXER_DEFINITIONS["rutor"],
        PUBLIC_INDEXER_DEFINITIONS["thepiratebay"],
        PUBLIC_INDEXER_DEFINITIONS["eztv"],
    ]

    async def _mock_search_indexer(*, indexer, **_kwargs):
        await asyncio.sleep(0.25)
        yield SimpleNamespace(source=indexer.source_name)

    monkeypatch.setattr(scraper, "_search_indexer", _mock_search_indexer)

    started_at = time.monotonic()
    results = await scraper._search_indexers_for_query(
        indexers=selected_indexers,
        query="toy story toons s01e01",
        metadata=metadata,
        catalog_type="series",
        season=1,
        episode=1,
        is_anime=False,
        processed_info_hashes=set(),
        processed_info_hashes_lock=asyncio.Lock(),
        max_streams=20,
        deadline=time.monotonic() + 30,
        parallelism=4,
    )
    elapsed = time.monotonic() - started_at

    assert len(results) == len(selected_indexers)
    assert elapsed < 0.75
