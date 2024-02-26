import re
from datetime import datetime

import scrapy

from db.models import TorrentStreams, Episode
from utils.parser import convert_size_to_bytes
from utils.torrent import parse_magnet


class FormulaTgxSpider(scrapy.Spider):
    name = "formula_tgx"
    allowed_domains = ["torrentgalaxy.to", "tgx.rs"]
    start_urls = [
        "https://torrentgalaxy.to/profile/egortech/torrents/0",
        "https://tgx.rs/profile/egortech/torrents/0",
    ]
    formula1_keyword_patterns = re.compile(r"formula[ .+]*[1234e]+", re.IGNORECASE)
    uploader_parsing_functions = {
        "egortech": "parse_torrent_details_egortech",
    }

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.TorrentDuplicatesPipeline": 100,
            "mediafusion_scrapy.pipelines.FormulaStorePipeline": 200,
        }
    }

    async def parse(self, response, **kwargs):
        uploader_profile_name = response.url.split("/")[4]
        self.logger.info(f"Scraping torrents from {uploader_profile_name}")
        parsing_function_name = self.uploader_parsing_functions[uploader_profile_name]
        parsing_function = getattr(self, parsing_function_name, None)

        # Extract the last page number only once at the beginning
        if response.url in self.start_urls:
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
                yield response.follow(next_page_url, self.parse)

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
                "seeders": seeders,
                "torrent_page_link": torrent_page_link,
                "unique_id": tgx_unique_id,
                "source": f"TorrentGalaxy ({uploader_profile_name})",
                "announce_list": announce_list,
                "catalog": ["formula_racing"],
            }

            torrent_stream = await TorrentStreams.get(info_hash)
            if torrent_stream:
                self.logger.info(f"Torrent stream already exists: {torrent_name}")
                torrent_stream.seeders = seeders
                await torrent_stream.save()
            else:
                yield response.follow(
                    torrent_page_link,
                    parsing_function,
                    meta={"torrent_data": torrent_data},
                )

    def parse_torrent_details_egortech(self, response):
        torrent_data = response.meta["torrent_data"]

        # Extracting file details and sizes
        file_details = []
        for row in response.xpath('//table[contains(@class, "table-striped")]/tr'):
            file_name = row.xpath('td[@class="table_col1"]/text()').get()
            file_size = row.xpath('td[@class="table_col2"]/text()').get()
            if file_name and file_size:
                file_details.append({"file_name": file_name, "file_size": file_size})

        cover_image_url = response.xpath(
            "//center/img[contains(@class, 'img-responsive') and contains(@data-src, '.png')]/@data-src"
        ).get()
        torrent_data["poster"] = cover_image_url
        torrent_data["background"] = cover_image_url

        # Processing the description for video, audio, and other details
        torrent_description = "".join(
            response.xpath(
                "//font/following-sibling::*[1]/following-sibling::text() | //font/following-sibling::*[1]/following-sibling::*//text()"
            ).extract()
        )

        quality_match = re.search(r"Quality:\s*(\S+)", torrent_description)
        codec_match = re.search(r"Video:\s*([A-Za-z0-9]+)", torrent_description)
        audio_match = re.search(r"Audio:\s*([A-Za-z0-9. ]+)", torrent_description)

        if quality_match:
            torrent_data["quality"] = quality_match.group(1)
        if codec_match:
            torrent_data["codec"] = codec_match.group(1)
        if audio_match:
            torrent_data["audio"] = audio_match.group(1)

        contains_index = torrent_description.find("Contains:")
        episodes = []

        if contains_index != -1:
            contents_section = torrent_description[
                contains_index + len("Contains:") :
            ].strip()

            items = [
                item.strip()
                for item in re.split(r"\r?\n", contents_section)
                if item.strip()
            ]

            for index, item in enumerate(items):
                file_detail = file_details[index]
                episodes.append(
                    Episode(
                        episode_number=index + 1,
                        filename=file_detail.get("file_name"),
                        size=convert_size_to_bytes(file_detail.get("file_size")),
                        file_index=index,
                        title=item,
                    )
                )

        torrent_data["episodes"] = episodes

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
                self.parse_torrent_details_egortech,
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

        # cleanup "." from torrent name for and add unique_id for title to be unique for indexing
        torrent_data[
            "title"
        ] = f"{torrent_data['torrent_name'].replace('.', '')} {torrent_data['unique_id']}"

        resolution_match = re.search(r"(\d{3,4}P)", torrent_data["torrent_name"])
        if resolution_match:
            torrent_data["resolution"] = resolution_match.group(1).lower()

        # Extract year from the torrent name
        year_match = re.search(r"\b(19|20)\d{2}\b", torrent_data["torrent_name"])
        if year_match:
            torrent_data["year"] = int(year_match.group())

        yield torrent_data
