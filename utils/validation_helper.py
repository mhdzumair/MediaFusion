import asyncio
import logging
from urllib.parse import urlparse

import aiohttp
import dramatiq
from aiohttp import ClientError

from db import schemas
from utils import const


async def is_valid_url(url: str) -> bool:
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
    return await is_valid_url(url) and await does_url_exist(url)


async def validate_m3u8_url(url: str, behaviour_hint: dict) -> bool:
    if not await is_valid_url(url):
        return False

    headers = behaviour_hint.get("proxyHeaders", {}).get("request", {})
    async with aiohttp.ClientSession() as session:
        try:
            async with session.head(
                url,
                allow_redirects=True,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                return (
                    "application/vnd.apple.mpegurl" in content_type
                    or "application/x-mpegurl" in content_type
                )
        except (ClientError, asyncio.TimeoutError) as err:
            logging.error(err)
            return False


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
    image_validation_tasks = [validate_image_url(metadata.poster)]
    if metadata.logo:
        image_validation_tasks.append(validate_image_url(metadata.logo))
    if metadata.background:
        image_validation_tasks.append(validate_image_url(metadata.background))

    # Run all image URL validations concurrently
    image_validation_results = await asyncio.gather(*image_validation_tasks)
    if not all(image_validation_results):
        raise ValidationError(
            f"Invalid image URL provided. {metadata.poster} {metadata.logo} {metadata.background}"
        )

    # Prepare validation tasks for streams
    stream_validation_tasks = []
    for stream in metadata.streams:
        if stream.url:
            stream_validation_tasks.append(
                validate_m3u8_url(
                    stream.url, stream.behaviorHints.model_dump(exclude_none=True)
                )
            )
        elif stream.ytId:
            stream_validation_tasks.append(validate_yt_id(stream.ytId))

    # Run all stream URL validations concurrently
    stream_validation_results = await asyncio.gather(*stream_validation_tasks)

    # Filter out valid streams based on the validation results
    valid_streams = [
        metadata.streams[i]
        for i, is_valid in enumerate(stream_validation_results)
        if is_valid
    ]
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


@dramatiq.actor(time_limit=30 * 60 * 1000, priority=5)  # time limit is 30 minutes
async def validate_tv_streams_in_db():
    """Validate TV streams in the database."""
    from db.models import TVStreams

    async def validate_and_update_tv_stream(stream):
        is_valid = await validate_m3u8_url(stream.url, stream.behaviorHints)
        stream.is_working = is_valid
        await stream.save()
        logging.info(f"Stream: {stream.name}, Status: {is_valid}")

    tv_streams = await TVStreams.all().to_list()
    tasks = [
        asyncio.create_task(validate_and_update_tv_stream(stream))
        for stream in tv_streams
    ]
    await asyncio.gather(*tasks)
