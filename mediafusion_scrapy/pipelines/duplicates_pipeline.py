from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem


class TorrentDuplicatesPipeline:
    def __init__(self):
        self.info_hashes_seen = set()

    def process_item(self, item):
        adapter = ItemAdapter(item)
        if adapter["info_hash"] in self.info_hashes_seen:
            raise DropItem(f"Duplicate item found: {adapter['info_hash']}")
        else:
            self.info_hashes_seen.add(adapter["info_hash"])
            return item
