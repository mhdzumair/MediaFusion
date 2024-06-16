from mediafusion_scrapy.spiders.live_tv import LiveTVSpider


class NowMeTVSpider(LiveTVSpider):
    name = "nowmetv"
    start_urls = ["https://nowmaxtv.com/"]
