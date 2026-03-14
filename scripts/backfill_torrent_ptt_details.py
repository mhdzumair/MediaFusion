"""
Backfill missing torrent stream technical metadata from PTT parsing.

Default mode is dry-run. Use --apply to persist changes.

Safety-first defaults:
- Scalar fields are filled only when blank.
- Relation links (languages/audio/channels/hdr) are added only when currently empty.
- Boolean flags are NOT backfilled unless --include-flags is set.

Use --overwrite-scalars and/or --merge-relations for more aggressive behavior.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import PTT
from sqlalchemy.orm import selectinload
from sqlmodel import select

# Add project root to import path.
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.crud.reference import (
    get_or_create_audio_channel,
    get_or_create_audio_format,
    get_or_create_hdr_format,
    get_or_create_language,
)
from db.database import get_async_session_context
from db.models import Stream, TorrentStream
from db.models.links import StreamAudioLink, StreamChannelLink, StreamHDRLink, StreamLanguageLink
from db.models.streams import StreamType

logger = logging.getLogger("backfill_torrent_ptt_details")

SCALAR_FIELD_MAPPING: dict[str, str] = {
    "resolution": "resolution",
    "quality": "quality",
    "codec": "codec",
    "bit_depth": "bit_depth",
    "release_group": "group",
}

FLAG_FIELD_MAPPING: dict[str, str] = {
    "is_remastered": "remastered",
    "is_upscaled": "upscaled",
    "is_proper": "proper",
    "is_repack": "repack",
    "is_extended": "extended",
    "is_complete": "complete",
    "is_dubbed": "dubbed",
    "is_subbed": "subbed",
}


@dataclass
class BackfillPlan:
    stream_id: int
    source: str
    name: str
    scalar_updates: dict[str, str] = field(default_factory=dict)
    flag_updates: dict[str, bool] = field(default_factory=dict)
    add_languages: list[str] = field(default_factory=list)
    add_audio_formats: list[str] = field(default_factory=list)
    add_channels: list[str] = field(default_factory=list)
    add_hdr_formats: list[str] = field(default_factory=list)
    parsed_snapshot: dict[str, Any] = field(default_factory=dict)

    def has_changes(self) -> bool:
        return bool(
            self.scalar_updates
            or self.flag_updates
            or self.add_languages
            or self.add_audio_formats
            or self.add_channels
            or self.add_hdr_formats
        )


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _normalize_string_list(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        return [part.strip() for part in raw_value.split(",") if part.strip()]
    if isinstance(raw_value, list):
        return [part.strip() for part in raw_value if isinstance(part, str) and part.strip()]
    return []


def _dedupe_keep_order(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _normalize_ptt_text_value(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        cleaned = raw_value.strip()
        return cleaned or None
    cleaned = str(raw_value).strip()
    return cleaned or None


def _safe_parse_ptt(stream_name: str) -> dict[str, Any]:
    try:
        parsed = PTT.parse_title(stream_name or "", True)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _pick_relation_additions(
    *,
    parsed_values: list[str],
    existing_values: set[str],
    merge_relations: bool,
) -> list[str]:
    if not parsed_values:
        return []

    if merge_relations:
        return [value for value in parsed_values if value not in existing_values]

    # Safety default: only fill relation links when no values exist yet.
    if existing_values:
        return []
    return parsed_values


def _build_plan_for_stream(stream: Stream, parsed: dict[str, Any], args: argparse.Namespace) -> BackfillPlan:
    existing_languages = _dedupe_keep_order([lang.name for lang in (stream.languages or []) if lang.name])
    existing_audio_formats = _dedupe_keep_order([fmt.name for fmt in (stream.audio_formats or []) if fmt.name])
    existing_channels = _dedupe_keep_order([ch.name for ch in (stream.channels or []) if ch.name])
    existing_hdr_formats = _dedupe_keep_order([hdr.name for hdr in (stream.hdr_formats or []) if hdr.name])

    parsed_languages = _dedupe_keep_order(_normalize_string_list(parsed.get("languages")))
    parsed_audio_formats = _dedupe_keep_order(_normalize_string_list(parsed.get("audio")))
    parsed_channels = _dedupe_keep_order(_normalize_string_list(parsed.get("channels")))
    parsed_hdr_formats = _dedupe_keep_order(_normalize_string_list(parsed.get("hdr")))

    plan = BackfillPlan(
        stream_id=stream.id,
        source=stream.source or "unknown",
        name=stream.name or "",
        parsed_snapshot={
            "resolution": parsed.get("resolution"),
            "quality": parsed.get("quality"),
            "codec": parsed.get("codec"),
            "bit_depth": parsed.get("bit_depth"),
            "group": parsed.get("group"),
            "languages": parsed_languages,
            "audio": parsed_audio_formats,
            "channels": parsed_channels,
            "hdr": parsed_hdr_formats,
        },
    )

    for stream_field, parsed_key in SCALAR_FIELD_MAPPING.items():
        parsed_value = _normalize_ptt_text_value(parsed.get(parsed_key))
        if not parsed_value:
            continue

        current_value = getattr(stream, stream_field)
        if args.overwrite_scalars:
            if current_value != parsed_value:
                plan.scalar_updates[stream_field] = parsed_value
            continue

        if _is_blank(current_value):
            plan.scalar_updates[stream_field] = parsed_value

    if args.include_flags:
        for stream_field, parsed_key in FLAG_FIELD_MAPPING.items():
            parsed_value = bool(parsed.get(parsed_key, False))
            # Conservative even when include_flags is enabled:
            # never force False, only fill missing-positive signal.
            if parsed_value and not bool(getattr(stream, stream_field, False)):
                plan.flag_updates[stream_field] = True

    plan.add_languages = _pick_relation_additions(
        parsed_values=parsed_languages,
        existing_values=set(existing_languages),
        merge_relations=args.merge_relations,
    )
    plan.add_audio_formats = _pick_relation_additions(
        parsed_values=parsed_audio_formats,
        existing_values=set(existing_audio_formats),
        merge_relations=args.merge_relations,
    )
    plan.add_channels = _pick_relation_additions(
        parsed_values=parsed_channels,
        existing_values=set(existing_channels),
        merge_relations=args.merge_relations,
    )
    plan.add_hdr_formats = _pick_relation_additions(
        parsed_values=parsed_hdr_formats,
        existing_values=set(existing_hdr_formats),
        merge_relations=args.merge_relations,
    )

    return plan


def _parse_csv_sources(raw_value: str) -> list[str]:
    return [part.strip() for part in raw_value.split(",") if part.strip()]


def _parse_csv_ints(raw_value: str) -> list[int]:
    if not raw_value.strip():
        return []

    parsed: list[int] = []
    for part in raw_value.split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        try:
            parsed.append(int(cleaned))
        except ValueError:
            logger.warning("Ignoring invalid stream id: %s", cleaned)
    return parsed


def _build_stream_query(
    args: argparse.Namespace,
    *,
    cutoff_dt: datetime | None,
    last_seen_stream_id: int | None = None,
    batch_limit: int | None = None,
):
    query = (
        select(Stream)
        .join(TorrentStream, TorrentStream.stream_id == Stream.id)
        .where(Stream.stream_type == StreamType.TORRENT)
        .options(
            selectinload(Stream.languages),
            selectinload(Stream.audio_formats),
            selectinload(Stream.channels),
            selectinload(Stream.hdr_formats),
        )
        .order_by(Stream.id.desc())
    )

    if args.sources:
        query = query.where(Stream.source.in_(args.sources))
    if args.stream_ids:
        query = query.where(Stream.id.in_(args.stream_ids))
    if cutoff_dt:
        query = query.where(Stream.created_at >= cutoff_dt)
    if last_seen_stream_id is not None:
        query = query.where(Stream.id < last_seen_stream_id)
    if batch_limit and batch_limit > 0:
        query = query.limit(batch_limit)

    return query


async def _apply_plan_for_stream(
    session,
    stream: Stream,
    plan: BackfillPlan,
    stats: dict[str, int],
) -> None:
    stream_changed = False

    for field_name, field_value in plan.scalar_updates.items():
        if getattr(stream, field_name) != field_value:
            setattr(stream, field_name, field_value)
            stats["scalar_updates"] += 1
            stream_changed = True

    for field_name, field_value in plan.flag_updates.items():
        if bool(getattr(stream, field_name)) is False and field_value is True:
            setattr(stream, field_name, True)
            stats["flag_updates"] += 1
            stream_changed = True

    existing_languages = {lang.name for lang in (stream.languages or []) if lang.name}
    for language_name in plan.add_languages:
        if language_name in existing_languages:
            continue
        language = await get_or_create_language(session, language_name)
        session.add(StreamLanguageLink(stream_id=stream.id, language_id=language.id))
        existing_languages.add(language_name)
        stats["language_links_added"] += 1
        stream_changed = True

    existing_audio_formats = {fmt.name for fmt in (stream.audio_formats or []) if fmt.name}
    for audio_name in plan.add_audio_formats:
        if audio_name in existing_audio_formats:
            continue
        audio_format = await get_or_create_audio_format(session, audio_name)
        session.add(StreamAudioLink(stream_id=stream.id, audio_format_id=audio_format.id))
        existing_audio_formats.add(audio_name)
        stats["audio_links_added"] += 1
        stream_changed = True

    existing_channels = {channel.name for channel in (stream.channels or []) if channel.name}
    for channel_name in plan.add_channels:
        if channel_name in existing_channels:
            continue
        channel = await get_or_create_audio_channel(session, channel_name)
        session.add(StreamChannelLink(stream_id=stream.id, channel_id=channel.id))
        existing_channels.add(channel_name)
        stats["channel_links_added"] += 1
        stream_changed = True

    existing_hdr_formats = {hdr.name for hdr in (stream.hdr_formats or []) if hdr.name}
    for hdr_name in plan.add_hdr_formats:
        if hdr_name in existing_hdr_formats:
            continue
        hdr_format = await get_or_create_hdr_format(session, hdr_name)
        session.add(StreamHDRLink(stream_id=stream.id, hdr_format_id=hdr_format.id))
        existing_hdr_formats.add(hdr_name)
        stats["hdr_links_added"] += 1
        stream_changed = True

    if stream_changed:
        await session.commit()
        stats["updated_streams"] += 1
        return
    await session.rollback()


async def _scan_and_maybe_apply(args: argparse.Namespace) -> tuple[dict[str, int], dict[str, int], list[BackfillPlan]]:
    discovery_stats = {
        "scanned": 0,
        "parse_empty": 0,
        "parse_failed_or_empty": 0,
        "planned": 0,
    }
    apply_stats = {
        "updated_streams": 0,
        "failed_streams": 0,
        "scalar_updates": 0,
        "flag_updates": 0,
        "language_links_added": 0,
        "audio_links_added": 0,
        "channel_links_added": 0,
        "hdr_links_added": 0,
    }
    preview_plans: list[BackfillPlan] = []

    cutoff_dt: datetime | None = None
    if args.last_days > 0:
        cutoff_dt = datetime.now(UTC) - timedelta(days=args.last_days)

    remaining = args.limit if args.limit > 0 else None
    last_seen_stream_id: int | None = None

    while True:
        current_batch_limit = args.batch_size
        if remaining is not None:
            if remaining <= 0:
                break
            current_batch_limit = min(current_batch_limit, remaining)

        async with get_async_session_context() as session:
            query = _build_stream_query(
                args,
                cutoff_dt=cutoff_dt,
                last_seen_stream_id=last_seen_stream_id,
                batch_limit=current_batch_limit,
            )
            streams = (await session.exec(query)).all()
            if not streams:
                break

            last_seen_stream_id = streams[-1].id
            if remaining is not None:
                remaining -= len(streams)

            for stream in streams:
                discovery_stats["scanned"] += 1
                parsed = _safe_parse_ptt(stream.name or "")
                if not parsed:
                    discovery_stats["parse_failed_or_empty"] += 1
                    continue

                has_any_supported_key = any(
                    parsed.get(key)
                    for key in (
                        "resolution",
                        "quality",
                        "codec",
                        "bit_depth",
                        "group",
                        "languages",
                        "audio",
                        "channels",
                        "hdr",
                        *FLAG_FIELD_MAPPING.values(),
                    )
                )
                if not has_any_supported_key:
                    discovery_stats["parse_empty"] += 1
                    continue

                plan = _build_plan_for_stream(stream, parsed, args)
                if not plan.has_changes():
                    continue

                discovery_stats["planned"] += 1
                if len(preview_plans) < args.preview:
                    preview_plans.append(plan)

                if not args.apply:
                    continue

                try:
                    await _apply_plan_for_stream(session, stream, plan, apply_stats)
                except Exception as error:
                    await session.rollback()
                    apply_stats["failed_streams"] += 1
                    logger.warning("Failed stream_id=%s: %s", plan.stream_id, error)

    return discovery_stats, apply_stats, preview_plans


def _log_preview(plans: list[BackfillPlan], preview_count: int) -> None:
    if preview_count <= 0:
        return
    for plan in plans[:preview_count]:
        logger.info(
            "stream_id=%s source=%s scalar=%s flags=%s add_languages=%s add_audio=%s add_channels=%s add_hdr=%s name=%s",
            plan.stream_id,
            plan.source,
            plan.scalar_updates,
            plan.flag_updates,
            plan.add_languages,
            plan.add_audio_formats,
            plan.add_channels,
            plan.add_hdr_formats,
            plan.name,
        )


async def run(args: argparse.Namespace) -> None:
    discovery_stats, apply_stats, preview_plans = await _scan_and_maybe_apply(args)
    logger.info("Discovery stats: %s", discovery_stats)
    logger.info("Planned updates: %s streams", discovery_stats["planned"])

    _log_preview(preview_plans, args.preview)

    if not args.apply:
        logger.info("Dry-run complete. Re-run with --apply to persist changes.")
        return

    logger.info("Apply stats: %s", apply_stats)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing torrent stream technical metadata from PTT parsing.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Default is dry-run.",
    )
    parser.add_argument(
        "--sources",
        type=str,
        default="Contribution Stream",
        help=(
            "Comma-separated stream.source values to target "
            '(default: "Contribution Stream"). Use empty string to target all sources.'
        ),
    )
    parser.add_argument(
        "--stream-ids",
        type=str,
        default="",
        help="Optional comma-separated stream IDs to target.",
    )
    parser.add_argument(
        "--last-days",
        type=int,
        default=0,
        help="Only consider streams created within the last N days (0 = all time).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit SQL rows scanned before in-memory filtering (0 = no SQL limit).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of rows fetched per SQL batch while scanning (default: 500).",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=25,
        help="How many planned rows to print in preview output.",
    )
    parser.add_argument(
        "--overwrite-scalars",
        action="store_true",
        help="Allow replacing non-empty scalar values (resolution/quality/codec/bit_depth/release_group).",
    )
    parser.add_argument(
        "--merge-relations",
        action="store_true",
        help="Allow adding parsed relation values even when relation already has values.",
    )
    parser.add_argument(
        "--include-flags",
        action="store_true",
        help="Backfill boolean flags (only False -> True, conservative behavior).",
    )

    parsed = parser.parse_args()
    if parsed.batch_size <= 0:
        parser.error("--batch-size must be greater than 0")
    parsed.sources = _parse_csv_sources(parsed.sources)
    parsed.stream_ids = _parse_csv_ints(parsed.stream_ids)
    return parsed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    asyncio.run(run(parse_args()))
