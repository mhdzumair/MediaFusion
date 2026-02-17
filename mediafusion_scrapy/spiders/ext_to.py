import random
import re
from copy import deepcopy
from datetime import datetime
from urllib.parse import quote_plus, unquote

import PTT
import scrapy

from db import crud
from db.database import get_async_session
from db.redis_database import REDIS_ASYNC_CLIENT
from utils.config import config_manager
from utils.parser import convert_size_to_bytes, is_non_video_title
from utils.runtime_const import SPORTS_ARTIFACTS
from utils.torrent import parse_magnet

MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\"'<>\s]*")


class ExtToSpider(scrapy.Spider):
    """Base spider for scraping ext.to torrent site via FlareSolverr.

    ext.to is behind Cloudflare protection. This spider uses the existing
    FlaresolverrMiddleware to bypass it by setting use_flaresolverr = True.
    No Playwright/Browserless is needed since ext.to serves server-rendered HTML.

    Supports two scraping modes:
      1. Profile-based: scrapes uploads from specific user profiles
         URL pattern: /user/<username>/uploads/ (paginated as /user/<username>/uploads/2/)
      2. Search-based: searches by query keywords
         URL pattern: /browse/?q=<query>&sort=seeds&order=desc

    HTML structure (verified Feb 2026):
      Browse page:
        - Table: table.table-striped.table-hover > tbody > tr
        - Title: a.torrent-title-link b (with <span> tags around highlighted terms)
        - Pagination: ul.pagination li.active + li a
      Profile page:
        - Table: same table structure
        - Title: td.text-left .float-left a b (plain, no .torrent-title-link class)
        - Pagination: div.pagination-block > a.page-link (URLs like /user/X/uploads/2/)
      Both pages:
        - Size: td.nowrap-td .add-block-wrapper with "Size" label
        - Seeders: td .add-block-wrapper span.text-success
        - IMDB: a[href*="imdb_id="]
      Detail page:
        - Magnet: embedded in "Watch online" link (webga.zx), extracted via regex
    """

    allowed_domains = config_manager.get_start_url("ext_to") or ["ext.to"]
    use_flaresolverr = True

    uploader_profiles: list[str] = []
    search_queries: list[str] = []
    catalog: list[str]
    background_image: str
    logo_image: str

    keyword_patterns: re.Pattern
    scraped_info_hash_key: str

    def __init__(self, scrape_all: str = "False", total_pages: int = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scrape_all = scrape_all.lower() == "true"
        self.total_pages = total_pages
        self.redis = REDIS_ASYNC_CLIENT

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.redis.aclose()

    async def start(self):
        domain = self.allowed_domains[0] if self.allowed_domains else "ext.to"

        for username in self.uploader_profiles:
            profile_url = f"https://{domain}/user/{username}/uploads/"
            yield scrapy.Request(
                profile_url,
                self.parse,
                meta={
                    "uploader_profile": username,
                    "is_profile_page": True,
                },
            )

        for query in self.search_queries:
            encoded_query = quote_plus(query)
            search_url = f"https://{domain}/browse/?q={encoded_query}&sort=seeds&order=desc"
            yield scrapy.Request(
                search_url,
                self.parse,
                meta={
                    "search_query": query,
                    "is_profile_page": False,
                },
            )

    def _extract_row_data(self, row, is_profile_page):
        """Extract torrent name, detail link, and uploader from a table row.

        Profile pages and browse pages use slightly different HTML for the title link.
        """
        if is_profile_page:
            link = row.css("td.text-left .float-left a")
        else:
            link = row.css("a.torrent-title-link")
            if not link:
                link = row.css("td.text-left .float-left a")

        if not link:
            return None, None

        name_parts = link.css("b ::text").getall()
        if not name_parts:
            return None, None

        torrent_name = "".join(name_parts).strip()
        detail_link = link.attrib.get("href")
        return torrent_name, detail_link

    async def parse(self, response, **kwargs):
        is_profile_page = response.meta.get("is_profile_page", False)
        uploader = response.meta.get("uploader_profile")
        search_query = response.meta.get("search_query", "")
        source_label = f"profile:{uploader}" if is_profile_page else f"search:{search_query}"
        self.logger.info(f"Parsing {source_label} from {response.url}")

        rows = response.css("table.table-striped.table-hover tbody tr")
        if not rows:
            self.logger.warning(f"No table rows found on {response.url}")
            return

        for row in rows:
            torrent_name, detail_link = self._extract_row_data(row, is_profile_page)
            if not torrent_name or not detail_link:
                continue

            if not self.keyword_patterns.search(torrent_name):
                self.logger.debug(f"Skipping torrent (keyword mismatch): {torrent_name}")
                continue

            if is_non_video_title(torrent_name):
                self.logger.debug(f"Skipping non-video content: {torrent_name}")
                continue

            self.logger.info(f"Found: {torrent_name}")

            size_text = None
            for wrapper in row.css("td.nowrap-td .add-block-wrapper"):
                label = wrapper.css("span.add-block::text").get()
                if label and "size" in label.lower():
                    size_text = wrapper.css("span:not(.add-block)::text").get()
                    break

            seeders_text = row.css("td .add-block-wrapper span.text-success::text").get()
            seeders = int(seeders_text) if seeders_text and seeders_text.isdigit() else None

            imdb_link = row.css('a[href*="imdb_id="]::attr(href)').get()
            imdb_id = None
            if imdb_link:
                imdb_match = re.search(r"imdb_id=(tt\d+)", imdb_link)
                if imdb_match:
                    imdb_id = imdb_match.group(1)

            detail_url = response.urljoin(detail_link)
            ext_id = detail_link.rstrip("/").rsplit("-", 1)[-1] if detail_link else None

            torrent_data = {
                "torrent_title": torrent_name,
                "torrent_name": torrent_name,
                "background": self.background_image,
                "logo": self.logo_image,
                "seeders": seeders,
                "website": detail_url,
                "unique_id": ext_id,
                "source": "ExtTo",
                "catalog_source": "ext_to",
                "uploader": uploader,
                "catalog": self.catalog,
                "scraped_info_hash_key": self.scraped_info_hash_key,
                "imdb_id": imdb_id,
                "expected_sources": ["ExtTo", "Contribution Stream"],
            }

            if size_text:
                try:
                    torrent_data["total_size"] = convert_size_to_bytes(size_text.strip())
                except (ValueError, AttributeError):
                    pass

            yield scrapy.Request(
                detail_url,
                self.parse_torrent_details,
                meta={"torrent_data": torrent_data},
            )

        if self.scrape_all:
            next_req = self._follow_next_page(response, is_profile_page, uploader, search_query)
            if next_req:
                yield next_req

    def _follow_next_page(self, response, is_profile_page, uploader, search_query):
        """Handle pagination for both profile and browse pages."""
        current_page = response.meta.get("page_number", 1)
        if self.total_pages and current_page >= self.total_pages:
            return None

        if is_profile_page:
            for link in response.css("div.pagination-block a.page-link:not(.is-active):not(.dotted-link)"):
                href = link.attrib.get("href", "")
                text = link.css("::text").get() or ""
                if ">>" in text:
                    return response.follow(
                        href,
                        self.parse,
                        meta={
                            "uploader_profile": uploader,
                            "is_profile_page": True,
                            "page_number": current_page + 1,
                        },
                    )
        else:
            next_page = response.css("ul.pagination li.active + li a::attr(href)").get()
            if next_page:
                return response.follow(
                    next_page,
                    self.parse,
                    meta={
                        "search_query": search_query,
                        "is_profile_page": False,
                        "page_number": current_page + 1,
                    },
                )

        return None

    def _extract_magnet(self, response):
        """Extract magnet link from the detail page.

        ext.to does not expose magnet links via standard <a href="magnet:..."> tags.
        Instead, the magnet URI is embedded as a query parameter in the "Watch online"
        link (webga.zx). We extract it via regex from the full page HTML.
        """
        match = MAGNET_RE.search(response.text)
        if match:
            raw = match.group(0)
            return unquote(raw.replace("&amp;", "&"))
        return None

    def _extract_uploader(self, response):
        """Extract uploader username from a detail page."""
        uploader_href = response.css('a.simple-user[href*="/user/"]::attr(href)').get()
        if not uploader_href:
            uploader_href = response.css('.detail-torrent-poster-info a[href*="/user/"]::attr(href)').get()
        if uploader_href:
            return uploader_href.strip("/").split("/")[-1]
        return None

    async def parse_torrent_details(self, response):
        torrent_data = deepcopy(response.meta["torrent_data"])

        magnet_link = self._extract_magnet(response)
        if not magnet_link:
            self.logger.warning(f"No magnet link found on detail page: {response.url}")
            return

        info_hash, announce_list = parse_magnet(magnet_link)
        if not info_hash:
            self.logger.warning(f"Failed to parse magnet link: {response.url}")
            return

        torrent_data["info_hash"] = info_hash
        torrent_data["magnet_link"] = magnet_link
        torrent_data["announce_list"] = announce_list

        if not torrent_data.get("uploader"):
            torrent_data["uploader"] = self._extract_uploader(response)

        if await self.redis.sismember(self.scraped_info_hash_key, info_hash):
            self.logger.info(f"Torrent already scraped: {torrent_data['torrent_name']}")
            async for session in get_async_session():
                await crud.update_torrent_seeders(session, info_hash, torrent_data.get("seeders"))
            return

        file_data = []
        for row in response.css("#torrent_files table tr"):
            file_name = row.css("td.file-name-line-td span.folder-name a::text").get()
            if not file_name:
                continue
            file_name = file_name.strip()

            size_divs = [s.strip() for s in row.css("td.file-size-td div.file-size::text").getall() if s.strip()]
            file_size = size_divs[1] if len(size_divs) >= 2 else (size_divs[0] if size_divs else None)
            if not file_size:
                continue

            try:
                parsed_data = PTT.parse_title(file_name)
                file_data.append(
                    {
                        "filename": file_name,
                        "size": convert_size_to_bytes(file_size),
                        "index": len(file_data) + 1,
                        "seasons": parsed_data.get("seasons"),
                        "episodes": parsed_data.get("episodes"),
                        "title": parsed_data.get("title"),
                    }
                )
            except (ValueError, AttributeError):
                continue

        if file_data:
            torrent_data["file_data"] = file_data

        description_parts = response.css("#main ::text, div.tab-pane.active ::text").getall()
        torrent_data["description"] = " ".join(part.strip() for part in description_parts if part.strip()).replace(
            "\xa0", " "
        )

        poster_image = response.css(
            "img.img-responsive::attr(data-src), img.img-responsive::attr(src), .card-body img::attr(src)"
        ).get()
        if poster_image:
            torrent_data["poster"] = response.urljoin(poster_image)
            torrent_data["background"] = response.urljoin(poster_image)
        else:
            torrent_data["poster"] = None

        for span in response.css("span[title]"):
            title_attr = span.attrib.get("title", "")
            text_content = span.css("::text").get() or ""
            if "ago" in text_content:
                for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
                    try:
                        torrent_data["created_at"] = datetime.strptime(title_attr.strip(), fmt)
                        break
                    except ValueError:
                        continue
                if "created_at" in torrent_data:
                    break

        yield torrent_data


class FormulaExtSpider(ExtToSpider):
    name = "formula_ext"
    uploader_profiles = [
        "egortech",
        "smcgill1969",
    ]
    search_queries = [
        "formula 1",
        "formula 2",
        "formula 3",
    ]
    catalog = ["formula_racing"]
    background_image = "https://i.postimg.cc/S4wcrGRZ/f1background.png?dl=1"
    logo_image = "https://i.postimg.cc/Sqf4V8tj/f1logo.png?dl=1"

    keyword_patterns = re.compile(r"formula[ .+]*[1234e]+", re.IGNORECASE)
    scraped_info_hash_key = "formula_ext_scraped_info_hash"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.FormulaParserPipeline": 200,
            "mediafusion_scrapy.pipelines.EventSeriesStorePipeline": 300,
        },
    }


