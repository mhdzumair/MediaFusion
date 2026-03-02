"""
Deduplicate non-user-created media entries with strict safety checks.

This script only migrates duplicates when an external-ID anchor is clear:
- A cluster is grouped by type/title/year.
- Exactly one media item in the cluster has external IDs (target anchor).
- Sources are media rows with no external IDs (or explicit shared-ID sources).

Default mode is dry-run. Use --apply to execute migrations.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

# Add project root to import path.
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.crud.media import get_canonical_external_id, invalidate_meta_cache
from db.crud.scraper_helpers import delete_metadata, migrate_media_links, update_meta_stream
from db.crud.stream_services import invalidate_media_stream_cache
from db.database import get_async_session_context
from db.enums import MediaType
from db.models import FileMediaLink, Media, MediaExternalID, StreamMediaLink

logger = logging.getLogger("deduplicate_media")


def extract_count_value(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value[0])
    except (TypeError, ValueError, IndexError, KeyError):
        return int(value)


@dataclass
class MediaRow:
    media_id: int
    media_type: MediaType
    title: str
    year: int | None
    total_streams: int
    created_at: datetime | None
    external_id_count: int
    external_ids: tuple[str, ...] = ()


def normalize_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def format_external_ids(external_ids: tuple[str, ...]) -> str:
    if not external_ids:
        return "none"
    return ", ".join(external_ids)


def choose_target(cluster: list[MediaRow]) -> MediaRow:
    # Prefer rows with external IDs, then with more streams, then oldest.
    return sorted(
        cluster,
        key=lambda row: (
            0 if row.external_id_count > 0 else 1,
            -row.total_streams,
            row.created_at or datetime.max,
            row.media_id,
        ),
    )[0]


async def load_candidates(include_types: set[MediaType]) -> list[MediaRow]:
    async with get_async_session_context() as session:
        result = await session.exec(
            select(
                Media.id,
                Media.type,
                Media.title,
                Media.year,
                Media.total_streams,
                Media.created_at,
                func.count(MediaExternalID.id).label("external_id_count"),
            )
            .outerjoin(MediaExternalID, MediaExternalID.media_id == Media.id)
            .where(
                Media.is_user_created.is_(False),
                Media.type.in_(list(include_types)),
                Media.title.is_not(None),
            )
            .group_by(Media.id)
        )

        rows: list[MediaRow] = []
        for row in result.all():
            rows.append(
                MediaRow(
                    media_id=row[0],
                    media_type=row[1],
                    title=row[2],
                    year=row[3],
                    total_streams=row[4] or 0,
                    created_at=row[5],
                    external_id_count=row[6] or 0,
                )
            )
        return rows


async def load_external_id_map(include_types: set[MediaType]) -> dict[int, set[str]]:
    async with get_async_session_context() as session:
        result = await session.exec(
            select(MediaExternalID.media_id, MediaExternalID.provider, MediaExternalID.external_id)
            .join(Media, Media.id == MediaExternalID.media_id)
            .where(
                Media.is_user_created.is_(False),
                Media.type.in_(list(include_types)),
            )
        )

        external_id_map: dict[int, set[str]] = defaultdict(set)
        for media_id, provider, external_id in result.all():
            external_id_map[media_id].add(f"{provider}:{external_id}")
        return external_id_map


def attach_external_ids(rows: list[MediaRow], external_id_map: dict[int, set[str]]) -> None:
    for row in rows:
        row.external_ids = tuple(sorted(external_id_map.get(row.media_id, set())))
        row.external_id_count = len(row.external_ids)


async def migrate_cluster(
    target: MediaRow,
    sources: list[MediaRow],
    apply_changes: bool,
    only_empty_sources: bool,
) -> dict[str, int]:
    moved_stream_links = 0
    moved_file_links = 0
    skipped_non_empty = 0
    deleted_sources = 0

    if not sources:
        return {"moved_stream_links": 0, "moved_file_links": 0, "sources_deleted": 0, "sources_skipped": 0}

    async with get_async_session_context() as session:
        for source in sources:
            source_canonical_id = await get_canonical_external_id(session, source.media_id)
            stream_link_count_row = (
                await session.exec(
                    select(func.count(StreamMediaLink.id)).where(StreamMediaLink.media_id == source.media_id)
                )
            ).one()
            file_link_count_row = (
                await session.exec(
                    select(func.count(FileMediaLink.id)).where(FileMediaLink.media_id == source.media_id)
                )
            ).one()
            stream_link_count = extract_count_value(stream_link_count_row)
            file_link_count = extract_count_value(file_link_count_row)
            is_empty_source = (stream_link_count or 0) == 0 and (file_link_count or 0) == 0

            if only_empty_sources and not is_empty_source:
                skipped_non_empty += 1
                logger.info(
                    "skip media_id=%s (non-empty source: stream_links=%s file_links=%s)",
                    source.media_id,
                    stream_link_count,
                    file_link_count,
                )
                continue

            if not apply_changes:
                logger.info(
                    "[DRY-RUN] %s media_id=%s (%s) -> media_id=%s (stream_links=%s file_links=%s)",
                    "delete-empty" if is_empty_source else "merge",
                    source.media_id,
                    source_canonical_id,
                    target.media_id,
                    stream_link_count,
                    file_link_count,
                )
                continue

            if not is_empty_source:
                stats = await migrate_media_links(session, source.media_id, target.media_id)
                moved_stream_links += stats["stream_links_migrated"]
                moved_file_links += stats["file_links_migrated"]

            target_media = await session.get(Media, target.media_id)
            if target_media and not target_media.migrated_from_id:
                target_media.migrated_from_id = source_canonical_id
                session.add(target_media)

            await delete_metadata(session, f"mf:{source.media_id}", new_media_id=target.media_id)
            await update_meta_stream(session, f"mf:{target.media_id}", target.media_type.value)

            await invalidate_media_stream_cache(source.media_id)
            await invalidate_media_stream_cache(target.media_id)
            await invalidate_meta_cache(f"mf:{source.media_id}")
            await invalidate_meta_cache(f"mf:{target.media_id}")
            deleted_sources += 1

        if apply_changes:
            await session.commit()

    return {
        "moved_stream_links": moved_stream_links,
        "moved_file_links": moved_file_links,
        "sources_deleted": deleted_sources if apply_changes else 0,
        "sources_skipped": skipped_non_empty,
    }


async def run(args: argparse.Namespace) -> None:
    include_types = {MediaType.MOVIE, MediaType.SERIES, MediaType.TV}
    if args.media_type != "all":
        include_types = {MediaType(args.media_type)}

    rows = await load_candidates(include_types)
    external_id_map = await load_external_id_map(include_types)
    attach_external_ids(rows, external_id_map)

    groups: dict[tuple[MediaType, str, int | None], list[MediaRow]] = defaultdict(list)
    for row in rows:
        key = (row.media_type, normalize_title(row.title), row.year)
        groups[key].append(row)

    duplicate_clusters = [cluster for cluster in groups.values() if len(cluster) > 1]
    duplicate_clusters.sort(key=lambda cluster: (cluster[0].media_type.value, normalize_title(cluster[0].title)))

    if args.limit:
        duplicate_clusters = duplicate_clusters[: args.limit]

    logger.info("Found %s duplicate clusters", len(duplicate_clusters))

    total_sources = 0
    total_moved_stream_links = 0
    total_moved_file_links = 0
    total_deleted = 0
    total_skipped_non_empty = 0
    total_skipped_ambiguous = 0
    total_skipped_no_anchor = 0

    for cluster in duplicate_clusters:
        cluster_external_rows = [row for row in cluster if row.external_id_count > 0]

        if not cluster_external_rows:
            total_skipped_no_anchor += 1
            continue

        if len(cluster_external_rows) > 1:
            total_skipped_ambiguous += 1
            continue

        target = cluster_external_rows[0]

        # Default safe behavior: only source rows with no external IDs.
        sources = [row for row in cluster if row.media_id != target.media_id and row.external_id_count == 0]

        # Optional: include source rows with an exact shared external-ID pair.
        if args.include_external_sources:
            shared_external_sources = [
                row
                for row in cluster
                if row.media_id != target.media_id
                and row.external_id_count > 0
                and set(row.external_ids).intersection(set(target.external_ids))
            ]
            sources.extend(shared_external_sources)

        if not sources:
            continue

        total_sources += len(sources)
        logger.info(
            "Cluster type=%s title=%r year=%s -> target media_id=%s (target_external_ids=%s, sources=%s)",
            target.media_type.value,
            target.title,
            target.year,
            target.media_id,
            format_external_ids(target.external_ids),
            [source.media_id for source in sources],
        )

        stats = await migrate_cluster(
            target,
            sources,
            apply_changes=args.apply,
            only_empty_sources=args.only_empty_sources,
        )
        total_moved_stream_links += stats["moved_stream_links"]
        total_moved_file_links += stats["moved_file_links"]
        total_deleted += stats["sources_deleted"]
        total_skipped_non_empty += stats["sources_skipped"]

    logger.info("------ Summary ------")
    logger.info("mode=%s", "APPLY" if args.apply else "DRY-RUN")
    logger.info("clusters_considered=%s", len(duplicate_clusters))
    logger.info("source_media_considered=%s", total_sources)
    logger.info("stream_links_moved=%s", total_moved_stream_links)
    logger.info("file_links_moved=%s", total_moved_file_links)
    logger.info("source_media_deleted=%s", total_deleted)
    logger.info("source_media_skipped_non_empty=%s", total_skipped_non_empty)
    logger.info("clusters_skipped_ambiguous_anchors=%s", total_skipped_ambiguous)
    logger.info("clusters_skipped_no_external_anchor=%s", total_skipped_no_anchor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deduplicate duplicate media rows.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Default is dry-run.",
    )
    parser.add_argument(
        "--media-type",
        default="all",
        choices=["all", "movie", "series", "tv"],
        help="Restrict deduplication to a media type.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of duplicate clusters processed (0 = no limit).",
    )
    parser.add_argument(
        "--include-external-sources",
        action="store_true",
        help="Also migrate sources with external IDs when they share an exact provider:external_id with the target.",
    )
    parser.add_argument(
        "--only-empty-sources",
        action="store_true",
        help="Only remove duplicate sources that have no stream/file links.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(parse_args()))
