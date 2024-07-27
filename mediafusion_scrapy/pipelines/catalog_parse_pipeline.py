class CatalogParsePipeline:
    def process_item(self, item, spider):
        if "video_type" not in item or "languages" not in item:
            return item
        video_type = item["video_type"]
        languages = item["languages"]
        item["catalog"] = [f"{lang.lower()}_{video_type}" for lang in languages]
        return item
