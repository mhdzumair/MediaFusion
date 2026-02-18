import re
from datetime import datetime

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem

from utils.sports_parser import RESOLUTION_MAP, extract_date_from_title, normalize_resolution


class SportVideoParserPipeline:
    """Pipeline for parsing sport-video.org.ua content.

    Uses shared sports parser utilities for date extraction and resolution
    normalization.
    """

    def __init__(self):
        self.title_regex = re.compile(r"^.*?\s(\d{2}\.\d{2}\.\d{4})")

    def process_item(self, item):
        adapter = ItemAdapter(item)
        if "title" not in adapter or "torrent_name" not in adapter:
            raise DropItem(f"title or torrent_name not found in item: {item}")

        # Try local regex first, then fall back to shared date extraction
        match = self.title_regex.search(adapter["title"]) or self.title_regex.search(adapter["torrent_name"])
        if match:
            date_str = match.group(1)
            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
        else:
            # Use shared date extraction
            extracted_date, _ = extract_date_from_title(adapter["title"])
            if not extracted_date:
                extracted_date, _ = extract_date_from_title(adapter["torrent_name"])
            date_obj = datetime.combine(extracted_date, datetime.min.time()) if extracted_date else datetime.now()

        # Try to match or infer resolution using shared utility
        raw_resolution = adapter.get("aspect_ratio", "").replace(" ", "")
        resolution = self._infer_resolution(raw_resolution)

        if "video_codec" in adapter:
            codec = re.sub(r"\s+", "", adapter["video_codec"]).replace(",", " - ")
            item["codec"] = codec

        if "length" in adapter or "lenght" in adapter:
            item["runtime"] = re.sub(r"\s+", "", adapter.get("length", adapter.get("lenght", "")))

        item.update(
            {
                "created_at": datetime(date_obj.year, date_obj.month, date_obj.day),
                "year": date_obj.year,
                "languages": [re.sub(r"[ .]", "", item.get("language", "English"))],
                "is_search_imdb_title": False,
                "resolution": resolution,
            }
        )
        return item

    def _infer_resolution(self, aspect_ratio: str) -> str | None:
        """Infer resolution from aspect ratio string.

        Uses shared RESOLUTION_MAP and normalize_resolution from sports_parser.
        """
        # Normalize the aspect_ratio string by replacing common variants of "x"
        normalized_aspect_ratio = aspect_ratio.replace("×", "x").replace("х", "x")

        # Direct match in shared RESOLUTION_MAP
        if normalized_aspect_ratio in RESOLUTION_MAP:
            return RESOLUTION_MAP[normalized_aspect_ratio]

        # Try shared normalize_resolution
        resolution = normalize_resolution(normalized_aspect_ratio)
        if resolution:
            return resolution

        # Attempt to infer based on height
        if "x" in normalized_aspect_ratio:
            height = normalized_aspect_ratio.split("x")[-1]
            for res_key, res_val in RESOLUTION_MAP.items():
                if res_key.endswith(height):
                    return res_val
            return f"{height}p" if height.isdigit() else None

        # Fallback to checking against common labels
        for label in ["4k", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p"]:
            if label in normalized_aspect_ratio.lower():
                return label

        return None
