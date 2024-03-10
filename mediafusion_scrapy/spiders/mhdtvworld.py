from mediafusion_scrapy.spiders.live_tv import LiveTVSpider


class MhdTVWorldSpider(LiveTVSpider):
    name = "mhdtvworld"
    start_urls = ["https://mhdtvmax.net/"]
