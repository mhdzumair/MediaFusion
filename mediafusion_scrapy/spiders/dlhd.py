import random
import re
from datetime import datetime

import pytz
import scrapy

from utils.parser import get_json_data


class DaddyLiveHDSpider(scrapy.Spider):
    name = "dlhd"
    site_url = "https://1.dlhd.sx"
    start_urls = [f"{site_url}/schedule/schedule-generated.json"]

    category_map = {
        "Tv Shows": "Other Sports",
        "Soccer": "Football",
        "Cricket": "Cricket",
        "Tennis": "Tennis",
        "Motorsport": "Motor Sport",
        "Boxing": "Other Sports",
        "MMA": "Other Sports",
        "Golf": "Golf",
        "Snooker": "Other Sports",
        "Am. Football": "American Football",
        "Athletics": "Other Sports",
        "Aussie rules": "Other Sports",
        "Baseball": "Baseball",
        "Basketball": "Basketball",
        "Bowling": "Other Sports",
        "Cycling": "Other Sports",
        "Darts": "Dart",
        "Floorball": "Other Sports",
        "Futsal": "Other Sports",
        "Gymnastics": "Other Sports",
        "Handball": "Other Sports",
        "Horse Racing": "Other Sports",
        "Ice Hockey": "Hockey",
        "Lacrosse": "Other Sports",
        "Netball": "Other Sports",
        "Rugby League": "Rugby",
        "Rugby Union": "Rugby",
        "Squash": "Other Sports",
        "Volleyball": "Other Sports",
        "GAA": "Other Sports",
        "Clubber": "Other Sports",
    }

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveStreamResolverPipeline": 100,
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
        "DUPEFILTER_DEBUG": True,
    }

    m3u8_base_url = "https://webhdrus.onlinehdhls.ru/lb/premium{}/index.m3u8"
    referer = "https://lewblivehdplay.ru/"

    def __init__(self, *args, **kwargs):
        super(DaddyLiveHDSpider, self).__init__(*args, **kwargs)
        self.sports_artifacts = get_json_data("resources/json/sports_artifacts.json")
        self.gmt = pytz.timezone("Etc/GMT")

    def parse(self, response, **kwargs):
        data = response.json()
        for date_section, sports in data.items():
            date_str = date_section.split(" - ")[
                0
            ]  # Assuming the date is always first part
            # Remove the ordinal suffix from the date
            date_str = re.sub(r"(st|nd|rd|th)", "", date_str)
            date = datetime.strptime(date_str, "%A %d %B %Y").date()
            for sport, events in sports.items():
                for event in events:
                    category = self.category_map.get(sport, "Other Sports")
                    time = datetime.strptime(event["time"], "%H:%M").time()
                    datetime_obj = datetime.combine(date, time)
                    # Make the datetime object timezone aware (UK GMT)
                    aware_datetime = self.gmt.localize(datetime_obj)
                    # Convert to UNIX timestamp
                    event_start_timestamp = int(aware_datetime.timestamp())

                    item = {
                        "stream_source": "DaddyLiveHD (1.dlhd.sx)",
                        "genres": [category],
                        "poster": random.choice(
                            self.sports_artifacts[category]["poster"]
                        ),
                        "background": random.choice(
                            self.sports_artifacts[category]["background"]
                        ),
                        "logo": random.choice(self.sports_artifacts[category]["logo"]),
                        "is_add_title_to_poster": True,
                        "title": event["event"],
                        "channels": event["channels"],
                        "event_start_timestamp": event_start_timestamp,
                        "streams": [],
                    }
                    yield from self.parse_channels(item)

    def parse_channels(self, item):
        for channel in item["channels"]:
            item_copy = item.copy()
            m3u8_url = self.m3u8_base_url.format(channel["channel_id"])
            item_copy.update(
                {
                    "stream_name": channel["channel_name"],
                    "stream_headers": {
                        "Referer": self.referer,
                        "Origin": self.referer.rstrip("/"),
                    },
                    "channel_id": channel["channel_id"],
                }
            )

            yield scrapy.Request(
                m3u8_url,
                self.parse_stream_link,
                meta={
                    "item": item_copy,
                    "dont_redirect": True,
                    "handle_httpstatus_list": [301],
                },
                headers={"Referer": self.referer},
                dont_filter=True,
            )

    def parse_stream_link(self, response):
        item = response.meta["item"]
        stream_url = response.headers.get("Location", b"").decode("utf-8")
        if not stream_url:
            self.logger.error(
                f"Failed to get stream URL for {item['stream_name']} channel_id: {item['channel_id']}"
            )
            return
        item["stream_url"] = stream_url
        yield item
