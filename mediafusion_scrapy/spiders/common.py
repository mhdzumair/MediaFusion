import logging
import re

import scrapy

from utils.config import config_manager
from utils.runtime_const import REDIS_SYNC_CLIENT


class CommonTamilSpider(scrapy.Spider):
    name = None
    source = None

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.TorrentDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.CatalogParsePipeline": 150,
            "mediafusion_scrapy.pipelines.MovieStorePipeline": 200,
            "mediafusion_scrapy.pipelines.SeriesStorePipeline": 300,
            "mediafusion_scrapy.pipelines.RedisCacheURLPipeline": 400,
        },
    }

    def __init__(
        self,
        pages: int = 1,
        start_page: int = 1,
        search_keyword: str = None,
        scrap_catalog_id: str = "all",
        *args,
        **kwargs,
    ):
        super(CommonTamilSpider, self).__init__(*args, **kwargs)
        self.pages = pages
        self.start_page = start_page
        self.search_keyword = search_keyword
        if scrap_catalog_id != "all" and "_" not in scrap_catalog_id:
            self.logger.error(
                f"Invalid catalog ID: {scrap_catalog_id}. Expected format: <language>_<video_type>"
            )
            return
        self.scrap_catalog_id = scrap_catalog_id
        logging.info(f"Scraping catalog ID: {self.scrap_catalog_id}")
        self.redis = REDIS_SYNC_CLIENT
        self.scraped_urls_key = f"{self.name}_scraped_urls"
        self.catalogs = config_manager.get_scraper_config(self.name, "catalogs")
        self.homepage = config_manager.get_scraper_config(self.name, "homepage")
        self.supported_search_forums = config_manager.get_scraper_config(
            self.name, "supported_search_forums"
        )

    def __del__(self):
        self.redis.close()

    def generate_forum_data(self):
        data = []
        link_prefix = f"{self.homepage}/index.php?/forums/forum/"
        for language, catalog in self.catalogs.items():
            for video_type, forum_ids in catalog.items():
                scrap_links = (
                    [link_prefix + str(forum_id) for forum_id in forum_ids]
                    if isinstance(forum_ids, list)
                    else [link_prefix + str(forum_ids)]
                )
                for link in scrap_links:
                    for page in range(self.start_page, self.start_page + self.pages):
                        paginated_link = f"{link}/page/{page}/"
                        data.append((paginated_link, language, video_type))
        return data

    def start_requests(self):
        if self.search_keyword:
            # Construct the search URL and initiate search
            search_url = f"{self.homepage}/index.php?/search/&q={self.search_keyword}&type=forums_topic&search_and_or=or&search_in=titles&sortby=relevancy"
            yield scrapy.Request(search_url, self.parse_search_results)
        else:
            if self.scrap_catalog_id == "all":
                for url, language, video_type in self.generate_forum_data():
                    yield scrapy.Request(
                        url,
                        self.parse_page_results,
                        meta={
                            "item": {
                                "language": language,
                                "video_type": video_type,
                                "source": self.source,
                            }
                        },
                    )
            else:
                language, video_type = self.scrap_catalog_id.split("_")
                forum_ids = self.catalogs.get(language, {}).get(video_type)
                if not forum_ids:
                    self.logger.error(f"Invalid catalog ID: {self.scrap_catalog_id}")
                    return

                forum_links = (
                    [
                        f"{self.homepage}/index.php?/forums/forum/{forum_id}"
                        for forum_id in forum_ids
                    ]
                    if isinstance(forum_ids, list)
                    else [f"{self.homepage}/index.php?/forums/forum/{forum_ids}"]
                )

                for forum_link in forum_links:
                    yield scrapy.Request(
                        forum_link,
                        self.parse_page_results,
                        meta={
                            "item": {
                                "language": language,
                                "video_type": video_type,
                                "source": self.source,
                            }
                        },
                    )

    def parse_search_results(self, response):
        movies = response.css("li[data-role='activityItem']")
        for movie in movies:
            movie_page_link = movie.css("a[data-linktype='link']::attr(href)").get()
            if not movie_page_link:
                continue

            if not self.check_scraped_urls(movie_page_link):
                yield response.follow(
                    movie_page_link,
                    self.parse_movie_page,
                    meta={
                        "item": {
                            "source": self.source,
                            "webpage_url": movie_page_link,
                        }
                    },
                )

        # Handling pagination
        next_page_link = response.css("a[rel='next']::attr(href)").get()
        if next_page_link:
            yield response.follow(next_page_link, self.parse_search_results)

    def parse_page_results(self, response):
        movies = response.css("li[data-rowid]")
        for movie in movies:
            movie_page_link = movie.css("a[data-ipshover-target]::attr(href)").get()
            if not movie_page_link:
                continue

            if not self.check_scraped_urls(movie_page_link):
                item = response.meta["item"].copy()
                item["webpage_url"] = movie_page_link
                yield response.follow(
                    movie_page_link, self.parse_movie_page, meta={"item": item}
                )

    def check_scraped_urls(self, page_link):
        if self.redis.sismember(self.scraped_urls_key, page_link):
            self.logger.info(f"Skipping already scraped URL: {page_link}")
            return True
        return False

    def parse_movie_page(self, response):
        item = response.meta["item"].copy()
        poster = response.css(
            "div[data-commenttype='forums'] img::attr(data-src), div[data-commenttype='forums'] img::attr(src)"
        ).get()
        created_at = response.css("time::attr(datetime)").get()
        torrent_links = response.css("a[data-fileext='torrent']::attr(href)").getall()

        if self.search_keyword:
            forum_link = response.css("a[href*='forums/forum/']::attr(href)").get()
            if not forum_link:
                return
            forum_id = re.search(r"forums/forum/([^/]+)/", forum_link).group(1)
            language, video_type = self.supported_search_forums.get(
                forum_id, {"language": None, "video_type": None}
            ).values()
            if not language:
                self.logger.error(f"Unsupported forum {forum_id}")
                return
            item.update({"language": language, "video_type": video_type})

        if not torrent_links:
            self.logger.warning(f"No torrents found for {response.url}")
            self.redis.sadd(self.scraped_urls_key, response.url)
            return

        for torrent_link in torrent_links:
            torrent_item = item.copy()
            torrent_item.update(
                {
                    "type": (
                        "series" if torrent_item["video_type"] == "series" else "movie"
                    ),
                    "poster": poster,
                    "created_at": created_at,
                    "language": torrent_item["language"].title(),
                    "torrent_link": torrent_link,
                    "scraped_url_key": self.scraped_urls_key,
                }
            )

            yield torrent_item
