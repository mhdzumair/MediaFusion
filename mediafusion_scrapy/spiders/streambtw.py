import random
import re

import scrapy

from utils.runtime_const import SPORTS_ARTIFACTS
from db.redis_database import REDIS_SYNC_CLIENT


class StreamBTWSpider(scrapy.Spider):
    name = "streambtw"
    start_urls = ["https://streambtw.com/"]
    referer = "https://streambtw.com/"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveStreamResolverPipeline": 100,
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
        "LOG_LEVEL": "DEBUG",
        "DUPEFILTER_DEBUG": True,
    }
    category_map = {
        "Soccer": "Football",
        "NFL": "American Football",
        "NBA": "Basketball",
        "NHL": "Hockey",
        "MLB": "Baseball",
        "24x7": "Other Sports",
    }

    def __init__(self, *args, **kwargs):
        super(StreamBTWSpider, self).__init__(*args, **kwargs)
        self.redis = REDIS_SYNC_CLIENT

    def __del__(self):
        self.redis.close()

    def parse(self, response, **kwargs):
        for timeline in response.xpath(
            '//div[contains(@class,"single-timeline-area")]'
        ):
            category = timeline.xpath("./div/p/text()").get()
            category = self.category_map.get(category, "Other Sports")

            events = timeline.xpath(
                './/a[div[contains(@class, "single-timeline-content")]]'
            )
            for event in events:
                event_name = event.xpath(".//h6/text()").get()
                description = event.xpath(".//p/text()").get()
                event_url = event.xpath("@href").get()
                logo = event.xpath(".//img/@src").get()

                item = {
                    "genres": [category],
                    "description": description,
                    "poster": random.choice(SPORTS_ARTIFACTS[category]["poster"]),
                    "background": random.choice(
                        SPORTS_ARTIFACTS[category]["background"]
                    ),
                    "logo": logo,
                    "is_add_title_to_poster": True,
                    "title": event_name,
                    "url": event_url,
                    "streams": [],
                }

                yield response.follow(
                    event_url, self.parse_event, meta={"item": item}, dont_filter=True
                )

    def parse_event(self, response):
        script_text = response.xpath(
            "//script[contains(text(), 'm3u8List') or contains(text(), 'Clappr.Player')]/text()"
        ).get()

        if script_text:
            m3u8_urls = re.findall(r'"(https?://[^"]+)"', script_text)
            for m3u8_url in m3u8_urls:
                item = response.meta["item"].copy()
                item.update(
                    {
                        "stream_name": f"{item['title']} - Live Stream",
                        "stream_url": m3u8_url,
                        "stream_source": "StreamBTW",
                        "stream_headers": {
                            "Referer": self.referer,
                            "Origin": self.referer.rstrip("/"),
                        },
                    }
                )
                yield item
        else:
            self.logger.error(
                f"No suitable script found in event to extract M3U8 URLs. URL: {response.url}"
            )
