from utils.const import CATALOG_DATA


class CatalogParsePipeline:
    def process_item(self, item, spider):
        if "video_type" not in item or "languages" not in item:
            return item
        video_type = item["video_type"]
        languages = item["languages"]
        torrent_name = item["torrent_name"].lower()
        catalogs = item.get("catalog", [])
        for language in languages:
            if language.lower() == "english" and "eng" not in torrent_name:
                # Fix for ESubs torrents
                continue
            if video_type == "dubbed" and language.lower() == "english":
                continue
            catalog_name = f"{language.lower()}_{video_type}"
            if catalog_name not in CATALOG_DATA:
                continue
            if catalog_name not in catalogs:
                catalogs.append(catalog_name)
        item["catalog"] = catalogs
        return item
