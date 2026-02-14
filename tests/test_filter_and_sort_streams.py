"""
Tests for filter_and_sort_streams in utils/parser.py

Covers:
- Resolution filtering (selected vs not selected)
- Resolution sorting by user-defined order
- Quality filtering (selected vs not selected)
- Quality sorting by user-defined order
- Language filtering (selected vs not selected)
- Language sorting by user-defined order
- Max file size filtering
- Min file size filtering
- Max streams per resolution limiting
- Max total streams cap
- Stream name include filter (keyword + regex)
- Stream name exclude filter (keyword + regex)
- Combined filters + sorting integration
"""

import math
from datetime import UTC, datetime

import pytest

from db.schemas.config import SortingOption, UserData
from db.schemas.media import TorrentStreamData
from utils.parser import filter_and_sort_streams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GB = 1024 * 1024 * 1024


def make_stream(
    *,
    name: str = "Test.Stream",
    info_hash: str | None = None,
    size: int = 2 * GB,
    resolution: str | None = "1080p",
    quality: str | None = "WEB-DL",
    languages: list[str] | None = None,
    seeders: int = 10,
    created_at: datetime | None = None,
) -> TorrentStreamData:
    """Create a minimal TorrentStreamData for testing.

    Note: quality values must match entries in const.QUALITY_GROUPS exactly:
    - BluRay/UHD group: "BluRay", "BluRay REMUX", "BRRip", "BDRip", "UHDRip", "REMUX", "BLURAY"
    - WEB/HD group: "WEB-DL", "WEB-DLRip", "WEBRip", "HDRip", "WEBMux"
    - DVD/TV/SAT group: "DVD", "DVDRip", "HDTV", "SATRip", "TVRip", "PPVRip", "PDTV"
    - CAM/Screener group: "CAM", "TeleSync", "TeleCine", "SCR"
    """
    if info_hash is None:
        # Generate a unique hash from the name
        info_hash = name.replace(".", "").replace(" ", "").ljust(40, "0")[:40]
    return TorrentStreamData(
        info_hash=info_hash,
        name=name,
        size=size,
        source="test",
        resolution=resolution,
        quality=quality,
        languages=languages or ["English"],
        seeders=seeders,
        created_at=created_at or datetime.now(UTC),
        meta_id="tt1234567",
    )


def make_user_data(**overrides) -> UserData:
    """Create a UserData with sensible defaults for testing, accepting overrides."""
    defaults = {
        "sr": ["1080p", "720p", "480p"],
        "qf": ["WEB/HD", "BluRay/UHD"],
        "ls": ["English", "Hindi", "Tamil"],
        "tsp": [
            {"k": "resolution", "d": "desc"},
            {"k": "size", "d": "desc"},
        ],
        "ms": math.inf,
        "mns": 0,
        "mspr": 10,
        "mxs": 50,
        "snfm": "disabled",
        "snfp": [],
        "snfr": False,
    }
    defaults.update(overrides)
    return UserData(**defaults)


# ---------------------------------------------------------------------------
# Resolution filtering
# ---------------------------------------------------------------------------


class TestResolutionFiltering:
    @pytest.mark.asyncio
    async def test_selected_resolutions_pass(self):
        streams = [
            make_stream(name="S.1080p", resolution="1080p"),
            make_stream(name="S.720p", resolution="720p"),
        ]
        user_data = make_user_data(sr=["1080p", "720p"])
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 2
        assert reasons["Resolution Not Selected"] == 0

    @pytest.mark.asyncio
    async def test_unselected_resolution_filtered(self):
        streams = [
            make_stream(name="S.4k", resolution="4k"),
            make_stream(name="S.1080p", resolution="1080p"),
        ]
        user_data = make_user_data(sr=["1080p"])
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "S.1080p"
        assert reasons["Resolution Not Selected"] == 1


# ---------------------------------------------------------------------------
# Resolution sorting by user order
# ---------------------------------------------------------------------------


