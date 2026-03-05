"""
Backfill stream language links from parsed torrent names.

Default mode is dry-run. Use --apply to persist changes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import PTT
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import select

# Add project root to import path.
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.crud.reference import get_or_create_language
from db.database import get_async_session_context
from db.models import Stream
from db.models.links import StreamLanguageLink

logger = logging.getLogger("backfill_stream_languages")


@dataclass
class LanguageBackfillCandidate:
    stream_id: int
    source: str
    name: str
    existing_languages: list[str]
    parsed_languages: list[str]


def _normalize_string_list(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        return [part.strip() for part in raw_value.split(",") if part.strip()]
    if isinstance(raw_value, list):
        return [part.strip() for part in raw_value if isinstance(part, str) and part.strip()]
    return []


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _parse_languages_from_name(stream_name: str) -> list[str]:
    parsed = PTT.parse_title(stream_name, True)
    return _dedupe_keep_order(_normalize_string_list(parsed.get("languages")))


async def _load_candidates(args: argparse.Namespace) -> list[LanguageBackfillCandidate]:
    language_count_subquery = (
        select(
            StreamLanguageLink.stream_id,
            func.count(StreamLanguageLink.language_id).label("language_count"),
        )
        .group_by(StreamLanguageLink.stream_id)
        .subquery()
    )

    async with get_async_session_context() as session:
        cutoff_dt = datetime.now(UTC) - timedelta(days=args.last_days)
        query = (
            select(Stream)
            .outerjoin(language_count_subquery, language_count_subquery.c.stream_id == Stream.id)
            .where(func.coalesce(language_count_subquery.c.language_count, 0) == 0)
            .where(Stream.created_at >= cutoff_dt)
            .options(selectinload(Stream.languages))
            .order_by(Stream.created_at.desc())
        )
        if args.sources:
            query = query.where(Stream.source.in_(args.sources))
        if args.limit:
            query = query.limit(args.limit)

        streams = (await session.exec(query)).all()

    candidates: list[LanguageBackfillCandidate] = []
    for stream in streams:
        existing_languages = _dedupe_keep_order([lang.name for lang in (stream.languages or []) if lang.name])
        parsed_languages = _parse_languages_from_name(stream.name or "")
        # Fill only when parser can detect at least one language.
        if not parsed_languages:
            continue
        if existing_languages:
            continue

        candidates.append(
            LanguageBackfillCandidate(
                stream_id=stream.id,
                source=stream.source or "unknown",
                name=stream.name or "",
                existing_languages=existing_languages,
                parsed_languages=parsed_languages,
            )
        )

    return candidates


async def _apply_backfill(candidates: list[LanguageBackfillCandidate]) -> tuple[int, int]:
    updated = 0
    failed = 0
    async with get_async_session_context() as session:
        for candidate in candidates:
            try:
                await session.exec(
                    sa_delete(StreamLanguageLink).where(StreamLanguageLink.stream_id == candidate.stream_id)
                )
                for language_name in candidate.parsed_languages:
                    language = await get_or_create_language(session, language_name)
                    session.add(
                        StreamLanguageLink(
                            stream_id=candidate.stream_id,
                            language_id=language.id,
                        )
                    )
                await session.commit()
                updated += 1
            except Exception as error:
                failed += 1
                logger.warning("Failed stream_id=%s: %s", candidate.stream_id, error)
                await session.rollback()
    return updated, failed


async def run(args: argparse.Namespace) -> None:
    candidates = await _load_candidates(args)
    logger.info("Found %s candidate streams", len(candidates))

    for candidate in candidates[: args.preview]:
        logger.info(
            "stream_id=%s source=%s existing=%s parsed=%s name=%s",
            candidate.stream_id,
            candidate.source,
            candidate.existing_languages,
            candidate.parsed_languages,
            candidate.name,
        )

    if not args.apply:
        logger.info("Dry-run complete. Re-run with --apply to persist changes.")
        return

    updated, failed = await _apply_backfill(candidates)
    logger.info("Backfill complete: updated=%s failed=%s", updated, failed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill stream languages from stream names.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Default is dry-run.",
    )
    parser.add_argument(
        "--last-days",
        type=int,
        default=7,
        help="Only consider streams created within the last N days.",
    )
    parser.add_argument(
        "--sources",
        type=str,
        default="",
        help="Comma-separated source filter (e.g. TamilMV,TamilBlasters).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of streams scanned before filtering (0 = no SQL limit).",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=25,
        help="How many candidate rows to print.",
    )
    parsed = parser.parse_args()
    parsed.sources = [source.strip() for source in parsed.sources.split(",") if source.strip()]
    return parsed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    asyncio.run(run(parse_args()))
