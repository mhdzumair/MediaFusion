import pytest

from scrapers.scraper_tasks import MetadataFetcher


class _DummyReadSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_anime_provider_prefers_kitsu_and_skips_anilist_when_enough_results(monkeypatch):
    fetcher = MetadataFetcher(cache_ttl_minutes=1)
    calls = {"kitsu": 0, "anilist": 0}

    async def _cache_get(**kwargs):
        return None

    async def _cache_set(data, **kwargs):
        return None

    async def _search_media(*args, **kwargs):
        return []

    async def _search_imdb(*args, **kwargs):
        return []

    async def _search_tmdb(*args, **kwargs):
        return []

    async def _search_tvdb(*args, **kwargs):
        return []

    async def _search_kitsu(*args, **kwargs):
        calls["kitsu"] += 1
        return [{"kitsu_id": "101", "title": "Anime A", "type": "series"}]

    async def _search_anilist(*args, **kwargs):
        calls["anilist"] += 1
        return [{"mal_id": "202", "title": "Anime B", "type": "series"}]

    monkeypatch.setattr(fetcher.cache, "get", _cache_get)
    monkeypatch.setattr(fetcher.cache, "set", _cache_set)
    monkeypatch.setattr("db.database.get_read_session_context", lambda: _DummyReadSessionContext())
    monkeypatch.setattr("db.crud.media.search_media", _search_media)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_imdb", _search_imdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_tmdb", _search_tmdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_tvdb", _search_tvdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_kitsu", _search_kitsu)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_mal", _search_anilist)
    monkeypatch.setattr("scrapers.scraper_tasks.settings.anime_metadata_source_order", ["kitsu", "anilist"])

    results = await fetcher.search_multiple_results("Solo Leveling", limit=1, media_type="series", include_anime=True)

    assert len(results) == 1
    assert results[0]["kitsu_id"] == "101"
    assert calls["kitsu"] == 1
    assert calls["anilist"] == 0


@pytest.mark.asyncio
async def test_anime_provider_falls_back_to_anilist_when_kitsu_empty(monkeypatch):
    fetcher = MetadataFetcher(cache_ttl_minutes=1)
    calls = {"kitsu": 0, "anilist": 0}

    async def _cache_get(**kwargs):
        return None

    async def _cache_set(data, **kwargs):
        return None

    async def _search_media(*args, **kwargs):
        return []

    async def _search_imdb(*args, **kwargs):
        return []

    async def _search_tmdb(*args, **kwargs):
        return []

    async def _search_tvdb(*args, **kwargs):
        return []

    async def _search_kitsu(*args, **kwargs):
        calls["kitsu"] += 1
        return []

    async def _search_anilist(*args, **kwargs):
        calls["anilist"] += 1
        return [{"mal_id": "303", "title": "Anime C", "type": "series"}]

    monkeypatch.setattr(fetcher.cache, "get", _cache_get)
    monkeypatch.setattr(fetcher.cache, "set", _cache_set)
    monkeypatch.setattr("db.database.get_read_session_context", lambda: _DummyReadSessionContext())
    monkeypatch.setattr("db.crud.media.search_media", _search_media)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_imdb", _search_imdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_tmdb", _search_tmdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_tvdb", _search_tvdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_kitsu", _search_kitsu)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_mal", _search_anilist)
    monkeypatch.setattr("scrapers.scraper_tasks.settings.anime_metadata_source_order", ["kitsu", "anilist"])

    results = await fetcher.search_multiple_results("Frieren", limit=1, media_type="series", include_anime=True)

    assert len(results) == 1
    assert results[0]["mal_id"] == "303"
    assert calls["kitsu"] == 1
    assert calls["anilist"] == 1


@pytest.mark.asyncio
async def test_anime_provider_order_can_prioritize_anilist(monkeypatch):
    fetcher = MetadataFetcher(cache_ttl_minutes=1)
    calls = {"kitsu": 0, "anilist": 0}

    async def _cache_get(**kwargs):
        return None

    async def _cache_set(data, **kwargs):
        return None

    async def _search_media(*args, **kwargs):
        return []

    async def _search_imdb(*args, **kwargs):
        return []

    async def _search_tmdb(*args, **kwargs):
        return []

    async def _search_tvdb(*args, **kwargs):
        return []

    async def _search_kitsu(*args, **kwargs):
        calls["kitsu"] += 1
        return [{"kitsu_id": "404", "title": "Anime D", "type": "series"}]

    async def _search_anilist(*args, **kwargs):
        calls["anilist"] += 1
        return [{"mal_id": "505", "title": "Anime E", "type": "series"}]

    monkeypatch.setattr(fetcher.cache, "get", _cache_get)
    monkeypatch.setattr(fetcher.cache, "set", _cache_set)
    monkeypatch.setattr("db.database.get_read_session_context", lambda: _DummyReadSessionContext())
    monkeypatch.setattr("db.crud.media.search_media", _search_media)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_imdb", _search_imdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_tmdb", _search_tmdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_tvdb", _search_tvdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_kitsu", _search_kitsu)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_mal", _search_anilist)
    monkeypatch.setattr("scrapers.scraper_tasks.settings.anime_metadata_source_order", ["anilist", "kitsu"])

    results = await fetcher.search_multiple_results("Kaiju No 8", limit=1, media_type="series", include_anime=True)

    assert len(results) == 1
    assert results[0]["mal_id"] == "505"
    assert calls["anilist"] == 1
    assert calls["kitsu"] == 0


@pytest.mark.asyncio
async def test_anime_provider_request_order_override_takes_precedence(monkeypatch):
    fetcher = MetadataFetcher(cache_ttl_minutes=1)
    calls = {"kitsu": 0, "anilist": 0}

    async def _cache_get(**kwargs):
        return None

    async def _cache_set(data, **kwargs):
        return None

    async def _search_media(*args, **kwargs):
        return []

    async def _search_imdb(*args, **kwargs):
        return []

    async def _search_tmdb(*args, **kwargs):
        return []

    async def _search_tvdb(*args, **kwargs):
        return []

    async def _search_kitsu(*args, **kwargs):
        calls["kitsu"] += 1
        return [{"kitsu_id": "606", "title": "Anime F", "type": "series"}]

    async def _search_anilist(*args, **kwargs):
        calls["anilist"] += 1
        return [{"mal_id": "707", "title": "Anime G", "type": "series"}]

    monkeypatch.setattr(fetcher.cache, "get", _cache_get)
    monkeypatch.setattr(fetcher.cache, "set", _cache_set)
    monkeypatch.setattr("db.database.get_read_session_context", lambda: _DummyReadSessionContext())
    monkeypatch.setattr("db.crud.media.search_media", _search_media)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_imdb", _search_imdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_tmdb", _search_tmdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_tvdb", _search_tvdb)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_kitsu", _search_kitsu)
    monkeypatch.setattr("scrapers.scraper_tasks.search_multiple_mal", _search_anilist)
    monkeypatch.setattr("scrapers.scraper_tasks.settings.anime_metadata_source_order", ["kitsu", "anilist"])

    results = await fetcher.search_multiple_results(
        "Dandadan",
        limit=1,
        media_type="series",
        include_anime=True,
        anime_source_order=["anilist", "kitsu"],
    )

    assert len(results) == 1
    assert results[0]["mal_id"] == "707"
    assert calls["anilist"] == 1
    assert calls["kitsu"] == 0
