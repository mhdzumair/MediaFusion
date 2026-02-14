import difflib
import logging
import re

import dramatiq
import httpx
from ipytv import playlist
from ipytv.channel import IPTVAttr

from db import crud, schemas
from db.config import settings
from db.database import get_async_session
from db.redis_database import REDIS_ASYNC_CLIENT
from utils import validation_helper
from utils.parser import is_contain_18_plus_keywords
from utils.validation_helper import validate_live_stream_url


async def add_tv_metadata(batch, namespace: str):
    for metadata_json in batch:
        metadata = schemas.TVMetaData.model_validate(metadata_json)
        if is_contain_18_plus_keywords(metadata.title) or any(
            is_contain_18_plus_keywords(genre) for genre in metadata.genres
        ):
            logging.info(f"Skipping 18+ TV metadata: {metadata.title}")
            return

        logging.info(f"Adding TV metadata: {metadata.title}")
        try:
            metadata.streams = await validation_helper.validate_tv_metadata(metadata)
        except validation_helper.ValidationError as e:
            logging.error(f"Error validating TV metadata: {metadata.title}, {e}")
            return

        metadata.namespace = namespace
        async for session in get_async_session():
            channel_id = await crud.save_tv_channel_metadata(session, metadata)
        logging.info(f"Added TV metadata: {metadata.title}, Channel ID: {channel_id}")


async def parse_m3u_playlist(
    namespace: str,
    playlist_source: str,
    playlist_url: str = None,
    playlist_redis_key: str = None,
):
    logging.info(f"Parsing M3U playlist: {playlist_url}")
    if playlist_redis_key:
        playlist_content = await REDIS_ASYNC_CLIENT.get(playlist_redis_key)
        if not playlist_content:
            logging.error(f"Playlist not found in Redis: {playlist_redis_key}")
            return

        playlist_content = playlist_content.decode("utf-8")
        iptv_playlist = playlist.loads(playlist_content)
        await REDIS_ASYNC_CLIENT.delete(playlist_redis_key)
    else:
        iptv_playlist = playlist.loadu(playlist_url)

    batch_size = 10
    batch = []

    for channel in iptv_playlist:
        # Skip .mp4 and .mkv streams for now.
        if channel.url.endswith((".mp4", ".mkv")):
            logging.info(f"Skipping M3U channel: {channel.name} with .mp4/.mkv stream.")
            continue

        channel_name = re.sub(r"\s+", " ", channel.name).strip()
        country = channel.attributes.get(IPTVAttr.TVG_COUNTRY.value)
        stream_title = channel.attributes.get(IPTVAttr.TVG_NAME.value, channel_name)
        poster, background, logo = [
            channel.attributes.get(attr) for attr in [IPTVAttr.TVG_LOGO_SMALL, IPTVAttr.TVG_LOGO, IPTVAttr.TVG_LOGO]
        ]
        genres = [
            re.sub(r"\s+", " ", genre).strip()
            for genre in re.split("[,;|]", channel.attributes.get(IPTVAttr.GROUP_TITLE.value, ""))
        ]

        metadata = schemas.TVMetaData(
            title=channel_name,
            poster=validation_helper.is_valid_url(poster) and poster or None,
            background=validation_helper.is_valid_url(background) and background or None,
            logo=validation_helper.is_valid_url(logo) and logo or None,
            country=country,
            tv_language=channel.attributes.get(IPTVAttr.TVG_LANGUAGE.value),
            genres=genres,
            streams=[
                schemas.TVStreams(
                    name=stream_title,
                    url=channel.url,
                    source=playlist_source,
                    country=country,
                )
            ],
        )

        batch.append(metadata.model_dump())

        if len(batch) >= batch_size:
            await add_tv_metadata(batch=batch, namespace=namespace)
            batch = []

    if batch:
        await add_tv_metadata(batch=batch, namespace=namespace)


