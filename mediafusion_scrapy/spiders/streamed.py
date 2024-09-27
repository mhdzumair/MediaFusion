import json
import random
import re
from datetime import datetime
from urllib.parse import urljoin

import pytz
import scrapy

from utils.runtime_const import SPORTS_ARTIFACTS


class StreamedSpider(scrapy.Spider):
    name = "streamed"

    api_base_url = "https://streamed.su/api"
    live_matches_url = f"{api_base_url}/matches/live"
    stream_url_template = f"{api_base_url}/stream/{{source}}/{{id}}"
    image_url_template = (
        f"{api_base_url}/images/poster/{{batch_id_1}}/{{batch_id_2}}.webp"
    )

    m3u8_base_url = "https://{domain}/{source}/js/{id}/{stream_no}/playlist.m3u8"
    mediafusion_referer = "https://mediafusion.addon/"

    category_mapping = {
        "afl": "Afl",
        "american-football": "American Football",
        "baseball": "Baseball",
        "basketball": "Basketball",
        "billiards": "Billiards",
        "cricket": "Cricket",
        "darts": "Dart",
        "fight": "Fighting",
        "football": "Football",
        "golf": "Golf",
        "hockey": "Hockey",
        "motor-sports": "Motor Sport",
        "other": "Other Sports",
        "rugby": "Rugby",
        "tennis": "Tennis",
    }

    domains = None
    domain_host = None

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveStreamResolverPipeline": 100,
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
        "DUPEFILTER_DEBUG": True,
        "DEFAULT_REQUEST_HEADERS": {"Referer": mediafusion_referer},
    }

    def start_requests(self):
        yield scrapy.Request(self.live_matches_url, self.parse_live_matches)

    def parse_live_matches(self, response):
        matches = response.json()
        for match in matches:
            if not match.get("sources"):
                self.logger.info(f"No sources available for match: {match['title']}")
                continue

            category = self.category_mapping.get(match["category"], "Other")

            item = {
                "stream_source": "Streamed (streamed.su)",
                "genres": [category],
                "title": match["title"],
                "event_start_timestamp": match["date"] / 1000,  # Convert to seconds
                "streams": [],
            }

            # Handle poster image
            if "poster" in match and match["poster"]:
                item["poster"] = urljoin(self.api_base_url, match["poster"])
            elif (
                match.get("teams")
                and match["teams"].get("home", {}).get("badge")
                and match["teams"].get("away", {}).get("badge")
            ):
                item["poster"] = self.image_url_template.format(
                    batch_id_1=match["teams"]["home"]["badge"],
                    batch_id_2=match["teams"]["away"]["badge"],
                )
            else:
                item["poster"] = random.choice(SPORTS_ARTIFACTS[category]["poster"])

            item["background"] = random.choice(SPORTS_ARTIFACTS[category]["background"])
            item["logo"] = random.choice(SPORTS_ARTIFACTS[category]["logo"])
            item["is_add_title_to_poster"] = True

            for source in match["sources"]:
                yield scrapy.Request(
                    self.stream_url_template.format(
                        source=source["source"], id=source["id"]
                    ),
                    self.parse_stream,
                    meta={"item": item},
                )

    def parse_stream(self, response):
        item = response.meta["item"].copy()
        stream_data_list = response.json()

        for stream_data in stream_data_list:
            if self.domains and self.domain_host:
                yield self.create_stream_item(stream_data, item)
            else:
                yield scrapy.Request(
                    stream_data["embedUrl"],
                    self.parse_embed,
                    meta={"item": item, "stream_data": stream_data},
                    headers={"Referer": self.mediafusion_referer},
                )

    def parse_embed(self, response):
        item = response.meta["item"]
        stream_data = response.meta["stream_data"]

        if self.domains is None or self.domain_host is None:
            self.extract_domain_info(response)

        if self.domains and self.domain_host:
            yield self.create_stream_item(stream_data, item)
        else:
            self.logger.error(
                f"Failed to extract domain information for stream: {stream_data['id']}"
            )

    def extract_domain_info(self, response):
        script_content = response.xpath(
            '//script[contains(text(), "var k=")]/text()'
        ).get()
        if script_content:
            vars_match = re.search(
                r'var k="(\w+)",i="([^"]+)",s="(\d+)",l=(\[.+?\]),h="([^"]+)";',
                script_content,
            )
            if vars_match:
                _, _, _, domains, domain_host = vars_match.groups()
                self.domains = json.loads(domains)
                self.domain_host = domain_host

    def create_stream_item(self, stream_data, item):
        m3u8_url = self.m3u8_base_url.format(
            domain=f"{random.choice(self.domains)}.{self.domain_host}",
            source=stream_data["source"],
            id=stream_data["id"],
            stream_no=stream_data["streamNo"],
        )

        stream_item = item.copy()
        stream_item.update(
            {
                "stream_name": f"{'HD' if stream_data['hd'] else 'SD'} - 🌐 {stream_data['language']}\n"
                f"🔗 {stream_data['source'].title()} Stream {stream_data['streamNo']}",
                "stream_url": m3u8_url,
                "referer": self.mediafusion_referer,
                "description": f"{item['title']} - {datetime.fromtimestamp(item['event_start_timestamp'], tz=pytz.utc).strftime('%I:%M%p GMT')}",
            }
        )

        return stream_item
