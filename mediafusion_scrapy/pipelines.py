import logging

from beanie import WriteRules
from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem

from db import database
from db.models import TorrentStreams, Season, MediaFusionSeriesMetaData


class TorrentDuplicatesPipeline:
    def __init__(self):
        self.info_hashes_seen = set()

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        if adapter["info_hash"] in self.info_hashes_seen:
            raise DropItem(f"Duplicate item found: {item!r}")
        else:
            self.info_hashes_seen.add(adapter["info_hash"])
            return item


class FormulaStorePipeline:
    async def process_item(self, item, spider):
        if "unique_id" not in item:
            logging.warning(f"unique_id not found in item: {item}")
            return item

        # Construct the meta_id
        meta_id = f"mf{item['unique_id']}"

        # Create a season object
        season = Season(season_number=1, episodes=item["episodes"])

        # Create the stream
        stream = TorrentStreams(
            id=item["info_hash"],
            torrent_name=item["torrent_name"],
            announce_list=item["announce_list"],
            size=item["total_size"],
            languages=item["languages"],
            resolution=item.get("resolution"),
            codec=item.get("codec"),
            quality=item.get("quality"),
            audio=item.get("audio"),
            encoder=item.get("encoder"),
            source=item["source"],
            catalog=item["catalog"],
            created_at=item["created_at"],
            season=season,
            meta_id=meta_id,
            seeders=item["seeders"],
        )

        series = MediaFusionSeriesMetaData(
            id=meta_id,
            title=item["title"],
            year=item.get("year"),
            poster=item.get("poster"),
            background=item.get("background"),
            streams=[stream],
            type="series",
        )

        await series.insert(link_rule=WriteRules.WRITE)
        logging.info(f"Inserted new formula: {item['torrent_name']}")

        return item
