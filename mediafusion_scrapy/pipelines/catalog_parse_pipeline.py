from utils.const import CATALOG_DATA


class CatalogParsePipeline:
    def process_item(self, item):
        if "video_type" not in item:
            return item
        video_type = item["video_type"]

        # Use languages from torrent parser, falling back to spider's
        # language from the forum context (e.g. "Tamil" from tamil_hdrip forum)
        languages = item.get("languages") or []
        if not languages and "language" in item:
            lang = item["language"]
            if isinstance(lang, str):
                languages = [lang]
            elif isinstance(lang, list):
                languages = lang

        if not languages:
            return item

        torrent_name = item.get("torrent_name", "").lower()
        catalogs = item.get("catalog", [])
        source = item.get("source")
        for language in languages:
            if language.lower() == "english" and "eng" not in torrent_name:
                # Fix for ESubs torrents
                continue
            if video_type == "dubbed" and language.lower() == "english":
                continue
            catalog_name = f"{language.lower()}_{video_type}"
            if catalog_name not in CATALOG_DATA:
                continue
            if source == "ExtTo" and language.lower() != "english":
                # Do not add non-english catalogs from ExtTo
                continue
            if catalog_name not in catalogs:
                catalogs.append(catalog_name)
        item["catalog"] = catalogs
        return item
