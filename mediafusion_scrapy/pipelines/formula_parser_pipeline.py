import logging
import re
from datetime import datetime

from scrapy.exceptions import DropItem

from db.schemas import StreamFileData
from utils.sports_parser import RESOLUTION_MAP


class FormulaParserPipeline:
    """Pipeline for parsing Formula 1/2/3 content from various uploaders.

    Uses shared sports parser utilities for resolution normalization.
    """

    def __init__(self):
        _broadcasters = (
            r"SkyF1HD|SkyF1UHD|SkyUHD|SkyF1|Sky\s*Sports.*?F1.*?(?:UHD|HD)"
            r"|Sky\s*Sports\s*Main\s*Event\s*UHD|V\s*Sport\s*Ultra\s*HD"
        )

        # Standard short-form: F1.YYYY.R01.Event.Broadcaster.Resolution
        self._short_form_pattern = re.compile(
            r"(?:Formula[.\s]?)?F?(?P<Series>[123])"
            r"\.(?P<Year>\d{4})"
            r"(?:\.R(?P<Round>\d+))?"
            r"\.(?P<Event>.+?)"
            rf"\.(?P<Broadcaster>{_broadcasters})"
            r"\.(?P<Resolution>\d+P)"
            r"(?:\.Multi)?$",
            re.IGNORECASE,
        )

        # Special/documentary: Formula 2.Title YYYY.Broadcaster.Resolution
        # Year is embedded in the event field rather than after the series number.
        self._special_form_pattern = re.compile(
            r"(?:Formula[.\s]?)(?P<Series>[123])"
            r"\.(?P<Event>.+?)\s(?P<Year>\d{4})"
            rf"\.(?P<Broadcaster>{_broadcasters})"
            r"\.(?P<Resolution>\d+P)"
            r"(?:\.Multi)?$",
            re.IGNORECASE,
        )

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
                    r"\.\s*(?P<Event>\S+)"  # Event name without space before broadcaster
                    r"\s*(?P<Broadcaster>SkyF1HD|SkyUHD|SkyF1UHD|Sky Sports Main Event UHD|Sky Sports F1 UHD|Sky Sports Arena|V Sport Ultra HD|SkyF1)"  # Broadcaster without preceding dot
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

        # Use shared RESOLUTION_MAP with local overrides
        self.smcgill1969_resolutions = {
            "4K": RESOLUTION_MAP.get("4K", "4k"),
            "SD": RESOLUTION_MAP.get("SD", "576p"),
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

        self._broadcaster_pattern = re.compile(
            r"\.?(SkyF1(?:UHD|HD)?|Sky ?Sports.*?|F1TV|V ?Sport.*?)\.?"
            r"(?:\d+[Pp]|4K|SD|UHD|2160P|1080P)",
            re.IGNORECASE,
        )

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

    def _episode_title_from_filename(self, filename: str) -> str:
        """Derive a human-readable episode title from a torrent filename.

        Strips the file extension, broadcaster tags, resolution suffixes, and
        replaces dots with spaces.  e.g.
          "Free.Practice.1.SkyF1HD.1080p.mkv" -> "Free Practice 1"
        """
        name = re.sub(r"\.[^.]+$", "", filename)
        name = self._broadcaster_pattern.split(name)[0]
        name = re.sub(r"[\._](\d+[Pp]|4K|SD|UHD|2160P|1080P).*", "", name)
        name = name.replace(".", " ").replace("_", " ").strip(" -")
        return name or filename

    @staticmethod
    def _normalize_title(raw: str) -> str:
        """Normalise egortech-style titles to pure dot-separated form.

        Handles mixed separators like ``"Formula 1 2026. Barcelona Shakedown. SkyF1HD. 1080P"``
        by converting ``". "`` → ``"."`` and inserting a dot between the series
        number and year when they're separated by a space.
        """
        title = re.sub(r"\.\.+", ".", raw)
        title = re.sub(r"\.\s+", ".", title)
        title = re.sub(
            r"(Formula\s*[123])\s+(\d{4})",
            r"\1.\2",
            title,
        )
        return title.strip(". ")

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

    def _apply_egortech_match(self, data: dict, torrent_data: dict):
        """Apply parsed regex groups to torrent_data for egortech uploads."""
        series = f"Formula {data['Series']}"
        formula_round = f"R{data['Round']}" if data.get("Round") else None
        formula_event = data.get("Event").replace(".", " ").replace(" Grand Prix", "") if data.get("Event") else None
        torrent_data.update(
            {
                "title": " ".join(
                    filter(
                        None,
                        [
                            series,
                            data.get("Year"),
                            formula_round,
                            formula_event,
                            data.get("Extra"),
                            "(egortech)",
                        ],
                    )
                ),
                "year": int(data["Year"]),
                "resolution": data["Resolution"].lower().replace("k", "K"),
            }
        )

    def parse_egortech_title(self, title, torrent_data: dict):
        for pattern in self.name_parser_patterns.get("egortech"):
            match = pattern.match(title)
            if match:
                self._apply_egortech_match(match.groupdict(), torrent_data)
                return

        # Fallback 1: short-form (F1.YYYY... / Formula.1.YYYY...)
        match = self._short_form_pattern.match(title)
        if match:
            self._apply_egortech_match(match.groupdict(), torrent_data)
            return

        # Fallback 2: special/documentary (Formula 2.Title YYYY.Broadcaster.Res)
        match = self._special_form_pattern.match(title)
        if match:
            self._apply_egortech_match(match.groupdict(), torrent_data)
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
                event, episode_name = self.event_and_episode_name_parser(data["EventAndEpisodeName"])

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
                event, episode_name = self.event_and_episode_name_parser(data["EventAndEpisodeName"])
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
                        "resolution": self.smcgill1969_resolutions.get(data["Resolution"]),
                        "event": event,
                        "episode_name": episode_name,
                    }
                )
                return
        logging.warning(f"Failed to parse title: {title}")

    def parse_egortech_description(self, torrent_data: dict):
        torrent_description = torrent_data.get("description")
        file_data = torrent_data.get("file_data")
        if not file_data:
            return

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
            contents_section = torrent_description[contains_index + len("Contains:") :].strip()

            events = [item.strip() for item in re.split(r"\r?\n", contents_section) if item.strip()]

            for index, (event, file_detail) in enumerate(zip(events, file_data)):
                parsed = self.episode_name_parser_egortech(event, torrent_data["created_at"])
                episodes.append(
                    StreamFileData(
                        season_number=1,
                        episode_number=index + 1,
                        filename=file_detail.get("filename", ""),
                        size=file_detail["size"],
                        file_index=index,
                        file_type="video",
                        episode_title=parsed["title"],
                    )
                )
        else:
            for index, file_detail in enumerate(file_data):
                file_name = file_detail.get("filename", "")
                episode_title = self._episode_title_from_filename(file_name)

                episodes.append(
                    StreamFileData(
                        season_number=1,
                        episode_number=index + 1,
                        filename=file_name,
                        size=file_detail.get("size", 0),
                        file_index=index,
                        file_type="video",
                        episode_title=episode_title,
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

        file_data = torrent_data.get("file_data")
        if not file_data:
            return

        audio_section = re.search(r"Audios:\n(.+)", torrent_description)

        if audio_section:
            audio_info = audio_section.group(1).strip()
            torrent_data["languages"] = audio_info.split(", ")

        torrent_data["poster"] = self.default_poster
        torrent_data["is_add_title_to_poster"] = True

        episode_name = torrent_data.get("episode_name", "")

        torrent_data["episodes"] = [
            StreamFileData(
                season_number=1,
                episode_number=1,
                filename=file_data[0].get("filename", ""),
                size=torrent_data.get("total_size", 0),
                file_index=0,
                file_type="video",
                episode_title=episode_name or None,
            )
        ]

    def parse_smcgill1969_description(self, torrent_data: dict):
        torrent_description = torrent_data.get("description")
        file_data = torrent_data.get("file_data")
        if not file_data:
            return

        codec_matches = re.findall(r"Codec ID\s*:\s*(\S+)", torrent_description)
        if codec_matches:
            torrent_data["codec"] = codec_matches[0]
            torrent_data["audio"] = codec_matches[1] if len(codec_matches) > 1 else None

        torrent_data["poster"] = self.default_poster
        torrent_data["is_add_title_to_poster"] = True

        episode_name = torrent_data.get("episode_name", "")

        episodes = []
        for index, file_detail in enumerate(file_data):
            file_name = file_detail.get("filename", "")
            title = self._episode_title_from_filename(file_name) if len(file_data) > 1 else episode_name

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
