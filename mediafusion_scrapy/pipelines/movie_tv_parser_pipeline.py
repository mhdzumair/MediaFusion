import PTT
from scrapy.exceptions import DropItem

from scrapers.imdb_data import search_imdb, get_imdb_title_data
from scrapers.tmdb_data import search_tmdb
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

        if data["type"] == "series" and len(data.get("season", [])) > 1:
            raise DropItem(f"Multiple seasons found in title: {title}")

        title = data["title"]
        if data.get("imdb_id"):
            imdb_data = await get_imdb_title_data(data["imdb_id"], data["type"])
        else:
            imdb_data = await search_imdb(title, data.get("year"), data["type"])
            if not imdb_data:
                imdb_data = await search_tmdb(title, data.get("year"), data["type"])

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
