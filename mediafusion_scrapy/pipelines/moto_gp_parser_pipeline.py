import logging
import random
import re

from scrapy.exceptions import DropItem

from db.schemas import StreamFileData
from utils.runtime_const import SPORTS_ARTIFACTS
from utils.sports_parser import RESOLUTION_MAP


class MotoGPParserPipeline:
    """Pipeline for parsing MotoGP content from smcgill1969.

    Uses shared sports parser utilities for resolution normalization.
    """

    def __init__(self):
        self.name_parser_patterns = {
            "smcgill1969": [
                re.compile(
                    r"MotoGP"
                    r"\.(?P<Year>\d{4})"
                    r"x(?P<Round>\d{2})"
                    r"\.(?P<EventAndEpisodeName>.+?)"
                    r"\.(?P<Broadcaster>BTSportHD|TNTSportsHD)"
                    r"\.(?P<Resolution>4K|SD|1080p)"
                ),
                re.compile(
                    r"MotoGP"
                    r"\.(?P<Year>\d{4})"
                    r"(?:-\d{2}-\d{2})?"
                    r"\.(?P<EventAndEpisodeName>.+?)"
                    r"(?:\.(?P<Quality>WEB-DL|HDTV))?"
                    r"(?:\.(?P<Broadcaster>BTSportHD|TNTSportsHD))?"
                    r"\.(?P<Resolution>4K|SD|1080p)"
                ),
            ],
        }
        self.default_poster = random.choice(SPORTS_ARTIFACTS["MotoGP Racing"]["poster"])

        self.smcgill1969_resolutions = {
            "4K": RESOLUTION_MAP.get("4K", "4k"),
            "SD": RESOLUTION_MAP.get("SD", "576p"),
            "1080p": "1080p",
        }
        self.known_countries_first_words = ["San", "Great"]

        self._broadcaster_pattern = re.compile(
            r"\.?(BTSportHD|TNTSportsHD)\.?"
            r"(?:\d+[Pp]|4K|SD)",
            re.IGNORECASE,
        )

        self.title_parser_functions = {
            "smcgill1969": self.parse_smcgill1969_title,
        }
        self.description_parser_functions = {
            "smcgill1969": self.parse_smcgill1969_description,
        }

    @staticmethod
    def _normalize_title(raw: str) -> str:
        """Normalise titles to pure dot-separated form."""
        title = re.sub(r"\.\.+", ".", raw)
        title = re.sub(r"\.\s+", ".", title)
        return title.strip(". ")

    def _episode_title_from_filename(self, filename: str) -> str:
        """Derive a human-readable episode title from a torrent filename."""
        name = re.sub(r"\.[^.]+$", "", filename)
        name = self._broadcaster_pattern.split(name)[0]
        name = re.sub(r"[\._](\d+[Pp]|4K|SD|UHD|2160P|1080P).*", "", name)
        name = name.replace(".", " ").replace("_", " ").strip(" -")
        return name or filename

    def process_item(self, item):
        uploader = item.get("uploader")
        if not uploader or uploader not in self.title_parser_functions:
            raise DropItem(f"Unknown or missing uploader '{uploader}' for: {item.get('torrent_title')}")
        title = self._normalize_title(item["torrent_title"])
        self.title_parser_functions[uploader](title, item)
        if not item.get("title"):
            raise DropItem(f"Title not parsed: {title}")
        self.description_parser_functions[uploader](item)
        if not item.get("episodes"):
            raise DropItem(f"Episodes not parsed: {item!r}")
        return item

    def event_and_episode_name_parser(self, event_and_episode_name):
        parts = event_and_episode_name.split(".")
        if parts[0] in self.known_countries_first_words and len(parts) > 1:
            event = f"{parts[0]} {parts[1]}"
            episode_name = " ".join(parts[2:])
        else:
            event = parts[0]
            episode_name = " ".join(parts[1:])
        return event, episode_name

    def parse_smcgill1969_title(self, title, torrent_data: dict):
        for pattern in self.name_parser_patterns.get("smcgill1969"):
            match = pattern.match(title)
            if match:
                data = match.groupdict()
                series = "MotoGP"
                event, episode_name = self.event_and_episode_name_parser(data["EventAndEpisodeName"])

                torrent_data.update(
                    {
                        "title": " ".join(
                            filter(
                                None,
                                [
                                    series,
                                    data.get("Year"),
                                    event,
                                    "(smcgill1969)",
                                ],
                            )
                        ),
                        "year": int(data["Year"]),
                        "resolution": self.smcgill1969_resolutions.get(data["Resolution"]),
                        "event": event,
                        "episode_name": episode_name,
                    }
                )
                return
        logging.warning(f"Failed to parse title: {title}")

    def parse_smcgill1969_description(self, torrent_data: dict):
        torrent_description = torrent_data.get("description")

        codec_matches = re.findall(r"Codec ID\s*:\s*(\S+)", torrent_description)
        if codec_matches:
            torrent_data["codec"] = codec_matches[0]
            torrent_data["audio"] = codec_matches[1] if len(codec_matches) > 1 else None

        torrent_data["poster"] = self.default_poster
        torrent_data["is_add_title_to_poster"] = True

        episode_name = torrent_data.get("episode_name", "")

        episodes = []
        for index, file_detail in enumerate(torrent_data.get("file_data", [])):
            file_name = file_detail.get("filename", "")
            title = (
                self._episode_title_from_filename(file_name)
                if len(torrent_data.get("file_data", [])) > 1
                else episode_name
            )

            episodes.append(
                StreamFileData(
                    season_number=1,
                    episode_number=index + 1,
                    filename=file_name,
                    size=file_detail.get("size", 0),
                    file_index=index,
                    file_type="video",
                    episode_title=title or None,
                )
            )

        torrent_data["episodes"] = episodes
