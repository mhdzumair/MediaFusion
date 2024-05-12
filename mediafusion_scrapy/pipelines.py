import asyncio
import logging
import random
import re
from datetime import datetime
from uuid import uuid4

import redis.asyncio as redis_async
import scrapy
from beanie import WriteRules
from itemadapter import ItemAdapter
from scrapy import signals
from scrapy.exceptions import DropItem
from scrapy.http.request import NO_CALLBACK
from scrapy.utils.defer import maybe_deferred_to_future

from db import crud
from db.config import settings
from db.models import (
    TorrentStreams,
    Season,
    MediaFusionSeriesMetaData,
    Episode,
)
from db.schemas import TVMetaData
from utils import torrent, const
from utils.parser import convert_size_to_bytes
from utils.runtime_const import SPORTS_ARTIFACTS


class TorrentDuplicatesPipeline:
    def __init__(self):
        self.info_hashes_seen = set()

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        if adapter["info_hash"] in self.info_hashes_seen:
            raise DropItem(f"Duplicate item found: {adapter['info_hash']}")
        else:
            self.info_hashes_seen.add(adapter["info_hash"])
            return item


class FormulaParserPipeline:
    def __init__(self):
        self.name_parser_patterns = {
            "egortech": [
                re.compile(
                    # Captures "Formula 1/2/3", year, optional round or extra, optional event, broadcaster, and resolution. "Multi" is optional.
                    r"Formula\s*(?P<Series>[123])"  # Series name (Formula 1, Formula 2, or Formula 3)
                    r"\. (?P<Year>\d{4})"  # Year
                    r"\. (?:R(?P<Round>\d+)|(?P<Extra>[^.]+))?"  # Optional round or extra info
                    r"\.* (?:(?P<Event>[^.]*?)\.)?"  # Optional event info
                    r" (?P<Broadcaster>[^.]+)"  # Broadcaster
                    r"\. (?P<Resolution>\d+P)"  # Resolution
                    r"(?:\.Multi)?"  # Optional "Multi" indicating multiple audio tracks
                ),
                re.compile(
                    # Similar to the first pattern but expects spaces around dots and captures the "Extra" field differently.
                    r"Formula\s*(?P<Series>[123])"  # Series name with optional spac
                    r"\.\s*(?P<Year>\d{4})"  # Year with flexible spacing around the period
                    r"\.\s*R(?P<Round>\d+)"  # Round number with flexible spacing
                    r"\.\s*(?P<Event>[^.]*)"  # Event name with flexible spacing
                    r"\.\s*(?P<Extra>[^.]*?)"  # Optional extra info with flexible spacing
                    r"\.\s*(?P<Broadcaster>[^.]+)"  # Broadcaster with flexible spacing
                    r"\.\s*(?P<Resolution>\d+P)"  # Resolution with flexible spacing
                ),
                re.compile(
                    r"Formula\s*(?P<Series>[123])"  # Series name with optional space
                    r"\.\s*(?P<Year>\d{4})"  # Year with flexible spacing
                    r"\.\s*R(?P<Round>\d+)"  # Round number with flexible spacing
                    r"\.\s*(?P<Event>[^\s]+)"  # Event name without space before broadcaster
                    r"\s*(?P<Broadcaster>SkyF1HD|Sky Sports Main Event UHD|Sky Sports F1 UHD|Sky Sports Arena|V Sport Ultra HD|SkyF1)"  # Broadcaster without preceding dot
                    r"\.\s*(?P<Resolution>\d+P)"  # Resolution with flexible spacing
                ),
                re.compile(
                    r"Formula(?P<Series>\d+)"  # Series name (e.g., Formula2)
                    r"(?P<Year>\d{4})\."  # Year (e.g., 2022)
                    r"Round\.(?P<Round>\d{2})\."  # Round number with 'Round.' prefix (e.g., 07)
                    r"(?P<Event>[^.]+(?:\.[^.]+)*)\."  # Event, allowing for multiple dot-separated values (e.g., British.Weekend)
                    r"(?P<Broadcaster>[A-Za-z0-9]+)\."  # Broadcaster, alphanumeric (e.g., SkyF1)
                    r"(?P<Resolution>\d+P)",  # Resolution, digits followed by 'P' (e.g., 1080P)
                ),
                re.compile(
                    r"Formula\s*(?P<Series>[12eE]+)"  # Series name, allowing 1, 2, E, with optional space
                    r"\.\s*(?P<Year>\d{4})"  # Year
                    r"\.\s*Round[ .]*(?P<Round>\d+)"  # Round, with 'Round' prefix
                    r"\.\s*(?P<Event>[^.]+(?:\.\s*[^.]+)*)"  # Event, possibly multi-word with dot and space
                    r"\.\s*(?P<Broadcaster>[^.]+(?:\s*[^.]+)*)"  # Broadcaster, possibly multi-word with space
                    r"\.\s*(?P<Resolution>\d+P)",  # Resolution
                ),
            ],
            "F1Carreras": [
                re.compile(
                    r"Formula(?P<Series>\d)\."  # Series name with flexible space or dot
                    r"(?P<Year>\d{4})"  # Year
                    r"(?:x\d{2})?"  # Optional 'x' followed by two digits
                    r"\.Round(?P<Round>\d{2})\."  # Round
                    r"(?P<EventAndEpisodeName>.+?)\."  # Event and Episode name
                    r"(?P<Resolution>\d+p)\."  # Resolution
                    r"(?P<Broadcaster>F1TV)\."  # Broadcaster
                    r"(?P<Quality>WEB-DL)\."  # Quality
                    r"(?P<AudioType>AAC2\.0)\."  # Audio type
                    r"(?P<Codec>H\.264)"  # Codec
                ),
                re.compile(
                    r"Formula(?P<Series>\d)\."  # Series name with flexible space or dot
                    r"(?P<Year>\d{4})"  # Year
                    r"(?:x\d{2})?"  # Optional 'x' followed by two digits
                    r"\.Round(?P<Round>\d{2})\."  # Round
                    r"(?P<EventAndEpisodeName>.+?)\."  # Event and Episode name (non-greedy match up to the first dot)
                    r"(?P<Broadcaster>F1TV)\."  # Broadcaster
                    r"(?P<Resolution>\d+p)\."  # Resolution
                    r"(?P<Quality>WEB-DL)\."  # Quality
                    r"(?P<AudioType>AAC2\.0)\."  # Audio type
                    r"(?:Multi\.)?"  # Optional 'Multi' indicating multiple audio tracks
                    r"(?P<Codec>H\.264)"  # Codec
                ),
                re.compile(
                    r"Formula[.\s]?(?P<Series>\d)\."  # Series name with flexible space or dot
                    r"(?P<Year>\d{4})"  # Year
                    r"(?:x\d{2})?"  # Optional 'x' followed by two digits
                    r"\.Round(?P<Round>\d{2})\."  # Round
                    r"(?P<EventAndEpisodeName>.+?)\."  # Event and Episode name (non-greedy match up to the first dot)
                    r"(?P<Broadcaster>F1TV)\."  # Broadcaster
                    r"(?P<Resolution>\d+p)\."  # Resolution
                    r"(?:\.(?P<Quality>WEB-DL))?"  # Optional Quality
                    r"(?:\.(?P<AudioType>AAC2\.0))?"  # Optional Audio type
                    r"(?:Multi\.)?"  # Optional 'Multi' indicating multiple audio tracks
                    r"(?:\.(?P<Codec>H\.264))?"  # Optional Codec
                ),
                re.compile(
                    r"Formula[.\s]?(?P<Series>\d)\."  # Series name with flexible space or dot
                    r"(?P<Year>\d{4})"  # Year
                    r"(?:x\d{2})?"  # Optional 'x' followed by two digits
                    r"\.Round(?P<Round>\d{2})\."  # Round
                    r"(?P<EventAndEpisodeName>.+?)\."  # Event and Episode name
                    r"(?:\.(?P<Broadcaster>SkySports|F1TV))?"  # Optional Broadcaster
                    r"(?P<Resolution>\d+p)"  # Resolution
                    r"(?:\.(?P<Quality>WEB-DL))?"  # Optional Quality
                    r"(?:\.(?P<AudioType>AAC2\.0))?"  # Optional Audio type
                    r"(?:\.Multi)?"  # Optional 'Multi'
                    r"(?:\.(?P<Codec>H\.264))?"  # Optional Codec
                ),
                re.compile(
                    r"Formula[.\s]?(?P<Series>\d)\."  # Series name with flexible space or dot
                    r"(?P<Year>\d{4})"  # Year
                    r"(?:x\d{2})?"  # Optional 'x' followed by two digits
                    r"\.Round(?P<Round>\d{2})\."  # Round
                    r"(?P<EventAndEpisodeName>.+?)\."  # Event and Episode name
                    r"(?P<Resolution>\d+p)"  # Resolution
                    r"(?:\.(?P<Quality>WEB-DL|HDTV))?"  # Optional Quality
                    r"(?:\.(?P<Broadcaster>SkySports|F1TV))?"  # Optional Broadcaster
                    r"(?:\.(?P<AudioType>AAC2\.0))?"  # Optional Audio type
                    r"(?:\.Multi)?"  # Optional 'Multi'
                    r"(?:\.(?P<Codec>H\.264))?"  # Optional Codec
                ),
            ],
            "smcgill1969": [
                re.compile(
                    r"Formula[.\s]?(?P<Series>\d)"  # Series name with flexible space or dot
                    r"\.(?P<Year>\d{4})"  # Year
                    r"x(?P<Round>\d{2})"  # Round
                    r"\.(?P<EventAndEpisodeName>.+?)"  # Event and Episode name
                    r"\.(?P<Broadcaster>SkyF1(UHD|HD))"  # Broadcaster
                    r"\.(?P<Resolution>4K|SD|1080p)"  # Resolution and Quality
                ),
                re.compile(
                    r"Formula[.\s]?(?P<Series>\d)"  # Series name with flexible space or dot
                    r"\.(?P<Year>\d{4})"  # Year
                    r"(?:-\d{2}-\d{2})?"  # ignore Date part starting with a hyphen
                    r"\.(?P<EventAndEpisodeName>.+?)"  # Event and Episode name
                    r"(?:\.(?P<Quality>WEB-DL|HDTV))?"  # Optional Quality
                    r"(?:\.(?P<Broadcaster>SkySports|F1TV))?"  # Optional Broadcaster
                    r"\.(?P<Resolution>4K|SD|1080p)"  # Resolution
                ),
            ],
        }

        self.egortech_episode_name_parser_pattern = [
            re.compile(
                r"^(?P<Event>.+?)"  # Capture everything as the event up to the event until the date
                r"\s\((?P<Date>\d{2}\.\d{2}\.\d{4})\)"  # Date in format DD.MM.YYYY
            )
        ]

        self.default_poster = "https://i.postimg.cc/DZP4x8kM/Poster1.jpg"

        self.smcgill1969_resolutions = {
            "4K": "4K",
            "SD": "576p",
            "1080p": "1080p",
        }

        self.known_countries_first_words = [
            "Abu",
            "Arabia",
            "Great",
            "Las",
            "Emilia",
            "Saudi",
        ]

        self.title_parser_functions = {
            "egortech": self.parse_egortech_title,
            "F1Carreras": self.parse_f1carreras_title,
            "smcgill1969": self.parse_smcgill1969_title,
        }
        self.description_parser_functions = {
            "egortech": self.parse_egortech_description,
            "F1Carreras": self.parse_f1carreras_description,
            "smcgill1969": self.parse_smcgill1969_description,
        }

    def process_item(self, item, spider):
        uploader = item["uploader"]
        title = re.sub(r"\.\.+", ".", item["torrent_name"])
        self.title_parser_functions[uploader](title, item)
        if not item.get("title"):
            raise DropItem(f"Title not parsed: {title}")
        self.description_parser_functions[uploader](item)
        if not item.get("episodes"):
            raise DropItem(f"Episodes not parsed: {item!r}")
        return item

    def parse_egortech_title(self, title, torrent_data: dict):
        for pattern in self.name_parser_patterns.get("egortech"):
            match = pattern.match(title)
            if match:
                data = match.groupdict()
                series = f"Formula {data['Series']}"
                formula_round = f"R{data['Round']}" if data.get("Round") else None
                formula_event = (
                    data.get("Event").replace(".", " ").replace(" Grand Prix", "")
                    if data.get("Event")
                    else None
                )
                torrent_data.update(
                    {
                        "title": " ".join(  # Join the series, year, round, event, and extra fields
                            filter(
                                None,
                                [
                                    series,
                                    data.get("Year"),
                                    formula_round,
                                    formula_event,
                                    data.get("Extra"),
                                    "(egortech)",  # add the uploader to the title for uniqueness
                                ],
                            )
                        ),
                        "year": int(data["Year"]),
                        "resolution": data["Resolution"].lower().replace("k", "K"),
                    }
                )
                return
        logging.warning(f"Failed to parse title: {title}")

    def event_and_episode_name_parser(self, event_and_episode_name):
        parts = event_and_episode_name.split(".")
        if parts[0] in self.known_countries_first_words and len(parts) > 1:
            event = f"{parts[0]} {parts[1]}"
            episode_name = " ".join(parts[2:])
        else:
            event = parts[0]
            episode_name = " ".join(parts[1:])
        return event, episode_name

    def parse_f1carreras_title(self, title, torrent_data: dict):
        for pattern in self.name_parser_patterns.get("F1Carreras"):
            match = pattern.match(title)
            if match:
                data = match.groupdict()
                series = f"Formula {data['Series']}"
                event, episode_name = self.event_and_episode_name_parser(
                    data["EventAndEpisodeName"]
                )

                formula_round = f"R{data['Round']}" if data.get("Round") else None

                torrent_data.update(
                    {
                        "title": " ".join(
                            filter(
                                None,
                                [
                                    series,
                                    data.get("Year"),
                                    formula_round,
                                    event,
                                    "(F1Carreras)",  # add the uploader to the title for uniqueness
                                ],
                            )
                        ),
                        "year": int(data["Year"]),
                        "resolution": data["Resolution"].lower().replace("k", "K"),
                        "quality": data["Quality"],
                        "audio_type": data["AudioType"],
                        "codec": data["Codec"],
                        "event": event,
                        "episode_name": episode_name,
                    }
                )
                return
        logging.warning(f"Failed to parse title: {title}")

    def parse_smcgill1969_title(self, title, torrent_data: dict):
        for pattern in self.name_parser_patterns.get("smcgill1969"):
            match = pattern.match(title)
            if match:
                data = match.groupdict()
                series = f"Formula {data['Series']}"
                event, episode_name = self.event_and_episode_name_parser(
                    data["EventAndEpisodeName"]
                )
                formula_round = f"R{data['Round']}" if data.get("Round") else None

                torrent_data.update(
                    {
                        "title": " ".join(
                            filter(
                                None,
                                [
                                    series,
                                    data.get("Year"),
                                    formula_round,
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

    def parse_egortech_description(self, torrent_data: dict):
        torrent_description = torrent_data.get("description")
        file_details = torrent_data.get("file_details")

        quality_match = re.search(r"Quality:\s*(\S+)", torrent_description)
        codec_match = re.search(r"Video:\s*([A-Za-z0-9]+)", torrent_description)
        audio_match = re.search(r"Audio:\s*([A-Za-z0-9. ]+)", torrent_description)

        if quality_match:
            torrent_data["quality"] = quality_match.group(1)
        if codec_match:
            torrent_data["codec"] = codec_match.group(1)
        if audio_match:
            torrent_data["audio"] = audio_match.group(1)

        contains_index = torrent_description.find("Contains:")
        episodes = []

        if contains_index != -1:
            contents_section = torrent_description[
                contains_index + len("Contains:") :
            ].strip()

            events = [
                item.strip()
                for item in re.split(r"\r?\n", contents_section)
                if item.strip()
            ]

            for index, (event, file_detail) in enumerate(zip(events, file_details)):
                data = self.episode_name_parser_egortech(
                    event, torrent_data["created_at"]
                )
                episodes.append(
                    Episode(
                        episode_number=index + 1,
                        filename=file_detail.get("file_name"),
                        size=convert_size_to_bytes(file_detail.get("file_size")),
                        file_index=index,
                        title=data["title"],
                        released=data["date"],
                    )
                )
        else:
            # logic to parse episode details directly from file details when description does not contain "Contains:"
            for index, file_detail in enumerate(file_details):
                file_name = file_detail.get("file_name")
                file_size = file_detail.get("file_size")
                data = self.episode_name_parser_egortech(
                    file_name, torrent_data["created_at"]
                )
                episodes.append(
                    Episode(
                        episode_number=index + 1,
                        filename=file_name,
                        size=convert_size_to_bytes(file_size),
                        file_index=index,
                        title=data["title"],
                        released=data["date"],
                    )
                )

        torrent_data["episodes"] = episodes

    def episode_name_parser_egortech(self, title, torrent_created_at=None):
        for pattern in self.egortech_episode_name_parser_pattern:
            match = pattern.search(title)
            if match:
                data = match.groupdict()
                parsed_data = {
                    "title": data["Event"].strip(),
                    "date": datetime.strptime(data["Date"], "%d.%m.%Y"),
                }
                return parsed_data
        return {"title": title, "date": torrent_created_at}

    def parse_f1carreras_description(self, torrent_data: dict):
        torrent_description = torrent_data.get("description")
        if torrent_description is None:
            return

        audio_section = re.search(r"Audios:\n(.+)", torrent_description)

        if audio_section:
            audio_info = audio_section.group(1).strip()
            torrent_data["languages"] = audio_info.split(", ")

        torrent_data["poster"] = self.default_poster
        torrent_data["is_add_title_to_poster"] = True

        torrent_data["episodes"] = [
            Episode(
                episode_number=1,
                filename=torrent_data["file_details"][0].get("file_name"),
                size=torrent_data.get("total_size"),
                title=torrent_data.get("episode_name"),
                released=torrent_data.get("created_at"),
                file_index=1,
            )
        ]

    def parse_smcgill1969_description(self, torrent_data: dict):
        torrent_description = torrent_data.get("description")

        codec_matches = re.findall(r"Codec ID\s*:\s*(\S+)", torrent_description)
        if codec_matches:
            torrent_data["codec"] = codec_matches[0]
            torrent_data["audio"] = codec_matches[1] if len(codec_matches) > 1 else None

        torrent_data["poster"] = self.default_poster
        torrent_data["is_add_title_to_poster"] = True

        episodes = []
        for index, file_detail in enumerate(torrent_data.get("file_details", [])):
            file_name = file_detail.get("file_name")
            file_size = file_detail.get("file_size")

            episodes.append(
                Episode(
                    episode_number=index + 1,
                    filename=file_name,
                    size=convert_size_to_bytes(file_size),
                    file_index=index,
                    title=" ".join(file_name.split(".")[1:-1]),
                    released=torrent_data.get("created_at"),
                )
            )

        torrent_data["episodes"] = episodes


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
        self.default_poster = random.choice(SPORTS_ARTIFACTS["MotoGP"]["poster"])

        self.smcgill1969_resolutions = {
            "4K": "4K",
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
        title = re.sub(r"\.\.+", ".", item["torrent_name"])
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
        for index, file_detail in enumerate(torrent_data.get("file_details", [])):
            file_name = file_detail.get("file_name")
            file_size = file_detail.get("file_size")

            episodes.append(
                Episode(
                    episode_number=index + 1,
                    filename=file_name,
                    size=convert_size_to_bytes(file_size),
                    file_index=index,
                    title=" ".join(file_name.split(".")[1:-1]),
                    released=torrent_data.get("created_at"),
                )
            )

        torrent_data["episodes"] = episodes


class QueueBasedPipeline:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing_task = None

    async def init(self):
        self.processing_task = asyncio.create_task(self.process_queue())

    async def close(self):
        await self.queue.join()
        self.processing_task.cancel()

    @classmethod
    def from_crawler(cls, crawler):
        p = cls()
        crawler.signals.connect(p.init, signal=signals.spider_opened)
        crawler.signals.connect(p.close, signal=signals.spider_closed)
        return p

    async def process_item(self, item, spider):
        # Instead of processing the item directly, add it to the queue
        await self.queue.put((item, spider))
        return item

    async def process_queue(self):
        logging.info("Starting processing queue")
        while True:
            item, spider = await self.queue.get()
            try:
                await self.parse_item(item, spider)
            except Exception as e:
                logging.error(f"Error processing item: {e}")
            finally:
                self.queue.task_done()

    async def parse_item(self, item, spider):
        raise NotImplementedError


class EventSeriesStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = redis_async.Redis.from_url(settings.redis_url)

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        series = await MediaFusionSeriesMetaData.find_one(
            {"title": item["title"]}, fetch_links=True
        )

        if not series:
            meta_id = f"mf{uuid4().fields[-1]}"
            poster = item.get("poster")
            background = item.get("background")

            # Create an initial entry for the series
            series = MediaFusionSeriesMetaData(
                id=meta_id,
                title=item["title"],
                year=item["year"],
                poster=poster,
                background=background,
                streams=[],
                is_poster_working=bool(poster),
                is_add_title_to_poster=item.get("is_add_title_to_poster", False),
            )
            await series.insert()
            logging.info("Added series %s", series.title)

        meta_id = series.id

        stream = next((s for s in series.streams if s.id == item["info_hash"]), None)
        if stream is None:
            # Create the stream
            stream = TorrentStreams(
                id=item["info_hash"],
                torrent_name=item["torrent_name"],
                announce_list=item["announce_list"],
                size=item["total_size"],
                languages=item["languages"],
                resolution=item.get("resolution"),
                codec=item.get("codec"),
                quality=item.get("quality"),
                audio=item.get("audio"),
                encoder=item.get("encoder"),
                source=item["source"],
                catalog=item["catalog"],
                created_at=item["created_at"],
                season=Season(season_number=1, episodes=item["episodes"]),
                meta_id=meta_id,
                seeders=item["seeders"],
            )
            # Add the stream to the series
            series.streams.append(stream)
            logging.info(
                "Added stream %s to series %s", stream.torrent_name, series.title
            )

        self.organize_episodes(series)

        await series.save(link_rule=WriteRules.WRITE)
        logging.info("Updated series %s", series.title)
        await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])

        return item

    def organize_episodes(self, series):
        # Flatten all episodes from all streams and sort by release date
        all_episodes = sorted(
            (
                episode
                for stream in series.streams
                for episode in stream.season.episodes
            ),
            key=lambda e: (
                e.released.date(),
                e.filename,
            ),  # Sort primarily by released date, then by filename
        )

        # Assign episode numbers, ensuring the same title across different qualities gets the same number
        episode_number = 1
        last_title = None
        for episode in all_episodes:
            if episode.title != last_title:
                if last_title:
                    episode_number = episode_number + 1
                last_title = episode.title

            episode.episode_number = episode_number

        # Now distribute episodes back to their respective streams, ensuring they are in the correct order
        for stream in series.streams:
            stream.season.episodes.sort(
                key=lambda e: e.episode_number
            )  # Ensure episodes are ordered by episode number


class TVStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item, spider):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        tv_metadata = TVMetaData.model_validate(item)
        await crud.save_tv_channel_metadata(tv_metadata)
        return item


class TorrentDownloadAndParsePipeline:
    async def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        torrent_link = adapter.get("torrent_link")

        if not torrent_link:
            raise DropItem(f"No torrent link found in item: {item}")

        response = await maybe_deferred_to_future(
            spider.crawler.engine.download(
                scrapy.Request(torrent_link, callback=NO_CALLBACK)
            )
        )

        if response.status != 200:
            spider.logger.error(
                f"Failed to download torrent file: {response.url} with status {response.status}"
            )
            return item

        # Validate the content-type of the response
        if "application/x-bittorrent" not in response.headers.get(
            "Content-Type", b""
        ).decode("utf-8", "ignore"):
            spider.logger.warning(
                f"Unexpected Content-Type for {response.url}: {response.headers.get('Content-Type')}"
            )
            return item

        torrent_metadata = torrent.extract_torrent_metadata(
            response.body, item.get("is_parse_ptn", True)
        )

        if not torrent_metadata:
            raise DropItem(f"Failed to extract torrent metadata: {item}")

        item.update(torrent_metadata)
        return item


class SportVideoParserPipeline:
    RESOLUTIONS = {
        "3840x2160": "4K",
        "2560x1440": "1440p",
        "1920x1080": "1080p",
        "1280x720": "720p",
        "854x480": "480p",
        "640x360": "360p",
        "426x240": "240p",
    }

    def __init__(self):
        self.title_regex = re.compile(r"^.*?\s(\d{2}\.\d{2}\.\d{4})")

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        if "title" not in adapter:
            raise DropItem(f"title not found in item: {item}")

        match = self.title_regex.search(adapter["title"]) or self.title_regex.search(
            adapter["torrent_name"]
        )
        if match:
            date_str = match.group(1)
            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
        else:
            date_obj = datetime.now()

        # Try to match or infer resolution
        raw_resolution = adapter.get("aspect_ratio", "").replace(" ", "")
        resolution = self.infer_resolution(raw_resolution)

        if "video_codec" in adapter:
            codec = re.sub(r"\s+", "", adapter["video_codec"]).replace(",", " - ")
            item["codec"] = codec

        if "length" in adapter or "lenght" in adapter:
            item["runtime"] = re.sub(
                r"\s+", "", adapter.get("length", adapter.get("lenght", ""))
            )

        item.update(
            {
                "created_at": date_obj.strftime("%Y-%m-%d"),
                "year": date_obj.year,
                "languages": [re.sub(r"[ .]", "", item["language"])],
                "is_imdb": False,
                "resolution": resolution,
            }
        )
        return item

    def infer_resolution(self, aspect_ratio):
        # Normalize the aspect_ratio string by replacing common variants of "x" with the standard one
        normalized_aspect_ratio = aspect_ratio.replace("×", "x").replace("х", "x")

        # Direct match in RESOLUTIONS dictionary
        resolution = self.RESOLUTIONS.get(normalized_aspect_ratio)
        if resolution:
            return resolution

        # Attempt to infer based on height
        height = (
            normalized_aspect_ratio.split("x")[-1]
            if "x" in normalized_aspect_ratio
            else None
        )
        if height:
            for res, label in self.RESOLUTIONS.items():
                if res.endswith(height):
                    return label

        # Fallback to checking against common labels
        for label in ["4K", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p"]:
            if label in normalized_aspect_ratio:
                return label

        # If no match found
        return None


class MovieStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item, spider):
        if "title" not in item:
            raise DropItem(f"title not found in item: {item}")

        if item.get("type") != "movie":
            return item

        await crud.save_movie_metadata(item, item.get("is_imdb", True))
        return item


class SeriesStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item, spider):
        if "title" not in item:
            raise DropItem(f"title not found in item: {item}")

        if item.get("type") != "series":
            return item
        await crud.save_series_metadata(item)
        return item


class LiveEventStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = redis_async.Redis.from_url(settings.redis_url)

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            raise DropItem(f"name not found in item: {item}")

        await crud.save_events_data(self.redis, item)
        return item


class RedisCacheURLPipeline:
    def __init__(self):
        self.redis = redis_async.Redis.from_url(settings.redis_url)

    async def close(self):
        await self.redis.aclose()

    @classmethod
    def from_crawler(cls, crawler):
        p = cls()
        crawler.signals.connect(p.close, signal=signals.spider_closed)
        return p

    async def process_item(self, item, spider):
        if "webpage_url" not in item:
            raise DropItem(f"webpage_url not found in item: {item}")

        await self.redis.sadd(item["scraped_url_key"], item["webpage_url"])
        return item


class LiveStreamResolverPipeline:
    async def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        stream_url = adapter.get("stream_url")
        stream_headers = adapter.get("stream_headers")
        if not stream_headers:
            referer = adapter.get("referer")
            stream_headers = {"Referer": referer} if referer else {}

        if not stream_url:
            raise DropItem(f"No stream URL found in item: {item}")

        response = await maybe_deferred_to_future(
            spider.crawler.engine.download(
                scrapy.Request(
                    stream_url,
                    callback=NO_CALLBACK,
                    headers=stream_headers,
                    method="HEAD",
                    dont_filter=True,
                )
            )
        )
        content_type = response.headers.get("Content-Type", b"").decode().lower()

        if response.status == 200 and content_type in const.M3U8_VALID_CONTENT_TYPES:
            stream_headers.update(
                {
                    "User-Agent": response.request.headers.get("User-Agent").decode(),
                    "Referer": response.request.headers.get("Referer").decode(),
                }
            )

            item["streams"].append(
                {
                    "name": adapter["stream_name"],
                    "url": adapter["stream_url"],
                    "source": adapter["stream_source"],
                    "behaviorHints": {
                        "notWebReady": True,
                        "proxyHeaders": {
                            "request": stream_headers,
                        },
                    },
                }
            )
            return item
        else:
            raise DropItem(
                f"Invalid M3U8 URL: {stream_url} with Content-Type: {content_type} response: {response.status}"
            )
