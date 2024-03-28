import logging
import os
import re
from datetime import datetime
from uuid import uuid4

import redis.asyncio as redis_async
from beanie import WriteRules
from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem

from db import crud
from db.config import settings
from db.models import (
    TorrentStreams,
    Season,
    MediaFusionSeriesMetaData,
    Episode,
)
from db.schemas import TVMetaData
from utils import torrent
from utils.parser import convert_size_to_bytes


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
        self.egortech_name_parser_patterns = [
            re.compile(
                # Captures "Formula 1/2/3", year, optional round or extra, optional event, broadcaster, and resolution. "Multi" is optional.
                r"(?P<Series>Formula\s*[123])"  # Series name (Formula 1, Formula 2, or Formula 3)
                r"\. (?P<Year>\d{4})"  # Year
                r"\. (?:R(?P<Round>\d+)|(?P<Extra>[^.]+))?"  # Optional round or extra info
                r"\.* (?:(?P<Event>[^.]*?)\.)?"  # Optional event info
                r" (?P<Broadcaster>[^.]+)"  # Broadcaster
                r"\. (?P<Resolution>\d+P)"  # Resolution
                r"(?:\.Multi)?"  # Optional "Multi" indicating multiple audio tracks
            ),
            re.compile(
                # Similar to the first pattern but expects spaces around dots and captures the "Extra" field differently.
                r"(?P<Series>Formula\s*[123])"  # Series name with optional spac
                r"\.\s*(?P<Year>\d{4})"  # Year with flexible spacing around the period
                r"\.\s*R(?P<Round>\d+)"  # Round number with flexible spacing
                r"\.\s*(?P<Event>[^.]*)"  # Event name with flexible spacing
                r"\.\s*(?P<Extra>[^.]*?)"  # Optional extra info with flexible spacing
                r"\.\s*(?P<Broadcaster>[^.]+)"  # Broadcaster with flexible spacing
                r"\.\s*(?P<Resolution>\d+P)"  # Resolution with flexible spacing
            ),
            re.compile(
                r"(?P<Series>Formula\s*[123])"  # Series name with optional space
                r"\.\s*(?P<Year>\d{4})"  # Year with flexible spacing
                r"\.\s*R(?P<Round>\d+)"  # Round number with flexible spacing
                r"\.\s*(?P<Event>[^\s]+)"  # Event name without space before broadcaster
                r"\s*(?P<Broadcaster>SkyF1HD|Sky Sports Main Event UHD|Sky Sports F1 UHD|Sky Sports Arena|V Sport Ultra HD|SkyF1)"  # Broadcaster without preceding dot
                r"\.\s*(?P<Resolution>\d+P)"  # Resolution with flexible spacing
            ),
            re.compile(
                r"(?P<Series>Formula\d+)\."  # Series name (e.g., Formula2)
                r"(?P<Year>\d{4})\."  # Year (e.g., 2022)
                r"Round\.(?P<Round>\d{2})\."  # Round number with 'Round.' prefix (e.g., 07)
                r"(?P<Event>[^.]+(?:\.[^.]+)*)\."  # Event, allowing for multiple dot-separated values (e.g., British.Weekend)
                r"(?P<Broadcaster>[A-Za-z0-9]+)\."  # Broadcaster, alphanumeric (e.g., SkyF1)
                r"(?P<Resolution>\d+P)",  # Resolution, digits followed by 'P' (e.g., 1080P)
            ),
            re.compile(
                r"(?P<Series>Formula\s*[12eE]+)"  # Series name, allowing 1, 2, E, with optional space
                r"\.\s*(?P<Year>\d{4})"  # Year
                r"\.\s*Round\s*(?P<Round>\d+)"  # Round, with 'Round' prefix
                r"\.\s*(?P<Event>[^.]+(?:\.\s*[^.]+)*)"  # Event, possibly multi-word with dot and space
                r"\.\s*(?P<Broadcaster>[^.]+(?:\s*[^.]+)*)"  # Broadcaster, possibly multi-word with space
                r"\.\s*(?P<Resolution>\d+P)",  # Resolution
            ),
        ]

        self.egortech_episode_name_parser_pattern = [
            re.compile(
                r"^(?P<Event>.+?)"  # Capture everything as the event up to the event until the date
                r"\s\((?P<Date>\d{2}\.\d{2}\.\d{4})\)"  # Date in format DD.MM.YYYY
            )
        ]

        self.title_parser_functions = {
            "egortech": self.parse_egortech_title,
        }
        self.description_parser_functions = {
            "egortech": self.parse_egortech_description,
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
        for pattern in self.egortech_name_parser_patterns:
            match = pattern.match(title)
            if match:
                data = match.groupdict()
                formula_round = f"R{data['Round']}" if data.get("Round") else None
                formula_event = (
                    data.get("Event").replace(".", " ") if data.get("Event") else None
                )
                torrent_data.update(
                    {
                        "title": " ".join(  # Join the series, year, round, event, and extra fields
                            filter(
                                None,
                                [
                                    data.get("Series"),
                                    data.get("Year"),
                                    formula_round,
                                    formula_event,
                                    data.get("Extra"),
                                ],
                            )
                        ),
                        "year": int(data["Year"]),
                        "resolution": data["Resolution"].lower(),
                    }
                )

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

            items = [
                item.strip()
                for item in re.split(r"\r?\n", contents_section)
                if item.strip()
            ]

            for index, (item, file_detail) in enumerate(zip(items, file_details)):
                data = self.episode_name_parser_egortech(item)
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
                data = self.episode_name_parser_egortech(file_name)
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

    def episode_name_parser_egortech(self, title):
        for pattern in self.egortech_episode_name_parser_pattern:
            match = pattern.search(title)
            if match:
                data = match.groupdict()
                parsed_data = {
                    "title": data["Event"].strip(),
                    "date": datetime.strptime(data["Date"], "%d.%m.%Y"),
                }
                return parsed_data
        return {"title": title, "date": None}


class FormulaStorePipeline:
    def __init__(self):
        self.redis = redis_async.Redis.from_url(settings.redis_url)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.redis.aclose()

    async def process_item(self, item, spider):
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
            )
            await series.insert()
            logging.info("Added series %s", series.title)

        meta_id = series.id

        existing_stream = next(
            (s for s in series.streams if s.id == item["info_hash"]),
            None,
        )
        if existing_stream:
            # If the stream already exists, return
            logging.info("Stream already exists for series %s", series.title)
            return

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

        await series.save(link_rule=WriteRules.WRITE)
        logging.info("Updated series %s", series.title)
        await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])

        return item


