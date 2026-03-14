import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from db.config import settings
from scrapers.anime_source_benchmark import (
    get_source_release_group_hints,
    get_source_reliability,
    get_source_tier,
)


@dataclass(frozen=True)
class ScraplingIndexerDefinition:
    key: str
    source_name: str
    query_url_templates: tuple[str, ...]
    row_selectors: tuple[str, ...]
    title_selectors: tuple[str, ...]
    detail_selectors: tuple[str, ...]
    magnet_selectors: tuple[str, ...]
    size_selectors: tuple[str, ...]
    seeder_selectors: tuple[str, ...]
    supports_movie: bool
    supports_series: bool
    supports_anime: bool
    search_pages_per_query: int = 1
    solve_cloudflare: bool = True
    fetcher_mode: str | None = None
    http_fallback: bool = False
    max_detail_url_length: int = 260
    anime_tier: int = 3
    anime_reliability: float = 0.5
    anime_release_group_hints: tuple[str, ...] = ()


REPO_ROOT = Path(__file__).resolve().parents[1]
PROWLARR_INDEXERS_PATH = REPO_ROOT / "resources" / "json" / "prowlarr-indexers.json"

GENERIC_ROW_SELECTORS = (
    "table.table-list tbody tr",
    "table.torrent-list tbody tr",
    "table tbody tr",
    "table tr",
    "tr",
    "div.torrent",
    "div.search-result",
    "li",
)
GENERIC_TITLE_SELECTORS = (
    "td.name a:last-child::text",
    "td.name a:nth-child(2)::text",
    "td:nth-child(2) a:last-child::text",
    "td:nth-child(2) a::text",
    "a.detLink::text",
    "a[href*='/torrent/']::text",
    "a[href*='details']::text",
    "a[title]::attr(title)",
)
GENERIC_DETAIL_SELECTORS = (
    "td.name a:last-child::attr(href)",
    "td.name a:nth-child(2)::attr(href)",
    "td:nth-child(2) a:last-child::attr(href)",
    "td:nth-child(2) a::attr(href)",
    "a.detLink::attr(href)",
    "a[href*='/torrent/']::attr(href)",
    "a[href*='details']::attr(href)",
)
GENERIC_MAGNET_SELECTORS = (
    "a[href^='magnet:?']::attr(href)",
    "a[title*='Magnet']::attr(href)",
)
GENERIC_SIZE_SELECTORS = (
    "td.size *::text",
    "td.size::text",
    "td:nth-child(4)::text",
    "td:nth-child(5)::text",
    "span.size::text",
)
GENERIC_SEEDER_SELECTORS = (
    "td.seeds::text",
    "td.seeders::text",
    "td:nth-child(6)::text",
    "td:nth-child(7)::text",
    "span.seeds::text",
)

KEY_ALIASES = {
    "nyaasi": "nyaa",
    "oxtorrent_co": "oxtorrent",
    "rutracker_ru": "rutracker",
}


def _normalize_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return KEY_ALIASES.get(normalized, normalized)


def _normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}".rstrip("/")


def _default_query_templates(base_url: str) -> tuple[str, ...]:
    return (
        f"{base_url}/search/{{query}}/{{page}}/",
        f"{base_url}/search/{{query}}",
        f"{base_url}/search/{{query}}/1/",
        f"{base_url}/search?q={{query}}",
        f"{base_url}/search/?q={{query}}",
        f"{base_url}/?q={{query}}",
        f"{base_url}/?s={{query}}",
        f"{base_url}/torrents-search.php?search={{query}}",
        f"{base_url}/index.php?do=search&subaction=search&story={{query}}",
    )


