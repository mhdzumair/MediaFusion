import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import scrapy

from scrapers.public_indexer_registry import INDEXER_OVERRIDES
from utils.config import config_manager
from utils.parser import convert_size_to_bytes
from utils.torrent import parse_magnet

MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\"'<>\s]*")


def _override_tuple(indexer_key: str, field_name: str) -> tuple[str, ...]:
    override = INDEXER_OVERRIDES.get(indexer_key, {})
    value = override.get(field_name, ())
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _override_first_template(indexer_key: str) -> str | None:
    templates = _override_tuple(indexer_key, "query_url_templates")
    if not templates:
        return None
    return templates[0]


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
    detail_fallback_selectors: tuple[str, ...] = ()
    max_detail_hops: int = 1

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
        item = dict(response.meta["item"])
        current_depth = int(response.meta.get("detail_depth", 0))
        if not magnet_link:
            if current_depth >= self.max_detail_hops:
                return
            fallback_href = self._first(response, self.detail_fallback_selectors)
            if not fallback_href:
                return
            fallback_url = response.urljoin(fallback_href)
            yield scrapy.Request(
                fallback_url,
                callback=self.parse_detail,
                meta={
                    "item": item,
                    "detail_depth": current_depth + 1,
                },
            )
            return

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


class ThePirateBaySpider(BasePublicIndexerSpider):
    name = "thepiratebay"
    source = "ThePirateBay"
    catalog_source = "thepiratebay"
    scraped_info_hash_key = "thepiratebay_scraped_info_hash"
    default_start_urls = ("https://thepiratebay.org/recent",)
    search_url_template = _override_first_template("thepiratebay")
    use_anti_bot_solver = False
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "SCRAPLING_SOLVE_CLOUDFLARE": False,
        "SCRAPLING_FETCHER_MODE": "dynamic",
    }
    row_selectors = _override_tuple("thepiratebay", "row_selectors")
    title_selectors = _override_tuple("thepiratebay", "title_selectors")
    detail_selectors = _override_tuple("thepiratebay", "detail_selectors")
    magnet_selectors = _override_tuple("thepiratebay", "magnet_selectors")
    size_selectors = _override_tuple("thepiratebay", "size_selectors")
    seeder_selectors = _override_tuple("thepiratebay", "seeder_selectors")
    next_page_selectors = ()


class RutorSpider(BasePublicIndexerSpider):
    name = "rutor"
    source = "RuTor"
    catalog_source = "rutor"
    scraped_info_hash_key = "rutor_scraped_info_hash"
    default_start_urls = ("https://rutor.info/top",)
    search_url_template = _override_first_template("rutor")
    use_anti_bot_solver = False
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "SCRAPLING_SOLVE_CLOUDFLARE": False,
        "SCRAPLING_FETCHER_MODE": "dynamic",
    }
    row_selectors = _override_tuple("rutor", "row_selectors")
    title_selectors = _override_tuple("rutor", "title_selectors")
    detail_selectors = _override_tuple("rutor", "detail_selectors")
    magnet_selectors = _override_tuple("rutor", "magnet_selectors")
    size_selectors = _override_tuple("rutor", "size_selectors")
    seeder_selectors = _override_tuple("rutor", "seeder_selectors")
    next_page_selectors = ()


class LimeTorrentsSpider(BasePublicIndexerSpider):
    name = "limetorrents"
    source = "LimeTorrents"
    catalog_source = "limetorrents"
    scraped_info_hash_key = "limetorrents_scraped_info_hash"
    default_start_urls = (
        "https://www.limetorrents.fun/browse-torrents/Movies/",
        "https://www.limetorrents.fun/browse-torrents/TV-shows/",
    )
    search_url_template = _override_first_template("limetorrents")
    use_anti_bot_solver = False
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "SCRAPLING_SOLVE_CLOUDFLARE": False,
        "SCRAPLING_FETCHER_MODE": "dynamic",
    }
    row_selectors = _override_tuple("limetorrents", "row_selectors")
    title_selectors = _override_tuple("limetorrents", "title_selectors")
    detail_selectors = _override_tuple("limetorrents", "detail_selectors")
    magnet_selectors = _override_tuple("limetorrents", "magnet_selectors")
    size_selectors = _override_tuple("limetorrents", "size_selectors")
    seeder_selectors = _override_tuple("limetorrents", "seeder_selectors")
    next_page_selectors = ()


