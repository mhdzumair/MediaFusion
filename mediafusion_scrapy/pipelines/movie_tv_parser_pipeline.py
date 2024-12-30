import PTT
from scrapy.exceptions import DropItem

from scrapers.scraper_tasks import meta_fetcher
from utils.const import QUALITY_GROUPS


class MovieTVParserPipeline:

    async def process_item(self, item, spider):
        data = item.copy()
        title = data["torrent_title"]
        if "title" not in data:
            data.update(PTT.parse_title(title, True))

        if not data.get("title"):
            raise DropItem(f"Title not parsed: {title}")

        if "file_data" not in data:
            raise DropItem(f"File data not found in item: {data}")

        if data["type"] == "movie":
            data["type"] = "series" if data["seasons"] else "movie"

        title = data["title"]
        if data.get("imdb_id"):
            imdb_data = await meta_fetcher.get_metadata(data["imdb_id"], data["type"])
        else:
            imdb_data = await meta_fetcher.search_metadata(
                title, data.get("year"), data["type"], data.get("created_at")
            )

        if not imdb_data:
            raise DropItem(f"IMDb data not found for title: {title}")

        data.update(imdb_data)
        data["is_search_imdb_title"] = False
        data["id"] = data["imdb_id"]
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
