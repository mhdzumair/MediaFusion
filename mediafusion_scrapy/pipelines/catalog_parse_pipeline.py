class CatalogParsePipeline:
    def process_item(self, item, spider):
        video_type = item["video_type"]
        languages = item["languages"]
        item["catalog"] = [f"{lang.lower()}_{video_type}" for lang in languages]
        return item