class MotoGPExtSpider(ExtToSpider):
    name = "motogp_ext"
    uploader_profiles = [
        "smcgill1969",
    ]
    search_queries = [
        "motogp",
    ]
    catalog = ["motogp_racing"]
    background_image = random.choice(SPORTS_ARTIFACTS["MotoGP Racing"]["background"])
    logo_image = random.choice(SPORTS_ARTIFACTS["MotoGP Racing"]["logo"])

    keyword_patterns = re.compile(r"MotoGP[ .+]*", re.IGNORECASE)
    scraped_info_hash_key = "motogp_ext_scraped_info_hash"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.MotoGPParserPipeline": 200,
            "mediafusion_scrapy.pipelines.EventSeriesStorePipeline": 300,
        },
    }


class WWEExtSpider(ExtToSpider):
    name = "wwe_ext"
    search_queries = [
        "wwe",
    ]
    catalog = ["fighting"]
    background_image = random.choice(SPORTS_ARTIFACTS["WWE"]["background"])
    logo_image = random.choice(SPORTS_ARTIFACTS["WWE"]["logo"])

    keyword_patterns = re.compile(r"wwe[ .+]*", re.IGNORECASE)
    scraped_info_hash_key = "wwe_ext_scraped_info_hash"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.WWEParserPipeline": 200,
            "mediafusion_scrapy.pipelines.MovieStorePipeline": 300,
        },
    }


