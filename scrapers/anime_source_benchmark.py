from dataclasses import dataclass


@dataclass(frozen=True)
class AnimeSourceBenchmark:
    """Benchmark snapshot used to rank anime scraping sources."""

    key: str
    reference_ecosystem: str
    tier: int
    source_class: str
    reliability_score: float
    release_group_hints: tuple[str, ...]
    query_patterns: tuple[str, ...]
    notes: str


ANIME_SOURCE_BENCHMARK_MATRIX: dict[str, AnimeSourceBenchmark] = {
    "nyaa": AnimeSourceBenchmark(
        key="nyaa",
        reference_ecosystem="stremio+kodi",
        tier=1,
        source_class="public_indexer",
        reliability_score=0.96,
        release_group_hints=("subsplease", "erai-raws", "horriblesubs", "nyaa"),
        query_patterns=("{title} - {episode:02d}", "{title} {episode:02d}", "{title} {season}"),
        notes="Primary anime torrent source with the strongest recall and release-group coverage.",
    ),
    "animetosho": AnimeSourceBenchmark(
        key="animetosho",
        reference_ecosystem="kodi",
        tier=2,
        source_class="public_indexer",
        reliability_score=0.84,
        release_group_hints=("subsplease", "erai-raws", "anime tosho"),
        query_patterns=("{title} {episode:02d}", "{title}", "{title} batch"),
        notes="Useful fallback with broad anime indexing and batch releases.",
    ),
    "subsplease": AnimeSourceBenchmark(
        key="subsplease",
        reference_ecosystem="kodi",
        tier=2,
        source_class="public_indexer",
        reliability_score=0.87,
        release_group_hints=("subsplease",),
        query_patterns=("{title} - {episode:02d}", "{title} {episode:02d}", "{title}"),
        notes="High signal weekly releases with predictable naming patterns.",
    ),
    "uindex": AnimeSourceBenchmark(
        key="uindex",
        reference_ecosystem="stremio",
        tier=2,
        source_class="public_indexer",
        reliability_score=0.78,
        release_group_hints=("anime", "batch"),
        query_patterns=("{title} - {episode:02d}", "{title}", "{title} complete"),
        notes="General-purpose source with anime category support.",
    ),
    "eztv": AnimeSourceBenchmark(
        key="eztv",
        reference_ecosystem="stremio",
        tier=3,
        source_class="public_indexer",
        reliability_score=0.61,
        release_group_hints=("amzn", "nf", "web"),
        query_patterns=("{title} S{season:02d}E{episode:02d}", "{title} {episode:02d}"),
        notes="Low-priority anime fallback, better for mainstream simulcast naming.",
    ),
    "animepahe": AnimeSourceBenchmark(
        key="animepahe",
        reference_ecosystem="kodi",
        tier=3,
        source_class="hoster",
        reliability_score=0.58,
        release_group_hints=("pahe", "hls"),
        query_patterns=("{title} {episode:02d}", "{title}"),
        notes="Hoster-class fallback when torrent indexers fail.",
    ),
    "limetorrents": AnimeSourceBenchmark(
        key="limetorrents",
        reference_ecosystem="stremio",
        tier=3,
        source_class="public_indexer",
        reliability_score=0.56,
        release_group_hints=("dual audio", "batch", "web"),
        query_patterns=("{title} - {episode:02d}", "{title} {episode:02d}", "{title}"),
        notes="Broad public indexer fallback with occasional anime batch coverage.",
    ),
    "therarbg": AnimeSourceBenchmark(
        key="therarbg",
        reference_ecosystem="stremio",
        tier=3,
        source_class="public_indexer",
        reliability_score=0.54,
        release_group_hints=("web", "amzn", "nf"),
        query_patterns=("{title} {episode:02d}", "{title}", "{title} season {season}"),
        notes="General fallback with mixed anime hit quality, useful when tier-1/2 miss.",
    ),
    "yourbittorrent": AnimeSourceBenchmark(
        key="yourbittorrent",
        reference_ecosystem="stremio",
        tier=4,
        source_class="public_indexer",
        reliability_score=0.45,
        release_group_hints=("batch", "anime"),
        query_patterns=("{title} {episode:02d}", "{title} batch", "{title}"),
        notes="Low-priority long-tail fallback with variable uptime.",
    ),
    "torlock": AnimeSourceBenchmark(
        key="torlock",
        reference_ecosystem="stremio",
        tier=4,
        source_class="public_indexer",
        reliability_score=0.42,
        release_group_hints=("anime", "pack"),
        query_patterns=("{title} {episode:02d}", "{title}", "{title} complete"),
        notes="Last-resort source due frequent challenge/timeout behavior.",
    ),
}


def get_source_benchmark(source_key: str) -> AnimeSourceBenchmark | None:
    return ANIME_SOURCE_BENCHMARK_MATRIX.get((source_key or "").strip().lower())


def get_source_tier(source_key: str) -> int:
    benchmark = get_source_benchmark(source_key)
    return benchmark.tier if benchmark else 3


def get_source_reliability(source_key: str) -> float:
    benchmark = get_source_benchmark(source_key)
    return benchmark.reliability_score if benchmark else 0.5


def get_source_release_group_hints(source_key: str) -> tuple[str, ...]:
    benchmark = get_source_benchmark(source_key)
    return benchmark.release_group_hints if benchmark else ()
