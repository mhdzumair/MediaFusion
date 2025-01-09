import logging
import random
import re

from scrapy.exceptions import DropItem

from db.models import EpisodeFile
from utils.runtime_const import SPORTS_ARTIFACTS


class MotoGPParserPipeline:
    def __init__(self):
        self.name_parser_patterns = {
            "smcgill1969": [
                re.compile(
                    r"MotoGP"  # Series name with flexible space or dot
                    r"\.(?P<Year>\d{4})"  # Year
                    r"x(?P<Round>\d{2})"  # Round
                    r"\.(?P<EventAndEpisodeName>.+?)"  # Event and Episode name
                    r"\.(?P<Broadcaster>BTSportHD|TNTSportsHD)"  # Broadcaster
                    r"\.(?P<Resolution>4K|SD|1080p)"  # Resolution and Quality
                ),
                re.compile(
                    r"MotoGP"  # Series name with flexible space or dot
                    r"\.(?P<Year>\d{4})"  # Year
                    r"(?:-\d{2}-\d{2})?"  # ignore Date part starting with a hyphen
                    r"\.(?P<EventAndEpisodeName>.+?)"  # Event and Episode name
                    r"(?:\.(?P<Quality>WEB-DL|HDTV))?"  # Optional Quality
                    r"(?:\.(?P<Broadcaster>BTSportHD|TNTSportsHD))?"  # Optional Broadcaster
                    r"\.(?P<Resolution>4K|SD|1080p)"  # Resolution
                ),
            ],
        }
        self.default_poster = random.choice(SPORTS_ARTIFACTS["MotoGP Racing"]["poster"])

        self.smcgill1969_resolutions = {
            "4K": "4k",
            "SD": "576p",
            "1080p": "1080p",
        }
        self.known_countries_first_words = ["San", "Great"]

        self.title_parser_functions = {
            "smcgill1969": self.parse_smcgill1969_title,
        }
        self.description_parser_functions = {
            "smcgill1969": self.parse_smcgill1969_description,
        }

    def process_item(self, item, spider):
        uploader = item["uploader"]
        title = re.sub(r"\.\.+", ".", item["torrent_title"])
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
                event, episode_name = self.event_and_episode_name_parser(
                    data["EventAndEpisodeName"]
                )

                torrent_data.update(
                    {
                        "title": " ".join(
                            filter(
                                None,
                                [
                                    series,
                                    data.get("Year"),
                                    event,
                                    "(smcgill1969)",  # add the uploader to the title for uniqueness
                                ],
                            )
                        ),
                        "year": int(data["Year"]),
                        "resolution": self.smcgill1969_resolutions.get(
                            data["Resolution"]
                        ),
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

        episodes = []
        for index, file_detail in enumerate(torrent_data.get("file_data", [])):
            file_name = file_detail.get("filename")

            episodes.append(
                EpisodeFile(
                    season_number=1,
                    episode_number=index + 1,
                    filename=file_name,
                    size=file_detail.get("size"),
                    file_index=index,
                    title=" ".join(file_name.split(".")[1:-1]),
                    released=torrent_data.get("created_at"),
                )
            )

        torrent_data["episodes"] = episodes
