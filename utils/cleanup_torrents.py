import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict

import PTT
from beanie import BulkWriter
from tqdm import tqdm

from db import database
from db.models import (
    TorrentStreams,
    MediaFusionSeriesMetaData,
    MediaFusionMovieMetaData,
    MediaFusionMetaData,
)
from utils.parser import calculate_max_similarity_ratio, is_contain_18_plus_keywords

BATCH_SIZE = 1000  # Adjust the batch size as needed
SKIP_TORRENTS = 0  # Skip the first n torrents
LAST_UPDATED = datetime.now(tz=timezone.utc) - timedelta(days=1)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def cleanup_torrents(dry_run: bool = True) -> Dict[str, int]:
    metrics = {
        "total": 0,
        "removed": 0,
        "no_metadata": 0,
        "valid": 0,
        "ratio": 0,
        "adult": 0,
        "year_mismatch": 0,
        "removed_non_imdb": 0,
        "non_movie": 0,
    }
    lock = asyncio.Lock()

    # Create a cursor for efficient pagination
    cursor = TorrentStreams.find(TorrentStreams.updated_at < LAST_UPDATED).sort(
        -TorrentStreams.updated_at
    )
    total_torrents = await cursor.count()
    total_torrents = max(0, total_torrents - SKIP_TORRENTS)

    progress_bar = tqdm(total=total_torrents, desc="Processing torrents")
    torrent_bulk_writer = BulkWriter()
    movies_bulk_writer = BulkWriter()
    series_bulk_writer = BulkWriter()

    cursor = cursor.skip(SKIP_TORRENTS)

    processed_count = 0
    async for torrent in cursor:
        await process_torrent(
            torrent,
            dry_run,
            metrics,
            lock,
            torrent_bulk_writer,
            movies_bulk_writer,
            series_bulk_writer,
        )

        processed_count += 1
        if processed_count >= BATCH_SIZE:
            await torrent_bulk_writer.commit()
            await movies_bulk_writer.commit()
            await series_bulk_writer.commit()
            torrent_bulk_writer.operations.clear()
            movies_bulk_writer.operations.clear()
            series_bulk_writer.operations.clear()
            processed_count = 0

        progress_bar.update(1)
        progress_bar.set_postfix(metrics)

    # Commit any remaining operations
    await torrent_bulk_writer.commit()
    await movies_bulk_writer.commit()
    await series_bulk_writer.commit()

    progress_bar.close()
    return metrics


async def process_torrent(
    torrent: TorrentStreams,
    dry_run: bool,
    metrics: Dict[str, int],
    lock: asyncio.Lock,
    torrent_bulk_writer: BulkWriter,
    movies_bulk_writer: BulkWriter,
    series_bulk_writer: BulkWriter,
) -> str:
    async with lock:
        metrics["total"] += 1

    meta_data = await MediaFusionMetaData.get_motor_collection().find_one(
        {"_id": torrent.meta_id}
    )

    if not meta_data:
        await torrent.delete(bulk_writer=torrent_bulk_writer)
        async with lock:
            metrics["no_metadata"] += 1
            metrics["removed"] += 1
        logger.debug(
            f"No metadata found for torrent: {torrent.torrent_name}, {torrent.meta_id}"
        )
        return "removed"

    if meta_data["type"] == "movie":
        meta_data = MediaFusionMovieMetaData(**meta_data)
        meta_data_bulk_writer = movies_bulk_writer
    else:
        meta_data = MediaFusionSeriesMetaData(**meta_data)
        meta_data_bulk_writer = series_bulk_writer

    if (
        not dry_run
        and torrent.meta_id.startswith("mf")
        and torrent.source
        in [
            "TamilBlasters",
            "TamilMV",
        ]
    ):
        await torrent.delete(bulk_writer=torrent_bulk_writer)
        await meta_data.delete(bulk_writer=meta_data_bulk_writer)
        async with lock:
            metrics["removed"] += 1
            metrics["removed_non_imdb"] += 1
        logger.debug(f"Removed non-IMDb torrent: {torrent.torrent_name}")
        return "removed"

    if is_contain_18_plus_keywords(torrent.torrent_name):
        if not dry_run:
            await torrent.delete(bulk_writer=torrent_bulk_writer)
        async with lock:
            metrics["removed"] += 1
            metrics["adult"] += 1
        logger.debug(f"Removed torrent due to adult content: {torrent.torrent_name}")
        return "removed"

    parsed_data = PTT.parse_title(torrent.torrent_name, True)

    max_ratio = calculate_max_similarity_ratio(
        parsed_data.get("title", ""), meta_data.title, meta_data.aka_titles
    )
    expected_ratio = 70 if torrent.source in ["TamilMV", "TamilBlasters"] else 85
    if (
        max_ratio < expected_ratio
        and torrent.meta_id.startswith("tt")
        and not any([x in torrent.catalog for x in ["wwe_tgx", "ufc_tgx"]])
    ):
        if not dry_run:
            await torrent.delete(bulk_writer=torrent_bulk_writer)
        async with lock:
            metrics["removed"] += 1
            metrics["ratio"] += 1
        logger.debug(
            f"Removed torrent due to low similarity ratio: {parsed_data.get('title')} != {meta_data.title} (ratio: {max_ratio}%) (full title: {torrent.torrent_name})"
        )
        return "removed"

    if torrent.meta_id.startswith("tt") and meta_data.type == "movie":
        if parsed_data.get("seasons"):
            if not dry_run:
                await torrent.delete(bulk_writer=torrent_bulk_writer)
            async with lock:
                metrics["removed"] += 1
                metrics["non_movie"] += 1
            logger.debug(
                f"Removed non-movie torrent: {torrent.torrent_name}, found season: {parsed_data['seasons']}. Expected movie."
            )
            return "removed"

        if meta_data.type == "movie" and parsed_data.get("year") != meta_data.year:
            if not dry_run:
                await torrent.delete(bulk_writer=torrent_bulk_writer)
            async with lock:
                metrics["removed"] += 1
                metrics["year_mismatch"] += 1
            logger.debug(
                f"Removed torrent due to year mismatch: {torrent.torrent_name} ({parsed_data.get('year')} != {meta_data.year}) ({meta_data.id}) ({meta_data.type})"
            )
            return "removed"

    if (
        not dry_run
        and torrent.meta_id.startswith("tt")
        and "torrentgalaxy" not in torrent.source.lower()
    ):
        torrent.languages = parsed_data.get("languages")
        torrent.resolution = parsed_data.get("resolution")
        torrent.codec = parsed_data.get("codec")
        torrent.quality = parsed_data.get("quality")
        torrent.audio = (
            parsed_data.get("audio")[0] if parsed_data.get("audio") else None
        )
        torrent.updated_at = datetime.now(tz=timezone.utc)
        await torrent.save(bulk_writer=torrent_bulk_writer)
        return "updated"

    torrent.updated_at = datetime.now(tz=timezone.utc)
    await torrent.save(bulk_writer=torrent_bulk_writer)
    return "updated"


# Initialize the database connection and start the cleanup process
async def main(is_dry_run: bool = True, log_level: str = "INFO"):
    logger.setLevel(log_level)
    logger.info(f"Running cleanup with dry_run={is_dry_run}, log_level={log_level}")
    await database.init()

    summary = await cleanup_torrents(is_dry_run)
    logger.info(f"Summary: {summary}")


if __name__ == "__main__":
    is_perform_cleanup = "--cleanup" in sys.argv
    log_level = "DEBUG" if "--debug" in sys.argv else "INFO"
    asyncio.run(main(not is_perform_cleanup, log_level))
