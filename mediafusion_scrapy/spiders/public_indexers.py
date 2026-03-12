import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import scrapy

from utils.config import config_manager
from utils.parser import convert_size_to_bytes
from utils.torrent import parse_magnet

MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\"'<>\s]*")


class BasePublicIndexerSpider(scrapy.Spider):
    """Base spider for public torrent indexers emitting magnet-based items."""

    source = ""
    catalog_source = ""
    default_start_urls: tuple[str, ...] = ()
    search_url_template: str | None = None
    scraped_info_hash_key = ""
    use_anti_bot_solver = True

    row_selectors: tuple[str, ...] = ()
    title_selectors: tuple[str, ...] = ()
    detail_selectors: tuple[str, ...] = ()
    magnet_selectors: tuple[str, ...] = ()
    size_selectors: tuple[str, ...] = ()
    seeder_selectors: tuple[str, ...] = ()
    next_page_selectors: tuple[str, ...] = ()

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.MovieTVParserPipeline": 200,
            "mediafusion_scrapy.pipelines.CatalogParsePipeline": 300,
            "mediafusion_scrapy.pipelines.MovieStorePipeline": 400,
            "mediafusion_scrapy.pipelines.SeriesStorePipeline": 500,
        },
    }

    def __init__(
        self,
        scrape_all: str = "false",
        total_pages: int | str | None = None,
        search_keyword: str | None = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.scrape_all = str(scrape_all).lower() == "true"
        self.search_keyword = search_keyword
        self.total_pages = 1
        if self.scrape_all:
            self.total_pages = 3
        if total_pages is not None:
            try:
                self.total_pages = max(1, int(total_pages))
            except (TypeError, ValueError):
                self.logger.warning("Invalid total_pages value '%s'; defaulting to %s", total_pages, self.total_pages)

    def _configured_start_urls(self) -> list[str]:
        configured_urls = config_manager.get_start_url(self.name)
        if isinstance(configured_urls, str) and configured_urls.strip():
            return [configured_urls.strip()]
        if isinstance(configured_urls, (list, tuple)):
            urls = [url.strip() for url in configured_urls if isinstance(url, str) and url.strip()]
            if urls:
                return urls
        return [url for url in self.default_start_urls if url]

    async def start(self):
        if self.search_keyword and self.search_url_template:
            encoded_query = quote_plus(self.search_keyword)
            for page in range(1, self.total_pages + 1):
                yield scrapy.Request(
                    self.search_url_template.format(query=encoded_query, page=page),
                    callback=self.parse,
                    meta={"page_number": page},
                )
            return

        for url in self._configured_start_urls():
            yield scrapy.Request(url, callback=self.parse, meta={"page_number": 1})

    @staticmethod
    def _is_challenge_page(response: scrapy.http.Response) -> bool:
        title = (response.css("title::text").get() or "").strip().lower()
        if "just a moment" in title or "attention required" in title:
            return True
        return "cf-chl-" in response.text

    @staticmethod
    def _clean_text(value: str | None) -> str | None:
        if not value:
            return None
        cleaned = re.sub(r"\s+", " ", value).strip()
        return cleaned or None

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        if not value:
            return None
        match = re.search(r"\d[\d,]*", value)
        if not match:
            return None
        try:
            return int(match.group(0).replace(",", ""))
        except ValueError:
            return None

    @staticmethod
    def _parse_pub_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None

    def _first(self, selector_scope, selectors: tuple[str, ...]) -> str | None:
        for selector in selectors:
            value = selector_scope.css(selector).get()
            cleaned = self._clean_text(value)
            if cleaned:
                return cleaned
        return None

    def _extract_total_size(self, selector_scope) -> int | None:
        for selector in self.size_selectors:
            chunks = [self._clean_text(part) for part in selector_scope.css(selector).getall()]
            text = " ".join(part for part in chunks if part)
            if not text:
                continue
            match = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB|MB|KB|B)", text, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                return convert_size_to_bytes(f"{match.group(1)} {match.group(2).upper()}")
            except (ValueError, AttributeError):
                continue
        return None

    def _extract_rows(self, response: scrapy.http.Response):
        for selector in self.row_selectors:
            rows = response.css(selector)
            if rows:
                return rows
        return []

    def _extract_magnet(self, response: scrapy.http.Response) -> str | None:
        magnet = self._first(response, self.magnet_selectors)
        if magnet:
            return magnet.replace("&amp;", "&")
        match = MAGNET_RE.search(response.text)
        if match:
            return match.group(0).replace("&amp;", "&")
        return None

    def _build_item(
        self,
        *,
        title: str,
        detail_url: str | None,
        seeders: int | None,
        total_size: int | None,
        created_at: datetime | None,
    ) -> dict:
        item = {
            "torrent_title": title,
            "torrent_name": title,
            "source": self.source,
            "catalog_source": self.catalog_source,
            "catalog": [self.catalog_source],
            "scraped_info_hash_key": self.scraped_info_hash_key,
            "expected_sources": [self.source, "Contribution Stream"],
            "website": detail_url,
            "webpage_url": detail_url,
        }
        if seeders is not None:
            item["seeders"] = seeders
        if total_size is not None:
            item["total_size"] = total_size
        if created_at is not None:
            item["created_at"] = created_at
        return item

    def _apply_magnet(self, item: dict, magnet_link: str) -> dict:
        item["magnet_link"] = magnet_link
        info_hash, announce_list = parse_magnet(magnet_link)
        if info_hash:
            item["info_hash"] = info_hash
            item["announce_list"] = announce_list
        return item

    def parse(self, response: scrapy.http.Response):
        if self._is_challenge_page(response):
            self.logger.warning("Blocked by anti-bot challenge on %s", response.url)
            return

        rows = self._extract_rows(response)
        for row in rows:
            title = self._first(row, self.title_selectors)
            if not title:
                continue

            detail_href = self._first(row, self.detail_selectors)
            detail_url = response.urljoin(detail_href) if detail_href else None
            total_size = self._extract_total_size(row)
            seeders = self._parse_int(self._first(row, self.seeder_selectors))

            magnet_link = self._first(row, self.magnet_selectors)
            if magnet_link:
                item = self._build_item(
                    title=title,
                    detail_url=detail_url or response.url,
                    seeders=seeders,
                    total_size=total_size,
                    created_at=None,
                )
                yield self._apply_magnet(item, magnet_link.replace("&amp;", "&"))
                continue

            if detail_url:
                item = self._build_item(
                    title=title,
                    detail_url=detail_url,
                    seeders=seeders,
                    total_size=total_size,
                    created_at=None,
                )
                yield scrapy.Request(detail_url, callback=self.parse_detail, meta={"item": item})

        current_page = int(response.meta.get("page_number", 1))
        if not self.scrape_all or current_page >= self.total_pages:
            return

        next_href = self._first(response, self.next_page_selectors)
        if next_href:
            yield response.follow(next_href, callback=self.parse, meta={"page_number": current_page + 1})

    def parse_detail(self, response: scrapy.http.Response):
        if self._is_challenge_page(response):
            self.logger.warning("Blocked by anti-bot challenge on detail page %s", response.url)
            return

        magnet_link = self._extract_magnet(response)
        if not magnet_link:
            return

        item = dict(response.meta["item"])
        item["website"] = response.url
        item["webpage_url"] = response.url
        yield self._apply_magnet(item, magnet_link)


