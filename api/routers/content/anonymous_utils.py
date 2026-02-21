"""Helpers for privacy-safe anonymous contribution display names."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.models import User

ANONYMOUS_FALLBACK_NAME = "Anonymous"
ANONYMOUS_NAME_MAX_LENGTH = 32
_ANONYMOUS_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")


def normalize_anonymous_display_name(value: str | None) -> str | None:
    """Normalize a user-provided anonymous display name.

    Returns None when the value is empty or invalid so callers can safely fall back.
    """
    if value is None:
        return None

    normalized = " ".join(value.strip().split())
    if not normalized:
        return None

    if len(normalized) > ANONYMOUS_NAME_MAX_LENGTH:
        return None

    if not _ANONYMOUS_NAME_PATTERN.fullmatch(normalized):
        return None

    return normalized


def resolve_uploader_identity(
    user: "User | None",
    is_anonymous: bool,
    anonymous_display_name: str | None = None,
) -> tuple[str, int | None]:
    """Resolve uploader display name and uploader_user_id for streams."""
    if is_anonymous:
        custom_name = normalize_anonymous_display_name(anonymous_display_name)
        return custom_name or ANONYMOUS_FALLBACK_NAME, None

    if user is None:
        return "Deleted User", None

    return user.username or f"User #{user.id}", user.id
