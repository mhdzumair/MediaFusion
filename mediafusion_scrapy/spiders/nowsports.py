from mediafusion_scrapy.spiders.live_tv import LiveTVSpider


class NowSportsSpider(LiveTVSpider):
    name = "nowsports"
    start_urls = ["https://nowmesports.com/"]