class X1337Spider(BasePublicIndexerSpider):
    name = "x1337"
    source = "1337x"
    catalog_source = "x1337"
    scraped_info_hash_key = "x1337_scraped_info_hash"
    default_start_urls = (
        "https://1337x.to/popular-movies",
        "https://1337x.to/popular-tv",
    )
    search_url_template = "https://1337x.to/search/{query}/{page}/"
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        # 1337x is heavily protected; these settings prioritize challenge
        # completion over crawl speed.
        "CONCURRENT_REQUESTS": 2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 8,
        "HTTPCACHE_ENABLED": False,
        "SCRAPLING_NETWORK_IDLE": False,
        "SCRAPLING_WAIT_TIME_MS": 4000,
        "SCRAPLING_MAX_TIMEOUT": 120000,
        "SCRAPLING_CLOUDFLARE_MAX_ATTEMPTS": 5,
        "SCRAPLING_GOOGLE_SEARCH_REFERER": True,
        "SCRAPLING_FETCHER_MODE": "stealthy",
        "SCRAPLING_SOLVE_CLOUDFLARE": True,
    }

    row_selectors = (
        "table.table-list tbody tr",
        "table.table-list tr",
    )
    title_selectors = (
        "td.name a:last-child::text",
        "td.name a:nth-child(2)::text",
        "a[href*='/torrent/']::text",
    )
    detail_selectors = (
        "td.name a:last-child::attr(href)",
        "td.name a:nth-child(2)::attr(href)",
        "a[href*='/torrent/']::attr(href)",
    )
    magnet_selectors = ("a[href^='magnet:?']::attr(href)",)
    size_selectors = (
        "td.size *::text",
        "td.size::text",
    )
    seeder_selectors = (
        "td.seeds::text",
        "td.seeders::text",
    )
    next_page_selectors = (
        "ul.pagination li.active + li a::attr(href)",
        "div.pagination li.active + li a::attr(href)",
    )


