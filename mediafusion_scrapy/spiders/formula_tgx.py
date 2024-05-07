import re

from mediafusion_scrapy.spiders.tgx import TgxSpider


class FormulaTgxSpider(TgxSpider):
    name = "formula_tgx"
    uploader_profiles = [
        "egortech",
        "F1Carreras",
        "smcgill1969",
    ]
    catalog = ["formula_racing"]
    background_image = "https://i.postimg.cc/S4wcrGRZ/f1background.png?dl=1"
    logo_image = "https://i.postimg.cc/Sqf4V8tj/f1logo.png?dl=1"

    keyword_patterns = re.compile(r"formula[ .+]*[1234e]+", re.IGNORECASE)
    scraped_info_hash_key = "formula_tgx_scraped_info_hash"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.TorrentDuplicatesPipeline": 100,
            "mediafusion_scrapy.pipelines.FormulaParserPipeline": 200,
            "mediafusion_scrapy.pipelines.EventSeriesStorePipeline": 300,
        }
    }
