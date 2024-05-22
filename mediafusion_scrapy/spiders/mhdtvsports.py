from mediafusion_scrapy.spiders.live_tv import LiveTVSpider


class MhdTVSportsSpider(LiveTVSpider):
    name = "mhdtvsports"
    start_urls = ["https://mhdsportv.com/"]