INDEXER_OVERRIDES = {
    "x1337": {
        "source_name": "1337x",
        "query_url_templates": ("https://1337x.to/search/{query}/{page}/",),
        "row_selectors": ("table.table-list tbody tr", "table.table-list tr"),
        "title_selectors": (
            "td.name a:last-child::text",
            "td.name a:nth-child(2)::text",
            "a[href*='/torrent/']::text",
        ),
        "detail_selectors": (
            "td.name a:last-child::attr(href)",
            "td.name a:nth-child(2)::attr(href)",
            "a[href*='/torrent/']::attr(href)",
        ),
        "magnet_selectors": ("a[href^='magnet:?']::attr(href)",),
        "size_selectors": ("td.size *::text", "td.size::text"),
        "seeder_selectors": ("td.seeds::text", "td.seeders::text"),
        "supports_movie": True,
        "supports_series": True,
        "supports_anime": False,
        "solve_cloudflare": True,
        "fetcher_mode": "stealthy",
    },
    "nyaa": {
        "source_name": "Nyaa",
        "query_url_templates": ("https://nyaa.si/?f=0&c=1_0&q={query}&p={page}",),
        "row_selectors": ("table.torrent-list tbody tr",),
        "title_selectors": (
            "td:nth-child(2) a:last-child::text",
            "td:nth-child(2) a::text",
        ),
        "detail_selectors": (
            "td:nth-child(2) a:last-child::attr(href)",
            "td:nth-child(2) a::attr(href)",
        ),
        "magnet_selectors": (
            "td:nth-child(3) a[href^='magnet:?']::attr(href)",
            "a[href^='magnet:?']::attr(href)",
        ),
        "size_selectors": ("td:nth-child(4)::text",),
        "seeder_selectors": ("td:nth-child(6)::text",),
        "supports_movie": False,
        "supports_series": False,
        "supports_anime": True,
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
    },
    "bitsearch": {
        "query_url_templates": (
            "https://bitsearch.to/search?q={query}",
            "https://bitsearch.mrunblock.bond/search?q={query}",
        ),
        "fetcher_mode": "stealthy",
        "solve_cloudflare": True,
    },
    "uindex": {
        "source_name": "UIndex",
        "query_url_templates": ("https://uindex.org/search.php?search={query}&c=0",),
        "row_selectors": ("table tr",),
        "title_selectors": ("a[href*='/details.php?id=']::text",),
        "detail_selectors": ("a[href*='/details.php?id=']::attr(href)",),
        "magnet_selectors": ("a[href^='magnet:?']::attr(href)",),
        "size_selectors": ("td:nth-child(3)::text",),
        "seeder_selectors": ("td:nth-child(4) span.g::text", "td:nth-child(4)::text"),
        "supports_movie": True,
        "supports_series": True,
        "supports_anime": True,
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
    },
    "bt4g": {
        "source_name": "BT4G",
        "query_url_templates": ("https://bt4gprx.com/search?q={query}&page=rss",),
        "row_selectors": ("item",),
        "title_selectors": ("title::text",),
        "detail_selectors": ("link::text",),
        "magnet_selectors": ("link::text",),
        "size_selectors": ("description::text",),
        "seeder_selectors": (),
        "supports_movie": True,
        "supports_series": True,
        "supports_anime": False,
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
        "http_fallback": True,
    },
    "animetosho": {
        "source_name": "AnimeTosho",
        "query_url_templates": (
            "https://animetosho.org/search?q={query}",
            "https://animetosho.org/search?q={query}&page={page}",
        ),
        "row_selectors": ("div.home_list_entry", "article", "li", "tr"),
        "title_selectors": ("a[href*='/view/']::text", "a::text"),
        "detail_selectors": ("a[href*='/view/']::attr(href)", "a::attr(href)"),
        "magnet_selectors": ("a[href^='magnet:?']::attr(href)",),
        "size_selectors": ("span.size::text", "*::text"),
        "seeder_selectors": ("span.seeders::text",),
        "supports_movie": False,
        "supports_series": False,
        "supports_anime": True,
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
        "http_fallback": True,
    },
    "subsplease": {
        "source_name": "SubsPlease",
        "query_url_templates": ("https://subsplease.org/api/?f=search&tz=UTC&s={query}",),
        "row_selectors": ("article", "li"),
        "title_selectors": ("a::text",),
        "detail_selectors": ("a::attr(href)",),
        "magnet_selectors": ("a[href^='magnet:?']::attr(href)",),
        "size_selectors": ("*::text",),
        "seeder_selectors": ("*::text",),
        "supports_movie": False,
        "supports_series": False,
        "supports_anime": True,
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
        "http_fallback": True,
    },
    "thepiratebay": {
        "query_url_templates": ("https://thepiratebay.org/search.php?q={query}",),
        "row_selectors": ("li.list-entry",),
        "title_selectors": ("span.item-name a::text",),
        "detail_selectors": ("span.item-name a::attr(href)",),
        "magnet_selectors": ("a[href^='magnet:?']::attr(href)",),
        "size_selectors": ("span.item-size::text",),
        "seeder_selectors": ("span.item-seed::text",),
    },
    "torlock": {
        "query_url_templates": ("https://www.torlock.com/all/torrents/{query}.html",),
        "row_selectors": ("tr",),
        "title_selectors": (
            "a[href*='/torrent/']::text",
            "a[href*='.t0r.space/torrent/']::text",
        ),
        "detail_selectors": (
            "a[href*='/torrent/']::attr(href)",
            "a[href*='.t0r.space/torrent/']::attr(href)",
        ),
        "size_selectors": ("td.ts::text",),
        "seeder_selectors": ("td.tul::text",),
        "max_detail_url_length": 180,
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
        "http_fallback": True,
    },
    "torrentdownloads": {
        "query_url_templates": ("https://www.torrentdownloads.pro/search/?search={query}",),
        "row_selectors": ("div.grey_bar3",),
        "title_selectors": ("p a[href^='/torrent/']::text",),
        "detail_selectors": ("p a[href^='/torrent/']::attr(href)",),
        "size_selectors": ("span::text",),
        "seeder_selectors": ("span:nth-of-type(2)::text",),
    },
    "yourbittorrent": {
        "query_url_templates": (
            "https://yourbittorrent.com/?q={query}",
            "https://yourbittorrent2.com/?q={query}",
        ),
    },
    "limetorrents": {
        "query_url_templates": (
            "https://www.limetorrents.fun/search/all/{query}/seeds/1/",
            "https://www.limetorrents.lol/search/all/{query}/seeds/1/",
        ),
        "row_selectors": ("table.table2 tr", "table tr"),
        "title_selectors": (
            "td.tdleft div.tt-name a:last-child::text",
            "td.tdleft div.tt-name a:nth-child(2)::text",
        ),
        "detail_selectors": (
            "td.tdleft div.tt-name a:last-child::attr(href)",
            "td.tdleft div.tt-name a:nth-child(2)::attr(href)",
        ),
        "magnet_selectors": (
            "a[href^='magnet:?']::attr(href)",
            "a[title*='Magnet']::attr(href)",
        ),
        "size_selectors": ("td.tdnormal::text",),
        "seeder_selectors": ("td.tdseed::text",),
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
        "http_fallback": True,
    },
    "rutor": {
        "query_url_templates": ("https://rutor.info/search/{query}",),
        "row_selectors": ("table tr",),
        "title_selectors": (
            "td:nth-child(2) a[href^='/torrent/'][href*='-']::text",
            "td:nth-child(2) a[href*='/torrent/'][href*='-']::text",
        ),
        "detail_selectors": (
            "td:nth-child(2) a[href^='/torrent/'][href*='-']::attr(href)",
            "td:nth-child(2) a[href*='/torrent/'][href*='-']::attr(href)",
        ),
        "magnet_selectors": (
            "td:nth-child(2) a[href^='magnet:?']::attr(href)",
            "a[href^='magnet:?']::attr(href)",
        ),
        "size_selectors": ("td:nth-child(4)::text",),
        "seeder_selectors": ("td:nth-child(5) span.green::text",),
        "solve_cloudflare": False,
    },
    "oxtorrent": {
        "query_url_templates": (
            "https://www.oxtorrent.co/recherche/{query}",
            "https://www.oxtorrent.co/search_torrent?torrentSearch={query}",
        ),
        "row_selectors": ("table tbody tr", "table tr"),
        "title_selectors": ("td:nth-child(1) a[href^='/torrent/']::text",),
        "detail_selectors": ("td:nth-child(1) a[href^='/torrent/']::attr(href)",),
        "magnet_selectors": ("a[href^='magnet:?']::attr(href)",),
        "size_selectors": ("td:nth-child(2)::text",),
        "seeder_selectors": ("td:nth-child(3)::text",),
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
        "http_fallback": True,
    },
    "eztv": {
        "query_url_templates": (
            "https://eztvx.to/search/{query}",
            "https://eztv.wf/search/{query}",
        ),
        "row_selectors": ("tr.forum_header_border",),
        "title_selectors": ("a.epinfo::text",),
        "detail_selectors": ("a.epinfo::attr(href)",),
        "size_selectors": ("td.forum_thread_post::text",),
        "seeder_selectors": ("td.forum_thread_post_end font::text", "td.forum_thread_post_end::text"),
        "supports_movie": False,
        "supports_series": True,
        "supports_anime": True,
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
    },
    "torrentdownload": {
        "query_url_templates": (
            "https://www.torrentdownload.info/search?q={query}",
            "https://www.torrentdownload.info/searchr?q={query}",
            "https://www.torrentdownload.info/searchd?q={query}",
        ),
        "row_selectors": ("tr",),
        "title_selectors": ("td.tdleft .tt-name a[href*='-']::text",),
        "detail_selectors": ("td.tdleft .tt-name a[href*='-']::attr(href)",),
        "size_selectors": ("td.tdnormal::text",),
        "seeder_selectors": ("td.tdseed::text",),
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
        "http_fallback": True,
    },
    "therarbg": {
        "query_url_templates": ("https://therarbg.to/get-posts/keywords:{query}/",),
        "row_selectors": ("div.wrapper",),
        "title_selectors": ("a[href^='/post-detail/']::text",),
        "detail_selectors": ("a[href^='/post-detail/']::attr(href)",),
        "solve_cloudflare": False,
        "fetcher_mode": "dynamic",
        "http_fallback": True,
    },
    "yts": {
        "query_url_templates": ("https://yts.bz/browse-movies/{query}/all/all/0/latest/0/all",),
        "row_selectors": ("div.browse-movie-wrap",),
        "title_selectors": ("a.browse-movie-title::text",),
        "detail_selectors": ("a.browse-movie-link::attr(href)",),
        "magnet_selectors": ("a[href^='magnet:?']::attr(href)",),
        "supports_movie": True,
        "supports_series": False,
        "supports_anime": False,
        "solve_cloudflare": False,
        "fetcher_mode": "stealthy",
    },
}

