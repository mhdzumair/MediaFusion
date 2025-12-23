import logging
from datetime import datetime, timedelta

import dramatiq
from pyasynctracker import batch_scrape_info_hashes, find_max_seeders
from pydantic import BaseModel, Field
from sqlmodel import select

from db import sql_crud
from db.database import get_async_session
from db.sql_models import TorrentStream
from utils.runtime_const import TRACKERS


class TorrentProjection(BaseModel):
    info_hash: str = Field(alias="id")
    announce_list: list[str]


@dramatiq.actor(time_limit=10 * 60 * 1000, priority=5, max_retries=3)
async def update_torrent_seeders(page=0, page_size=25, *args, **kwargs):
    # Calculate offset for pagination
    offset = page * page_size
    
    async for session in get_async_session():
        # Fetch torrents where seeders is null and updated_at is more than 7 days old
        query = (
            select(TorrentStream.id, TorrentStream.announce_urls)
            .where(TorrentStream.seeders.is_(None))
            .where(TorrentStream.updated_at < datetime.now() - timedelta(days=7))
            .offset(offset)
            .limit(page_size)
        )
        
        result = await session.exec(query)
        torrents = result.all()

        if not torrents:
            logging.info(f"No torrents to update on page {page}")
            return

        # Prepare data for batch scraping
        data_list = []
        for torrent in torrents:
            info_hash = torrent[0]
            announce_urls = torrent[1] if torrent[1] else []
            # Extract URL strings from announce_urls relationship
            urls = [url.name for url in announce_urls] if announce_urls else TRACKERS
            data_list.append((info_hash, urls))
        
        results = await batch_scrape_info_hashes(data_list, timeout=30)
        max_seeders_data = find_max_seeders(results)

        # Perform batch updates
        for torrent_id, max_seeders in max_seeders_data.items():
            await sql_crud.update_torrent_seeders(session, torrent_id, max_seeders)
            logging.info(
                f"Updating seeders for torrent {torrent_id} with max seeders: {max_seeders}"
            )

        logging.info(f"Updated {len(max_seeders_data)} torrents")

    # Schedule the next batch
    update_torrent_seeders.send_with_options(
        args=(page + 1, page_size), delay=60000
    )  # Delay for 1 minute between batches
