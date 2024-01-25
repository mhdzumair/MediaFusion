import logging

import requests
from urllib.parse import urlparse, quote
from requests.exceptions import RequestException

from db import schemas


def is_valid_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return all([parsed_url.scheme, parsed_url.netloc])


def does_url_exist(url: str) -> bool:
    try:
        response = requests.head(url, allow_redirects=True, timeout=5)
        return response.status_code == 200
    except RequestException:
        return False


def validate_image_url(url: str) -> bool:
    return is_valid_url(url) and does_url_exist(url)


def validate_m3u8_url(url: str, behaviour_hint: dict) -> bool:
    if not is_valid_url(url):
        return False

    try:
        headers = behaviour_hint.get("proxyHeaders", {}).get("request", {})
        response = requests.get(url, allow_redirects=True, headers=headers, timeout=30)
        content_type = response.headers.get("Content-Type", "").lower()
        return (
                "application/vnd.apple.mpegurl" in content_type
                or "application/x-mpegurl" in content_type
        )
    except RequestException as err:
        logging.error(err)
        return False


class ValidationError(Exception):
    pass


def validate_yt_id(yt_id: str) -> bool:
    image_url = f"https://img.youtube.com/vi/{yt_id}/mqdefault.jpg"
    response = requests.head(image_url, allow_redirects=True, timeout=5)
    return response.status_code == 200


def validate_tv_metadata(metadata: schemas.TVMetaData) -> list[schemas.TVStreams]:
    if (
            not validate_image_url(metadata.poster)
            or (metadata.logo and not validate_image_url(metadata.logo))
            or (metadata.background and not validate_image_url(metadata.background))
    ):
        raise ValidationError("Invalid image URL provided.")

    # Validate stream URLs at least 1 stream should be valid
    valid_streams = [
        stream
        for stream in metadata.streams
        if (
                   stream.url
                   and validate_m3u8_url(
               stream.url, stream.behaviorHints.model_dump(exclude_none=True)
           )
           )
           or (stream.ytId and validate_yt_id(stream.ytId))
    ]
    if not valid_streams:
        raise ValidationError("Invalid stream URLs provided.")

    unique_streams = {stream.url or stream.ytId: stream for stream in valid_streams}
    return list(unique_streams.values())


def is_video_file(filename: str) -> bool:
    video_extensions = ["3gp", "mp4", "m4v", "mkv", "webm", "mov", "avi", "wmv", "mpg", "flv"]
    ans = any(filename.endswith(x) for x in video_extensions)
    return ans
