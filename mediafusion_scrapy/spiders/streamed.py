import random
import re

import scrapy

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

    m3u8_base_url = "https://{}ignores.top/js"
    sub_domains = {
        "": "Main Server",
        "inst1.": "Instance 1",
        "inst2.": "Instance 2",
        "inst3.": "Instance 3",
    }
    mediafusion_referer = "https://mediafusion.addon/"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveStreamResolverPipeline": 100,
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
        "DUPEFILTER_DEBUG": True,
        "DEFAULT_REQUEST_HEADERS": {"Referer": mediafusion_referer},
    }

    def __init__(self, *args, **kwargs):
        super(StreamedSpider, self).__init__(*args, **kwargs)
        self.sports_artifacts = get_json_data("resources/json/sports_artifacts.json")

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
                "stream_source": "Streamed (streamed.su)",
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
                meta={"item": item},
                headers={"Referer": self.mediafusion_referer},
            )

    def parse_event(self, response):
        script_text = response.xpath(
            '//script[contains(text(), "const data =")]/text()'
        ).get()

        event_start_timestamp = 0
        if script_text:
            # Use a regular expression to find the timestamp
            timestamp_match = re.search(r"date:(\d+)", script_text)

            if timestamp_match:
                # Extract the timestamp and convert to UTC datetime
                event_timestamp_ms = int(timestamp_match.group(1))
                event_start_timestamp = event_timestamp_ms / 1000

        # If no timer, proceed to scrape available stream links
        stream_links = response.xpath('//a[contains(@href, "/watch/")]')
        if not stream_links:
            # No streams available atm
            self.logger.info(f"No streams available for this event yet. {response.url}")
            return

        for link in stream_links:
            stream_name = link.xpath(".//h1/text()").get().strip()
            stream_url = link.xpath(".//@href").get()
            stream_quality = link.xpath(".//h2/text()").get().strip()
            language = link.xpath(".//div[last()]/text()").get().strip()

            for sub_domain, sub_domain_name in self.sub_domains.items():
                m3u8_url = f"{self.m3u8_base_url.format(sub_domain)}{stream_url.replace('/watch', '')}/playlist.m3u8"
                item = response.meta["item"].copy()
                item.update(
                    {
                        "stream_name": f"{stream_name} - 📡 {sub_domain_name}\n📺 {stream_quality} - 🌐 {language}",
                        "stream_url": m3u8_url,
                        "referer": self.mediafusion_referer,
                        "event_start_timestamp": event_start_timestamp,
                    }
                )
                yield item
