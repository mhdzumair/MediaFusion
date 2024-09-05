import random
import re

import scrapy

from utils.runtime_const import SPORTS_ARTIFACTS, REDIS_SYNC_CLIENT


class CricTimeSpider(scrapy.Spider):
    name = "crictime"
    start_urls = ["https://af.crictime.com"]
    js_base_url = "https://www.factorp.xyz/hembedplayer/"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveStreamResolverPipeline": 100,
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
    }

    def __init__(self, *args, **kwargs):
        super(CricTimeSpider, self).__init__(*args, **kwargs)
        self.redis = REDIS_SYNC_CLIENT

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
                    "poster": random.choice(SPORTS_ARTIFACTS[category]["poster"]),
                    "background": random.choice(
                        SPORTS_ARTIFACTS[category]["background"]
                    ),
                    "logo": random.choice(SPORTS_ARTIFACTS[category]["logo"]),
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
        ea = response.xpath("//script[contains(text(), 'ea =')]/text()").re_first(
            r"ea = \"(.+?)\";"
        )

        # Trying to extract the pattern directly from the script
        script_content = response.xpath(
            "//script[contains(text(), 'hlsUrl')]/text()"
        ).get()
        if not script_content:
            self.logger.error(
                f"Failed to extract script content from URL: {response.url}"
            )
            return

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

        item = response.meta["item"].copy()
        item.update(
            {
                "stream_url": m3u8_url,
                "stream_name": response.meta["stream_name"],
                "stream_source": "StreamBTW",
                "referer": response.url,
            }
        )
        yield item
