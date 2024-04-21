import asyncio
import logging
import re

import dramatiq
from ipytv import playlist
from ipytv.channel import IPTVAttr

from db import schemas, crud
from utils import validation_helper
from utils.parser import is_contain_18_plus_keywords


@dramatiq.actor(priority=5, time_limit=5 * 60 * 1000)
async def add_tv_metadata(batch):
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
            await asyncio.sleep(3)
            raise e

        channel_id = await crud.save_tv_channel_metadata(metadata)
        logging.info(f"Added TV metadata: {metadata.title}, Channel ID: {channel_id}")


@dramatiq.actor(priority=5, time_limit=15 * 60 * 1000)
def parse_m3u_playlist(
    playlist_source: str, playlist_url: str = None, playlist_content: str = None
):
    logging.info(f"Parsing M3U playlist: {playlist_url}")
    if playlist_content:
        iptv_playlist = playlist.loads(playlist_content)
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
            add_tv_metadata.send(batch)
            batch = []

    if batch:
        add_tv_metadata.send(batch)