INDEXER_PRIORITY = {
    "x1337": 100,
    "nyaa": 95,
    "subsplease": 94,
    "animetosho": 94,
    "eztv": 93,
    "torrentdownloads": 90,
    "torrentdownload": 88,
    "torlock": 85,
    "thepiratebay": 82,
    "therarbg": 80,
    "uindex": 74,
    "bitsearch": 70,
    "yourbittorrent": 65,
    "oxtorrent": 63,
    "rutor": 60,
    "bt4g": 58,
    "limetorrents": 55,
    "yts": 50,
}


def _supports_content_types(indexer_data: dict) -> tuple[bool, bool, bool]:
    categories = indexer_data.get("capabilities", {}).get("categories", [])
    has_tv_anime = False
    has_movie = False
    has_series = False

    for category in categories:
        category_id = int(category.get("id", 0) or 0)
        if category_id == 2000:
            has_movie = True
        if category_id == 5000:
            has_series = True
        for sub in category.get("subCategories", []):
            sub_id = int(sub.get("id", 0) or 0)
            if sub_id == 5070:
                has_tv_anime = True

    return has_movie, has_series, has_tv_anime


def _build_definition_from_prowlarr(indexer_data: dict) -> ScraplingIndexerDefinition | None:
    if not indexer_data.get("enable", True):
        return None
    if not indexer_data.get("supportsSearch", False):
        return None
    if indexer_data.get("protocol") != "torrent":
        return None
    if indexer_data.get("privacy") != "public":
        return None

    definition_name = indexer_data.get("definitionName") or indexer_data.get("name", "")
    key = _normalize_key(definition_name)
    source_name = indexer_data.get("name") or definition_name

    indexer_urls = indexer_data.get("indexerUrls") or []
    if not indexer_urls:
        return None
    base_url = _normalize_base_url(indexer_urls[0])
    if not base_url:
        return None

    supports_movie, supports_series, supports_anime = _supports_content_types(indexer_data)
    if not (supports_movie or supports_series or supports_anime):
        return None

    definition = ScraplingIndexerDefinition(
        key=key,
        source_name=source_name,
        query_url_templates=_default_query_templates(base_url),
        row_selectors=GENERIC_ROW_SELECTORS,
        title_selectors=GENERIC_TITLE_SELECTORS,
        detail_selectors=GENERIC_DETAIL_SELECTORS,
        magnet_selectors=GENERIC_MAGNET_SELECTORS,
        size_selectors=GENERIC_SIZE_SELECTORS,
        seeder_selectors=GENERIC_SEEDER_SELECTORS,
        supports_movie=supports_movie,
        supports_series=supports_series,
        supports_anime=supports_anime,
        search_pages_per_query=1,
        solve_cloudflare=True,
        fetcher_mode="stealthy",
        anime_tier=get_source_tier(key),
        anime_reliability=get_source_reliability(key),
        anime_release_group_hints=get_source_release_group_hints(key),
    )

    override = INDEXER_OVERRIDES.get(key)
    if override:
        merged = {**definition.__dict__, **override}
        definition = ScraplingIndexerDefinition(**merged)
    return definition