class YTSSpider(BasePublicIndexerSpider):
    name = "yts"
    source = "YTS"
    catalog_source = "yts"
    scraped_info_hash_key = "yts_scraped_info_hash"
    default_start_urls = ("https://yts.mx/browse-movies/0/all/all/0/latest/0/all",)
    search_url_template = _override_first_template("yts")
    use_anti_bot_solver = False
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "SCRAPLING_SOLVE_CLOUDFLARE": False,
        "SCRAPLING_FETCHER_MODE": "stealthy",
    }
    row_selectors = _override_tuple("yts", "row_selectors")
    title_selectors = _override_tuple("yts", "title_selectors")
    detail_selectors = _override_tuple("yts", "detail_selectors")
    magnet_selectors = _override_tuple("yts", "magnet_selectors")
    size_selectors = _override_tuple("yts", "size_selectors")
    seeder_selectors = _override_tuple("yts", "seeder_selectors")
    next_page_selectors = ()


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


class NyaaSpider(BasePublicIndexerSpider):
    name = "nyaa"
    source = "Nyaa"
    catalog_source = "anime_series"
    scraped_info_hash_key = "nyaa_scraped_info_hash"
    default_start_urls = ("https://nyaa.si/?f=0&c=1_2&q=&s=seeders&o=desc",)
    search_url_template = "https://nyaa.si/?f=0&c=1_0&q={query}&p={page}"
    use_anti_bot_solver = False
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "SCRAPLING_SOLVE_CLOUDFLARE": False,
        "SCRAPLING_FETCHER_MODE": "dynamic",
    }
    row_selectors = ("table.torrent-list tbody tr",)
    title_selectors = ("td:nth-child(2) a:last-child::text", "td:nth-child(2) a::text")
    detail_selectors = ("td:nth-child(2) a:last-child::attr(href)", "td:nth-child(2) a::attr(href)")
    magnet_selectors = ("td:nth-child(3) a[href^='magnet:?']::attr(href)", "a[href^='magnet:?']::attr(href)")
    size_selectors = ("td:nth-child(4)::text",)
    seeder_selectors = ("td:nth-child(6)::text",)
    next_page_selectors = ("li.next a::attr(href)",)


class AnimeToshoSpider(BasePublicIndexerSpider):
    name = "animetosho"
    source = "AnimeTosho"
    catalog_source = "anime_series"
    scraped_info_hash_key = "animetosho_scraped_info_hash"
    default_start_urls = ("https://animetosho.org/search?q=&a=0",)
    search_url_template = "https://animetosho.org/search?q={query}&page={page}"
    use_anti_bot_solver = False
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "SCRAPLING_SOLVE_CLOUDFLARE": False,
        "SCRAPLING_FETCHER_MODE": "dynamic",
    }
    row_selectors = ("div.home_list_entry", "article", "li")
    title_selectors = ("a[href*='/view/']::text", "a::text")
    detail_selectors = ("a[href*='/view/']::attr(href)", "a::attr(href)")
    magnet_selectors = ("a[href^='magnet:?']::attr(href)",)
    size_selectors = ("span.size::text", "*::text")
    seeder_selectors = ("span.seeders::text",)
    next_page_selectors = ("a[rel='next']::attr(href)",)


