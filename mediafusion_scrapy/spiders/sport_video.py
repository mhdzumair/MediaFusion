from urllib.parse import urlparse

import redis
import scrapy

from db.config import settings


class SportVideoSpider(scrapy.Spider):
    name = "sport_video"
    allowed_domains = ["www.sport-video.org.ua"]
    categories = {
        "american_football": "https://www.sport-video.org.ua/americanfootball.html",
        "basketball": "https://www.sport-video.org.ua/basketball.html",
        "baseball": "https://www.sport-video.org.ua/baseball.html",
        "football": "https://www.sport-video.org.ua/football.html",
        "hockey": "https://www.sport-video.org.ua/hockey.html",
        "rugby": "https://www.sport-video.org.ua/rugby.html",
        "other_sports": "https://www.sport-video.org.ua/other.html",
    }

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.TorrentDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.SportVideoParserPipeline": 200,
            "mediafusion_scrapy.pipelines.MovieStorePipeline": 300,
            "mediafusion_scrapy.pipelines.RedisCacheURLPipeline": 400,
        },
    }

    def __init__(self, scrape_all: str = "True", *args, **kwargs):
        super(SportVideoSpider, self).__init__(*args, **kwargs)
        self.scrape_all = scrape_all.lower() == "true"
        self.redis = redis.Redis(
            connection_pool=redis.ConnectionPool.from_url(settings.redis_url)
        )
        self.scraped_urls_key = "sport_video_scraped_urls"

    def __del__(self):
        self.redis.close()

    def start_requests(self):
        for category, url in self.categories.items():
            yield scrapy.Request(url, self.parse, meta={"category": category})

    def parse(self, response, **kwargs):
        category = response.meta["category"]
        # Parse the current page URL to extract the base path for comparison
        current_category_base = urlparse(response.url).path.rsplit(".", 1)[0]

        if self.scrape_all:
            # Navigate through the pagination
            page_links = set(
                response.css("div[id^='wb_Pagination'] ul li a::attr(href)").getall()
            )
            for link in page_links:
                # Ensure the link is not just a 'Next' or 'Prev' button
                if current_category_base in link:
                    yield response.follow(
                        link, self.parse_page, meta={"category": category}
                    )
        # Only scrape the first page
        yield from self.parse_page(response)

    def parse_page(self, response):
        category = response.meta["category"]
        # Generalized selector for all content blocks
        content_blocks = response.css('div[id^="wb_LayoutGrid"]')
        for content in content_blocks:
            # Extract only the first part of the title
            title_words = content.css('div[id^="wb_Text"] strong::text').getall()
            title = "".join(title_words).replace("(NEW)", "").strip()

            # Extract poster URL
            poster = content.css('div[id^="wb_PhotoGallery"] img::attr(src)').get()
            if poster:
                poster = response.urljoin(poster)

            # Extract torrent page link
            torrent_page_link = content.css('div[id^="wb_Shape"] a::attr(href)').get()
            if title and torrent_page_link:
                torrent_page_link = response.urljoin(torrent_page_link)
                # Check if URL has been scraped before
                if self.redis.sismember(self.scraped_urls_key, torrent_page_link):
                    self.logger.info(
                        f"Skipping already scraped URL: {torrent_page_link}"
                    )
                    continue

                yield response.follow(
                    torrent_page_link,
                    self.parse_torrent_page,
                    meta={
                        "item": {
                            "title": title.strip(),
                            "poster": poster,
                            "background": poster,
                            "webpage_url": torrent_page_link,
                            "is_parse_ptt": False,
                            "source": "sport-video.org.ua",
                            "is_add_title_to_poster": True,
                            "catalog": category,
                            "type": "movie",
                            "scraped_url_key": self.scraped_urls_key,
                        }
                    },
                )

    def parse_torrent_page(self, response):
        # Retrieve passed item data
        base_item = response.meta["item"]

        # Initialize variables to hold metadata and the current metadata block
        metadata_blocks = []
        current_block = {}

        table_rows = response.css("table tr")
        for row in table_rows:
            header = (
                row.css("td.cell0 strong::text")
                .extract_first(default="")
                .strip()
                .lower()
                .replace(" ", "_")
            )
            value = " ".join(row.css("td.cell0:nth-child(2) *::text").extract()).strip()

            # When encountering a 'description', it means a new torrent metadata block starts
            if header == "description" and current_block:
                # Save the current block and start a new one
                metadata_blocks.append(current_block)
                current_block = {}

            # Add the metadata to the current block
            current_block[header] = value

        # add the last block
        if current_block:
            metadata_blocks.append(current_block)

        # Extract all torrent links
        torrent_links = response.css('a[href$=".torrent"]::attr(href)').getall()

        # Correctly match metadata blocks to torrent links
        for i, link in enumerate(torrent_links):
            item = base_item.copy()  # Create a new item for each torrent link
            # If there's corresponding metadata, add it to the item
            if i < len(metadata_blocks):
                item.update(metadata_blocks[i])
                item["torrent_link"] = response.urljoin(link)
                yield item
