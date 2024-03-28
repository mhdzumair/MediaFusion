import random
import re
from datetime import datetime

import redis
import scrapy

from db.config import settings
from utils.parser import get_json_data


class StreamedSpider(scrapy.Spider):
    name = "streamed"
    allowed_domains = ["streamed.su"]
    categories = {
        "American Football": "https://streamed.su/category/american-football",
        "Basketball": "https://streamed.su/category/basketball",
        "Baseball": "https://streamed.su/category/baseball",
        "Cricket": "https://streamed.su/category/cricket",
        "Football": "https://streamed.su/category/football",
        "Fighting": "https://streamed.su/category/fight",
        "Hockey": "https://streamed.su/category/hockey",
        "Tennis": "https://streamed.su/category/tennis",
        "Rugby": "https://streamed.su/category/rugby",
        "Golf": "https://streamed.su/category/golf",
        "Dart": "https://streamed.su/category/darts",
        "Afl": "https://streamed.su/category/afl",
        "Motor Sport": "https://streamed.su/category/motor-sports",
        "Other Sports": "https://streamed.su/category/other",
    }

    m3u8_base_url = "https://tvembed.cc/js"
    m3u8_valid_content_types = [
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    ]

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
    }

    def __init__(self, *args, **kwargs):
        super(StreamedSpider, self).__init__(*args, **kwargs)
        self.redis = redis.Redis(
            connection_pool=redis.ConnectionPool.from_url(settings.redis_url)
        )
        self.sports_artifacts = get_json_data("resources/json/sports_artifacts.json")

    def __del__(self):
        self.redis.close()

    def start_requests(self):
        for category, url in self.categories.items():
            yield scrapy.Request(url, self.parse, meta={"category": category})

    def parse(self, response, **kwargs):
        category = response.meta["category"]
        events = response.xpath('//a[contains(@href,"/watch/")]')

        for event in events:
            event_name = event.xpath(".//h1/text()").get().strip()
            event_url = event.xpath(".//@href").get()

            item = {
                "genres": [category],
                "poster": random.choice(self.sports_artifacts[category]["poster"]),
                "background": random.choice(
                    self.sports_artifacts[category]["background"]
                ),
                "logo": random.choice(self.sports_artifacts[category]["logo"]),
                "is_add_title_to_poster": True,
                "title": event_name,
                "url": response.urljoin(event_url),
            }

            yield response.follow(event_url, self.parse_event, meta={"item": item})

    def parse_event(self, response):
        item = response.meta["item"].copy()
        item.update(
            {
                "event_url": response.url,
                "streams": [],
            }
        )

        script_text = response.xpath(
            '//script[contains(text(), "const data =")]/text()'
        ).get()

        if script_text:
            # Use a regular expression to find the timestamp
            timestamp_match = re.search(r"date:(\d+)", script_text)

            if timestamp_match:
                # Extract the timestamp and convert to UTC datetime
                event_timestamp_ms = int(timestamp_match.group(1))
                if event_timestamp_ms == 0:
                    item["is_24_hour_event"] = True
                else:
                    event_datetime_utc = datetime.utcfromtimestamp(
                        event_timestamp_ms / 1000
                    )
                    item["event_start"] = event_datetime_utc

        # If no timer, proceed to scrape available stream links
        stream_links = response.xpath('//a[contains(@href, "/watch/")]')
        if not stream_links:
            # No streams available atm
            self.logger.info("No streams available for this event yet.")
            return

        for link in stream_links:
            stream_name = link.xpath(".//h1/text()").get().strip()
            stream_url = link.xpath(".//@href").get()
            stream_quality = link.xpath(".//h2/text()").get().strip()
            m3u8_url = (
                f"{self.m3u8_base_url}{stream_url.replace('/watch', '')}/playlist.m3u8"
            )

            yield scrapy.Request(
                url=m3u8_url,
                callback=self.validate_m3u8_url,
                meta={
                    "item": item,
                    "stream_name": f"{stream_name} - {stream_quality}",
                    "stream_url": m3u8_url,
                },
                dont_filter=True,
            )

    def validate_m3u8_url(self, response):
        meta = response.meta
        item = meta["item"]
        content_type = response.headers.get("Content-Type", b"").decode().lower()

        if response.status == 200 and content_type in self.m3u8_valid_content_types:
            # Stream is valid; add it to the item's streams list
            item["streams"].append(
                {
                    "name": meta["stream_name"],
                    "url": meta["stream_url"],
                    "source": "streamed.su",
                    "behaviorHints": {
                        "notWebReady": True,
                        "is_redirect": True
                        if response.meta.get("redirect_times", 0) > 0
                        else False,
                        "proxyHeaders": {
                            "request": {
                                "User-Agent": response.request.headers.get(
                                    "User-Agent"
                                ).decode(),
                                "Referer": response.request.headers.get(
                                    "Referer"
                                ).decode(),
                            }
                        },
                    },
                }
            )
            return item
        else:
            self.logger.error(
                f"Invalid M3U8 URL: {meta['stream_url']} with Content-Type: {content_type} response: {response.status}"
            )
