import logging
import random
from datetime import datetime, timedelta

import pytz
import scrapy
from dateutil import parser as date_parser

from utils.config import config_manager
from utils.runtime_const import SPORTS_ARTIFACTS


class DaddyLiveHDSpider(scrapy.Spider):
    name = "dlhd"
    start_urls = [config_manager.get_scraper_config(name, "schedule_url")]

    # The number of hours to consider the event as starting within next hours from now.
    start_within_next_hours = 1
    started_within_hours_ago = 6

    category_map = config_manager.get_scraper_config(name, "category_mapping")
    m3u8_base_url = config_manager.get_scraper_config(name, "m3u8_base_url")
    referer = config_manager.get_scraper_config(name, "referer")
    gmt = pytz.timezone("Etc/GMT")

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveStreamResolverPipeline": 100,
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
        "DUPEFILTER_DEBUG": True,
    }

    def start_requests(self):
        yield scrapy.Request(self.start_urls[0], self.parse)

    def parse(self, response, **kwargs):
        data = response.json()
        current_time = datetime.now(tz=self.gmt)
        for date_section, sports in data.items():
            date_str = date_section.split(" - ")[0]
            event_date = date_parser.parse(date_str).date()
            for sport, events in sports.items():
                for event in events:
                    time = datetime.strptime(event["time"], "%H:%M").time()
                    datetime_obj = datetime.combine(event_date, time)
                    # Make the datetime object timezone aware (UK GMT)
                    aware_datetime = self.gmt.localize(datetime_obj)

                    # Check if event starts within the specified time range
                    time_difference = aware_datetime - current_time
                    if not (
                        timedelta(hours=-self.started_within_hours_ago)
                        <= time_difference
                        <= timedelta(hours=self.start_within_next_hours)
                    ):
                        logging.warning(
                            "Skipping event %s as it doesn't start within the specified time range. %s",
                            event["event"],
                            time_difference,
                        )
                        continue

                    # Convert to UNIX timestamp
                    event_start_timestamp = int(aware_datetime.timestamp())
                    if event_start_timestamp != 0:
                        event_start_time = datetime.fromtimestamp(
                            event_start_timestamp
                        ).strftime("%I:%M%p GMT")
                        description = f'{event["event"]} - {event_start_time}'
                    else:
                        description = event["event"]
                    category = self.category_map.get(sport, "Other Sports")

                    item = {
                        "stream_source": "DaddyLiveHD (1.dlhd.sx)",
                        "genres": [category],
                        "poster": random.choice(SPORTS_ARTIFACTS[category]["poster"]),
                        "background": random.choice(
                            SPORTS_ARTIFACTS[category]["background"]
                        ),
                        "logo": random.choice(SPORTS_ARTIFACTS[category]["logo"]),
                        "is_add_title_to_poster": True,
                        "title": event["event"],
                        "description": description,
                        "channels": event["channels"],
                        "event_start_timestamp": event_start_timestamp,
                        "streams": [],
                    }
                    yield from self.parse_channels(item)

    def parse_channels(self, item):
        for channel in item["channels"]:
            item_copy = item.copy()
            m3u8_url = self.m3u8_base_url.format(channel_id=channel["channel_id"])
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