class TVStorePipeline:
    async def process_item(self, item, spider):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        tv_metadata = TVMetaData.model_validate(item)
        await crud.save_tv_channel_metadata(tv_metadata)
        return item


class TorrentFileParserPipeline:
    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        if "torrent_file_path" not in adapter:
            raise DropItem(f"torrent_file_path not found in item: {item}")

        with open(item["torrent_file_path"], "rb") as torrent_file:
            torrent_metadata = torrent.extract_torrent_metadata(
                torrent_file.read(), item.get("is_parse_ptn", True)
            )
        os.remove(item["torrent_file_path"])
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
        self.title_regex = re.compile(r"^(.*?)\s(\d{2}\.\d{2}\.\d{4})$")

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        if "title" not in adapter:
            raise DropItem(f"title not found in item: {item}")

        match = self.title_regex.search(adapter["title"])
        if not match:
            spider.logger.warning(
                f"Title format is incorrect, cannot extract date: {adapter['title']}"
            )
            raise DropItem(f"Cannot parse date from title: {adapter['title']}")

        title, date_str = match.groups()
        date_obj = datetime.strptime(date_str, "%d.%m.%Y")

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
                "title": title.strip(),
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


class MovieStorePipeline:
    async def process_item(self, item, spider):
        if "title" not in item:
            raise DropItem(f"title not found in item: {item}")

        await crud.save_movie_metadata(item, item.get("is_imdb", True))
        return item


class LiveEventStorePipeline:
    def __init__(self):
        self.redis = redis_async.Redis.from_url(settings.redis_url)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.redis.aclose()

    async def process_item(self, item, spider):
        if "title" not in item:
            raise DropItem(f"name not found in item: {item}")

        await crud.save_events_data(self.redis, item)
        return item
