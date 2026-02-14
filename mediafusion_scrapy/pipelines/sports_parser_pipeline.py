import logging
import random
import re
from datetime import datetime

from scrapy.exceptions import DropItem

from scrapers.scraper_tasks import meta_fetcher
from scrapers.tmdb_data import search_tmdb
from utils.runtime_const import SPORTS_ARTIFACTS
from utils.sports_parser import DATE_PATTERNS, extract_date_from_title


class BaseParserPipeline:
    """Base pipeline for parsing sports content (WWE, UFC).

    Uses shared sports parser utilities for date extraction.
    """

    name_parser_patterns = [
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Date>\d{{4}}\.\d{{2}}\.\d{{2}})[.\s](?P<Resolution>\d{{3,4}}[pi])[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Date>\d{{4}}\.\d{{2}}\.\d{{2}})[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Date>\d{{2}}\.\d{{2}}\.\d{{4}})[.\s](?P<Resolution>\d{{3,4}}[pi])[.\s](?P<Language>\w+)",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Date>\d{{2}}\.\d{{2}}\.\d{{4}})[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Date>\d{{4}}-\d{{2}}-\d{{2}})[.\s](?P<Resolution>\d{{3,4}}[pi])[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Date>\d{{4}}-\d{{2}}-\d{{2}})[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Year>\d{{4}})[.\s](?P<Resolution>\d{{3,4}}[pi])[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Year>\d{{4}})[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](?P<Resolution>\d{{3,4}}[pi])[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s](HDTV|WEB|WEB-DL|Sportnet360)[.\s]",
        r"{event}[.\s](?P<Event>[\w\s.]+)[.\s]",
    ]
    imdb_cache = {}

    def __init__(self, event_name, known_imdb_ids=None, static_poster=None, static_logo=None):
        self.event_name = event_name.lower()
        self.name_parser_patterns = [
            re.compile(pattern.format(event=event_name), re.IGNORECASE) for pattern in self.name_parser_patterns
        ]
        self.known_imdb_ids = known_imdb_ids or {}
        self.static_poster = static_poster or {}
        self.static_logo = static_logo or {}

    async def process_item(self, item, spider):
        title = re.sub(r"\.\.+", ".", item["torrent_title"])
        self.parse_title(title, item)
        if not item.get("title"):
            raise DropItem(f"Title not parsed: {title}")
        item.update(
            dict(
                type="movie",
                is_search_imdb_title=False,
                genres=[self.event_name.upper()],
                is_add_title_to_poster=True,
            )
        )
        self.parse_description(item)
        await self.update_imdb_data(item)
        return item

    def parse_title(self, title, torrent_data: dict):
        for pattern in self.name_parser_patterns:
            match = pattern.match(title)
            if match:
                data = match.groupdict()
                event = data.get("Event").replace(".", " ").strip()
                event_title = f"{self.event_name.upper()} {event}"
                date_str = data.get("Date", data.get("Year", ""))

                # Try to parse date using shared patterns
                date = None
                for pattern_str, date_format in DATE_PATTERNS:
                    if re.match(pattern_str, date_str):
                        try:
                            date = datetime.strptime(date_str, date_format).date()
                            break
                        except ValueError:
                            continue

                # Fallback: try shared date extraction from full title
                if not date:
                    extracted, _ = extract_date_from_title(title)
                    date = extracted

                # Final fallback to created_at date
                if not date:
                    date = torrent_data["created_at"].date()

                torrent_data.update(
                    {
                        "title": f"{event_title} {date_str}".strip(),
                        "date": date,
                        "resolution": (data.get("Resolution").replace("i", "p") if data.get("Resolution") else None),
                        "event": event_title,
                        "year": date.year,
                    }
                )
                return
        logging.warning(f"Failed to parse title: {title}")

    def parse_description(self, torrent_data: dict):
        description = torrent_data.pop("description")

        codec_match = re.search(r"Codec info\s*=\s*(\S+)", description)
        audio_match = re.search(r"Audio\s*Codec info\s*=\s*(\S+)", description)
        resolution_match = re.search(r"Resolution\s*=\s*\d+x(\d+)", description)

        if codec_match:
            torrent_data["codec"] = codec_match.group(1)
        if audio_match:
            torrent_data["audio"] = audio_match.group(1)
        if resolution_match:
            torrent_data["resolution"] = resolution_match.group(1) + "p"

        largest_file = max(
            torrent_data["file_data"],
            key=lambda x: x["size"],
        )
        largest_file_index = torrent_data["file_data"].index(largest_file)
        torrent_data["largest_file"] = {
            "index": largest_file_index,
            "filename": largest_file["filename"],
        }

    async def update_imdb_data(self, torrent_data: dict):
        year = torrent_data["date"].year
        title = torrent_data.get("event")
        torrent_data["logo"] = self.static_logo.get(title.lower())
        imdb_id = self.known_imdb_ids.get(title.lower())
        if not imdb_id:
            result = self.imdb_cache.get(f"{title}_{year}")
            if not result:
                result = await meta_fetcher.search_metadata(title, year, torrent_data["date"])
            if not result:
                logging.warning(f"Failed to find IMDb title for {title}")
                if not torrent_data["poster"]:
                    torrent_data["poster"] = random.choice(SPORTS_ARTIFACTS[self.event_name.upper()]["poster"])
                return

            imdb_id = result.get("imdb_id")
            self.imdb_cache[f"{title}_{year}"] = result

            if result.get("type") != "series":
                torrent_data["id"] = imdb_id
                torrent_data.update(
                    dict(
                        poster=result.get("poster"),
                        background=result.get("background"),
                        runtime=result.get("runtime"),
                        imdb_rating=result.get("imdb_rating"),
                        description=result.get("description"),
                        stars=result.get("stars"),
                        genres=result.get("genres"),
                        aka_titles=result.get("aka_titles"),
                    )
                )
                return

        static_poster = self.static_poster.get(title.lower())
        if static_poster:
            torrent_data["poster"] = static_poster
            return

        result = self.imdb_cache.get(f"{title}_{year}")
        if not result:
            result = await meta_fetcher.get_metadata(imdb_id, "series")

        filtered_episode = [
            episode for episode in result["episodes"] if episode["released"].date() == torrent_data["date"]
        ]
        if not filtered_episode:
            logging.warning(f"Failed to find episode for {title} on {torrent_data['date']}")
            if not torrent_data["poster"]:
                torrent_data["poster"] = random.choice(SPORTS_ARTIFACTS[self.event_name.upper()]["poster"])
            return
        episode = filtered_episode[0]

        torrent_data.update(
            dict(
                poster=episode["thumbnail"],
                background=episode["thumbnail"],
                description=episode["overview"],
                imdb_rating=episode["imdb_rating"],
            )
        )


