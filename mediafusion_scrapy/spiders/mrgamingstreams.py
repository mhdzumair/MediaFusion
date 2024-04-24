import random
import re
from datetime import datetime

import pytz
import redis
import scrapy

from db.config import settings
from utils.parser import get_json_data


class MrGamingStreamsSpider(scrapy.Spider):
    name = "mrgamingstreams"
    categories = {
        "Baseball": "https://mrgamingstreams.live/mlb.html",
        "American Football": "https://mrgamingstreams.live/nfl.html",
        "Basketball": "https://mrgamingstreams.live/nba.html",
        "Hockey": "https://mrgamingstreams.live/nhl.html",
        "Football": "https://mrgamingstreams.live/soccer.html",
        "Fighting": "https://mrgamingstreams.live/boxing.html",
        "Motor Sport": "https://mrgamingstreams.live/formula-1.html",
    }

    et_tz = pytz.timezone("America/New_York")  # Eastern Time zone

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveStreamResolverPipeline": 100,
            "mediafusion_scrapy.pipelines.LiveEventStorePipeline": 300,
        },
        "LOG_LEVEL": "DEBUG",
        "DUPEFILTER_DEBUG": True,
    }

    def __init__(self, *args, **kwargs):
        super(MrGamingStreamsSpider, self).__init__(*args, **kwargs)
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
        rows = response.xpath('//table[@id="thealmightytable"]/tr')

        for row in rows:
            event_url = row.xpath("./td[3]/a/@href").get()
            if not event_url:
                continue

            time_str = row.xpath("./td[1]/text()").get().strip()
            event_name = row.xpath("./td[2]/text()").get().strip()

            event_start_timestamp = 0

            if category == "Football":
                league = time_str
                event_name = f"{league} - {event_name}"
            elif time_str == "--:--":
                self.logger.info(f"Event time not available for: {event_name}")
            else:
                # Combine date with time and parse it into a datetime object
                now = datetime.now(self.et_tz)
                try:
                    event_time = datetime.strptime(time_str, "%I:%M %p").replace(
                        year=now.year, month=now.month, day=now.day
                    )
                    event_time = self.et_tz.localize(event_time)
                    event_start_timestamp = event_time.timestamp()
                except ValueError:
                    self.logger.error(
                        f"Failed to parse event time: {time_str} for event: {event_name}"
                    )

            item = {
                "genres": [category],
                "poster": random.choice(self.sports_artifacts[category]["poster"]),
                "background": random.choice(
                    self.sports_artifacts[category]["background"]
                ),
                "logo": random.choice(self.sports_artifacts[category]["logo"]),
                "is_add_title_to_poster": True,
                "event_start_timestamp": event_start_timestamp,
                "title": event_name,
                "url": response.urljoin(event_url),
                "streams": [],
            }

            yield response.follow(
                event_url, self.parse_event, meta={"item": item}, dont_filter=True
            )

    def parse_event(self, response):
        iframe_src = response.xpath("//iframe/@src").get()

        if iframe_src and "youtube.com" not in iframe_src:
            yield scrapy.Request(
                url=response.urljoin(iframe_src),
                callback=self.parse_iframe,
                meta=response.meta,
                dont_filter=True,
            )
        else:
            self.logger.error(
                f"No iframe found for event: {response.meta['item']['title']} - {response.url}"
            )

    def parse_iframe(self, response):
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
                        "stream_source": "MrGamingStreams",
                        "referer": response.url,
                    }
                )
                yield item
        else:
            self.logger.error(
                "No suitable script found in iframe to extract M3U8 URLs."
            )
