"""Shared title validation and safety checks for user content imports."""

from collections.abc import Iterable
from typing import Any

from utils.parser import is_contain_18_plus_keywords


def normalize_import_title(value: str | None) -> str | None:
    """Trim and normalize a user-provided title value."""
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def stream_texts_indicate_adult(*texts: str | None) -> bool:
    """Return True if any non-empty string matches configured adult torrent/stream title rules."""
    for raw in texts:
        if not raw:
            continue
        normalized = normalize_import_title(raw)
        if normalized and is_contain_18_plus_keywords(normalized):
            return True
    return False


def stream_texts_indicate_adult_from_file_rows(file_rows: Iterable[dict[str, Any]] | None) -> bool:
    """Check per-file fields (filename, meta_title, episode_title, title) from import file_data payloads."""
    if not file_rows:
        return False
    for row in file_rows:
        if not isinstance(row, dict):
            continue
        texts: list[str | None] = []
        for key in ("filename", "meta_title", "episode_title", "title"):
            val = row.get(key)
            texts.append(val if isinstance(val, str) else None)
        if stream_texts_indicate_adult(*texts):
            return True
    return False


def resolve_and_validate_import_title(
    explicit_title: str | None,
    fallback_title: str | None,
    *,
    additional_titles: Iterable[str | None] | None = None,
) -> tuple[str | None, str | None]:
    """Return normalized title and optional validation error."""
    normalized_explicit = normalize_import_title(explicit_title)
    normalized_fallback = normalize_import_title(fallback_title)
    resolved_title = normalized_explicit or normalized_fallback

    if resolved_title is None:
        return None, "Title is required for this contribution."

    if is_contain_18_plus_keywords(resolved_title):
        return None, "Adult content titles are not allowed in user contributions."

    if additional_titles:
        for raw_title in additional_titles:
            normalized_title = normalize_import_title(raw_title)
            if normalized_title and is_contain_18_plus_keywords(normalized_title):
                return None, "Adult content titles are not allowed in user contributions."

    return resolved_title, None
