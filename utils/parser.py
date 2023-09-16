import PTN
import re

from db.schemas import Stream
from streaming_providers.exceptions import ProviderException
from utils.site_data import ALWAYS_ENABLED, LANGUAGE_CATALOGS


def extract_stream_details(stream_name, video_qualities: dict, user_data) -> list[Stream]:
    stream_list = []
    for quality_string, hash_value in video_qualities.items():
        stream_details = {}

        # Quality
        quality = re.search(
            r"(4K|\d{3,4}p|HQ HDRip|HDRip|HQ PreDVDRip|HQ Real-PreDVDRip|HQ-Real PreDVDRip|HQ-S Print)", quality_string
        )
        if quality:
            quality = quality.group(0)
        else:
            quality = "N/A"

        # Size
        size = re.search(r"(\d+(\.\d+)?(MB|GB))", quality_string)
        if size:
            size = size.group(0)
        else:
            size = "N/A"

        # Languages
        lang_dict = {
            "Tam": "Tamil",
            "Mal": "Malayalam",
            "Tel": "Telugu",
            "Kan": "Kannada",
            "Hin": "Hindi",
            "Eng": "English",
        }

        languages = []
        for short, full in lang_dict.items():
            if re.search(f"({short}|{full})", quality_string, re.IGNORECASE):
                languages.append(full)

        stream_details["name"] = "TamilBlasters"
        stream_details["stream_name"] = stream_name
        streaming_provider = user_data.streaming_provider.service.title() if user_data.streaming_provider else "Torrent"
        stream_details["description"] = f"{quality}, {size}, {' + '.join(languages)}, {streaming_provider}"
        stream_details["infoHash"] = hash_value

        stream_list.append(Stream(**stream_details))

    return stream_list


def generate_catalog_ids(preferred_movie_languages, preferred_series_languages):
    catalog_ids = ALWAYS_ENABLED.copy()

    for language, catalogs in LANGUAGE_CATALOGS.items():
        if language in preferred_movie_languages:
            catalog_ids.extend(catalogs["movie"])

        if language in preferred_series_languages:
            catalog_ids.extend(catalogs["series"])

        # Handle the dubbed option
        if "Dubbed" in preferred_movie_languages and language + "_dubbed" in catalogs["movie"]:
            catalog_ids.append(language.lower() + "_dubbed")

    return catalog_ids


def clean_name(name: str) -> str:
    # Only allow alphanumeric characters, spaces, and `.,:_-`
    cleaned_name = re.sub(r"[^a-zA-Z0-9 .,:_-]", "", name)
    return cleaned_name


def select_episode_file(torrent_files: list, episode: int, file_name_key: str) -> dict:
    """Select the file with the specified episode number."""

    for file in torrent_files:
        torrent_data = PTN.parse(file[file_name_key])
        file_episode = torrent_data.get("episode")
        if file_episode and int(file_episode) == episode:
            return file
    else:
        raise ProviderException(f"Episode {episode} not found in this torrent", "episode_not_found.mp4")
