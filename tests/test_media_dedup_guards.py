from datetime import date, datetime
from types import SimpleNamespace

import pytest

from db.crud import scraper_helpers
from db.crud.catalog import get_catalog_meta_list
from db.crud.scraper_helpers import (
    _create_external_id_from_metadata,
    _normalize_date_value,
    _normalize_year_value,
)
from db.enums import MediaType
from db.models.media import Media


def test_media_last_stream_added_defaults_to_none() -> None:
    media = Media(type=MediaType.MOVIE, title="Sample")
    assert media.last_stream_added is None


def test_normalize_year_value_accepts_numeric_string() -> None:
    assert _normalize_year_value("2015") == 2015
    assert _normalize_year_value(" 1999 ") == 1999


def test_normalize_year_value_rejects_invalid_values() -> None:
    assert _normalize_year_value("2015-01-01") is None
    assert _normalize_year_value("abc") is None
    assert _normalize_year_value(None) is None


def test_normalize_date_value_accepts_scraper_date_strings() -> None:
    assert _normalize_date_value("2024-07-19") == date(2024, 7, 19)
    assert _normalize_date_value("2024-07-19T00:00:00Z") == date(2024, 7, 19)


def test_normalize_date_value_accepts_date_objects() -> None:
    expected = date(2024, 7, 19)

    assert _normalize_date_value(expected) == expected
    assert _normalize_date_value(datetime(2024, 7, 19, 12, 30)) == expected


def test_normalize_date_value_rejects_invalid_values() -> None:
    assert _normalize_date_value("not-a-date") is None
    assert _normalize_date_value("") is None
    assert _normalize_date_value(None) is None


@pytest.mark.asyncio
async def test_create_external_id_from_metadata_returns_conflict_media_id(monkeypatch) -> None:
    async def _fake_add_external_id(session, media_id: int, provider: str, external_id: str):
        if provider == "imdb" and external_id == "tt9999999":
            return SimpleNamespace(media_id=777)
        return SimpleNamespace(media_id=media_id)

    monkeypatch.setattr(scraper_helpers, "add_external_id", _fake_add_external_id)

    conflict_media_id = await _create_external_id_from_metadata(
        session=SimpleNamespace(),
        media_id=10,
        external_id=None,
        metadata_data={"imdb_id": "tt9999999"},
    )

    assert conflict_media_id == 777


@pytest.mark.asyncio
async def test_catalog_query_filters_out_zero_stream_media_for_movies() -> None:
    class _EmptyResult:
        def unique(self):
            return self

        def all(self):
            return []

    class _FakeSession:
        def __init__(self):
            self.query = None

        async def exec(self, query):
            self.query = query
            return _EmptyResult()

    fake_session = _FakeSession()
    user_data = SimpleNamespace(
        user_id=1,
        nudity_filter=["Disable"],
        certification_filter=["Disable"],
    )

    result = await get_catalog_meta_list(
        session=fake_session,
        catalog_type=MediaType.MOVIE,
        catalog_id="popular",
        user_data=user_data,
        skip=0,
        limit=20,
    )

    assert result.metas == []
    assert fake_session.query is not None
    compiled = str(fake_session.query)
    assert "media.total_streams" in compiled
