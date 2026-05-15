"""
Auto-fix annotation queue entries by inferring season/episode from filenames.

Default mode is dry-run. Use --apply to persist fixes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import text

# Add project root to import path.
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import get_async_session_context
from utils.annotation_autofix import auto_map_episode_links_from_filename

logger = logging.getLogger("fix_annotation_requests")

_CANDIDATE_PAIRS_SQL = text(
    """
    WITH unlinked_pairs AS (
        SELECT DISTINCT sf.stream_id, sml.media_id
        FROM stream_file sf
        INNER JOIN stream s ON s.id = sf.stream_id
        INNER JOIN stream_media_link sml ON sml.stream_id = sf.stream_id
        INNER JOIN media m ON m.id = sml.media_id
        LEFT JOIN file_media_link fml_any ON fml_any.file_id = sf.id
        LEFT JOIN annotation_request_dismissal ard
          ON ard.stream_id = sf.stream_id
         AND ard.media_id = sml.media_id
        WHERE s.is_active = true
          AND s.is_blocked = false
          AND m.type = 'SERIES'
          AND fml_any.id IS NULL
          AND ard.id IS NULL
    ),
    null_episode_pairs AS (
        SELECT DISTINCT sf.stream_id, fml_series.media_id
        FROM stream_file sf
        INNER JOIN stream s ON s.id = sf.stream_id
        INNER JOIN file_media_link fml_series ON fml_series.file_id = sf.id
        INNER JOIN stream_media_link sml
            ON sml.stream_id = sf.stream_id
           AND sml.media_id = fml_series.media_id
        INNER JOIN media m ON m.id = fml_series.media_id
        LEFT JOIN annotation_request_dismissal ard
          ON ard.stream_id = sf.stream_id
         AND ard.media_id = fml_series.media_id
        WHERE s.is_active = true
          AND s.is_blocked = false
          AND m.type = 'SERIES'
          AND (fml_series.episode_number IS NULL OR fml_series.season_number IS NULL)
          AND ard.id IS NULL
    )
    SELECT DISTINCT pairs.stream_id, pairs.media_id
    FROM (
        SELECT stream_id, media_id FROM unlinked_pairs
        UNION
        SELECT stream_id, media_id FROM null_episode_pairs
    ) AS pairs
    WHERE (CAST(:stream_id AS INTEGER) IS NULL OR pairs.stream_id = CAST(:stream_id AS INTEGER))
      AND (CAST(:media_id AS INTEGER) IS NULL OR pairs.media_id = CAST(:media_id AS INTEGER))
    ORDER BY pairs.stream_id, pairs.media_id
    """
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-fix annotation requests from filename parsing.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Default is dry-run.",
    )
    parser.add_argument(
        "--stream-id",
        type=int,
        default=None,
        help="Limit to a single stream_id.",
    )
    parser.add_argument(
        "--media-id",
        type=int,
        default=None,
        help="Limit to a single media_id.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of stream/media pairs processed.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print pair-level results.",
    )
    return parser.parse_args()


def _extract_pair(row: object) -> tuple[int, int]:
    stream_id = getattr(row, "stream_id", None)
    media_id = getattr(row, "media_id", None)
    if stream_id is None or media_id is None:
        stream_id = row[0]
        media_id = row[1]
    return int(stream_id), int(media_id)


async def run(args: argparse.Namespace) -> None:
    params = {
        "stream_id": args.stream_id,
        "media_id": args.media_id,
    }

    async with get_async_session_context() as session:
        result = await session.exec(_CANDIDATE_PAIRS_SQL, params=params)
        pairs = [_extract_pair(row) for row in result.all()]

        if args.limit:
            pairs = pairs[: args.limit]

        if not pairs:
            logger.info("No annotation candidate pairs found.")
            return

        logger.info("Found %s candidate stream/media pairs.", len(pairs))
        logger.info("Mode: %s", "APPLY" if args.apply else "DRY-RUN")

        resolved_pairs = 0
        unresolved_pairs = 0
        changed_pairs = 0
        total_changes = 0
        ignored_non_video_pairs = 0

        for stream_id, media_id in pairs:
            pair_resolved, change_count, relevant_video_files = await auto_map_episode_links_from_filename(
                session=session,
                stream_id=stream_id,
                media_id=media_id,
                apply_changes=args.apply,
            )

            if relevant_video_files == 0:
                ignored_non_video_pairs += 1
                if args.verbose:
                    logger.info(
                        "stream_id=%s media_id=%s ignored (no playable video files)",
                        stream_id,
                        media_id,
                    )
                continue

            if pair_resolved:
                resolved_pairs += 1
            else:
                unresolved_pairs += 1

            if change_count > 0:
                changed_pairs += 1
                total_changes += change_count

            if args.verbose:
                logger.info(
                    "stream_id=%s media_id=%s resolved=%s changes=%s",
                    stream_id,
                    media_id,
                    pair_resolved,
                    change_count,
                )

        if args.apply:
            if total_changes > 0:
                await session.commit()
            else:
                await session.rollback()
        else:
            await session.rollback()

        logger.info("Summary:")
        logger.info("- pairs_processed: %s", len(pairs))
        logger.info("- pairs_resolved: %s", resolved_pairs)
        logger.info("- pairs_unresolved: %s", unresolved_pairs)
        logger.info("- pairs_ignored_non_video: %s", ignored_non_video_pairs)
        logger.info("- pairs_with_changes: %s", changed_pairs)
        logger.info("- total_link_changes: %s", total_changes)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    asyncio.run(run(parse_args()))
