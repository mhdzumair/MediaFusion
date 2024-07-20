import asyncio
import sys
import logging
from datetime import datetime
from typing import Dict

import PTT
from beanie import BulkWriter
from tqdm import tqdm

from db import database
from db.models import (
    TorrentStreams,
    MediaFusionSeriesMetaData,
    MediaFusionMovieMetaData,
)
from utils.parser import calculate_max_similarity_ratio, is_contain_18_plus_keywords

BATCH_SIZE = 1000  # Adjust the batch size as needed

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def cleanup_torrents(dry_run: bool = True) -> Dict[str, int]:
    metrics = {
        "total_torrents": 0,
        "removed_torrents": 0,
        "updated_torrents": 0,
        "no_metadata": 0,
        "valid": 0,
        "ratio_error": 0,
        "adult_content": 0,
        "year_mismatch": 0,
        "removed_non_imdb": 0,
    }
    lock = asyncio.Lock()
    total_torrents = await TorrentStreams.count()

    for skip in tqdm(range(0, total_torrents, BATCH_SIZE), desc="Processing torrents"):
        torrents = await TorrentStreams.find().skip(skip).limit(BATCH_SIZE).to_list()
        bulk_writer = BulkWriter()
        tasks = [
            process_torrent(torrent, dry_run, metrics, lock, bulk_writer)
            for torrent in torrents
        ]
        await asyncio.gather(*tasks)
        await bulk_writer.commit()

    return metrics


async def process_torrent(
    torrent: TorrentStreams,
    dry_run: bool,
    metrics: Dict[str, int],
    lock: asyncio.Lock,
    bulk_writer: BulkWriter,
) -> str:
    async with lock:
        metrics["total_torrents"] += 1

    meta_data = None
    if any("series" in catalog for catalog in torrent.catalog):
        meta_data = await MediaFusionSeriesMetaData.get(torrent.meta_id)
    else:
        meta_data = await MediaFusionMovieMetaData.get(torrent.meta_id)

    if not meta_data:
        async with lock:
            metrics["no_metadata"] += 1
        return "no_metadata"

    if not dry_run:
        if torrent.meta_id.startswith("mf") and torrent.source in [
            "TamilBlasters",
            "TamilMV",
        ]:
            await torrent.delete(bulk_writer=bulk_writer)
            await meta_data.delete(bulk_writer=bulk_writer)
            async with lock:
                metrics["removed_torrents"] += 1
                metrics["removed_non_imdb"] += 1
            return "removed"

    if is_contain_18_plus_keywords(torrent.torrent_name):
        if not dry_run:
            await torrent.delete(bulk_writer=bulk_writer)
        async with lock:
            metrics["removed_torrents"] += 1
            metrics["adult_content"] += 1
        logger.debug(f"Removed torrent due to adult content: {torrent.torrent_name}")
        return "removed"

    parsed_data = PTT.parse_title(torrent.torrent_name)

    max_ratio = calculate_max_similarity_ratio(
        parsed_data.get("title", ""), meta_data.title, meta_data.aka_titles
    )
    expected_ratio = 70 if torrent.source in ["TamilMV", "TamilBlasters"] else 85
    if max_ratio < expected_ratio and torrent.meta_id.startswith("tt"):
        if not dry_run:
            await torrent.delete(bulk_writer=bulk_writer)
        async with lock:
            metrics["removed_torrents"] += 1
            metrics["ratio_error"] += 1
        logger.debug(
            f"Removed torrent due to low similarity ratio: {parsed_data.get('title')} != {meta_data.title} (ratio: {max_ratio}%) (full title: {torrent.torrent_name})"
        )
        return "removed"

    if "year" in parsed_data and torrent.meta_id.startswith("tt"):
        if meta_data.type == "movie" and parsed_data["year"] != meta_data.year:
            if not dry_run:
                await torrent.delete(bulk_writer=bulk_writer)
            async with lock:
                metrics["removed_torrents"] += 1
                metrics["year_mismatch"] += 1
            logger.debug(
                f"Removed torrent due to year mismatch: {torrent.torrent_name} (torrent year: {parsed_data['year']}, meta year: {meta_data.year})"
            )
            return "removed"

    if not dry_run and torrent.meta_id.startswith("tt"):
        torrent.languages = parsed_data.get("languages")
        torrent.resolution = parsed_data.get("resolution")
        torrent.codec = parsed_data.get("codec")
        torrent.quality = parsed_data.get("quality")
        torrent.audio = parsed_data.get("audio")
        await torrent.save(bulk_writer=bulk_writer)
        async with lock:
            metrics["updated_torrents"] += 1
        return "updated"

    async with lock:
        metrics["valid"] += 1
    return "valid"


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
