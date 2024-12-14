import random
import re
from datetime import datetime

import scrapy
from scrapy_playwright.page import PageMethod

from db.config import settings
from db.models import TorrentStreams
from utils.config import config_manager
from utils.parser import convert_size_to_bytes
from utils.runtime_const import SPORTS_ARTIFACTS
from db.redis_database import REDIS_ASYNC_CLIENT
from utils.torrent import parse_magnet


class TgxSpider(scrapy.Spider):
    allowed_domains = config_manager.get_start_url("tgx")
    uploader_profiles: list[str] = []
    search_queries: list[str] = []
    catalog: list[str]
    background_image: str
    logo_image: str

    keyword_patterns: re.Pattern
    scraped_info_hash_key: str

    def __init__(self, scrape_all: str = "False", *args, **kwargs):
        super(TgxSpider, self).__init__(*args, **kwargs)
        self.scrape_all = scrape_all.lower() == "true"
        self.redis = REDIS_ASYNC_CLIENT

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.redis.aclose()

    def start_requests(self):
        for uploader_profile in self.uploader_profiles:
            parse_url = f"https://{random.choice(self.allowed_domains)}/profile/{uploader_profile}/torrents/0"
            yield scrapy.Request(
                parse_url,
                self.parse,
                meta={
                    "uploader_profile": uploader_profile,
                    "playwright": True,
                    "playwright_page_goto_kwargs": {
                        "wait_until": "domcontentloaded",
                        "timeout": 60000,
                    },
                    "is_search_query": False,
                    "parse_url": parse_url,
                },
            )
        for search_query in self.search_queries:
            parse_url = f"https://{random.choice(self.allowed_domains)}/torrents.php?{search_query}"
            yield scrapy.Request(
                parse_url,
                self.parse,
                meta={
                    "playwright": True,
                    "playwright_page_goto_kwargs": {
                        "wait_until": "domcontentloaded",
                        "timeout": 60000,
                    },
                    "is_search_query": True,
                    "parse_url": parse_url,
                },
            )

    async def parse(self, response, **kwargs):
        if "galaxyfence.php" in response.url:
            self.logger.warning("Encountered galaxyfence.php. Retrying")
            parse_url = response.meta.get("parse_url")
            yield scrapy.Request(
                parse_url, self.parse, meta=response.meta, dont_filter=True, priority=10
            )
            return

        uploader_profile_name = response.meta.get("uploader_profile")
        is_search_query = response.meta.get("is_search_query")
        if not is_search_query:
            self.logger.info(f"Scraping torrents from {uploader_profile_name}")
            # Extract the last page number only once at the beginning
            if self.scrape_all and response.url.endswith("/0"):
                last_page_number = response.css(
                    "ul.pagination li.page-item:not(.disabled) a::attr(href)"
                ).re(r"/profile/.*/torrents/(\d+)")[-2]
                last_page_number = (
                    int(last_page_number) if last_page_number.isdigit() else 0
                )

                # Generate requests for all pages
                for page_number in range(1, last_page_number + 1):
                    next_page_url = (
                        f"{response.url.split('/torrents/')[0]}/torrents/{page_number}"
                    )
                    response.meta["parse_url"] = next_page_url
                    yield response.follow(next_page_url, self.parse, meta=response.meta)
        else:
            if self.scrape_all and response.url.endswith("page=0"):
                last_page_number = response.css(
                    "ul.pagination li.page-item:not(.disabled) a::attr(href)"
                ).re(r"/torrents.php.*page=(\d+)")[-2]
                last_page_number = (
                    int(last_page_number) if last_page_number.isdigit() else 0
                )

                # Generate requests for all pages
                for page_number in range(1, last_page_number + 1):
                    next_page_url = (
                        f"{response.url.replace('page=0', '')}page={page_number}"
                    )
                    response.meta["parse_url"] = next_page_url
                    yield response.follow(next_page_url, self.parse, meta=response.meta)
            self.logger.info(f"Scraping torrents from search query: {response.url}")

        # Extract torrents from the page
        for torrent in response.css("div.tgxtablerow.txlight"):
            torrent_page_relative_link = torrent.css("div#click::attr(data-href)").get()

            torrent.css("div#click::attr(data-href)")

            torrent_name = torrent.css(
                "div.tgxtablecell.clickable-row.click.textshadow.rounded.txlight a b::text"
            ).get()

            if not self.keyword_patterns.search(torrent_name):
                self.logger.info(f"Skipping torrent: {torrent_name}")
                continue
            self.logger.info(torrent_name)

            if is_search_query:
                uploader_profile_name = torrent.css("span.username.txlight::text").get()
                self.logger.info(f"Scraping torrents from {uploader_profile_name}")

            tgx_unique_id = torrent_page_relative_link.split("/")[-2]
            torrent_page_link = response.urljoin(torrent_page_relative_link)
            torrent_link = torrent.css(
                'a[href*="watercache.nanobytes.org"]::attr(href)'
            ).get()
            magnet_link = torrent.css('a[href^="magnet:?"]::attr(href)').get()
            info_hash, announce_list = parse_magnet(magnet_link)
            if not info_hash:
                self.logger.warning(
                    f"Failed to parse magnet link: {response.url}, {torrent_name}"
                )
                continue

            seeders = torrent.css(
                "div.tgxtablecell span[title='Seeders/Leechers'] font[color='green'] b::text"
            ).get()

            seeders = int(seeders) if seeders and seeders.isdigit() else None
            imdb_id = torrent.css("a[href*='search=tt']::attr(href)").re_first(r"tt\d+")

            torrent_data = {
                "info_hash": info_hash,
                "torrent_title": torrent_name,
                "torrent_name": torrent_name,
                "torrent_link": torrent_link,
                "magnet_link": magnet_link,
                "background": self.background_image,
                "logo": self.logo_image,
                "seeders": seeders,
                "website": torrent_page_link,
                "unique_id": tgx_unique_id,
                "source": "TorrentGalaxy",
                "uploader": uploader_profile_name,
                "announce_list": announce_list,
                "catalog": self.catalog,
                "scraped_info_hash_key": self.scraped_info_hash_key,
                "imdb_id": imdb_id,
            }

            if await self.redis.sismember(self.scraped_info_hash_key, info_hash):
                self.logger.info(f"Torrent already scraped: {torrent_name}")
                await TorrentStreams.find_one({"_id": info_hash}).update(
                    {"$set": {"seeders": seeders}},
                )
            else:
                yield response.follow(
                    torrent_page_link,
                    self.parse_torrent_details,
                    meta={
                        "playwright": True,
                        "playwright_page_goto_kwargs": {
                            "wait_until": "domcontentloaded",
                            "referer": response.url,
                            "timeout": 60000,
                        },
                        "torrent_data": torrent_data,
                    },
                )

    def parse_torrent_details(self, response):
        torrent_data = response.meta["torrent_data"].copy()

        if response.xpath("//blockquote[contains(., 'GALAXY CHECKPOINT')]"):
            self.logger.warning(
                f"Encountered GALAXY CHECKPOINT. Retrying: {response.url}"
            )
            yield self.retry_request(response)
            return

        # Extracting file details and sizes if available
        file_data = []
        file_list = response.xpath(
            '//button[contains(@class, "flist")]//em/text()'
        ).get()
        file_count = int(file_list.strip("()")) if file_list else 0
        for row in response.xpath('//table[contains(@class, "table-striped")]//tr'):
            file_name = row.xpath('td[@class="table_col1"]/text()').get()
            file_size = row.xpath('td[@class="table_col2"]/text()').get()
            if file_name and file_size:
                file_data.append({"filename": file_name, "size": file_size})

        if file_count == len(file_data):
            torrent_data["file_data"] = file_data

        cover_image_url = response.xpath(
            "//div[contains(@class, 'container-fluid')]/center//img[contains(@class, 'img-responsive')]/@data-src"
        ).get()

        if cover_image_url:
            torrent_data["poster"] = cover_image_url
            torrent_data["background"] = cover_image_url
        else:
            torrent_data["poster"] = None

        # Getting the description for parsing video, audio, and other details
        torrent_description = "".join(
            response.xpath(
                "//font/following-sibling::*[1]/following-sibling::text() | "
                "//font/following-sibling::*[1]/following-sibling::*//text() | "
                "//center/font/following::br/following-sibling::text() | "
                "//strong/following-sibling::text()[normalize-space()] | "
                "//strong/following-sibling::br/following-sibling::text()[normalize-space()] | "
                "//div[contains(@class, 'container-fluid')]//text()[normalize-space()]"
            ).extract()
        )
        torrent_data["description"] = torrent_description.replace("\xa0", " ")

        total_size = response.xpath(
            "//div[b='Total Size:']/following-sibling::div/text()"
        ).get()
        if total_size:
            torrent_data["total_size"] = convert_size_to_bytes(total_size)

        # Extracting date created
        date_created = response.xpath(
            "//div[b[contains(., 'Added:')]]/following-sibling::div/text()"
        ).get()
        if date_created:
            # Processing to extract the date and time
            torrent_data["created_at"] = datetime.strptime(
                date_created.strip(), "%d-%m-%Y %H:%M"
            )

        # Extracting language
        language = response.xpath(
            "//div[b='Language:']/following-sibling::div/text()"
        ).get()
        if language:
            torrent_data["languages"] = [language.strip()]

        yield torrent_data

    def handle_failure(self, failure):
        self.logger.error(repr(failure))
        yield self.retry_request(failure.request)

    def retry_request(self, request):
        retry_count = request.meta.get("retry_count", 0)
        if retry_count < 3:  # Set your retry limit
            new_url = request.url.replace(
                request.url.split("/")[2], random.choice(self.allowed_domains)
            )
            self.logger.info(f"Retrying with new URL: {new_url}")
            return scrapy.Request(
                url=new_url,
                callback=self.parse_torrent_details,
                errback=self.handle_failure,
                meta={
                    "playwright": True,
                    "playwright_page_goto_kwargs": {
                        "wait_until": "domcontentloaded",
                        "referer": request.url,
                        "timeout": 60000,
                    },
                    "torrent_data": request.meta["torrent_data"],
                    "retry_count": retry_count + 1,
                },
            )
        else:
            self.logger.error("Max retries reached")
            return None


