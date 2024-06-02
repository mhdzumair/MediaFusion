import logging
from datetime import datetime, timedelta

import dramatiq
from beanie import BulkWriter
from pyasynctracker import batch_scrape_info_hashes, find_max_seeders
from pydantic import BaseModel, Field

from db.models import TorrentStreams
from utils.runtime_const import TRACKERS


class TorrentProjection(BaseModel):
    info_hash: str = Field(alias="_id")
    announce_list: list[str]


@dramatiq.actor(time_limit=10 * 60 * 1000, priority=5, max_retries=3)
async def update_torrent_seeders(page=0, page_size=25, *args, **kwargs):
    # Calculate offset for pagination
    offset = page * page_size
    # Fetch torrents which updated_at is more than 1 day, limited by pagination
    torrents = (
        await TorrentStreams.find(
            {"updated_at": {"$lt": datetime.now() - timedelta(days=1)}},
            skip=offset,
            limit=page_size,
        )
        .project(TorrentProjection)
        .to_list()
    )

    if not torrents:
        logging.info(f"No torrents to update on page {page}")
        return

    data_list = [
        (torrent.info_hash, torrent.announce_list or TRACKERS) for torrent in torrents
    ]
    results = await batch_scrape_info_hashes(data_list, timeout=30)
    max_seeders_data = find_max_seeders(results)

    # Perform update in the database
    bulk_writer = BulkWriter()
    for torrent_id, max_seeders in max_seeders_data.items():
        await TorrentStreams.find({"_id": torrent_id}).update(
            {"$set": {"seeders": max_seeders, "updated_at": datetime.now()}},
            bulk_writer=bulk_writer,
        )
        logging.info(
            f"Updating seeders for torrent {torrent_id} with max seeders: {max_seeders}"
        )

    logging.info(f"Committing {len(max_seeders_data)} updates to the database")
    await bulk_writer.commit()

    # Schedule the next batch
    update_torrent_seeders.send_with_options(
        args=(page + 1, page_size), delay=60000
    )  # Delay for 1 minute between batches
