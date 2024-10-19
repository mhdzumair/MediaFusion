class CatalogParsePipeline:
    def process_item(self, item, spider):
        if "video_type" not in item or "languages" not in item:
            return item
        video_type = item["video_type"]
        languages = item["languages"]
        torrent_name = item["torrent_name"].lower()
        catalogs = []
        for language in languages:
            if language == "English" and "eng" not in torrent_name:
                # Fix for ESubs torrents
                continue
            if video_type == "dubbed" and language == "English":
                continue
            catalogs.append(f"{language.lower()}_{video_type}")
        item["catalog"] = catalogs
        return item
