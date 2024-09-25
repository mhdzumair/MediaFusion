from mediafusion_scrapy.spiders.live_tv import LiveTVSpider


class TamilBulbSpider(LiveTVSpider):
    name = "tamilbulb"
    start_urls = ["https://tamilbulb.tv/"]
    use_flaresolverr = True