class WWEParserPipeline(BaseParserPipeline):
    def __init__(self):
        known_imdb_ids = {
            "wwe raw": "tt0185103",
            "wwe monday night raw": "tt0185103",
            "wwe smackdown": "tt0227972",
            "wwe friday night smackdown": "tt0227972",
            "wwe friday smackdown": "tt0227972",
            "wwe main event": "tt2659152",
            "wwe nxt": "tt1601141",
        }
        static_poster = {"wwe main event": "https://image.tmdb.org/t/p/original/lHG78elMzHCasoP7kYiYUUJ2yUX.jpg"}
        static_logo = {
            "wwe raw": "https://image.tmdb.org/t/p/original/6BwNeaEes8Fvd3XHxNqZRzPtsou.png",
            "wwe monday night raw": "https://image.tmdb.org/t/p/original/6BwNeaEes8Fvd3XHxNqZRzPtsou.png",
            "wwe smackdown": "https://image.tmdb.org/t/p/original/lsxhZMYlWGYfsbhezeWelbJceMI.png",
            "wwe friday night smackdown": "https://image.tmdb.org/t/p/original/lsxhZMYlWGYfsbhezeWelbJceMI.png",
            "wwe friday smackdown": "https://image.tmdb.org/t/p/original/lsxhZMYlWGYfsbhezeWelbJceMI.png",
            "wwe main event": "https://image.tmdb.org/t/p/original/lHG78elMzHCasoP7kYiYUUJ2yUX.jpg",
            "wwe nxt": "https://image.tmdb.org/t/p/original/k0lJrDhoyuW6GWEWmHR7E2dZ1ic.png",
        }
        super().__init__("wwe", known_imdb_ids, static_poster, static_logo)


class UFCParserPipeline(BaseParserPipeline):
    def __init__(self):
        super().__init__("ufc")

    async def update_imdb_data(self, torrent_data: dict):
        year = torrent_data.get("date").year
        title = torrent_data.get("event")
        tmdb_data = await search_tmdb(title, year)
        if not tmdb_data:
            if not torrent_data["poster"]:
                torrent_data["poster"] = random.choice(SPORTS_ARTIFACTS[self.event_name.upper()]["poster"])
            return
        torrent_data.update(
            dict(
                poster=tmdb_data["poster"],
                background=tmdb_data["background"],
                is_add_title_to_poster=False,
                imdb_rating=tmdb_data["tmdb_rating"],
                description=tmdb_data["description"],
            )
        )
