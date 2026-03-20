"""Shared filename / season-episode / video-extension logic for Usenet file lists.

Used by Debridr, TorBox, SABnzbd, NZBGet, and Easynews (search rows) so behavior stays aligned.

Season/episode detection uses the same PTT + ``fallback_parse_season_episode`` stack as
``streaming_providers.parser`` (SxxEyy, 1x01, text patterns, anime heuristics, etc.).

Date-based releases (e.g. talk shows) can be matched with ``episode_air_date`` (YYYY-MM-DD)
via PTT's ``date`` field and ``DATE_STR_REGEX`` + dateparser.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import dateparser
import PTT

from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import fallback_parse_season_episode
from utils.runtime_const import DATE_STR_REGEX

USENET_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".webm"},
)

_NO_VIDEO_IN_USENET_LIST = (
    "No video file found in this Usenet download (only non-video files present).",
    "no_video_file_found.mp4",
)


def is_usenet_video_basename(filename: str) -> bool:
    """True if the basename ends with a known video extension."""
    name = (filename or "").lower()
    return any(name.endswith(ext) for ext in USENET_VIDEO_EXTENSIONS)


def normalize_extension_field(ext: str | None) -> str:
    """Normalize API extension to a dotted lowercase form (e.g. mkv -> .mkv)."""
    if not ext or not str(ext).strip():
        return ""
    e = str(ext).strip().lower()
    return e if e.startswith(".") else f".{e}"


def easynews_row_looks_like_video(row: dict[str, Any]) -> bool:
    """Easynews search row: extension field and/or filename indicate video."""
    dotted = normalize_extension_field(row.get("extension"))
    if dotted and dotted in USENET_VIDEO_EXTENSIONS:
        return True
    fname = row.get("filename") or ""
    return is_usenet_video_basename(fname)


def easynews_episode_match_text(row: dict[str, Any]) -> str:
    """String to parse for season/episode or air date (filename preferred, else subject)."""
    return (row.get("filename") or "") or (row.get("subject") or "")


def _normalize_calendar_date(value: str | date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        dt = dateparser.parse(s)
        return dt.date().isoformat() if dt else None
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None


def extract_air_date_from_label(label: str) -> str | None:
    """Best-effort YYYY-MM-DD from release name (PTT date, then DATE_STR_REGEX)."""
    text = (label or "").strip()
    if not text:
        return None

    parsed = PTT.parse_title(text)
    raw_date = parsed.get("date")
    normalized = _normalize_calendar_date(raw_date)
    if normalized:
        return normalized

    match = DATE_STR_REGEX.search(text.replace("\\", "/"))
    if not match:
        return None
    try:
        dt = dateparser.parse(match.group(0))
        return dt.date().isoformat() if dt else None
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None


def usenet_label_matches_air_date(label: str, air_date_iso: str) -> bool:
    """True if ``label`` contains a calendar date equal to ``air_date_iso`` (YYYY-MM-DD)."""
    target = _normalize_calendar_date(air_date_iso)
    if not target:
        return False
    extracted = extract_air_date_from_label(label)
    return bool(extracted and extracted == target)


def usenet_label_matches_season_episode(label: str, season: int, episode: int) -> bool:
    """True if ``label`` parses to this season/episode (PTT + torrent-style fallback patterns)."""
    text = (label or "").strip()
    if not text:
        return False

    parsed = PTT.parse_title(text)
    seasons = parsed.get("seasons") or []
    episodes = parsed.get("episodes") or []
    if seasons and episodes:
        return seasons[0] == season and episodes[0] == episode
    if not seasons and episodes:
        # Episode-only in title; assume catalog season from playback context.
        return episodes[0] == episode

    fs, fe = fallback_parse_season_episode(text, default_season=season)
    if fs is None or fe is None:
        return False
    return fs == season and fe == episode


def select_usenet_file_index(
    files: list[dict[str, Any]],
    *,
    filename: str | None,
    season: int | None,
    episode: int | None,
    display_name: Callable[[dict[str, Any]], str],
    size_key: str = "size",
    match_path_suffix: bool = False,
    episode_air_date: str | None = None,
) -> int:
    """Pick index among **video** files only: exact name, season/episode, optional air date, else largest.

    Never returns a non-video path. If nothing qualifies, raises ``ProviderException``.

    ``episode_air_date``: optional ``YYYY-MM-DD`` for dated releases (e.g. late-night shows).
    """
    if not files:
        raise ValueError("files must be non-empty")

    if filename and filename.strip():
        want = filename.strip().lower()
        for idx, f in enumerate(files):
            label = display_name(f)
            if not is_usenet_video_basename(label):
                continue
            ll = label.lower()
            if ll == want or (match_path_suffix and ll.endswith(f"/{want}")):
                return idx

    if season is not None and episode is not None:
        for idx, f in enumerate(files):
            label = display_name(f)
            if not is_usenet_video_basename(label):
                continue
            if usenet_label_matches_season_episode(label, season, episode):
                return idx

    if episode_air_date:
        for idx, f in enumerate(files):
            label = display_name(f)
            if not is_usenet_video_basename(label):
                continue
            if usenet_label_matches_air_date(label, episode_air_date):
                return idx

    def _size(i: int) -> int:
        raw = files[i].get(size_key, 0)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    video_indices = [i for i, f in enumerate(files) if is_usenet_video_basename(display_name(f))]
    if not video_indices:
        raise ProviderException(*_NO_VIDEO_IN_USENET_LIST)
    return max(video_indices, key=_size)


def select_usenet_file_dict(
    files: list[dict[str, Any]],
    *,
    filename: str | None,
    season: int | None,
    episode: int | None,
    display_name: Callable[[dict[str, Any]], str],
    size_key: str = "size",
    match_path_suffix: bool = False,
    episode_air_date: str | None = None,
) -> dict[str, Any] | None:
    """Same rules as ``select_usenet_file_index`` but return the file dict (or None if empty)."""
    if not files:
        return None
    idx = select_usenet_file_index(
        files,
        filename=filename,
        season=season,
        episode=episode,
        display_name=display_name,
        size_key=size_key,
        match_path_suffix=match_path_suffix,
        episode_air_date=episode_air_date,
    )
    return files[idx]