def _load_definitions_from_prowlarr() -> dict[str, ScraplingIndexerDefinition]:
    raw_data = json.loads(PROWLARR_INDEXERS_PATH.read_text())
    definitions: dict[str, ScraplingIndexerDefinition] = {}
    for indexer_data in raw_data:
        definition = _build_definition_from_prowlarr(indexer_data)
        if definition:
            definitions[definition.key] = definition
    return definitions


def _build_extra_definitions() -> dict[str, ScraplingIndexerDefinition]:
    extra: dict[str, ScraplingIndexerDefinition] = {}
    for key in ("x1337", "uindex", "nyaa", "animetosho", "subsplease", "bt4g"):
        override = INDEXER_OVERRIDES.get(key)
        if not override:
            continue
        extra[key] = ScraplingIndexerDefinition(
            key=key,
            source_name=override["source_name"],
            query_url_templates=override["query_url_templates"],
            row_selectors=override["row_selectors"],
            title_selectors=override["title_selectors"],
            detail_selectors=override["detail_selectors"],
            magnet_selectors=override["magnet_selectors"],
            size_selectors=override["size_selectors"],
            seeder_selectors=override["seeder_selectors"],
            supports_movie=override["supports_movie"],
            supports_series=override["supports_series"],
            supports_anime=override["supports_anime"],
            search_pages_per_query=1,
            solve_cloudflare=override["solve_cloudflare"],
            fetcher_mode=override["fetcher_mode"],
            http_fallback=override.get("http_fallback", False),
            max_detail_url_length=override.get("max_detail_url_length", 260),
            anime_tier=override.get("anime_tier", get_source_tier(key)),
            anime_reliability=override.get("anime_reliability", get_source_reliability(key)),
            anime_release_group_hints=override.get(
                "anime_release_group_hints",
                get_source_release_group_hints(key),
            ),
        )
    return extra


