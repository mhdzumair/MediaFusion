import re
from urllib.parse import urljoin, urlparse

import scrapy

from scrapers.helpers import get_country_name
from utils import const


class LiveTVSpider(scrapy.Spider):
    fallback_pattern = re.compile(
        r"source: ['\"](.*?)['\"],\s*[\s\S]*?mimeType: ['\"]application/x-mpegURL['\"]"
    )

    # this site sometimes returns html instead of image
    exclude_validation_urls = [
        "https://imgur.com",
    ]

    category_substrings = [
        "/channel/",
        "/live/",
        "/channels/",
    ]

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.TVStorePipeline": 300,
        },
    }

    def parse(self, response, **kwargs):
        category_urls = [
            urljoin(response.url, link.get())
            for link in response.css("#header a::attr(href)")
            if any(substring in link.get() for substring in self.category_substrings)
        ]
        self.logger.info(f"Found {len(category_urls)} categories")

        # Iterate over each category URL to scrape channels
        for category_url in category_urls:
            yield scrapy.Request(
                category_url,
                callback=self.parse_categories,
            )

    def parse_categories(self, response):
        # Extract the total number of pages from the pagination text
        pagination_text = response.css("div.pagination span::text").get()
        total_pages = int(pagination_text.split(" ")[-1]) if pagination_text else 1
        self.logger.info(f"Found {total_pages} pages in category {response.url}")

        base_url = response.url
        if "/page/" in response.url:
            base_url = response.url.split("/page/")[0]
        else:
            base_url = base_url.rstrip("/")

        # Generate URLs for all pages in reverse order, excluding the first page since it's already processed
        page_urls = [f"{base_url}/page/{page}/" for page in range(total_pages, 1, -1)]

        # Iterate over each subsequent page URL in reverse order to scrape channels
        for page_url in page_urls:
            yield scrapy.Request(page_url, callback=self.parse_page)

        # Process the first page last
        yield from self.parse_page(response)

    def parse_page(self, response):
        channel_elements = response.css("article.item.movies")
        source_name = response.css(".logo a img::attr(alt)").get(default="NowMeTV")

        for channel_element in channel_elements:
            # Extract title, poster, and stream page URL using Scrapy's CSS selectors
            title = channel_element.css("h3 > a::text").get()
            channel_page_url = channel_element.css(".poster > a::attr(href)").get()

            channel_data = {
                "title": title,
                "channel_page_url": channel_page_url,
                "source": source_name,
            }

            # Enqueue a request to the channel page URL to scrape M3U8 URLs
            yield scrapy.Request(
                channel_page_url,
                callback=self.parse_channel_page,
                meta={
                    "channel_data": channel_data,
                },
            )

    def parse_channel_page(self, response):
        channel_data = self.extract_channel_data(response)
        player_api_base = self.extract_player_api_base(response)
        if not player_api_base:
            self.logger.error(f"Player API base URL not found for {response.url}")
            return

        poster = channel_data.get("poster")
        if poster:
            # The validation and subsequent actions happen in the callback.
            yield scrapy.Request(
                poster,
                callback=self.on_validate_poster,
                meta={
                    "channel_data": channel_data,
                    "player_api_base": player_api_base,
                    "response": response,  # Pass the original response to access player options later
                },
                dont_filter=True,
            )

    def on_validate_poster(self, response):
        meta = response.meta
        original_response = meta["response"]
        channel_data = meta["channel_data"]
        player_api_base = meta["player_api_base"]
        content_type = response.headers.get("Content-Type", b"").decode().lower()
        is_image = "image" in content_type

        is_allowed_url = any(
            url in response.url for url in self.exclude_validation_urls
        )

        if is_image or is_allowed_url:
            yield from self.process_player_options(
                original_response, channel_data, player_api_base
            )
        else:
            self.logger.error(f"Invalid poster URL: {response.url}")

    def extract_channel_data(self, response):
        """Extracts channel data such as genres and poster."""
        channel_data = response.meta.get("channel_data").copy()
        poster = response.css(".poster > img::attr(src)").get()
        genres = response.css(".sgeneros a[rel='tag']::text").getall()
        channel_data.update(
            {
                "genres": genres,
                "poster": poster,
                "background": poster,
                "tv_language": genres[0] if genres else "English",
            }
        )
        return channel_data

    def extract_player_api_base(self, response):
        """Extracts the player API base URL."""
        return (
            response.xpath("//script[contains(text(), 'player_api')]/text()")
            .re_first(r'"player_api":"([^"]+)"', default="")
            .replace("\\/", "/")
        )

    def extract_stream_details(self, element):
        """Extracts stream title and country name from an element."""
        stream_title = element.css("span.title::text").get().strip()
        country_flag_url = element.css("span.flag > img::attr(src)").get()
        country_name = "India"
        if country_flag_url:
            country_code = country_flag_url.split("/")[-1].split(".")[0]
            country_name = get_country_name(country_code)
        return stream_title, country_name

    def process_player_options(self, response, channel_data, player_api_base):
        for element in response.css("#playeroptionsul > li.dooplay_player_option"):
            yield from self.process_player_option(
                element, channel_data, player_api_base
            )

    def process_player_option(self, element, channel_data, player_api_base):
        """Processes each player option element to yield API request."""
        stream_title, country_name = self.extract_stream_details(element)
        data_post, data_nume, data_type = (
            element.attrib.get("data-post"),
            element.attrib.get("data-nume"),
            element.attrib.get("data-type"),
        )

        if all([data_post, data_nume, data_type]):
            api_url = f"{player_api_base}{data_post}/{data_type}/{data_nume}"
            yield scrapy.Request(
                url=api_url,
                callback=self.parse_api_response,
                meta={
                    "channel_data": channel_data,
                    "stream_title": stream_title,
                    "country_name": country_name,
                },
            )

    def parse_api_response(self, response):
        channel_data = response.meta.get("channel_data")
        stream_title = response.meta.get("stream_title")
        country_name = response.meta.get("country_name")

        # Deserialize JSON response
        api_data = response.json()
        iframe_url = urljoin(
            response.url, api_data.get("embed_url", "").replace("\\/", "/")
        )

        if iframe_url:
            yield scrapy.Request(
                url=iframe_url,
                callback=self.request_and_extract_video_url,
                meta={
                    "channel_data": channel_data,
                    "stream_title": stream_title,
                    "country_name": country_name,
                    "iframe_url": iframe_url,
                },
            )

    def extract_m3u8_urls(self, response):
        """Extracts M3U8 URLs using direct and fallback regex patterns."""
        parsed_url = urlparse(response.url)
        channel_id = parsed_url.query.split("=")[-1]

        m3u8_urls = re.findall(
            rf"{re.escape(channel_id)}['\"]:\s*{{\s*url:\s*['\"](.*?\.m3u8.*?)['\"]",
            response.text,
        )
        if not m3u8_urls:
            m3u8_urls = self.fallback_pattern.findall(response.text)

        user_agent = response.request.headers.get("User-Agent").decode()
        parsed_url = urlparse(response.url)
        referer = f"{parsed_url.scheme}://{parsed_url.netloc}"

        behavior_hints = {
            "notWebReady": True,
            "proxyHeaders": {
                "request": {
                    "User-Agent": user_agent,
                    "Referer": referer,
                }
            },
        }

        return m3u8_urls, behavior_hints

    def request_and_extract_video_url(self, response):
        channel_data = response.meta.get("channel_data")
        stream_title = response.meta.get("stream_title")
        country_name = response.meta.get("country_name")

        # Extract M3U8 URLs
        m3u8_urls, behavior_hints = self.extract_m3u8_urls(response)
        if not m3u8_urls:
            self.logger.error(
                "No M3U8 URLs found for channel url: %s, stream title: %s",
                channel_data["channel_page_url"],
                stream_title,
            )
            return

        for index, url in enumerate(m3u8_urls, 1):
            full_url = urljoin(response.url, url)
            # Instead of appending to streams_info, initiate validation request
            yield scrapy.Request(
                url=full_url,
                headers=behavior_hints["proxyHeaders"]["request"],
                callback=self.validate_m3u8_url,
                errback=self.handle_m3u8_failure,
                meta={
                    "index": index if len(m3u8_urls) > 1 else None,
                    "stream_title": stream_title,
                    "full_url": full_url,
                    "country_name": country_name,
                    "channel_data": channel_data,
                    "behavior_hints": behavior_hints,
                },
                dont_filter=True,
            )

    def validate_m3u8_url(self, response):
        meta = response.meta
        content_type = response.headers.get("Content-Type", b"").decode().lower()

        if response.status == 200 and content_type in const.M3U8_VALID_CONTENT_TYPES:
            # Content type is valid, proceed with adding the stream
            stream_info = {
                "name": f"{meta['stream_title']} - {meta['index']}"
                if meta["index"]
                else meta["stream_title"],
                "url": meta["full_url"],
                "country": meta["country_name"],
                "behaviorHints": meta["behavior_hints"],
                "source": meta["channel_data"]["source"],
            }
            # Update channel data with this stream and yield
            updated_channel_data = self.create_channel_data_with_stream(
                meta["channel_data"], stream_info, meta["country_name"]
            )
            yield updated_channel_data
        else:
            self.logger.error(
                f"Invalid M3U8 URL: {meta['full_url']} with Content-Type: {content_type}"
            )

    def handle_m3u8_failure(self, failure):
        self.logger.error(
            "Failed to get m3u8 URL from channel page: %s stream title: %s",
            failure.request.meta["channel_data"]["channel_page_url"],
            failure.request.meta["stream_title"],
        )

    def create_channel_data_with_stream(self, channel_data, stream_info, country_name):
        """Create channel data with a single stream info"""
        # Copy the original channel data to avoid mutating the original meta
        channel_data_copy = channel_data.copy()

        # Directly set the streams list to only include the current stream
        channel_data_copy["streams"] = [stream_info]
        channel_data_copy["country"] = country_name

        return channel_data_copy