class TestResolutionSorting:
    @pytest.mark.asyncio
    async def test_resolution_sorted_by_user_order_desc(self):
        """User prefers 720p > 1080p > 4k; desc sorts by user order (first = best)."""
        streams = [
            make_stream(name="S.1080p", resolution="1080p", size=2 * GB),
            make_stream(name="S.720p", resolution="720p", size=2 * GB),
            make_stream(name="S.4k", resolution="4k", size=2 * GB),
        ]
        user_data = make_user_data(
            sr=["720p", "1080p", "4k"],
            tsp=[{"k": "resolution", "d": "desc"}],
        )
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        names = [s.name for s in result]
        assert names == ["S.720p", "S.1080p", "S.4k"]

    @pytest.mark.asyncio
    async def test_resolution_sorted_by_user_order_asc(self):
        """Ascending reverses the user order."""
        streams = [
            make_stream(name="S.1080p", resolution="1080p", size=2 * GB),
            make_stream(name="S.720p", resolution="720p", size=2 * GB),
            make_stream(name="S.4k", resolution="4k", size=2 * GB),
        ]
        user_data = make_user_data(
            sr=["720p", "1080p", "4k"],
            tsp=[{"k": "resolution", "d": "asc"}],
        )
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        names = [s.name for s in result]
        assert names == ["S.4k", "S.1080p", "S.720p"]


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------


class TestQualityFiltering:
    @pytest.mark.asyncio
    async def test_selected_quality_passes(self):
        streams = [
            make_stream(name="S.WEB", quality="WEB-DL"),
            make_stream(name="S.BR", quality="BluRay"),
        ]
        user_data = make_user_data(qf=["WEB/HD", "BluRay/UHD"])
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 2
        assert reasons["Quality Not Selected"] == 0

    @pytest.mark.asyncio
    async def test_unselected_quality_filtered(self):
        streams = [
            make_stream(name="S.WEB", quality="WEB-DL"),
            make_stream(name="S.CAM", quality="CAM"),
        ]
        user_data = make_user_data(qf=["WEB/HD"])
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "S.WEB"
        assert reasons["Quality Not Selected"] == 1


# ---------------------------------------------------------------------------
# Quality sorting by user order
# ---------------------------------------------------------------------------


class TestQualitySorting:
    @pytest.mark.asyncio
    async def test_quality_sorted_by_user_order_desc(self):
        """User prefers WEB/HD over BluRay/UHD."""
        streams = [
            make_stream(name="S.BR", quality="BluRay", resolution="1080p", size=2 * GB),
            make_stream(name="S.WEB", quality="WEB-DL", resolution="1080p", size=2 * GB),
        ]
        user_data = make_user_data(
            sr=["1080p"],
            qf=["WEB/HD", "BluRay/UHD"],
            tsp=[{"k": "quality", "d": "desc"}],
        )
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        names = [s.name for s in result]
        assert names == ["S.WEB", "S.BR"]


# ---------------------------------------------------------------------------
# Language filtering
# ---------------------------------------------------------------------------


class TestLanguageFiltering:
    @pytest.mark.asyncio
    async def test_selected_language_passes(self):
        streams = [
            make_stream(name="S.En", languages=["English"]),
            make_stream(name="S.Hi", languages=["Hindi"]),
        ]
        user_data = make_user_data(ls=["English", "Hindi"])
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 2
        assert reasons["Language Not Selected"] == 0

    @pytest.mark.asyncio
    async def test_unselected_language_filtered(self):
        streams = [
            make_stream(name="S.En", languages=["English"]),
            make_stream(name="S.Fr", languages=["French"]),
        ]
        user_data = make_user_data(ls=["English"])
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "S.En"
        assert reasons["Language Not Selected"] == 1


# ---------------------------------------------------------------------------
# Language sorting by user order
# ---------------------------------------------------------------------------