class FormulaTgxSpider(TgxSpider):
    name = "formula_tgx"
    uploader_profiles = [
        "egortech",
        "F1Carreras",
        "smcgill1969",
    ]
    catalog = ["formula_racing"]
    background_image = "https://i.postimg.cc/S4wcrGRZ/f1background.png?dl=1"
    logo_image = "https://i.postimg.cc/Sqf4V8tj/f1logo.png?dl=1"

    keyword_patterns = re.compile(r"formula[ .+]*[1234e]+", re.IGNORECASE)
    scraped_info_hash_key = "formula_tgx_scraped_info_hash"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.FormulaParserPipeline": 200,
            "mediafusion_scrapy.pipelines.EventSeriesStorePipeline": 300,
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_CDP_URL": settings.playwright_cdp_url,
        "DOWNLOAD_HANDLERS": {
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_MAX_CONTEXTS": 2,
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 3,
    }


class MotoGPTgxSpider(TgxSpider):
    name = "motogp_tgx"
    uploader_profiles = [
        "smcgill1969",
    ]
    catalog = ["motogp_racing"]

    keyword_patterns = re.compile(r"MotoGP[ .+]*", re.IGNORECASE)
    scraped_info_hash_key = "motogp_tgx_scraped_info_hash"
    background_image = random.choice(SPORTS_ARTIFACTS["MotoGP"]["background"])
    logo_image = random.choice(SPORTS_ARTIFACTS["MotoGP"]["logo"])

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.MotoGPParserPipeline": 200,
            "mediafusion_scrapy.pipelines.EventSeriesStorePipeline": 300,
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_CDP_URL": settings.playwright_cdp_url,
        "DOWNLOAD_HANDLERS": {
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_MAX_CONTEXTS": 2,
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 3,
    }