class BT52Spider(BasePublicIndexerSpider):
    name = "bt52"
    source = "52BT"
    catalog_source = "bt52"
    scraped_info_hash_key = "bt52_scraped_info_hash"
    default_start_urls = ("https://www.529072.xyz/", "https://www.529073.xyz/")
    search_url_template = None
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "CONCURRENT_REQUESTS": 2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 8,
        "HTTPCACHE_ENABLED": False,
        "SCRAPLING_NETWORK_IDLE": False,
        "SCRAPLING_WAIT_TIME_MS": 4000,
        "SCRAPLING_MAX_TIMEOUT": 120000,
        "SCRAPLING_CLOUDFLARE_MAX_ATTEMPTS": 5,
        "SCRAPLING_GOOGLE_SEARCH_REFERER": True,
        "SCRAPLING_FETCHER_MODE": "stealthy",
        "SCRAPLING_SOLVE_CLOUDFLARE": True,
    }

    row_selectors = (
        "table tbody tr",
        "div#threadlist table tbody tr",
        "div#threadlist ul li",
    )
    title_selectors = (
        "a[href*='thread']::text",
        "a[href*='/forum.php?mod=viewthread']::text",
        "a[href*='/topic/']::text",
    )
    detail_selectors = (
        "a[href*='thread']::attr(href)",
        "a[href*='/forum.php?mod=viewthread']::attr(href)",
        "a[href*='/topic/']::attr(href)",
    )
    magnet_selectors = ("a[href^='magnet:?']::attr(href)",)
    size_selectors = ("*::text",)
    seeder_selectors = (
        "td.seed::text",
        "td.seeds::text",
        "td:nth-child(6)::text",
    )
    next_page_selectors = (
        "a.nxt::attr(href)",
        "a.next::attr(href)",
    )


class UIndexSpider(BasePublicIndexerSpider):
    name = "uindex"
    source = "UIndex"
    catalog_source = "uindex"
    scraped_info_hash_key = "uindex_scraped_info_hash"
    default_start_urls = (
        "https://uindex.org/search.php?c=1",  # Movies
        "https://uindex.org/search.php?c=2",  # TV
        "https://uindex.org/search.php?c=7",  # Anime
    )
    search_url_template = "https://uindex.org/search.php?search={query}&c=0"
    use_anti_bot_solver = False
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "SCRAPLING_SOLVE_CLOUDFLARE": False,
        "SCRAPLING_FETCHER_MODE": "dynamic",
    }

    row_selectors = ("table tr",)
    title_selectors = ("a[href*='/details.php?id=']::text",)
    detail_selectors = ("a[href*='/details.php?id=']::attr(href)",)
    magnet_selectors = ("a[href^='magnet:?']::attr(href)",)
    size_selectors = ("td:nth-child(3)::text",)
    seeder_selectors = (
        "td:nth-child(4) span.g::text",
        "td:nth-child(4)::text",
    )
    next_page_selectors = ()


class EZTVRSSSpider(scrapy.Spider):
    """EZTV RSS spider used as a high-traffic control source."""

    name = "eztv_rss"
    source = "EZTV"
    catalog_source = "eztv_rss"
    scraped_info_hash_key = "eztv_rss_scraped_info_hash"
    use_anti_bot_solver = False

    custom_settings = {
        "ITEM_PIPELINES": {
            "mediafusion_scrapy.pipelines.MagnetDownloadAndParsePipeline": 100,
            "mediafusion_scrapy.pipelines.MovieTVParserPipeline": 200,
            "mediafusion_scrapy.pipelines.CatalogParsePipeline": 300,
            "mediafusion_scrapy.pipelines.MovieStorePipeline": 400,
            "mediafusion_scrapy.pipelines.SeriesStorePipeline": 500,
        },
    }

    async def start(self):
        configured_url = config_manager.get_start_url(self.name)
        if isinstance(configured_url, str) and configured_url.strip():
            rss_url = configured_url.strip()
        else:
            rss_url = "https://eztvx.to/ezrss.xml"
        yield scrapy.Request(rss_url, callback=self.parse)

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def parse(self, response: scrapy.http.Response):
        for node in response.xpath("//*[local-name()='item']"):
            title = (node.xpath("./*[local-name()='title']/text()").get() or "").strip()
            if not title:
                continue

            detail_url = (node.xpath("./*[local-name()='link']/text()").get() or "").strip()
            magnet_link = (node.xpath("./*[local-name()='magnetURI']/text()").get() or "").strip()
            info_hash = (node.xpath("./*[local-name()='infoHash']/text()").get() or "").strip()
            pub_date = (node.xpath("./*[local-name()='pubDate']/text()").get() or "").strip()
            seeders = self._parse_int((node.xpath("./*[local-name()='seeds']/text()").get() or "").strip())
            content_length = self._parse_int(
                (node.xpath("./*[local-name()='contentLength']/text()").get() or "").strip()
            )

            if not magnet_link and info_hash:
                magnet_link = f"magnet:?xt=urn:btih:{info_hash}"
            if not magnet_link:
                continue

            parsed_info_hash, announce_list = parse_magnet(magnet_link)
            item = {
                "torrent_title": title,
                "torrent_name": title,
                "source": self.source,
                "catalog_source": self.catalog_source,
                "catalog": [self.catalog_source],
                "website": detail_url or response.url,
                "webpage_url": detail_url or response.url,
                "magnet_link": magnet_link,
                "scraped_info_hash_key": self.scraped_info_hash_key,
                "expected_sources": [self.source, "Contribution Stream"],
                "created_at": BasePublicIndexerSpider._parse_pub_date(pub_date),
            }

            if seeders is not None:
                item["seeders"] = seeders
            if content_length is not None:
                item["total_size"] = content_length
            if parsed_info_hash:
                item["info_hash"] = parsed_info_hash
                item["announce_list"] = announce_list

            yield item
