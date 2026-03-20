import logging

import PTT
from scrapy.exceptions import DropItem

from scrapers.scraper_tasks import meta_fetcher
from utils.const import QUALITY_GROUPS
from utils.sports_parser import detect_sports_category

logger = logging.getLogger(__name__)


class MovieTVParserPipeline:
    _RACING_CATALOGS = {"formula_racing", "motogp_racing"}

    async def process_item(self, item):
        data = item.copy()
        title = data["torrent_title"]
        if "title" not in data:
            data.update(PTT.parse_title(title, True))

        if not data.get("title"):
            raise DropItem(f"Title not parsed: {title}")

        if "file_data" not in data:
            raise DropItem(f"File data not found for title: {title}")

        data_type = data.get("type", "movie")
        if data_type == "movie":
            data["type"] = "series" if data.get("seasons") else "movie"

        title = data["title"]
        if data.get("imdb_id"):
            imdb_data = await meta_fetcher.get_metadata(data["imdb_id"], data["type"])
        else:
            imdb_data = await meta_fetcher.search_metadata(
                title, data.get("year"), data["type"], data.get("created_at")
            )

        if imdb_data:
            data.update(imdb_data)
            data["is_search_imdb_title"] = False
            if data.get("imdb_id"):
                data["id"] = data["imdb_id"]
        else:
            # Keep scraping resilient when IMDb lookups fail/transiently timeout.
            # Downstream store pipelines can persist items without external IDs.
            logger.warning(
                "IMDb data not found for title '%s'; continuing with parsed torrent metadata",
                title,
            )
            data["is_search_imdb_title"] = True
        title_for_category = data.get("torrent_title") or data.get("torrent_name") or data.get("title", "")
        detected_sports_category = detect_sports_category(title_for_category)
        if detected_sports_category in self._RACING_CATALOGS:
            data["catalog_source"] = detected_sports_category
            data["catalog"] = [detected_sports_category]
            logger.info(
                "Routing racing title '%s' to catalog '%s'",
                data.get("torrent_title") or data.get("title"),
                detected_sports_category,
            )
        else:
            data["catalog"] = [
                data["catalog_source"],
                f"{data['catalog_source']}_{data['type']}",
            ]

        if data["type"] == "series":
            data["video_type"] = "series"
        elif data.get("quality") in QUALITY_GROUPS["CAM/Screener"]:
            data["video_type"] = "tcrip"
        else:
            data["video_type"] = "hdrip"

        # Handle file data
        if data["type"] == "movie":
            largest_file = max(data["file_data"], key=lambda x: x["size"])
            data["largest_file"] = {
                "index": data["file_data"].index(largest_file),
                "filename": largest_file["filename"],
            }
        return data
