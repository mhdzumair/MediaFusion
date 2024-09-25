import asyncio
import json
import logging
from urllib import parse
from urllib.parse import urlparse

import aiohttp
from aiohttp import ClientError

from db import schemas
from utils import const
from utils.runtime_const import REDIS_ASYNC_CLIENT


def is_valid_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return all([parsed_url.scheme, parsed_url.netloc])


async def does_url_exist(url: str) -> bool:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.head(
                url, allow_redirects=True, timeout=10, headers=const.UA_HEADER
            ) as response:
                logging.info("URL: %s, Status: %s", url, response.status)
                return response.status == 200
        except (ClientError, asyncio.TimeoutError) as err:
            logging.error("URL: %s, Status: %s", url, err)
            return False


async def validate_image_url(url: str) -> bool:
    return is_valid_url(url) and await does_url_exist(url)


async def validate_live_stream_url(
    url: str, behaviour_hint: dict, validate_url: bool = False
) -> bool:
    if validate_url and not is_valid_url(url):
        return False

    headers = behaviour_hint.get("proxyHeaders", {}).get("request", {})
    async with aiohttp.ClientSession() as session:
        try:
            async with session.head(
                url,
                allow_redirects=True,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                content_type = response.headers.get("content-type", "").lower()
                is_valid = content_type in const.IPTV_VALID_CONTENT_TYPES
                return is_valid
        except (ClientError, asyncio.TimeoutError) as err:
            logging.error(err)
            return False


async def validate_m3u8_or_mpd_url_with_cache(url: str, behaviour_hint: dict):
    cache_key = f"m3u8_url:{parse.urlparse(url).netloc}"
    cache_data = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cache_data:
        return json.loads(cache_data)

    is_valid = await validate_live_stream_url(url, behaviour_hint)
    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(is_valid), ex=180)
    return is_valid


class ValidationError(Exception):
    pass


async def validate_yt_id(yt_id: str) -> bool:
    image_url = f"https://img.youtube.com/vi/{yt_id}/mqdefault.jpg"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.head(
                image_url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                return response.status == 200
        except (ClientError, asyncio.TimeoutError):
            return False


async def validate_tv_metadata(metadata: schemas.TVMetaData) -> list[schemas.TVStreams]:
    # Prepare validation tasks for streams
    stream_validation_tasks = []
    for stream in metadata.streams:
        if stream.url:
            stream_validation_tasks.append(
                validate_live_stream_url(
                    stream.url,
                    (
                        stream.behaviorHints.model_dump(exclude_none=True)
                        if stream.behaviorHints
                        else {}
                    ),
                    validate_url=True,
                )
            )
        elif stream.ytId:
            stream_validation_tasks.append(validate_yt_id(stream.ytId))

    # Run all stream URL validations concurrently
    stream_validation_results = await asyncio.gather(*stream_validation_tasks)

    # Filter out valid streams based on the validation results
    valid_streams = []
    for i, is_valid in enumerate(stream_validation_results):
        if is_valid:
            stream = metadata.streams[i]
            valid_streams.append(stream)

    if not valid_streams:
        raise ValidationError("Invalid stream URLs provided.")

    # Deduplicate streams based on URL or YT ID
    unique_streams = {stream.url or stream.ytId: stream for stream in valid_streams}
    return list(unique_streams.values())


def is_video_file(filename: str) -> bool:
    return filename.lower().endswith(
        (
            ".3g2",
            ".3gp",
            ".amv",
            ".asf",
            ".avi",
            ".drc",
            ".flv",
            ".gif",
            ".gifv",
            ".m2v",
            ".m4p",
            ".m4v",
            ".mkv",
            ".mng",
            ".mov",
            ".mp2",
            ".mp4",
            ".mpe",
            ".mpeg",
            ".mpg",
            ".mpv",
            ".mxf",
            ".nsv",
            ".ogg",
            ".ogv",
            ".qt",
            ".rm",
            ".rmvb",
            ".roq",
            ".svi",
            ".vob",
            ".webm",
            ".wmv",
            ".yuv",
        )
    )


def get_filter_certification_values(user_data: schemas.UserData) -> list[str]:
    certification_values = []
    for category in user_data.certification_filter:
        certification_values.extend(const.CERTIFICATION_MAPPING.get(category, []))
    return certification_values


def validate_parent_guide_nudity(metadata, user_data: schemas.UserData) -> bool:
    """
    Validate if the metadata has adult content based on the parent guide nudity status or if status is not available, based on certificates.
    """
    filter_certification_values = get_filter_certification_values(user_data)
    if (
        metadata.parent_guide_nudity_status
        and metadata.parent_guide_nudity_status in user_data.nudity_filter
    ):
        return False

    if metadata.parent_guide_certificates and any(
        certificate in filter_certification_values
        for certificate in metadata.parent_guide_certificates
    ):
        return False

    return True