class TestLanguageSorting:
    @pytest.mark.asyncio
    async def test_language_sorted_by_user_preference(self):
        """User prefers Tamil > English; Tamil stream should rank first."""
        streams = [
            make_stream(name="S.En", languages=["English"], resolution="1080p", size=2 * GB),
            make_stream(name="S.Ta", languages=["Tamil"], resolution="1080p", size=2 * GB),
        ]
        user_data = make_user_data(
            sr=["1080p"],
            ls=["Tamil", "English"],
            tsp=[{"k": "language", "d": "desc"}],
        )
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        names = [s.name for s in result]
        assert names == ["S.Ta", "S.En"]


# ---------------------------------------------------------------------------
# File size filtering
# ---------------------------------------------------------------------------


class TestFileSizeFiltering:
    @pytest.mark.asyncio
    async def test_max_size_filters_large_streams(self):
        streams = [
            make_stream(name="S.Small", size=5 * GB),
            make_stream(name="S.Big", size=100 * GB),
        ]
        user_data = make_user_data(ms=50 * GB)
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "S.Small"
        assert reasons["Max Size Exceeded"] == 1

    @pytest.mark.asyncio
    async def test_max_size_inf_allows_all(self):
        streams = [
            make_stream(name="S.Huge", size=500 * GB),
        ]
        user_data = make_user_data(ms=math.inf)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_min_size_filters_small_streams(self):
        streams = [
            make_stream(name="S.Tiny", size=500 * 1024 * 1024),  # 500 MB
            make_stream(name="S.Normal", size=5 * GB),
        ]
        user_data = make_user_data(mns=1 * GB)
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "S.Normal"
        assert reasons["Min Size Not Met"] == 1

    @pytest.mark.asyncio
    async def test_min_size_zero_allows_all(self):
        streams = [
            make_stream(name="S.Tiny", size=100),
            make_stream(name="S.Normal", size=5 * GB),
        ]
        user_data = make_user_data(mns=0)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_min_size_skips_unknown_size_streams(self):
        """Streams with size=0 (unknown) should NOT be filtered by min_size."""
        streams = [
            make_stream(name="S.Unknown", size=0),
            make_stream(name="S.Big", size=5 * GB),
        ]
        user_data = make_user_data(mns=1 * GB)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        # size=0 is treated as unknown, so min_size check is skipped
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_min_and_max_size_combined(self):
        """Both min and max size filters applied together."""
        streams = [
            make_stream(name="S.Tiny", size=500 * 1024 * 1024),  # 500 MB - too small
            make_stream(name="S.Good", size=5 * GB),  # 5 GB - in range
            make_stream(name="S.Huge", size=100 * GB),  # 100 GB - too big
        ]
        user_data = make_user_data(mns=1 * GB, ms=50 * GB)
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "S.Good"
        assert reasons["Min Size Not Met"] == 1
        assert reasons["Max Size Exceeded"] == 1


# ---------------------------------------------------------------------------
# Max streams per resolution
# ---------------------------------------------------------------------------


class TestMaxStreamsPerResolution:
    @pytest.mark.asyncio
    async def test_limits_per_resolution(self):
        streams = [make_stream(name=f"S.1080p.{i}", resolution="1080p", info_hash=f"{'a' * 39}{i}") for i in range(5)]
        user_data = make_user_data(mspr=3)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_different_resolutions_have_separate_limits(self):
        streams = [
            make_stream(name=f"S.1080p.{i}", resolution="1080p", info_hash=f"{'a' * 39}{i}") for i in range(5)
        ] + [make_stream(name=f"S.720p.{i}", resolution="720p", info_hash=f"{'b' * 39}{i}") for i in range(5)]
        user_data = make_user_data(mspr=2)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        # 2 per 1080p + 2 per 720p = 4
        assert len(result) == 4
        res_1080 = [s for s in result if s.resolution == "1080p"]
        res_720 = [s for s in result if s.resolution == "720p"]
        assert len(res_1080) == 2
        assert len(res_720) == 2


# ---------------------------------------------------------------------------
# Max total streams
# ---------------------------------------------------------------------------