@dramatiq.actor(priority=2, time_limit=15 * 60 * 1000, queue_name="scrapy")
def parse_m3u_playlist_background(
    namespace: str,
    playlist_source: str,
    playlist_url: str = None,
    playlist_redis_key: str = None,
):
    parse_m3u_playlist(
        namespace,
        playlist_source,
        playlist_url=playlist_url,
        playlist_redis_key=playlist_redis_key,
    )


@dramatiq.actor(time_limit=30 * 60 * 1000, priority=5)  # time limit is 30 minutes
async def validate_tv_streams_in_db(page=0, page_size=25, *args, **kwargs):
    """Validate TV streams in the database."""
    offset = page * page_size

    async for session in get_async_session():
        tv_streams = await crud.get_all_tv_streams_paginated(session, offset, page_size)

        if not tv_streams:
            logging.info(f"No TV streams to validate on page {page}")
            return

        updates = []
        deletes = []

        for stream in tv_streams:
            is_valid = await validate_live_stream_url(stream.url, stream.behaviorHints or {})
            logging.info(f"Stream: {stream.name}, Status: {is_valid}")

            if is_valid:
                updates.append((stream.id, True, 0))
            else:
                new_failure_count = (stream.test_failure_count or 0) + 1
                if new_failure_count >= 3:
                    deletes.append(stream.id)
                    logging.error(f"{stream.name} has failed 3 times and deleting it.")
                else:
                    updates.append((stream.id, False, new_failure_count))
                    logging.error(f"Stream failed validation: {stream.name}")

        # Apply updates
        for stream_id, is_active, failure_count in updates:
            await crud.update_tv_stream_status(session, stream_id, is_active, failure_count)

        # Apply deletes
        for stream_id in deletes:
            await crud.delete_tv_stream(session, stream_id)

        logging.info(f"Updated {len(updates)} streams, deleted {len(deletes)} streams")

    # Schedule the next batch in 2 minutes
    validate_tv_streams_in_db.send_with_options(args=(page + 1, page_size), delay=2 * 6000)


@dramatiq.actor(time_limit=30 * 60 * 1000, priority=5)
async def update_tv_posters_in_db(*args, **kwargs):
    """Validate TV posters in the database."""
    async for session in get_async_session():
        not_working_posters = await crud.get_tv_metadata_not_working_posters(session)
        logging.info(f"Found {len(not_working_posters)} TV posters to update.")

        if not not_working_posters:
            logging.info("No TV posters to update.")
            return

        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
            response = await client.get("https://iptv-org.github.io/api/channels.json", timeout=30)
            iptv_channels = response.json()

        iptv_org_channel_data = {}
        for channel in iptv_channels:
            iptv_org_channel_data[channel["name"].casefold()] = channel
            if channel.get("alt_names"):
                for alt_name in channel["alt_names"]:
                    iptv_org_channel_data[alt_name.casefold()] = channel
        iptv_org_channel_names = iptv_org_channel_data.keys()

        def get_similar_channel_name(name, cutoff=0.8):
            name = name.casefold()
            name = re.sub(r"\s*\[.*?]|\s*\(.*?\)", "", name)
            name = name.split(" – ")[0]
            matches = difflib.get_close_matches(name, iptv_org_channel_names, n=1, cutoff=cutoff)
            return matches[0] if matches else None

        updated_count = 0
        for tv_metadata in not_working_posters:
            iptv_org_channel_name = get_similar_channel_name(tv_metadata.title, cutoff=0.8)
            if not iptv_org_channel_name:
                logging.error(f"Channel not found in iptv-org: {tv_metadata.title}")
                continue

            iptv_org_channel = iptv_org_channel_data[iptv_org_channel_name]
            poster = iptv_org_channel.get("logo")
            if not poster:
                logging.error(f"Poster not found for channel: {tv_metadata.title}")
                continue

            await crud.update_tv_metadata_poster(session, tv_metadata.id, poster, True)
            updated_count += 1

        logging.info(f"Updated {updated_count} TV posters in the database.")
