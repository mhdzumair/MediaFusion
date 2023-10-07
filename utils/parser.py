import math
import re

from db.config import settings
from db.models import Streams
from db.schemas import Stream, UserData
from streaming_providers.realdebrid.utils import (
    order_streams_by_instant_availability_and_date,
)


def parse_stream_data(
    streams: list[Streams],
    user_data: UserData,
    secret_str: str,
    season: int = None,
    episode: int = None,
) -> list[Stream]:
    stream_list = []

    # filter out streams that are not available in the user's selected catalog
    streams = [
        stream
        for stream in streams
        if any(catalog in stream.catalog for catalog in user_data.selected_catalogs)
    ]

    # sort streams by instant availability and date if realdebrid is selected
    if (
        user_data.streaming_provider
        and user_data.streaming_provider.service == "realdebrid"
    ):
        streams = order_streams_by_instant_availability_and_date(streams, user_data)
    else:
        # Sort the streams by created_at time
        streams = sorted(streams, key=lambda x: x.created_at, reverse=True)

    for stream_data in streams:
        quality_detail = " - ".join(
            filter(
                None,
                [
                    stream_data.quality,
                    stream_data.resolution,
                    stream_data.codec,
                    stream_data.audio,
                ],
            )
        )

        episode_data = stream_data.get_episode(season, episode)

        if user_data.streaming_provider:
            streaming_provider = user_data.streaming_provider.service.title()
            if stream_data.cached:
                streaming_provider += " (Cached)"
        else:
            streaming_provider = "Torrent"

        description_parts = [
            quality_detail,
            convert_bytes_to_readable(
                episode_data.size if episode_data else stream_data.size
            ),
            " + ".join(stream_data.languages),
            stream_data.source,
            streaming_provider,
        ]
        description = ", ".join(filter(lambda x: bool(x), description_parts))

        stream_details = {
            "name": "MediaFusion",
            "description": description,
            "infoHash": stream_data.id,
            "fileIdx": episode_data.file_index
            if episode_data
            else stream_data.file_index,
            "behaviorHints": {"bingeGroup": f"MediaFusion-{quality_detail}"},
        }

        if user_data.streaming_provider:
            base_proxy_url = f"{settings.host_url}/{secret_str}/streaming_provider?info_hash={stream_data.id}"
            if episode_data:
                base_proxy_url += f"&season={season}&episode={episode}"
            stream_details["url"] = base_proxy_url
            stream_details.pop("infoHash")
            stream_details.pop("fileIdx")
            stream_details["behaviorHints"]["notWebReady"] = True

        stream_list.append(Stream(**stream_details))

    return stream_list


def clean_name(name: str) -> str:
    # Only allow alphanumeric characters, spaces, and `.,;:_~-[]()`
    cleaned_name = re.sub(r"[^a-zA-Z0-9 .,;:_~\-()\[\]]", "", name)
    return cleaned_name


def convert_bytes_to_readable(size_bytes: int) -> str:
    """
    Convert a size in bytes into a more human-readable format.
    """
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


def get_catalogs(catalog: str, languages: list[str]) -> list[str]:
    base_catalogs = ["hdrip", "tcrip", "dubbed", "series"]
    base_catalog = catalog.split("_")[1]

    if base_catalog not in base_catalogs:
        return [catalog]

    # Generate the catalog for each supported language
    return [f"{lang.lower()}_{base_catalog}" for lang in languages]
