from mediafusion_scrapy.spiders.live_tv import LiveTVSpider


class TamilUltraSpider(LiveTVSpider):
    name = "tamilultra"
    start_urls = ["https://tamilultra.tv/"]