class BaseEventSpider(TgxSpider):
    @staticmethod
    def get_custom_settings(pipeline):
        return {
            "ITEM_PIPELINES": {
                "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
                f"mediafusion_scrapy.pipelines.{pipeline}": 200,
                "mediafusion_scrapy.pipelines.MovieStorePipeline": 300,
            },
            "PLAYWRIGHT_BROWSER_TYPE": "chromium",
            "PLAYWRIGHT_CDP_URL": settings.playwright_cdp_url,
            "DOWNLOAD_HANDLERS": {
                "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
                "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            },
            "PLAYWRIGHT_MAX_CONTEXTS": 2,
            "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 3,
        }

    def __init__(
        self,
        catalog,
        search_queries,
        keyword_patterns,
        scraped_info_hash_key,
        background_image,
        logo_image,
        *args,
        **kwargs,
    ):
        self.catalog = catalog
        self.search_queries = search_queries
        self.keyword_patterns = re.compile(keyword_patterns, re.IGNORECASE)
        self.scraped_info_hash_key = scraped_info_hash_key
        self.background_image = background_image
        self.logo_image = logo_image
        super().__init__(*args, **kwargs)


class WWETGXSpider(BaseEventSpider):
    name = "wwe_tgx"
    custom_settings = BaseEventSpider.get_custom_settings("WWEParserPipeline")

    def __init__(self, *args, **kwargs):
        super().__init__(
            catalog=["fighting"],
            search_queries=["c7=1&c7=1&search=wwe&sort=id&order=desc&page=0"],
            keyword_patterns=r"wwe[ .+]*",
            scraped_info_hash_key="wwe_tgx_scraped_info_hash",
            background_image=random.choice(SPORTS_ARTIFACTS["WWE"]["background"]),
            logo_image=random.choice(SPORTS_ARTIFACTS["WWE"]["logo"]),
            *args,
            **kwargs,
        )


class UFCTGXSpider(BaseEventSpider):
    name = "ufc_tgx"
    custom_settings = BaseEventSpider.get_custom_settings("UFCParserPipeline")

    def __init__(self, *args, **kwargs):
        super().__init__(
            catalog=["fighting"],
            search_queries=["c7=1&c7=1&search=ufc&sort=id&order=desc&page=0"],
            keyword_patterns=r"ufc[ .+]*",
            scraped_info_hash_key="ufc_tgx_scraped_info_hash",
            background_image=random.choice(SPORTS_ARTIFACTS["UFC"]["background"]),
            logo_image=random.choice(SPORTS_ARTIFACTS["UFC"]["logo"]),
            *args,
            **kwargs,
        )
