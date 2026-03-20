"""Shared title validation and safety checks for user content imports."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import inspect as sa_inspect

from utils.parser import is_contain_18_plus_keywords


def normalize_import_title(value: str | None) -> str | None:
    """Trim and normalize a user-provided title value."""
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def is_import_metadata_adult(metadata: Any) -> bool:
    """Return True when metadata payload/object indicates adult content."""
    if metadata is None:
        return False

    if isinstance(metadata, dict):
        adult_value = metadata.get("adult")
        genres = metadata.get("genres")
        catalogs = metadata.get("catalogs")
    else:
        adult_value = getattr(metadata, "adult", None)
        insp = sa_inspect(metadata, raiseerr=False)
        if insp is not None and insp.mapper is not None:
            unloaded = insp.unloaded
            genres = None if (unloaded and "genres" in unloaded) else getattr(metadata, "genres", None)
            catalogs = None if (unloaded and "catalogs" in unloaded) else getattr(metadata, "catalogs", None)
        else:
            genres = getattr(metadata, "genres", None)
            catalogs = getattr(metadata, "catalogs", None)

    if isinstance(adult_value, bool) and adult_value:
        return True
    if isinstance(adult_value, str) and adult_value.strip().lower() in {"1", "true", "yes"}:
        return True

    if isinstance(genres, list):
        for genre in genres:
            if isinstance(genre, str) and genre.strip().lower() == "adult":
                return True

    if isinstance(catalogs, list):
        for catalog in catalogs:
            if isinstance(catalog, str) and catalog.strip().lower() == "adult":
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
