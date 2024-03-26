import logging
import re

import dramatiq
from ipytv import playlist
from ipytv.channel import IPTVAttr

from db import schemas, crud
from utils import validation_helper
from utils.parser import is_contain_18_plus_keywords


@dramatiq.actor(priority=5, time_limit=15 * 60 * 1000)
async def add_tv_metadata(metadata):
    metadata = schemas.TVMetaData.model_validate(metadata)
    if is_contain_18_plus_keywords(metadata.title):
        return

    logging.info(f"Adding TV metadata: {metadata.title}")
    try:
        metadata.streams = await validation_helper.validate_tv_metadata(metadata)
    except validation_helper.ValidationError as e:
        logging.error(f"Error validating TV metadata: {metadata.title}, {e}")
        return

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

    for channel in iptv_playlist:
        logging.info(f"Adding TV metadata: {channel.name}")
        country = channel.attributes.get(IPTVAttr.TVG_COUNTRY.value)
        stream_title = channel.attributes.get(IPTVAttr.TVG_NAME.value, channel.name)
        metadata = schemas.TVMetaData(
            title=channel.name,
            poster=channel.attributes.get(
                IPTVAttr.TVG_LOGO_SMALL.value,
                channel.attributes.get(IPTVAttr.TVG_LOGO.value),
            ),
            background=channel.attributes.get(IPTVAttr.TVG_LOGO.value),
            country=channel.attributes.get(IPTVAttr.TVG_COUNTRY.value),
            tv_language=channel.attributes.get(IPTVAttr.TVG_LANGUAGE.value),
            logo=channel.attributes.get(IPTVAttr.TVG_LOGO_SMALL.value),
            genres=re.split(
                "[,;|]", channel.attributes.get(IPTVAttr.GROUP_TITLE.value, "")
            ),
            streams=[
                schemas.TVStreams(
                    name=stream_title,
                    url=channel.url,
                    source=playlist_source,
                    country=country,
                )
            ],
        )
        add_tv_metadata.send(metadata.model_dump())
        logging.info(f"Added TV metadata: {channel.name} to the queue")
