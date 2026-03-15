"""Usenet stream compatibility checks across providers and indexers."""

from urllib.parse import urlparse

from db.schemas import StreamingProvider, UserData
from db.schemas.media import UsenetStreamData

# Providers that are bound to a specific Usenet source family.
# These providers should not be offered streams scraped from unrelated sources.
PROVIDER_BOUND_SOURCE_MARKERS: dict[str, set[str]] = {
    "easynews": {"easynews"},
    "torbox": {"torbox"},
}

# Downloader-style providers that fetch NZB URLs directly from external hosts.
# These providers should only consume NZBs from user-configured indexers.
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


def _stream_source_candidates(stream: UsenetStreamData) -> set[str]:
    return {
        _normalize_text(stream.source),
        _normalize_text(stream.indexer),
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
    stream: UsenetStreamData,
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
    if not allowed_indexer_names and not allowed_indexer_hosts:
        return (
            False,
            (
                "No enabled Newznab indexer is configured for this Usenet provider. "
                "Add at least one indexer in Profile -> Indexers."
            ),
        )

    if any(source and source in allowed_indexer_names for source in stream_source_candidates):
        return True, None

    if stream_host and stream_host in allowed_indexer_hosts:
        return True, None

    return (
        False,
        ("The selected NZB source is not part of your configured Newznab indexers for this Usenet provider."),
    )
