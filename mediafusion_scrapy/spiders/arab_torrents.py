import logging
from datetime import datetime

import scrapy

from scrapers.helpers import get_scraper_config


class ArabTorrentSpider(scrapy.Spider):
    name = "arab_torrents"
    source = "Arab-Torrents"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.MovieStorePipeline": 200,
            "mediafusion_scrapy.pipelines.SeriesStorePipeline": 300,
        },
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 522, 524, 408, 429],
        "RETRY_TIMES": 5,
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
        super(ArabTorrentSpider, self).__init__(*args, **kwargs)
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
        self.catalogs = get_scraper_config(self.name, "catalogs")
        self.homepage = get_scraper_config(self.name, "homepage")
        self.supported_search_forums = get_scraper_config(
            self.name, "supported_search_forums"
        )

    def generate_forum_data(self):
        data = []
        link_prefix = f"{self.homepage}/index.php?cat="
        for language, catalog in self.catalogs.items():
            for video_type, forum_ids in catalog.items():
                scrap_links = (
                    [link_prefix + str(forum_id) for forum_id in forum_ids]
                    if isinstance(forum_ids, list)
                    else [link_prefix + str(forum_ids)]
                )
                for link in scrap_links:
                    for page in range(self.start_page, self.start_page + self.pages):
                        paginated_link = f"{link}&p={page}"
                        data.append((paginated_link, language, video_type))
        return data

    def start_requests(self):
        if self.search_keyword:
            # Construct the search URL and initiate search
            search_url = f"{self.homepage}/index.php?search={self.search_keyword}"
            yield scrapy.Request(search_url, self.parse_page_results)
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
                        f"{self.homepage}/index.php?cat={forum_id}"
                        for forum_id in forum_ids
                    ]
                    if isinstance(forum_ids, list)
                    else [f"{self.homepage}/index.php?cat={forum_ids}"]
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

    def parse_page_results(self, response):
        movies = response.css("table#torrents tr")
        item = response.meta["item"].copy()

        if movies:
            # reverse the list to get the latest movies first
            movies = movies[::-1]
        for movie in movies:
            yield from self.parse_movie(response, movie, item)

    def parse_movie(self, response, movie, item):
        item = item.copy()
        title = movie.css("a[href^='magnet:?']::text").get()
        title = title.replace("تحميل", "").strip()
        poster = movie.xpath("//img[@class='posterIcon']/../@href").get()
        created_at = datetime.now()
        actors = movie.css("a.actor::text").getall()
        magnet_link = movie.css("a[href^='magnet:?']::attr(href)").get()
        category = movie.css("div.fcat::text").get()

        if self.search_keyword:
            video_type = "series" if "مسلسلات" in category else "movie"
            item.update({"language": "Arabic", "video_type": video_type})

        if not magnet_link:
            self.logger.warning(f"No magnet link found for {response.url}")
            return

        item.update(
            {
                "catalog": f"{item['language'].lower()}_{item['video_type']}",
                "type": "series" if item["video_type"] == "series" else "movie",
                "poster": poster,
                "created_at": created_at,
                "language": item["language"].title(),
                "magnet_link": magnet_link,
                "stars": actors,
            }
        )

        yield item
