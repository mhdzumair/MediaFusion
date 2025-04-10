import logging

import scrapy
from scrapy.utils.defer import maybe_deferred_to_future
from thefuzz import fuzz

from utils.config import config_manager


class DaddyLiveHDChannelsSpider(scrapy.Spider):
    name = "dlhd"
    iptv_org_api = "https://iptv-org.github.io/api/channels.json"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.LiveStreamResolverPipeline": 100,
            "mediafusion_scrapy.pipelines.TVStorePipeline": 300,
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [config_manager.get_scraper_config(self.name, "channels_url")]
        self.m3u8_base_url = config_manager.get_scraper_config(
            self.name, "m3u8_base_url"
        )
        self.referer = config_manager.get_scraper_config(self.name, "referer")
        self.category_map = config_manager.get_scraper_config(
            self.name, "category_mapping"
        )
        self.server_lookup_url = config_manager.get_scraper_config(
            self.name, "server_lookup_url"
        )
        self.channels_data = {}  # Will store IPTV-org channels data
        self.min_match_ratio = 85  # Minimum ratio for fuzzy matching

    def start_requests(self):
        # First fetch IPTV-org channels data
        yield scrapy.Request(
            self.iptv_org_api,
            callback=self.parse_iptv_org_data,
            headers={"Accept": "application/json"},
        )

    def parse_iptv_org_data(self, response):
        """Parse IPTV-org channels data and store it."""
        channels = response.json()

        # Filter out closed channels and store active ones
        for channel in channels:
            # Skip closed or NSFW channels
            if channel.get("is_nsfw") or channel.get("closed"):
                continue

            # Store both the main name and alternate names for matching
            channel_names = [channel["name"].lower()]
            if channel.get("alt_names"):
                channel_names.extend(name.lower() for name in channel["alt_names"])

            for name in channel_names:
                self.channels_data[name] = {
                    "id": channel["id"],
                    "country": channel.get("country"),
                    "languages": channel.get("languages", []),
                    "categories": channel.get("categories", []),
                    "logo": channel.get("logo"),
                    "website": channel.get("website"),
                    "broadcast_area": channel.get("broadcast_area", []),
                    "network": channel.get("network"),
                }

        # Now proceed to scrape channels
        yield scrapy.Request(
            self.start_urls[0],
            callback=self.parse_channels,
            headers={"Referer": self.referer},
        )

    def find_matching_channel(self, title):
        """Find matching channel in IPTV-org data using fuzzy matching."""
        title_lower = title.lower()

        # First try direct matching
        if title_lower in self.channels_data:
            return self.channels_data[title_lower]

        # Try fuzzy matching
        best_match = None
        best_ratio = 0

        for channel_name in self.channels_data.keys():
            ratio = fuzz.ratio(title_lower, channel_name)
            if ratio > best_ratio and ratio >= self.min_match_ratio:
                best_ratio = ratio
                best_match = self.channels_data[channel_name]

        return best_match

    async def parse_channels(self, response):
        """Parse the main channels page."""
        for channel_div in response.css("div.grid-item"):
            channel_link = channel_div.css("a")
            if not channel_link:
                continue

            title = channel_link.css("strong::text").get()
            if not title:
                continue

            # Skip adult content
            if "18+" in title:
                logging.info(f"Skipping adult content channel: {title}")
                continue

            # Extract channel ID from the href
            href = channel_link.attrib.get("href", "")
            channel_id = href.split("stream-")[-1].split(".")[0]
            if not channel_id.isdigit():
                continue

            # Try to find matching channel in IPTV-org data
            iptv_data = self.find_matching_channel(title)

            # Determine category and other metadata
            if iptv_data:
                category = self.map_iptv_category(iptv_data["categories"])
                languages = iptv_data["languages"]
                country = iptv_data["country"]
                logo = iptv_data.get("logo")
            else:
                category = self.determine_category(title)
                languages = []
                country = None
                logo = None

            # fetch server url data from server_lookup_url
            server_type = "premium"
            server_url = self.server_lookup_url.format(
                server_type=server_type, channel_id=channel_id
            )
            server_request = scrapy.Request(
                server_url,
                headers={"Referer": f"{self.referer}{server_type}tv/daddylivehd.php?id={channel_id}"},
            )
            server_response = await maybe_deferred_to_future(
                self.crawler.engine.download(server_request)
            )
            if (
                server_response.status != 200
                and server_response.headers.get("Content-Type") != "application/json"
            ):
                logging.error(
                    f"Failed to fetch server data for channel: {title} ({channel_id})"
                )
                continue

            server_data = server_response.json()
            server_key = server_data.get("server_key")
            if not server_key:
                logging.error(
                    f"Failed to find server key for channel: {title} ({channel_id})"
                )
                continue

            # Build stream URL
            stream_url = self.m3u8_base_url.format(
                server_key=server_key, server_type=server_type, channel_id=channel_id
            )

            # Create item with required fields for LiveStreamResolverPipeline
            item = {
                "title": title,
                "stream_name": title,
                "stream_url": stream_url,
                "stream_source": "DaddyLiveHD",
                "stream_headers": {
                    "Referer": self.referer,
                    "Origin": self.referer.rstrip("/"),
                },
                "response_headers": {
                    "Content-Type": "application/vnd.apple.mpegurl",
                },
                "genres": [category],
                "languages": languages,
                "country": country,
                "poster": logo,
                "background": logo,
                "logo": logo,
                "streams": [],
            }

            # Add additional metadata if available from IPTV-org
            if iptv_data:
                item.update(
                    {
                        "network": iptv_data.get("network"),
                        "broadcast_area": iptv_data.get("broadcast_area"),
                        "website": iptv_data.get("website"),
                        "iptv_id": iptv_data.get("id"),
                    }
                )

            yield item

    def map_iptv_category(self, categories):
        """Map IPTV-org categories to our category system."""
        category_mapping = {
            "sports": "Sports",
            "news": "News",
            "entertainment": "Entertainment",
            "movies": "Entertainment",
            "series": "Entertainment",
            "animation": "Entertainment",
            "documentary": "Documentary",
            "education": "Education",
            "music": "Music",
            "business": "News",
            "legislative": "News",
        }

        for category in categories:
            if category in category_mapping:
                return category_mapping[category]
        return "Other"

    def determine_category(self, title):
        """Fallback category determination based on channel name."""
        title_lower = title.lower()

        # Check for sports channels
        sports_keywords = [
            "espn",
            "sport",
            "fox sports",
            "bein",
            "sky sports",
            "tennis",
            "golf",
            "nba",
            "nfl",
            "mlb",
            "nhl",
        ]
        if any(keyword in title_lower for keyword in sports_keywords):
            return "Sports"

        # Check for news channels
        news_keywords = ["news", "cnn", "bbc", "msnbc", "fox news"]
        if any(keyword in title_lower for keyword in news_keywords):
            return "News"

        # Check for entertainment channels
        entertainment_keywords = [
            "hbo",
            "showtime",
            "movie",
            "disney",
            "netflix",
            "comedy",
            "cartoon",
            "nick",
            "mtv",
        ]
        if any(keyword in title_lower for keyword in entertainment_keywords):
            return "Entertainment"

        return "Other"  # Default category
