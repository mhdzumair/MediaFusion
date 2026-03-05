"""Shared title validation for user content imports."""

from utils.parser import is_contain_18_plus_keywords


def normalize_import_title(value: str | None) -> str | None:
    """Trim and normalize a user-provided title value."""
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def resolve_and_validate_import_title(
    explicit_title: str | None,
    fallback_title: str | None,
) -> tuple[str | None, str | None]:
    """Return normalized title and optional validation error."""
    normalized_explicit = normalize_import_title(explicit_title)
    normalized_fallback = normalize_import_title(fallback_title)
    resolved_title = normalized_explicit or normalized_fallback

    if resolved_title is None:
        return None, "Title is required for this contribution."

    if is_contain_18_plus_keywords(resolved_title):
        return None, "Adult content titles are not allowed in user contributions."

    return resolved_title, None
