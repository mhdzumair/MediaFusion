import re
from datetime import datetime

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem


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
