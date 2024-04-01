import random
import re
from datetime import datetime

import pytz
import redis
import scrapy

from db.config import settings
from utils import const
from utils.parser import get_json_data


class MrGamingStreamsSpider(scrapy.Spider):
    name = "mrgamingstreams"
    allowed_domains = ["mrgamingstreams.com"]
    categories = {
        "Baseball": "https://mrgamingstreams.com/mlb",
        "American Football": "https://mrgamingstreams.com/nfl",
        "Basketball": "https://mrgamingstreams.com/nba",
        "Hockey": "https://mrgamingstreams.com/nhl",
        "Football": "https://mrgamingstreams.com/soccer",
        "Fighting": "https://mrgamingstreams.com/fighting",
        "Motor Sport": "https://mrgamingstreams.com/motorsports",
    }

    et_tz = pytz.timezone("America/New_York")  # Eastern Time zone

    custom_settings = {
        "ITEM_PIPELINES": {
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

            # Combine date with time and parse it into a datetime object
            now = datetime.now(self.et_tz)
            event_time = datetime.strptime(time_str, "%I:%M %p").replace(
                year=now.year, month=now.month, day=now.day
            )
            event_time = self.et_tz.localize(event_time)
            event_start_timestamp = event_time.timestamp()

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
            }

            yield response.follow(
                event_url, self.parse_event, meta={"item": item}, dont_filter=True
            )

    def parse_event(self, response):
        item = response.meta["item"].copy()
        item.update(
            {
                "event_url": response.url,
                "streams": [],
            }
        )

        stream_buttons = response.xpath(
            "//div[contains(@class, 'streambuttoncase')]/button"
        )

        if not stream_buttons:
            self.logger.info("No streams available for this event yet.")
            return

        for button in stream_buttons:
            stream_data = button.xpath("./@onclick").get()
            if not stream_data:
                continue

            # Extract the M3U8 URL from the onclick attribute using a regular expression
            m3u8_url_match = re.search(
                r"showPlayer\('\w+', '(https?://[^']+)'\)", stream_data
            )
            if not m3u8_url_match:
                continue

            m3u8_url = m3u8_url_match.group(1)
            stream_name = button.xpath("text()").get().strip()

            yield scrapy.Request(
                url=m3u8_url,
                callback=self.validate_m3u8_url,
                meta={
                    "item": item,
                    "stream_name": stream_name,
                    "stream_url": m3u8_url,
                },
                dont_filter=True,
            )

    def validate_m3u8_url(self, response):
        meta = response.meta
        item = meta["item"]
        content_type = response.headers.get("Content-Type", b"").decode().lower()

        if response.status == 200 and content_type in const.M3U8_VALID_CONTENT_TYPES:
            # Stream is valid; add it to the item's streams list
            item["streams"].append(
                {
                    "name": meta["stream_name"],
                    "url": meta["stream_url"],
                    "source": "MrGamingStreams",
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