class UFCExtSpider(ExtToSpider):
    name = "ufc_ext"
    search_queries = [
        "ufc",
    ]
    catalog = ["fighting"]
    background_image = random.choice(SPORTS_ARTIFACTS["UFC"]["background"])
    logo_image = random.choice(SPORTS_ARTIFACTS["UFC"]["logo"])

    keyword_patterns = re.compile(r"ufc[ .+]*", re.IGNORECASE)
    scraped_info_hash_key = "ufc_ext_scraped_info_hash"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.UFCParserPipeline": 200,
            "mediafusion_scrapy.pipelines.MovieStorePipeline": 300,
        },
    }


class MoviesTVExtSpider(ExtToSpider):
    name = "movies_tv_ext"
    search_queries = [
        "movies 2026",
        "movies 2025",
        "series 2026",
        "series 2025",
    ]
    catalog = []
    background_image = None
    logo_image = None

    keyword_patterns = re.compile(
        r"^(?!.*(?:WWE|UFC|Formula|MotoGP)).*$",
        re.IGNORECASE,
    )
    scraped_info_hash_key = "movies_tv_ext_scraped_info_hash"

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.MovieTVParserPipeline": 200,
            "mediafusion_scrapy.pipelines.CatalogParsePipeline": 300,
            "mediafusion_scrapy.pipelines.MovieStorePipeline": 400,
            "mediafusion_scrapy.pipelines.SeriesStorePipeline": 500,
        },
    }