PUBLIC_INDEXER_DEFINITIONS: dict[str, ScraplingIndexerDefinition] = {
    **_load_definitions_from_prowlarr(),
    **_build_extra_definitions(),
}


def _sort_indexers(
    definitions: list[ScraplingIndexerDefinition],
    *,
    is_anime: bool,
) -> list[ScraplingIndexerDefinition]:
    if is_anime:
        return sorted(
            definitions,
            key=lambda definition: (
                definition.anime_tier,
                -definition.anime_reliability,
                -INDEXER_PRIORITY.get(definition.key, 0),
                definition.key,
            ),
        )
    return sorted(
        definitions,
        key=lambda definition: (
            -INDEXER_PRIORITY.get(definition.key, 0),
            definition.key,
        ),
    )


def get_indexers_for_catalog(*, catalog_type: str, is_anime: bool) -> list[ScraplingIndexerDefinition]:
    if is_anime:
        if settings.public_indexers_anime_include_series_fallback:
            return _sort_indexers(
                [
                    definition
                    for definition in PUBLIC_INDEXER_DEFINITIONS.values()
                    if definition.supports_anime or definition.supports_series
                ],
                is_anime=True,
            )
        return _sort_indexers(
            [definition for definition in PUBLIC_INDEXER_DEFINITIONS.values() if definition.supports_anime],
            is_anime=True,
        )
    if catalog_type == "movie":
        return _sort_indexers(
            [definition for definition in PUBLIC_INDEXER_DEFINITIONS.values() if definition.supports_movie],
            is_anime=False,
        )
    return _sort_indexers(
        [definition for definition in PUBLIC_INDEXER_DEFINITIONS.values() if definition.supports_series],
        is_anime=False,
    )
