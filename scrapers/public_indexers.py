import re
import time
from collections.abc import AsyncGenerator
from datetime import timedelta
from urllib.parse import quote_plus, unquote, urljoin

import httpx
import PTT
from parsel import Selector

from db.config import settings
from db.schemas import MetadataData, StreamFileData, TorrentStreamData
from mediafusion_scrapy.scrapling_adapter import solve_protected_page
from scrapers.base_scraper import BaseScraper
from scrapers.public_indexer_registry import (
    ScraplingIndexerDefinition,
    get_indexers_for_catalog,
)
from utils.parser import convert_size_to_bytes, is_contain_18_plus_keywords
from utils.runtime_const import PUBLIC_INDEXERS_SEARCH_TTL
from utils.torrent import parse_magnet


class PublicIndexerScraper(BaseScraper):
    """Scrapling-powered live search for configurable public indexers."""

    cache_key_prefix = "public_indexers"
    MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\"'<>\s]*")
    DETAIL_TORRENT_SELECTORS = (
        "a[href$='.torrent']::attr(href)",
        "a[href*='.torrent?']::attr(href)",
        "a[href*='/torrent/download/']::attr(href)",
    )

    MOVIE_SEARCH_QUERY_TEMPLATES = (
        "{title} ({year})",
        "{title} {year}",
        "{title}",
    )
    SERIES_SEARCH_QUERY_TEMPLATES = (
        "{title} S{season:02d}E{episode:02d}",
        "{title} {season}x{episode}",
        "{title} S{season:02d}",
        "{title}",
    )
    ANIME_MOVIE_QUERY_TEMPLATES = (
        "{title}",
        "{title} {year}",
    )
    ANIME_SERIES_QUERY_TEMPLATES = (
        "{title} - {episode:02d}",
        "{title} {episode:02d}",
        "{title}",
    )

    def __init__(self):
        super().__init__(cache_key_prefix=self.cache_key_prefix, logger_name=__name__)

    @BaseScraper.cache(ttl=PUBLIC_INDEXERS_SEARCH_TTL)
    @BaseScraper.rate_limit(calls=2, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentStreamData]:
        if catalog_type not in {"movie", "series"}:
            self.metrics.record_skip("Unsupported catalog type")
            return []

        is_anime = self._is_anime_metadata(metadata)
        indexers = self._select_indexers(catalog_type, is_anime)
        if not indexers:
            self.metrics.record_skip("No indexers configured")
            return []

        queries = self._build_queries(metadata, catalog_type, season, episode, is_anime)
        if not queries:
            self.metrics.record_skip("No search query")
            return []

        processed_info_hashes: set[str] = set()
        results: list[TorrentStreamData] = []
        max_streams = max(1, int(settings.prowlarr_immediate_max_process))
        deadline = time.monotonic() + max(5, int(settings.prowlarr_immediate_max_process_time))

        for query in queries:
            if len(results) >= max_streams:
                self.metrics.record_skip("Max process limit")
                break
            if time.monotonic() >= deadline:
                self.metrics.record_skip("Max process time")
                break

            for indexer in indexers:
                if len(results) >= max_streams:
                    break
                if time.monotonic() >= deadline:
                    break

                async for stream in self._search_indexer(
                    indexer=indexer,
                    query=query,
                    metadata=metadata,
                    catalog_type=catalog_type,
                    season=season,
                    episode=episode,
                    is_anime=is_anime,
                    processed_info_hashes=processed_info_hashes,
                ):
                    results.append(stream)
                    if len(results) >= max_streams:
                        self.metrics.record_skip("Max process limit")
                        break
                    if time.monotonic() >= deadline:
                        self.metrics.record_skip("Max process time")
                        break

        return results

    @staticmethod
    def _is_anime_metadata(metadata: MetadataData) -> bool:
        if metadata.get_mal_id() or metadata.get_kitsu_id() or metadata.get_anilist_id():
            return True
        genres = {genre.strip().lower() for genre in metadata.genres if isinstance(genre, str)}
        if "anime" in genres or "animation" in genres:
            return True
        catalogs = {catalog.strip().lower() for catalog in metadata.catalogs if isinstance(catalog, str)}
        return any("anime" in catalog for catalog in catalogs)

    def _select_indexers(self, catalog_type: str, is_anime: bool) -> list[ScraplingIndexerDefinition]:
        available = get_indexers_for_catalog(catalog_type=catalog_type, is_anime=is_anime)
        available_by_key = {definition.key: definition for definition in available}
        all_available_ids = tuple(available_by_key.keys())
        global_sites = (settings.public_indexers_live_search_sites or "").strip()

        if global_sites:
            raw_value = global_sites
            default_ids = all_available_ids
        elif is_anime:
            raw_value = settings.public_indexers_anime_live_search_sites
            default_ids = all_available_ids
        elif catalog_type == "movie":
            raw_value = settings.public_indexers_movie_live_search_sites
            default_ids = all_available_ids
        else:
            raw_value = settings.public_indexers_series_live_search_sites
            default_ids = all_available_ids

        configured_ids = self._parse_indexer_ids(raw_value, default_ids, all_available_ids)
        indexers: list[ScraplingIndexerDefinition] = []
        for indexer_id in configured_ids:
            definition = available_by_key.get(indexer_id)
            if not definition:
                self.logger.warning("Unknown public indexer '%s' in live search config.", indexer_id)
                continue
            indexers.append(definition)
        return indexers

    @staticmethod
    def _parse_indexer_ids(
        raw_value: str | None,
        default_ids: tuple[str, ...],
        all_available_ids: tuple[str, ...],
    ) -> list[str]:
        value = (raw_value or "").strip()
        if not value:
            return list(default_ids)
        if value.lower() in {"all", "*"}:
            return list(all_available_ids)
        ids = [part.strip().lower() for part in value.split(",") if part.strip()]
        return ids if ids else list(default_ids)

    def _build_queries(
        self,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
    ) -> list[str]:
        if is_anime and catalog_type == "movie":
            templates = self.ANIME_MOVIE_QUERY_TEMPLATES
        elif is_anime and catalog_type == "series":
            templates = self.ANIME_SERIES_QUERY_TEMPLATES
        elif catalog_type == "movie":
            templates = self.MOVIE_SEARCH_QUERY_TEMPLATES
        else:
            templates = self.SERIES_SEARCH_QUERY_TEMPLATES

        values: list[str] = []
        template_vars = {
            "title": metadata.title or "",
            "year": metadata.year or "",
            "season": season or 1,
            "episode": episode or 1,
        }
        for template in templates:
            rendered = template.format(**template_vars)
            normalized = re.sub(r"\s+", " ", rendered).strip(" -")
            if normalized:
                values.append(normalized)

        if settings.scrape_with_aka_titles:
            values.extend([title.strip() for title in metadata.aka_titles if isinstance(title, str) and title.strip()])

        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = re.sub(r"\s+", " ", value).strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(value.strip())
        return deduped

    async def _search_indexer(
        self,
        *,
        indexer: ScraplingIndexerDefinition,
        query: str,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_info_hashes: set[str],
    ) -> AsyncGenerator[TorrentStreamData, None]:
        encoded_query = quote_plus(query)
        for page in range(1, indexer.search_pages_per_query + 1):
            matched_template = False
            for template in indexer.query_url_templates:
                search_url = template.format(query=encoded_query, page=page)
                solved = await self._fetch_page(indexer, search_url)
                if not solved:
                    continue

                selector = Selector(text=solved["html"])
                rows = self._extract_rows(selector, indexer.row_selectors)
                if not rows:
                    continue
                matched_template = True
                self.metrics.record_found_items(len(rows))

                for row in rows:
                    stream = await self._process_row(
                        indexer=indexer,
                        row=row,
                        base_url=search_url,
                        metadata=metadata,
                        catalog_type=catalog_type,
                        season=season,
                        episode=episode,
                        is_anime=is_anime,
                        processed_info_hashes=processed_info_hashes,
                    )
                    if stream:
                        yield stream
                break
            if not matched_template:
                self.metrics.record_skip(f"No rows for {indexer.key}")

    async def _process_row(
        self,
        *,
        indexer: ScraplingIndexerDefinition,
        row,
        base_url: str,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_info_hashes: set[str],
    ) -> TorrentStreamData | None:
        title = self._first(row, indexer.title_selectors)
        detail_href = self._first(row, indexer.detail_selectors)
        if not title and detail_href:
            title = self._title_from_detail_href(detail_href)
        if not title:
            return None
        if is_contain_18_plus_keywords(title):
            self.metrics.record_skip("Adult content")
            return None

        parsed_data = PTT.parse_title(title, True)
        is_title_valid = self.validate_title_and_year(parsed_data, metadata, catalog_type, title)
        if not is_title_valid and detail_href:
            fallback_title = self._title_from_detail_href(detail_href)
            if fallback_title and fallback_title != title:
                fallback_parsed_data = PTT.parse_title(fallback_title, True)
                is_fallback_valid = self.validate_title_and_year(
                    fallback_parsed_data,
                    metadata,
                    catalog_type,
                    fallback_title,
                )

                if (
                    not is_fallback_valid
                    and indexer.key == "rutor"
                    and catalog_type == "movie"
                    and self._detail_slug_starts_with_title(detail_href, metadata.title)
                ):
                    heuristic_title = f"{metadata.title} {metadata.year}" if metadata.year else metadata.title
                    heuristic_parsed_data = PTT.parse_title(heuristic_title, True)
                    is_heuristic_valid = self.validate_title_and_year(
                        heuristic_parsed_data,
                        metadata,
                        catalog_type,
                        heuristic_title,
                    )
                    if is_heuristic_valid:
                        fallback_title = heuristic_title
                        fallback_parsed_data = heuristic_parsed_data
                        is_fallback_valid = True

                if is_fallback_valid:
                    title = fallback_title
                    parsed_data = fallback_parsed_data
                    is_title_valid = True

        if not is_title_valid:
            return None
        if catalog_type == "series" and not self._validate_series(
            parsed_data, season, episode, strict_season=not is_anime
        ):
            return None

        detail_url = urljoin(base_url, detail_href) if detail_href else ""
        if detail_url and len(detail_url) > indexer.max_detail_url_length:
            self.metrics.record_skip("Detail URL too long")
            return None

        magnet_link = self._first(row, indexer.magnet_selectors)
        direct_torrent_url = detail_url if self._is_torrent_file_url(detail_url) else None
        if not magnet_link and detail_url and not direct_torrent_url:
            magnet_link, detail_torrent_url = await self._extract_links_from_detail(indexer, detail_url)
            if detail_torrent_url and not direct_torrent_url:
                direct_torrent_url = detail_torrent_url

        info_hash = ""
        announce_list: list[str] = []
        if magnet_link:
            magnet_link = magnet_link.replace("&amp;", "&")
            info_hash, announce_list = parse_magnet(magnet_link)

        if not info_hash and direct_torrent_url:
            torrent_data, _ = await self.get_torrent_data(direct_torrent_url, parsed_data)
            if torrent_data:
                info_hash = str(torrent_data.get("info_hash") or "").lower()
                announce_list = torrent_data.get("announce_list", [])

        if not info_hash:
            self.metrics.record_skip("No magnet or torrent")
            return None

        info_hash = info_hash.lower()
        if info_hash in processed_info_hashes:
            self.metrics.record_skip("Duplicate info_hash")
            return None
        processed_info_hashes.add(info_hash)

        stream = TorrentStreamData(
            info_hash=info_hash,
            meta_id=metadata.get_canonical_id(),
            name=title,
            size=self._extract_size_bytes(row, indexer.size_selectors) or 0,
            source=indexer.source_name,
            seeders=self._parse_int(self._first(row, indexer.seeder_selectors)) or 0,
            announce_list=announce_list,
            files=self._build_files(title, parsed_data, catalog_type, season, episode),
            resolution=parsed_data.get("resolution"),
            codec=parsed_data.get("codec"),
            quality=parsed_data.get("quality"),
            bit_depth=parsed_data.get("bit_depth"),
            release_group=parsed_data.get("group"),
            audio_formats=parsed_data.get("audio", []) if isinstance(parsed_data.get("audio"), list) else [],
            channels=parsed_data.get("channels", []) if isinstance(parsed_data.get("channels"), list) else [],
            hdr_formats=parsed_data.get("hdr", []) if isinstance(parsed_data.get("hdr"), list) else [],
            languages=parsed_data.get("languages", []),
            is_remastered=parsed_data.get("remastered", False),
            is_upscaled=parsed_data.get("upscaled", False),
            is_proper=parsed_data.get("proper", False),
            is_repack=parsed_data.get("repack", False),
            is_extended=parsed_data.get("extended", False),
            is_complete=parsed_data.get("complete", False),
            is_dubbed=parsed_data.get("dubbed", False),
            is_subbed=parsed_data.get("subbed", False),
        )

        self.metrics.record_processed_item()
        self.metrics.record_quality(stream.quality)
        self.metrics.record_source(stream.source)
        return stream

    async def _extract_links_from_detail(
        self,
        indexer: ScraplingIndexerDefinition,
        detail_url: str,
    ) -> tuple[str | None, str | None]:
        solved = await self._fetch_page(indexer, detail_url)
        if not solved:
            return None, None
        selector = Selector(text=solved["html"])
        magnet = self._first(selector, indexer.magnet_selectors)
        if magnet:
            return magnet, None
        match = self.MAGNET_RE.search(solved["html"])
        if match:
            return match.group(0), None

        torrent_url = self._first(selector, self.DETAIL_TORRENT_SELECTORS)
        if torrent_url:
            return None, urljoin(detail_url, torrent_url)
        return None, None

    async def _fetch_page(self, indexer: ScraplingIndexerDefinition, url: str) -> dict | None:
        response: dict | None = None
        if indexer.http_fallback:
            http_first = await self._fetch_with_http(url)
            if self._is_response_usable(http_first):
                return http_first

        try:
            response = await self._fetch_with_scrapling(
                url=url,
                fetcher_mode=indexer.fetcher_mode or settings.scrapling_fetcher_mode,
                solve_cloudflare=False,
            )
            if indexer.solve_cloudflare and settings.scrapling_solve_cloudflare:
                html = str(response.get("html", "") or "")
                status = int(response.get("status", 0) or 0)
                if status in {403, 429, 503} or self._is_cloudflare_challenge_html(html):
                    response = await self._fetch_with_scrapling(
                        url=url,
                        fetcher_mode=indexer.fetcher_mode or settings.scrapling_fetcher_mode,
                        solve_cloudflare=True,
                    )
        except Exception as exc:
            self.logger.debug("Failed to fetch %s via %s: %s", url, indexer.key, exc)

        if self._is_response_usable(response):
            return response

        if indexer.http_fallback:
            fallback = await self._fetch_with_http(url)
            if self._is_response_usable(fallback):
                return fallback

        if response is None:
            self.metrics.record_error("request_error")
        elif not response.get("html"):
            self.metrics.record_skip("Empty response")
        else:
            self.metrics.record_error("http_error")
        return None

    async def _fetch_with_scrapling(
        self,
        *,
        url: str,
        fetcher_mode: str,
        solve_cloudflare: bool,
    ) -> dict:
        return await solve_protected_page(
            url,
            headless=settings.scrapling_headless,
            disable_resources=settings.scrapling_disable_resources,
            network_idle=settings.scrapling_network_idle,
            wait_time_ms=settings.scrapling_wait_time_ms,
            timeout_ms=settings.scrapling_timeout_ms,
            google_search_referer=settings.scrapling_google_search_referer,
            proxy_url=settings.scrapling_proxy_url or settings.requests_proxy_url,
            fetcher_mode=fetcher_mode,
            solve_cloudflare=solve_cloudflare,
            real_chrome=settings.scrapling_real_chrome,
        )

    @staticmethod
    def _is_response_usable(response: dict | None) -> bool:
        if not response:
            return False
        html = str(response.get("html", "") or "")
        if not html:
            return False
        status = int(response.get("status", 0) or 0)
        return not status or status < 400

    async def _fetch_with_http(self, url: str) -> dict | None:
        timeout_seconds = max(5.0, min(30.0, settings.scrapling_timeout_ms / 1000))
        transport = httpx.AsyncHTTPTransport(retries=1)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout_seconds,
                proxy=settings.requests_proxy_url or None,
                transport=transport,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                response = await client.get(url)
        except Exception as exc:
            self.logger.debug("HTTP fallback failed for %s: %s", url, exc)
            return None
        return {
            "html": response.text or "",
            "status": response.status_code,
            "url": str(response.url),
        }

    @staticmethod
    def _is_cloudflare_challenge_html(html: str) -> bool:
        lowered = html.lower()
        return (
            "cf-chl-" in lowered
            or "<title>just a moment" in lowered
            or "<title>attention required!" in lowered
            or "cf-turnstile" in lowered
        )

    @staticmethod
    def _extract_rows(selector: Selector, row_selectors: tuple[str, ...]):
        for css in row_selectors:
            rows = selector.css(css)
            if rows:
                return rows
        return []

    @staticmethod
    def _first(scope, selectors: tuple[str, ...]) -> str | None:
        for css in selectors:
            values = scope.css(css).getall()
            if not values:
                continue
            if "::text" in css:
                value = " ".join(values)
            else:
                value = values[0]
            cleaned = re.sub(r"\s+", " ", value).strip()
            if cleaned:
                return cleaned
        return None

    def _extract_size_bytes(self, row, size_selectors: tuple[str, ...]) -> int | None:
        for css in size_selectors:
            text_parts = [self._normalize_text(chunk) for chunk in row.css(css).getall()]
            text = " ".join([chunk for chunk in text_parts if chunk])
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

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        if not value:
            return ""
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _is_torrent_file_url(url: str | None) -> bool:
        if not url:
            return False
        lowered = url.lower()
        return ".torrent" in lowered

    @staticmethod
    def _title_from_detail_href(detail_href: str) -> str | None:
        href = (detail_href or "").strip()
        if not href:
            return None
        parts = [part for part in href.split("/") if part]
        if not parts:
            return None
        slug = parts[-1]
        slug = re.sub(r"\.[a-z0-9]{2,5}$", "", slug, flags=re.IGNORECASE)
        text = unquote(slug)
        text = text.replace("+", " ")
        text = re.sub(r"[-_]+", " ", text)
        normalized = re.sub(r"\s+", " ", text).strip()
        return normalized or None

    @staticmethod
    def _detail_slug_starts_with_title(detail_href: str, metadata_title: str | None) -> bool:
        slug_title = PublicIndexerScraper._title_from_detail_href(detail_href)
        if not slug_title or not metadata_title:
            return False
        normalized_slug = re.sub(r"[^a-z0-9]+", " ", slug_title.lower()).strip()
        normalized_title = re.sub(r"[^a-z0-9]+", " ", metadata_title.lower()).strip()
        return bool(normalized_title) and normalized_slug.startswith(normalized_title)

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
    def _validate_series(
        parsed_data: dict,
        season: int | None,
        episode: int | None,
        *,
        strict_season: bool,
    ) -> bool:
        seasons = parsed_data.get("seasons") or []
        episodes = parsed_data.get("episodes") or []
        if season is not None and seasons and season not in seasons:
            return False
        if episode is not None and episodes and episode not in episodes:
            return False
        if strict_season and season is not None and not seasons:
            return False
        return True

    @staticmethod
    def _build_files(
        title: str,
        parsed_data: dict,
        catalog_type: str,
        season: int | None,
        episode: int | None,
    ) -> list[StreamFileData]:
        if catalog_type == "movie":
            return [StreamFileData(file_index=0, filename=title, size=0, file_type="video")]

        parsed_seasons = parsed_data.get("seasons") or []
        parsed_episodes = parsed_data.get("episodes") or []
        season_number = parsed_seasons[0] if parsed_seasons else (season or 1)
        episode_number = parsed_episodes[0] if parsed_episodes else (episode or 1)
        return [
            StreamFileData(
                file_index=0,
                filename=title,
                size=0,
                file_type="video",
                season_number=season_number,
                episode_number=episode_number,
            )
        ]
