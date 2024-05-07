import re
import random

from mediafusion_scrapy.spiders.tgx import TgxSpider
from utils.runtime_const import SPORTS_ARTIFACTS


class MotoGPTgxSpider(TgxSpider):
    name = "motogp_tgx"
    uploader_profiles = [
        "smcgill1969",
    ]
    catalog = ["motogp_racing"]

    keyword_patterns = re.compile(r"MotoGP[ .+]*", re.IGNORECASE)
    scraped_info_hash_key = "motogp_tgx_scraped_info_hash"
    background_image = random.choice(SPORTS_ARTIFACTS["MotoGP"]["background"])
    logo_image = random.choice(SPORTS_ARTIFACTS["MotoGP"]["logo"])

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.TorrentDuplicatesPipeline": 100,
            "mediafusion_scrapy.pipelines.MotoGPParserPipeline": 200,
            "mediafusion_scrapy.pipelines.EventSeriesStorePipeline": 300,
        }
    }
