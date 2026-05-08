"""Registry for native (non-Newznab) public Usenet search sites.

NZBIndex: the Next.js UI shells out to a public JSON API (`/api/search`, `/api/download/{id}.nzb`)
discoverable in `/_next/static/chunks/*.js` — no browser required.

NZBKing: often returns HTTP 503 / timeouts from many networks; not registered until a stable host exists.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PublicUsenetIndexerDefinition:
    """Definition for a public Usenet search integration (HTML table or JSON API)."""

    key: str
    source_name: str
    handler: Literal["binsearch", "nzbindex"]
    query_url_templates: tuple[str, ...]
    # Origin for handler="nzbindex" — GET {site_origin}/api/search and /api/download/{id}.nzb
    site_origin: str = ""
    supports_movie: bool = True
    supports_series: bool = True
    supports_anime: bool = True
    search_pages_per_query: int = 1
    solve_cloudflare: bool = False
    fetcher_mode: str | None = None


BINSEARCH_BASE = "https://www.binsearch.info"

BINSEARCH_INDEXER = PublicUsenetIndexerDefinition(
    key="binsearch",
    source_name="Binsearch",
    handler="binsearch",
    query_url_templates=(f"{BINSEARCH_BASE}/search?q={{query}}&page={{page}}",),
    site_origin="",
    supports_movie=True,
    supports_series=True,
    supports_anime=True,
    search_pages_per_query=2,
    solve_cloudflare=False,
)

# Public JSON API discovered from site bundle (fetch /api/search?q=&page=0); no JS required.
NZBINDEX_ORIGIN = "https://www.nzbindex.com"
NZBINDEX_INDEXER = PublicUsenetIndexerDefinition(
    key="nzbindex",
    source_name="NZBIndex",
    handler="nzbindex",
    query_url_templates=(),
    site_origin=NZBINDEX_ORIGIN,
    supports_movie=True,
    supports_series=True,
    supports_anime=True,
    search_pages_per_query=2,
    solve_cloudflare=False,
)

ALL_PUBLIC_USENET_INDEXERS: tuple[PublicUsenetIndexerDefinition, ...] = (
    BINSEARCH_INDEXER,
    NZBINDEX_INDEXER,
)

_BY_KEY = {definition.key: definition for definition in ALL_PUBLIC_USENET_INDEXERS}


def get_usenet_indexers_for_catalog(*, catalog_type: str, is_anime: bool) -> list[PublicUsenetIndexerDefinition]:
    """Return enabled-by-catalog-type definitions."""
    results: list[PublicUsenetIndexerDefinition] = []
    for definition in ALL_PUBLIC_USENET_INDEXERS:
        if is_anime:
            if definition.supports_anime:
                results.append(definition)
            continue
        if catalog_type == "movie":
            if definition.supports_movie:
                results.append(definition)
        elif catalog_type == "series":
            if definition.supports_series:
                results.append(definition)
    return results


def get_usenet_indexer_by_key(key: str) -> PublicUsenetIndexerDefinition | None:
    return _BY_KEY.get(key)
