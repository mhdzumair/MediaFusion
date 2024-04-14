import random
import re
from datetime import datetime

import redis.asyncio as redis
import scrapy

from db.config import settings
from db.models import TorrentStreams
from utils.parser import convert_size_to_bytes
from utils.torrent import parse_magnet


class FormulaTgxSpider(scrapy.Spider):
    name = "formula_tgx"
    allowed_domains = ["torrentgalaxy.to", "tgx.rs", "torrentgalaxy.mx", "tgx.sb"]
    formula_uploader_profiles = [
        "egortech",
        "F1Carreras",
        "smcgill1969",
    ]

    formula1_keyword_patterns = re.compile(r"formula[ .+]*[1234e]+", re.IGNORECASE)

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.TorrentDuplicatesPipeline": 100,
            "mediafusion_scrapy.pipelines.FormulaParserPipeline": 200,
            "mediafusion_scrapy.pipelines.FormulaStorePipeline": 300,
        }
    }

    def __init__(self, scrape_all: str = "True", *args, **kwargs):
        super(FormulaTgxSpider, self).__init__(*args, **kwargs)
        self.scrape_all = scrape_all.lower() == "true"
        self.redis = redis.Redis(
            connection_pool=redis.ConnectionPool.from_url(settings.redis_url)
        )
        self.scraped_info_hash_key = "formula_tgx_scraped_info_hash"

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.redis.aclose()

    def start_requests(self):
        for uploader_profile in self.formula_uploader_profiles:
            yield scrapy.Request(
                f"https://{random.choice(self.allowed_domains)}/profile/{uploader_profile}/torrents/0",
                self.parse,
                meta={"uploader_profile": uploader_profile},
            )

    async def parse(self, response, **kwargs):
        uploader_profile_name = response.meta["uploader_profile"]
        self.logger.info(f"Scraping torrents from {uploader_profile_name}")

        # Extract the last page number only once at the beginning
        if self.scrape_all and response.url.endswith("0"):
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
                yield response.follow(next_page_url, self.parse, meta=response.meta)

        # Extract torrents from the page
        for torrent in response.css("div.tgxtablerow.txlight"):
            urls = torrent.css("div.tgxtablecell a::attr(href)").getall()

            torrent_name = torrent.css(
                "div.tgxtablecell.clickable-row.click.textshadow.rounded.txlight a b::text"
            ).get()

            if not self.formula1_keyword_patterns.search(torrent_name):
                continue

            tgx_unique_id = urls[0].split("/")[-2]
            torrent_page_link = response.urljoin(urls[0])
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

            torrent_data = {
                "info_hash": info_hash,
                "torrent_name": torrent_name,
                "torrent_link": torrent_link,
                "magnet_link": magnet_link,
                "background": "https://i.postimg.cc/S4wcrGRZ/f1background.png?dl=1",
                "logo": "https://i.postimg.cc/Sqf4V8tj/f1logo.png?dl=1",
                "seeders": seeders,
                "torrent_page_link": torrent_page_link,
                "unique_id": tgx_unique_id,
                "source": f"TorrentGalaxy ({uploader_profile_name})",
                "uploader": uploader_profile_name,
                "announce_list": announce_list,
                "catalog": ["formula_racing"],
                "scraped_info_hash_key": self.scraped_info_hash_key,
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
                    meta={"torrent_data": torrent_data},
                )

    def parse_torrent_details(self, response):
        torrent_data = response.meta["torrent_data"]

        # Extracting file details and sizes
        file_details = []
        for row in response.xpath('//table[contains(@class, "table-striped")]/tr'):
            file_name = row.xpath('td[@class="table_col1"]/text()').get()
            file_size = row.xpath('td[@class="table_col2"]/text()').get()
            if file_name and file_size:
                file_details.append({"file_name": file_name, "file_size": file_size})
        if not file_details:
            self.logger.warning(
                f"File details not found for {torrent_data['torrent_name']}. Retrying"
            )
            yield response.follow(
                response.url,
                self.parse_torrent_details,
                meta={"torrent_data": torrent_data},
            )
            return
        torrent_data["file_details"] = file_details

        cover_image_url = response.xpath(
            "//img[contains(@class, 'img-responsive') and contains(@data-src, '.png')]/@data-src"
        ).get()
        torrent_data["poster"] = cover_image_url

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
        else:
            # if the total size is not found, then tgx has shown captcha validation.
            # so we need to slow down and retry the request
            self.logger.warning(
                f"Total size not found for {torrent_data['torrent_name']}. Retrying"
            )
            yield response.follow(
                response.url,
                self.parse_torrent_details,
                meta={"torrent_data": torrent_data},
            )

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
