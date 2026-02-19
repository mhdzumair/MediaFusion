import logging
from datetime import datetime, timedelta

import dramatiq
from pyasynctracker import batch_scrape_info_hashes, find_max_seeders
from sqlalchemy.orm import selectinload
from sqlmodel import select

from db import crud
from db.database import get_background_session
from db.models import TorrentStream
from utils.runtime_const import TRACKERS


@dramatiq.actor(time_limit=10 * 60 * 1000, priority=5, max_retries=3)
async def update_torrent_seeders(page=0, page_size=25, *args, **kwargs):
    offset = page * page_size

    async with get_background_session() as session:
        query = (
            select(TorrentStream)
            .options(selectinload(TorrentStream.trackers))
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

        data_list = []
        for torrent in torrents:
            urls = [t.url for t in torrent.trackers] if torrent.trackers else TRACKERS
            data_list.append((torrent.info_hash, urls))

        results = await batch_scrape_info_hashes(data_list, timeout=30)
        max_seeders_data = find_max_seeders(results)

        for info_hash, max_seeders in max_seeders_data.items():
            await crud.update_torrent_seeders(session, info_hash, max_seeders)
            logging.info(f"Updating seeders for torrent {info_hash} with max seeders: {max_seeders}")

        logging.info(f"Updated {len(max_seeders_data)} torrents")

    update_torrent_seeders.send_with_options(args=(page + 1, page_size), delay=60000)