class TestMaxTotalStreams:
    @pytest.mark.asyncio
    async def test_total_cap_applied(self):
        streams = [make_stream(name=f"S.{i}", resolution="1080p", info_hash=f"{'c' * 39}{i}") for i in range(20)]
        user_data = make_user_data(mxs=5, mspr=50)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_total_cap_after_per_resolution(self):
        """Total cap is applied after per-resolution limiting."""
        streams = [
            make_stream(name=f"S.1080p.{i}", resolution="1080p", info_hash=f"{'d' * 39}{i}") for i in range(10)
        ] + [make_stream(name=f"S.720p.{i}", resolution="720p", info_hash=f"{'e' * 39}{i}") for i in range(10)]
        # Per-resolution = 5, total cap = 7
        user_data = make_user_data(mspr=5, mxs=7)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        # 5 per 1080p + 5 per 720p = 10, then capped to 7
        assert len(result) == 7


# ---------------------------------------------------------------------------
# Stream name filter
# ---------------------------------------------------------------------------


class TestStreamNameFilter:
    @pytest.mark.asyncio
    async def test_include_keyword_keeps_matching(self):
        streams = [
            make_stream(name="Movie.HEVC.1080p"),
            make_stream(name="Movie.x264.1080p"),
        ]
        user_data = make_user_data(snfm="include", snfp=["HEVC"], snfr=False)
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "Movie.HEVC.1080p"
        assert reasons["Stream Name Filter"] == 1

    @pytest.mark.asyncio
    async def test_exclude_keyword_removes_matching(self):
        streams = [
            make_stream(name="Movie.HEVC.1080p"),
            make_stream(name="Movie.x264.1080p"),
        ]
        user_data = make_user_data(snfm="exclude", snfp=["HEVC"], snfr=False)
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "Movie.x264.1080p"
        assert reasons["Stream Name Filter"] == 1

    @pytest.mark.asyncio
    async def test_include_regex(self):
        streams = [
            make_stream(name="Movie.HDR10.DV.1080p"),
            make_stream(name="Movie.SDR.1080p"),
            make_stream(name="Movie.HDR10Plus.1080p"),
        ]
        user_data = make_user_data(snfm="include", snfp=["HDR10|DV"], snfr=True)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 2
        names = {s.name for s in result}
        assert "Movie.SDR.1080p" not in names

    @pytest.mark.asyncio
    async def test_exclude_regex(self):
        streams = [
            make_stream(name="Movie.CAM.1080p"),
            make_stream(name="Movie.WEB-DL.1080p"),
            make_stream(name="Movie.TELECINE.1080p"),
        ]
        user_data = make_user_data(snfm="exclude", snfp=["CAM|TELECINE"], snfr=True)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "Movie.WEB-DL.1080p"

    @pytest.mark.asyncio
    async def test_disabled_filter_passes_all(self):
        streams = [
            make_stream(name="Movie.HEVC.1080p"),
            make_stream(name="Movie.x264.1080p"),
        ]
        user_data = make_user_data(snfm="disabled", snfp=["HEVC"])
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_include_keyword_case_insensitive(self):
        streams = [
            make_stream(name="Movie.hevc.1080p"),
            make_stream(name="Movie.x264.1080p"),
        ]
        user_data = make_user_data(snfm="include", snfp=["HEVC"], snfr=False)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 1
        assert result[0].name == "Movie.hevc.1080p"

    @pytest.mark.asyncio
    async def test_multiple_include_patterns_or_logic(self):
        """Stream matches if ANY pattern matches (OR logic)."""
        streams = [
            make_stream(name="Movie.HEVC.1080p"),
            make_stream(name="Movie.AV1.1080p"),
            make_stream(name="Movie.x264.1080p"),
        ]
        user_data = make_user_data(snfm="include", snfp=["HEVC", "AV1"], snfr=False)
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 2
        names = {s.name for s in result}
        assert names == {"Movie.HEVC.1080p", "Movie.AV1.1080p"}


