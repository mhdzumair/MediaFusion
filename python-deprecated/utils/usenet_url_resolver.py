"""Build user-scoped NZB URLs without storing credentials in DB."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from db.schemas import UserData
from db.schemas.media import UsenetStreamData
from utils.url_safety import SENSITIVE_QUERY_KEYS, sanitize_nzb_url


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def _extract_host(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""


def _find_matching_newznab_indexer(stream: UsenetStreamData, user_data: UserData):
    indexer_config = user_data.indexer_config
    if not indexer_config or not indexer_config.newznab_indexers:
        return None

    enabled_indexers = [indexer for indexer in indexer_config.newznab_indexers if indexer.enabled]
    if not enabled_indexers:
        return None

    source_candidates = {
        _normalize(stream.indexer),
        _normalize(stream.source),
    }
    for indexer in enabled_indexers:
        if _normalize(indexer.name) in source_candidates:
            return indexer

    stream_host = _extract_host(stream.nzb_url)
    if stream_host:
        for indexer in enabled_indexers:
            if _extract_host(indexer.url) == stream_host:
                return indexer

    return None


def build_user_scoped_nzb_url(stream: UsenetStreamData, user_data: UserData) -> str | None:
    """Resolve a playback-safe NZB URL for this specific user.

    DB/cache should only contain sanitized URLs. At request time we may need to
    re-attach a user's own API key for Newznab indexers.
    """
    safe_url = sanitize_nzb_url(stream.nzb_url)
    if not safe_url:
        return None

    indexer = _find_matching_newznab_indexer(stream, user_data)
    if not indexer:
        return safe_url

    try:
        stream_parts = urlsplit(safe_url)
    except ValueError:
        return safe_url

    # Keep path/query from persisted safe URL, but use user's configured netloc.
    indexer_parts = urlsplit(str(indexer.url).strip())
    netloc = indexer_parts.netloc or stream_parts.netloc

    query_items = parse_qsl(stream_parts.query, keep_blank_values=True)
    query_dict: dict[str, str] = {}
    for key, value in query_items:
        lowered = (key or "").strip().lower()
        if lowered in SENSITIVE_QUERY_KEYS:
            continue
        query_dict[key] = value

    if indexer.api_key:
        query_dict["apikey"] = indexer.api_key

    rebuilt_query = urlencode(query_dict, doseq=True)
    return urlunsplit(
        (
            stream_parts.scheme or indexer_parts.scheme,
            netloc,
            stream_parts.path,
            rebuilt_query,
            stream_parts.fragment,
        )
    )


def apply_user_scoped_nzb_urls(streams: list[UsenetStreamData], user_data: UserData) -> list[UsenetStreamData]:
    """Mutate stream list in-place with user-scoped NZB URLs."""
    for stream in streams:
        stream.nzb_url = build_user_scoped_nzb_url(stream, user_data)
    return streams
