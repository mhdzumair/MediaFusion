import random
import re

import redis
import scrapy

from db.config import settings
from utils import const
from utils.parser import get_json_data


class CricTimeSpider(scrapy.Spider):
    name = "crictime"
    start_urls = ["https://af.crictime.com"]
    js_base_url = "https://www.factorp.xyz/hembedplayer/"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
    }

    def __init__(self, *args, **kwargs):
        super(CricTimeSpider, self).__init__(*args, **kwargs)
        self.redis = redis.Redis(
            connection_pool=redis.ConnectionPool.from_url(settings.redis_url)
        )
        self.sports_artifacts = get_json_data("resources/json/sports_artifacts.json")

    def __del__(self):
        self.redis.close()

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(url, self.parse, meta={"category": "Cricket"})

    def parse(self, response, **kwargs):
        category = response.meta["category"]
        # Navigate through each card that contains the 'Live' label
        live_events = response.xpath(
            '//div[contains(@class,"card-body")][.//div[contains(@class,"live")]]'
        )

        for event in live_events:
            # Extract the team/event name
            event_name = event.xpath(".//h3/text()").get()

            # Extract streaming URLs and the corresponding quality from buttons
            for stream_quality in event.xpath(
                './/a[.//button[contains(@class, "watch-btn")]]'
            ):
                quality = stream_quality.xpath("./button/text()").get()
                if not quality:
                    continue
                stream_name = quality.strip() + " Quality"
                event_url = stream_quality.xpath("@href").get()

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
                    "streams": [],
                }

                yield response.follow(
                    event_url,
                    self.parse_event,
                    meta={"item": item, "stream_name": stream_name},
                )

    def parse_event(self, response):
        # Extract width, height, channel, and g values
        script_text = response.xpath(
            "//script[contains(text(), 'channel=')]/text()"
        ).get()
        width = height = channel = g = None
        if script_text:
            width = re.search(r"width=(\d+)", script_text).group(1)
            height = re.search(r"height=(\d+)", script_text).group(1)
            channel = re.search(r"channel='(\w+)'", script_text).group(1)
            g = re.search(r"g='(\d+)'", script_text).group(1)

        # Generate the URL for the JS script
        if channel and g and width and height:
            js_url = f"{self.js_base_url}{channel}/{g}/{width}/{height}"
            yield response.follow(
                js_url, callback=self.parse_js, meta=response.meta, dont_filter=True
            )
        else:
            self.logger.error(
                "Failed to extract necessary parameters for video streaming."
            )

    def parse_js(self, response):
        item = response.meta["item"]
        ea = response.xpath("//script[contains(text(), 'ea =')]/text()").re_first(
            r"ea = \"(.+?)\";"
        )

        # Trying to extract the pattern directly from the script
        script_content = response.xpath(
            "//script[contains(text(), 'hlsUrl')]/text()"
        ).get()
        url_match = re.search(r'var hlsUrl = .+"(.+)";', script_content)

        if not url_match:
            self.logger.error("Failed to extract M3U8 URL.")
            return

        m3u8_suffix_url = url_match.group(1)
        hash_pattern = re.search(r'enableVideo\("([^"]+)"\);', script_content)
        if not hash_pattern:
            self.logger.error("Failed to extract hash value.")
            return

        hash_value = hash_pattern.group(1)
        modified_hash = hash_value[:49] + hash_value[50:]
        m3u8_url = f"https://{ea}{m3u8_suffix_url}{modified_hash}"

        yield response.follow(
            m3u8_url,
            self.validate_m3u8_url,
            meta={
                "item": item,
                "stream_url": m3u8_url,
                "stream_name": response.meta["stream_name"],
            },
            dont_filter=True,
        )

    def validate_m3u8_url(self, response):
        meta = response.meta
        item = meta["item"].copy()
        content_type = response.headers.get("Content-Type", b"").decode().lower()

        if response.status == 200 and content_type in const.M3U8_VALID_CONTENT_TYPES:
            # Stream is valid; add it to the item's streams list
            item["streams"].append(
                {
                    "name": meta["stream_name"],
                    "url": meta["stream_url"],
                    "source": "CricTime",
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
