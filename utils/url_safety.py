"""URL safety helpers for credential-bearing links."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_QUERY_KEYS = {
    "apikey",
    "api_key",
    "token",
    "auth",
    "authorization",
    "passkey",
    "password",
    "pwd",
    "username",
    "user",
    "rsskey",
    "key",
    "secret",
}


def url_has_userinfo(url: str | None) -> bool:
    """Return True when URL authority contains embedded userinfo."""
    if not url or not isinstance(url, str):
        return False

    raw_url = url.strip()
    if not raw_url:
        return False

    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return False

    return "@" in (parts.netloc or "")


def url_has_sensitive_query_params(url: str | None) -> bool:
    """Return True when URL query contains credential-like params."""
    if not url or not isinstance(url, str):
        return False

    raw_url = url.strip()
    if not raw_url:
        return False

    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return False

    query_items = parse_qsl(parts.query, keep_blank_values=True)
    return any((key or "").strip().lower() in SENSITIVE_QUERY_KEYS for key, _ in query_items)


def sanitize_nzb_url(url: str | None) -> str | None:
    """Strip embedded username/password from URL authority.

    Sensitive query keys are stripped to avoid persisting credentials.
    """
    if not url or not isinstance(url, str):
        return None

    raw_url = url.strip()
    if not raw_url:
        return None

    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return raw_url

    netloc = parts.netloc or ""
    sanitized_netloc = netloc.rsplit("@", 1)[1] if "@" in netloc else netloc

    query_items = parse_qsl(parts.query, keep_blank_values=True)
    safe_query_items = [
        (key, value) for key, value in query_items if (key or "").strip().lower() not in SENSITIVE_QUERY_KEYS
    ]
    sanitized_query = urlencode(safe_query_items, doseq=True)

    return urlunsplit((parts.scheme, sanitized_netloc, parts.path, sanitized_query, parts.fragment))
