"""Usenet stream compatibility checks across providers and indexers."""

from functools import lru_cache
from urllib.parse import urlparse

from db.config import settings
from db.schemas import StreamingProvider, UserData

# Providers that are bound to a specific Usenet source family.
# These providers should not be offered streams scraped from unrelated sources.
PROVIDER_BOUND_SOURCE_MARKERS: dict[str, set[str]] = {
    "easynews": {"easynews"},
    "torbox": {"torbox"},
}

# Downloader-style providers that fetch NZB URLs directly from external hosts.
# NZBs must come from the user's Newznab indexers, or from operator-enabled public indexers.
# stremio_nntp uses direct NZB URLs too and should only expose user-indexer-bound
# streams, not provider-specific NZB links (e.g., Easynews/Torbox).
STRICT_INDEXER_BOUND_USENET_PROVIDERS = {"sabnzbd", "nzbget", "nzbdav", "stremio_nntp"}


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _extract_hostname(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url.strip())
    if not parsed.hostname:
        return None
    return parsed.hostname.lower()


def _stream_source_candidates(stream: object) -> set[str]:
    # API schema (UsenetStreamData) has both source and indexer; ORM UsenetStream has indexer only.
    source = getattr(stream, "source", None)
    indexer = getattr(stream, "indexer", None)
    return {
        _normalize_text(source if isinstance(source, str) else None),
        _normalize_text(indexer if isinstance(indexer, str) else None),
    }


def _matches_source_markers(candidates: set[str], markers: set[str]) -> bool:
    for candidate in candidates:
        if not candidate:
            continue
        if any(marker in candidate for marker in markers):
            return True
    return False


def _matches_host_markers(hostname: str | None, markers: set[str]) -> bool:
    return bool(hostname and any(marker in hostname for marker in markers))


@lru_cache(maxsize=1)
def _public_usenet_nzb_hosts() -> frozenset[str]:
    """Hostnames for NZB URLs served by built-in public Usenet indexers (Binsearch, NZBIndex, …)."""
    from scrapers.public_usenet_indexer_registry import ALL_PUBLIC_USENET_INDEXERS, BINSEARCH_BASE

    hosts: set[str] = set()
    for raw in [BINSEARCH_BASE, *[d.site_origin for d in ALL_PUBLIC_USENET_INDEXERS if d.site_origin]]:
        if not raw:
            continue
        parsed = urlparse(raw.strip())
        hn = (parsed.hostname or "").lower()
        if hn:
            hosts.add(hn)
            if hn.startswith("www."):
                hosts.add(hn.removeprefix("www."))
            else:
                hosts.add(f"www.{hn}")
    return frozenset(hosts)


def _stream_matches_public_usenet_indexer(
    stream_source_candidates: set[str],
    stream_host: str | None,
) -> bool:
    if not settings.is_scrap_from_public_usenet_indexers:
        return False

    from scrapers.public_usenet_indexer_registry import ALL_PUBLIC_USENET_INDEXERS

    for definition in ALL_PUBLIC_USENET_INDEXERS:
        if definition.key in stream_source_candidates:
            return True
        normalized_name = _normalize_text(definition.source_name)
        if normalized_name and normalized_name in stream_source_candidates:
            return True

    if stream_host and stream_host.lower() in _public_usenet_nzb_hosts():
        return True
    return False


def _get_enabled_newznab_signatures(user_data: UserData) -> tuple[set[str], set[str]]:
    """Return normalized enabled Newznab indexer names and hosts."""
    names: set[str] = set()
    hosts: set[str] = set()

    indexer_config = user_data.indexer_config
    if not indexer_config or not indexer_config.newznab_indexers:
        return names, hosts

    for indexer in indexer_config.newznab_indexers:
        if not indexer.enabled:
            continue

        normalized_name = _normalize_text(indexer.name)
        if normalized_name:
            names.add(normalized_name)

        host = _extract_hostname(indexer.url)
        if host:
            hosts.add(host)

    return names, hosts


def is_usenet_stream_compatible(
    stream: object,
    streaming_provider: StreamingProvider,
    user_data: UserData,
) -> tuple[bool, str | None]:
    """Validate whether a Usenet stream is compatible with the selected provider."""
    service = streaming_provider.service

    # File-upload NZBs are served from MediaFusion via signed URLs at playback time.
    if not stream.nzb_url:
        return True, None

    stream_source_candidates = _stream_source_candidates(stream)
    stream_host = _extract_hostname(stream.nzb_url)

    # Enforce source-family isolation for provider-bound services.
    required_markers = PROVIDER_BOUND_SOURCE_MARKERS.get(service)
    if required_markers and not (
        _matches_source_markers(stream_source_candidates, required_markers)
        or _matches_host_markers(stream_host, required_markers)
    ):
        return (
            False,
            "This Usenet stream source is not compatible with your selected provider.",
        )

    if service not in STRICT_INDEXER_BOUND_USENET_PROVIDERS:
        return True, None

    allowed_indexer_names, allowed_indexer_hosts = _get_enabled_newznab_signatures(user_data)

    if any(source and source in allowed_indexer_names for source in stream_source_candidates):
        return True, None

    if stream_host and stream_host in allowed_indexer_hosts:
        return True, None

    if _stream_matches_public_usenet_indexer(stream_source_candidates, stream_host):
        return True, None

    if not allowed_indexer_names and not allowed_indexer_hosts:
        return (
            False,
            (
                "No enabled Newznab indexer is configured for this Usenet provider. "
                "Add one under Profile → Indexers, or use NZBs from this instance's public "
                "Usenet indexers when the operator has them enabled."
            ),
        )

    return (
        False,
        "The selected NZB source is not part of your configured Newznab indexers for this Usenet provider.",
    )
