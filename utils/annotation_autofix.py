"""Helpers for auto-fixing series file annotations from filename patterns."""

import re

import PTT
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.models import FileMediaLink, StreamFile
from utils.validation_helper import is_video_file

_SEASON_EPISODE_FALLBACK_REGEXES = (
    re.compile(r"\bS(\d{1,3})\s*E(\d{1,3})(?:\s*(?:E|[-~])\s*(\d{1,3}))?\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})x(\d{1,3})(?:\s*-\s*(\d{1,3}))?\b", re.IGNORECASE),
)

# Bonus/extra file patterns — these files don't need episode annotation.
# Matched against the basename (without path).
_EXTRA_FILE_PATTERNS = (
    re.compile(r"\b(creditless|clean)\b", re.IGNORECASE),  # "Creditless Opening 1"
    re.compile(r"\b(ncop|nced|nc[\s\-]?op|nc[\s\-]?ed)\b", re.IGNORECASE),
    re.compile(r"\b(pv|cm)\s*\d*\b", re.IGNORECASE),  # "PV 1", "CM2"
    re.compile(r"\b(preview|promo|trailer|menu|digest)\b", re.IGNORECASE),
    # "Movie 01", "Film 2" — movie-within-series-pack extras.
    # Only triggers when a small number follows "movie"/"film" (not a year like 2024).
    re.compile(r"\b(movie|film)\s+0*[1-9]\d?\b", re.IGNORECASE),
)

# Regex used in the SQL query (PostgreSQL ~* operator) to exclude the same extras.
# Kept in sync with _EXTRA_FILE_PATTERNS above.
EXTRA_FILE_SQL_PATTERN = (
    r"(creditless|clean|ncop|nced|nc[\s\-]?(op|ed)"
    r"|\bpv\s*\d*\b|\bcm\s*\d*\b"
    r"|preview|promo|trailer|menu|digest"
    r"|(movie|film) +0*[1-9][0-9]?\b)"
)


def is_non_episode_extra(filename: str) -> bool:
    """Return True if the file is a bonus/extra that doesn't need episode annotation."""
    base = filename.rsplit("/", 1)[-1] if "/" in filename else filename
    base_no_ext = base.rsplit(".", 1)[0] if "." in base else base
    return any(p.search(base_no_ext) for p in _EXTRA_FILE_PATTERNS)


def infer_episode_mapping_from_filename(filename: str) -> tuple[int | None, int | None, int | None]:
    """Infer season/episode mapping from filename using parser + regex fallback.

    For anime releases that omit the season (e.g. "Series - 08 (1080p)"), PTT will
    find an episode number but no season; we default such files to season 1.
    """
    parsed = PTT.parse_title(filename, True)
    seasons = parsed.get("seasons", [])
    episodes = parsed.get("episodes", [])

    season_number = seasons[0] if seasons else None
    episode_number = episodes[0] if episodes else None
    episode_end = episodes[-1] if len(episodes) > 1 else None

    if season_number is not None and episode_number is not None:
        return season_number, episode_number, episode_end

    # Episode found but no season — common in anime releases where files are numbered
    # sequentially without an explicit season indicator (e.g. "[SubsPlease] Series - 08").
    # Default to season 1 unless the filename matches a known extra/bonus pattern.
    if episode_number is not None and not is_non_episode_extra(filename):
        return 1, episode_number, episode_end

    for pattern in _SEASON_EPISODE_FALLBACK_REGEXES:
        match = pattern.search(filename)
        if not match:
            continue
        season_number = int(match.group(1))
        episode_number = int(match.group(2))
        episode_end = int(match.group(3)) if match.group(3) else None
        return season_number, episode_number, episode_end

    return None, None, None


async def auto_map_episode_links_from_filename(
    session: AsyncSession,
    stream_id: int,
    media_id: int,
    apply_changes: bool = True,
) -> tuple[bool, int, int]:
    """Auto-populate missing file mappings from filename patterns.

    Returns:
        tuple[bool, int, int]:
            - bool: True when all relevant files for this stream/media pair are mapped
              with non-null season/episode values
            - int: number of mapping changes detected/applied
            - int: number of relevant video files considered
    """
    files_result = await session.exec(select(StreamFile).where(StreamFile.stream_id == stream_id))
    stream_files = files_result.all()
    if not stream_files:
        return False, 0, 0

    file_ids = [stream_file.id for stream_file in stream_files]
    links_result = await session.exec(
        select(FileMediaLink).where(
            FileMediaLink.media_id == media_id,
            FileMediaLink.file_id.in_(file_ids),
        )
    )
    existing_links = {link.file_id: link for link in links_result.all()}

    change_count = 0
    unresolved_files = 0
    relevant_video_files = 0

    for stream_file in stream_files:
        filename = stream_file.filename or ""
        if not is_video_file(filename):
            continue
        if "sample" in filename.lower():
            continue
        # Bonus/extra files (OP/ED credits, movie compilations) don't require
        # episode annotation — skip them entirely.
        if is_non_episode_extra(filename):
            continue
        relevant_video_files += 1

        existing_link = existing_links.get(stream_file.id)
        if existing_link and existing_link.episode_number is not None and existing_link.season_number is not None:
            continue

        season_number, episode_number, episode_end = infer_episode_mapping_from_filename(filename)
        if season_number is None or episode_number is None:
            unresolved_files += 1
            continue

        if existing_link:
            link_updated = False
            if existing_link.season_number is None:
                existing_link.season_number = season_number
                link_updated = True
            if existing_link.episode_number is None:
                existing_link.episode_number = episode_number
                link_updated = True
            if existing_link.episode_end is None and episode_end is not None:
                existing_link.episode_end = episode_end
                link_updated = True
            if link_updated:
                change_count += 1
            if apply_changes and link_updated:
                session.add(existing_link)
        else:
            change_count += 1
            if apply_changes:
                session.add(
                    FileMediaLink(
                        file_id=stream_file.id,
                        media_id=media_id,
                        season_number=season_number,
                        episode_number=episode_number,
                        episode_end=episode_end,
                    )
                )

    if relevant_video_files == 0:
        # No playable non-extra video files in this pair -> not an actionable annotation request.
        return True, change_count, 0

    return unresolved_files == 0, change_count, relevant_video_files
