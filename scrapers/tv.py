import asyncio
import logging
import re

import dramatiq
from beanie import BulkWriter
from ipytv import playlist
from ipytv.channel import IPTVAttr

from db import schemas, crud
from db.models import TVStreams
from utils import validation_helper
from utils.parser import is_contain_18_plus_keywords
from utils.runtime_const import REDIS_ASYNC_CLIENT
from utils.validation_helper import validate_m3u8_url


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
        channel_id = await crud.save_tv_channel_metadata(metadata)
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
            channel.attributes.get(attr)
            for attr in [IPTVAttr.TVG_LOGO_SMALL, IPTVAttr.TVG_LOGO, IPTVAttr.TVG_LOGO]
        ]
        genres = [
            re.sub(r"\s+", " ", genre).strip()
            for genre in re.split(
                "[,;|]", channel.attributes.get(IPTVAttr.GROUP_TITLE.value, "")
            )
        ]

        metadata = schemas.TVMetaData(
            title=channel_name,
            poster=validation_helper.is_valid_url(poster) and poster or None,
            background=validation_helper.is_valid_url(background)
            and background
            or None,
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
    tv_streams = await TVStreams.all().skip(offset).limit(page_size).to_list()

    if not tv_streams:
        logging.info(f"No TV streams to validate on page {page}")
        return

    async def validate_and_update_tv_stream(stream, bulk_writer):
        is_valid = await validate_m3u8_url(stream.url, stream.behaviorHints or {})
        logging.info(f"Stream: {stream.name}, Status: {is_valid}")
        if is_valid:
            stream.is_working = is_valid
            stream.test_failure_count = 0
            await stream.replace(bulk_writer=bulk_writer)
            return

        stream.test_failure_count += 1
        if stream.test_failure_count >= 3:
            await stream.delete(bulk_writer=bulk_writer)
            logging.error(f"{stream.name} has failed 3 times and deleting it.")
            return

        stream.is_working = is_valid
        await stream.replace(bulk_writer=bulk_writer)
        logging.error(f"Stream failed validation: {stream.name}")

    bulk_writer = BulkWriter()
    tasks = [
        asyncio.create_task(validate_and_update_tv_stream(stream, bulk_writer))
        for stream in tv_streams
    ]
    await asyncio.gather(*tasks)

    logging.info(f"Committing {len(bulk_writer.operations)} updates to the database")
    await bulk_writer.commit()

    # Schedule the next batch in 2 minutes
    validate_tv_streams_in_db.send_with_options(
        args=(page + 1, page_size), delay=2 * 6000
    )