# ---------------------------------------------------------------------------
# Combined / Integration tests
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """Test the full filter + sort pipeline with realistic data."""
        streams = [
            make_stream(
                name="Big.BluRay.4k", resolution="4k", quality="BluRay", size=80 * GB, languages=["English"], seeders=50
            ),
            make_stream(
                name="Good.WEB.1080p",
                resolution="1080p",
                quality="WEB-DL",
                size=4 * GB,
                languages=["English"],
                seeders=100,
            ),
            make_stream(
                name="Small.WEB.720p", resolution="720p", quality="WEB-DL", size=1 * GB, languages=["Hindi"], seeders=30
            ),
            make_stream(
                name="Tiny.CAM.480p",
                resolution="480p",
                quality="CAM",
                size=500 * 1024 * 1024,
                languages=["English"],
                seeders=5,
            ),
            make_stream(
                name="Hindi.WEB.1080p",
                resolution="1080p",
                quality="WEB-DL",
                size=3 * GB,
                languages=["Hindi"],
                seeders=80,
            ),
            make_stream(
                name="French.WEB.1080p",
                resolution="1080p",
                quality="WEB-DL",
                size=4 * GB,
                languages=["French"],
                seeders=40,
            ),
        ]
        user_data = make_user_data(
            sr=["1080p", "720p"],  # Only 1080p and 720p
            qf=["WEB/HD"],  # Only WEB/HD quality
            ls=["Hindi", "English"],  # Hindi preferred over English
            tsp=[
                {"k": "language", "d": "desc"},
                {"k": "size", "d": "desc"},
            ],
            ms=50 * GB,  # Max 50 GB
            mns=1 * GB,  # Min 1 GB
            mxs=10,
        )
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")

        # French filtered (not in language list)
        assert reasons["Language Not Selected"] == 1
        # 4k and 480p filtered (not in resolution list)
        assert reasons["Resolution Not Selected"] == 2
        # CAM quality is also filtered but 480p was already filtered by resolution

        # Remaining: Good.WEB.1080p (En, 4GB), Small.WEB.720p (Hi, 1GB), Hindi.WEB.1080p (Hi, 3GB)
        assert len(result) == 3

        # Sorted by language (Hindi first), then size desc
        # Hindi streams first: Hindi.WEB.1080p (3GB), Small.WEB.720p (1GB)
        # Then English: Good.WEB.1080p (4GB)
        assert result[0].name == "Hindi.WEB.1080p"
        assert result[1].name == "Small.WEB.720p"
        assert result[2].name == "Good.WEB.1080p"

    @pytest.mark.asyncio
    async def test_empty_streams_returns_empty(self):
        user_data = make_user_data()
        result, reasons = await filter_and_sort_streams([], user_data, "tt1234567:1:1")
        assert result == []

    @pytest.mark.asyncio
    async def test_all_filtered_returns_empty(self):
        streams = [
            make_stream(name="S.4k", resolution="4k"),
        ]
        user_data = make_user_data(sr=["1080p"])
        result, reasons = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        assert len(result) == 0
        assert reasons["Resolution Not Selected"] == 1

    @pytest.mark.asyncio
    async def test_sorting_multi_key(self):
        """Multiple sort keys: resolution first (user order), then seeders."""
        streams = [
            make_stream(name="A.720p.50s", resolution="720p", seeders=50, size=2 * GB),
            make_stream(name="B.1080p.10s", resolution="1080p", seeders=10, size=2 * GB),
            make_stream(name="C.1080p.100s", resolution="1080p", seeders=100, size=2 * GB),
            make_stream(name="D.720p.5s", resolution="720p", seeders=5, size=2 * GB),
        ]
        user_data = make_user_data(
            sr=["1080p", "720p"],
            tsp=[
                {"k": "resolution", "d": "desc"},
                {"k": "seeders", "d": "desc"},
            ],
        )
        result, _ = await filter_and_sort_streams(streams, user_data, "tt1234567:1:1")
        names = [s.name for s in result]
        # 1080p first (user order index 0), then 720p (index 1)
        # Within 1080p: 100 seeders > 10 seeders
        # Within 720p: 50 seeders > 5 seeders
        assert names == ["C.1080p.100s", "B.1080p.10s", "A.720p.50s", "D.720p.5s"]