class SubsPleaseSpider(BasePublicIndexerSpider):
    name = "subsplease"
    source = "SubsPlease"
    catalog_source = "anime_series"
    scraped_info_hash_key = "subsplease_scraped_info_hash"
    default_start_urls = ("https://subsplease.org/shows/",)
    search_url_template = "https://subsplease.org/?s={query}"
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "CONCURRENT_REQUESTS": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 4,
        "SCRAPLING_FETCHER_MODE": "stealthy",
        "SCRAPLING_SOLVE_CLOUDFLARE": True,
        "SCRAPLING_WAIT_TIME_MS": 3500,
    }
    row_selectors = ("article", "div.all-shows li", "li")
    title_selectors = ("h2.entry-title a::text", "a::text")
    detail_selectors = ("h2.entry-title a::attr(href)", "a::attr(href)")
    magnet_selectors = ("a[href^='magnet:?']::attr(href)",)
    size_selectors = ("span.size::text", "*::text")
    seeder_selectors = ("span.seeders::text",)
    next_page_selectors = ("a.next.page-numbers::attr(href)",)
    detail_fallback_selectors = (
        "a[href*='nyaa.si']::attr(href)",
        "a[href*='torrent']::attr(href)",
        "a[href*='release']::attr(href)",
    )
    max_detail_hops = 2


class AnimePaheSpider(BasePublicIndexerSpider):
    name = "animepahe"
    source = "AnimePahe"
    catalog_source = "anime_series"
    scraped_info_hash_key = "animepahe_scraped_info_hash"
    default_start_urls = ("https://animepahe.ru/anime",)
    search_url_template = "https://animepahe.ru/anime?m=search&q={query}&page={page}"
    custom_settings = {
        **BasePublicIndexerSpider.custom_settings,
        "CONCURRENT_REQUESTS": 2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 6,
        "SCRAPLING_FETCHER_MODE": "stealthy",
        "SCRAPLING_SOLVE_CLOUDFLARE": True,
        "SCRAPLING_WAIT_TIME_MS": 4000,
        "SCRAPLING_CLOUDFLARE_MAX_ATTEMPTS": 5,
    }
    row_selectors = ("div#results li", "article", "li")
    title_selectors = ("a::attr(title)", "a::text")
    detail_selectors = ("a::attr(href)",)
    magnet_selectors = ("a[href^='magnet:?']::attr(href)",)
    size_selectors = ("span::text",)
    seeder_selectors = ("span::text",)
    next_page_selectors = ("a[rel='next']::attr(href)",)
    detail_fallback_selectors = (
        "a[href*='nyaa.si']::attr(href)",
        "a[href*='animetosho']::attr(href)",
        "a[href*='torrent']::attr(href)",
    )
    max_detail_hops = 3


class BT4GRSSSpider(scrapy.Spider):
    """BT4G RSS spider for movie/series torrents."""

    name = "bt4g"
    source = "BT4G"
    catalog_source = "bt4g"
    scraped_info_hash_key = "bt4g_scraped_info_hash"
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
            rss_url = "https://bt4gprx.com/search?q=&page=rss"
        yield scrapy.Request(rss_url, callback=self.parse)

    @staticmethod
    def _parse_size(description: str | None) -> int | None:
        if not description:
            return None
        parts = [part.strip() for part in description.split("<br>") if part.strip()]
        for part in parts:
            match = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB|MB|KB|B)", part, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                return convert_size_to_bytes(f"{match.group(1)} {match.group(2).upper()}")
            except (ValueError, AttributeError):
                continue
        return None

    def parse(self, response: scrapy.http.Response):
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            self.logger.warning("Invalid BT4G RSS payload: %s", response.url)
            return

        for node in root.findall(".//item"):
            title = (node.findtext("title") or "").strip()
            magnet_link = (node.findtext("link") or "").strip().replace("&amp;", "&")
            if not title or not magnet_link:
                continue

            info_hash, announce_list = parse_magnet(magnet_link)
            if not info_hash:
                continue

            description = node.findtext("description")
            pub_date = node.findtext("pubDate")
            item = {
                "torrent_title": title,
                "torrent_name": title,
                "source": self.source,
                "catalog_source": self.catalog_source,
                "catalog": [self.catalog_source],
                "website": response.url,
                "webpage_url": response.url,
                "magnet_link": magnet_link,
                "scraped_info_hash_key": self.scraped_info_hash_key,
                "expected_sources": [self.source, "Contribution Stream"],
                "info_hash": info_hash,
                "announce_list": announce_list,
                "created_at": BasePublicIndexerSpider._parse_pub_date(pub_date),
            }
            size = self._parse_size(description)
            if size is not None:
                item["total_size"] = size
            yield item


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
