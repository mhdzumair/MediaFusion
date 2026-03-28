"""Scrapling-capable public Usenet indexers (no Newznab API)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
import html as html_module
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlencode

import PTT
from parsel import Selector

from db.config import settings
from db.schemas import MetadataData, UserData, UsenetStreamData
from scrapers.public_indexer_registry import ScraplingIndexerDefinition
from scrapers.public_indexers import PublicIndexerScraper
from scrapers.public_usenet_indexer_registry import (
    PublicUsenetIndexerDefinition,
    get_usenet_indexers_for_catalog,
)
from scrapers.source_health import SourceHealthSnapshot, get_source_health, record_source_outcome
from utils.parser import is_contain_18_plus_keywords
from utils.runtime_const import PUBLIC_USENET_INDEXERS_SEARCH_TTL
from utils.url_safety import sanitize_nzb_url

logger = logging.getLogger(__name__)

_BINSEARCH_ROW = "table.result-table tr"
_BINSEARCH_TITLE = (
    'td a[href^="/details/"]::text',
    'a[href^="/details/"]::text',
)
_BINSEARCH_SIZE = (
    "span.rounded-lg::text",
    "td span::text",
)
_BINSEARCH_GROUP = ('a[href^="/search?group="]::text',)


class PublicUsenetIndexerScraper(PublicIndexerScraper):
    """HTML public Usenet search (Binsearch and future handlers)."""

    cache_key_prefix = "public_usenet_indexers"

    async def _scrape_and_parse(
        self,
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ):
        """Torrent pipeline does not use this scraper."""
        return []

    @PublicIndexerScraper.cache(ttl=PUBLIC_USENET_INDEXERS_SEARCH_TTL)
    @PublicIndexerScraper.rate_limit(calls=2, period=timedelta(seconds=1))
    async def _scrape_usenet_cached(
        self,
        user_data: UserData | None,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[UsenetStreamData]:
        if catalog_type not in {"movie", "series"}:
            self.metrics.record_skip("Unsupported catalog type")
            return []

        is_anime = self._is_anime_metadata(metadata)
        if is_anime and user_data and getattr(user_data, "include_anime", True) is False:
            self.metrics.record_skip("Anime providers disabled in profile")
            return []

        indexers = await self._select_usenet_indexers(user_data, catalog_type, is_anime)
        if not indexers:
            self.metrics.record_skip("No public Usenet indexers configured")
            return []

        queries = self._build_queries(metadata, catalog_type, season, episode, is_anime)
        if not queries:
            self.metrics.record_skip("No search query")
            return []

        processed_nzb_keys: set[str] = set()
        results: list[UsenetStreamData] = []
        max_streams = max(1, int(settings.prowlarr_immediate_max_process))
        deadline = time.monotonic() + max(5, int(settings.prowlarr_immediate_max_process_time))
        parallelism = max(1, int(settings.public_indexers_live_search_parallelism))
        processed_lock = asyncio.Lock() if parallelism > 1 else None

        for query in queries:
            if len(results) >= max_streams:
                self.metrics.record_skip("Max process limit")
                break
            if time.monotonic() >= deadline:
                self.metrics.record_skip("Max process time")
                break

            remaining = max_streams - len(results)
            batch = await self._collect_usenet_streams_for_query(
                indexers=indexers,
                query=query,
                metadata=metadata,
                catalog_type=catalog_type,
                season=season,
                episode=episode,
                is_anime=is_anime,
                processed_nzb_keys=processed_nzb_keys,
                processed_nzb_keys_lock=processed_lock,
                max_streams=remaining,
                deadline=deadline,
                parallelism=parallelism,
            )
            results.extend(batch)

        return results

    async def scrape_usenet_and_parse(
        self,
        user_data: UserData | None,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[UsenetStreamData]:
        self.metrics.start()
        self.metrics.meta_data = metadata
        self.metrics.season = season
        self.metrics.episode = episode
        try:
            out = await self._scrape_usenet_cached(user_data, metadata, catalog_type, season, episode)
            return out if isinstance(out, list) else []
        except Exception as exc:
            self.metrics.record_error("unexpected_error")
            logger.exception("Public Usenet indexer scrape failed: %s", exc)
            return []
        finally:
            self.metrics.stop()
            self.metrics.log_summary(self.logger)
            await self.metrics.save_to_redis()

    def _fetch_adapter(self, indexer: PublicUsenetIndexerDefinition) -> ScraplingIndexerDefinition:
        """Shape compatible with PublicIndexerScraper._fetch_page."""
        return ScraplingIndexerDefinition(
            key=indexer.key,
            source_name=indexer.source_name,
            query_url_templates=(),
            row_selectors=(),
            title_selectors=(),
            detail_selectors=(),
            magnet_selectors=(),
            size_selectors=(),
            seeder_selectors=(),
            supports_movie=indexer.supports_movie,
            supports_series=indexer.supports_series,
            supports_anime=indexer.supports_anime,
            search_pages_per_query=1,
            solve_cloudflare=indexer.solve_cloudflare,
            fetcher_mode=indexer.fetcher_mode,
        )

    async def _select_usenet_indexers(
        self,
        user_data: UserData | None,
        catalog_type: str,
        is_anime: bool,
    ) -> list[PublicUsenetIndexerDefinition]:
        available = get_usenet_indexers_for_catalog(catalog_type=catalog_type, is_anime=is_anime)
        available_by_key = {definition.key: definition for definition in available}
        all_available_ids = tuple(available_by_key.keys())
        global_sites = (settings.public_usenet_indexers_live_search_sites or "").strip()

        if global_sites:
            raw_value = global_sites
            default_ids = all_available_ids
        elif is_anime:
            raw_value = settings.public_usenet_indexers_anime_live_search_sites
            default_ids = all_available_ids
        elif catalog_type == "movie":
            raw_value = settings.public_usenet_indexers_movie_live_search_sites
            default_ids = all_available_ids
        else:
            raw_value = settings.public_usenet_indexers_series_live_search_sites
            default_ids = all_available_ids

        configured_ids = self._parse_indexer_ids(raw_value, default_ids, all_available_ids)
        indexers: list[PublicUsenetIndexerDefinition] = []
        for indexer_id in configured_ids:
            definition = available_by_key.get(indexer_id)
            if not definition:
                self.logger.warning("Unknown public Usenet indexer %r in config.", indexer_id)
                continue
            indexers.append(definition)

        if not settings.public_indexers_live_search_enable_cloudflare_solver:
            indexers = [definition for definition in indexers if not definition.solve_cloudflare]

        if not settings.public_indexers_source_health_gates_enabled:
            return indexers

        health_bucket = self._health_bucket(catalog_type=catalog_type, is_anime=is_anime)
        health_by_key: dict[str, SourceHealthSnapshot] = {
            definition.key: await get_source_health(definition.key, health_bucket=health_bucket)
            for definition in indexers
        }

        allowed_keys: set[str] = set()
        blocked: list[PublicUsenetIndexerDefinition] = []
        for definition in indexers:
            snapshot = health_by_key[definition.key]
            if self._is_bootstrap_demoted(snapshot):
                blocked.append(definition)
                self.metrics.record_skip(f"public usenet indexer bootstrap demote: {definition.key}")
                continue
            allowed = self._is_snapshot_within_budget(
                snapshot,
                min_samples=settings.public_indexers_source_health_min_samples,
                min_success_rate=settings.public_indexers_source_min_success_rate,
                max_timeout_rate=settings.public_indexers_source_max_timeout_rate,
            )
            if allowed:
                allowed_keys.add(definition.key)
            else:
                recovery = max(0, settings.public_indexers_source_health_recovery_success_streak)
                if recovery > 0 and snapshot.consecutive_success >= recovery:
                    allowed_keys.add(definition.key)
                    self.metrics.record_skip(f"public usenet indexer recovery admit: {definition.key}")
                    continue
                blocked.append(definition)
                self.metrics.record_skip(f"public usenet indexer failure budget gate: {definition.key}")

        probation_keys: set[str] = set()
        if (
            blocked
            and settings.public_indexers_source_health_probation_enabled
            and settings.public_indexers_source_health_probation_ratio > 0
            and settings.public_indexers_source_health_probation_max_sources_per_query > 0
        ):
            max_probation = settings.public_indexers_source_health_probation_max_sources_per_query
            probation_added = 0
            for definition in blocked:
                if probation_added >= max_probation:
                    break
                if random.random() <= settings.public_indexers_source_health_probation_ratio:
                    probation_keys.add(definition.key)
                    probation_added += 1

        selected: list[PublicUsenetIndexerDefinition] = []
        for definition in indexers:
            if definition.key in allowed_keys:
                selected.append(definition)
        for definition in indexers:
            if definition.key in probation_keys and definition.key not in allowed_keys:
                selected.append(definition)
        return selected

    async def _collect_single_usenet_indexer(
        self,
        *,
        indexer: PublicUsenetIndexerDefinition,
        query: str,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_nzb_keys: set[str],
        processed_nzb_keys_lock: asyncio.Lock | None,
        max_streams: int,
        deadline: float,
    ) -> list[UsenetStreamData]:
        collected: list[UsenetStreamData] = []
        if max_streams <= 0 or time.monotonic() >= deadline:
            return collected

        async for stream in self._search_usenet_indexer(
            indexer=indexer,
            query=query,
            metadata=metadata,
            catalog_type=catalog_type,
            season=season,
            episode=episode,
            is_anime=is_anime,
            processed_nzb_keys=processed_nzb_keys,
            processed_nzb_keys_lock=processed_nzb_keys_lock,
        ):
            if time.monotonic() >= deadline:
                break
            collected.append(stream)
            if len(collected) >= max_streams:
                break
        return collected

    async def _collect_usenet_streams_for_query(
        self,
        *,
        indexers: list[PublicUsenetIndexerDefinition],
        query: str,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_nzb_keys: set[str],
        processed_nzb_keys_lock: asyncio.Lock | None,
        max_streams: int,
        deadline: float,
        parallelism: int,
    ) -> list[UsenetStreamData]:
        if max_streams <= 0:
            return []
        if time.monotonic() >= deadline:
            self.metrics.record_skip("Max process time")
            return []

        if parallelism <= 1 or len(indexers) <= 1:
            collected: list[UsenetStreamData] = []
            for indexer in indexers:
                if len(collected) >= max_streams:
                    self.metrics.record_skip("Max process limit")
                    break
                if time.monotonic() >= deadline:
                    self.metrics.record_skip("Max process time")
                    break
                batch = await self._collect_single_usenet_indexer(
                    indexer=indexer,
                    query=query,
                    metadata=metadata,
                    catalog_type=catalog_type,
                    season=season,
                    episode=episode,
                    is_anime=is_anime,
                    processed_nzb_keys=processed_nzb_keys,
                    processed_nzb_keys_lock=processed_nzb_keys_lock,
                    max_streams=max_streams - len(collected),
                    deadline=deadline,
                )
                collected.extend(batch)
            return collected[:max_streams]

        collected = []
        collected_lock = asyncio.Lock()
        indexer_queue: asyncio.Queue[PublicUsenetIndexerDefinition] = asyncio.Queue()
        for indexer in indexers:
            indexer_queue.put_nowait(indexer)
        stop_requested = asyncio.Event()

        async def _worker() -> None:
            while not stop_requested.is_set():
                if time.monotonic() >= deadline:
                    stop_requested.set()
                    return
                try:
                    indexer = indexer_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                batch = await self._collect_single_usenet_indexer(
                    indexer=indexer,
                    query=query,
                    metadata=metadata,
                    catalog_type=catalog_type,
                    season=season,
                    episode=episode,
                    is_anime=is_anime,
                    processed_nzb_keys=processed_nzb_keys,
                    processed_nzb_keys_lock=processed_nzb_keys_lock,
                    max_streams=max_streams,
                    deadline=deadline,
                )
                if not batch:
                    continue
                async with collected_lock:
                    for stream in batch:
                        if len(collected) >= max_streams:
                            stop_requested.set()
                            break
                        collected.append(stream)
                    if len(collected) >= max_streams:
                        stop_requested.set()

        workers = [asyncio.create_task(_worker()) for _ in range(min(parallelism, len(indexers)))]
        try:
            timeout_seconds = max(0.0, deadline - time.monotonic())
            if timeout_seconds <= 0:
                self.metrics.record_skip("Max process time")
                return []
            await asyncio.wait_for(asyncio.gather(*workers), timeout=timeout_seconds)
        except TimeoutError:
            self.metrics.record_skip("Max process time")
        finally:
            stop_requested.set()
            for task in workers:
                if not task.done():
                    task.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
        return collected[:max_streams]

    async def _search_usenet_indexer(
        self,
        *,
        indexer: PublicUsenetIndexerDefinition,
        query: str,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_nzb_keys: set[str],
        processed_nzb_keys_lock: asyncio.Lock | None,
    ) -> AsyncGenerator[UsenetStreamData, None]:
        if indexer.handler == "binsearch":
            async for stream in self._search_binsearch(
                indexer=indexer,
                query=query,
                metadata=metadata,
                catalog_type=catalog_type,
                season=season,
                episode=episode,
                is_anime=is_anime,
                processed_nzb_keys=processed_nzb_keys,
                processed_nzb_keys_lock=processed_nzb_keys_lock,
            ):
                yield stream
        elif indexer.handler == "nzbindex":
            async for stream in self._search_nzbindex(
                indexer=indexer,
                query=query,
                metadata=metadata,
                catalog_type=catalog_type,
                season=season,
                episode=episode,
                is_anime=is_anime,
                processed_nzb_keys=processed_nzb_keys,
                processed_nzb_keys_lock=processed_nzb_keys_lock,
            ):
                yield stream

    async def _fetch_json_from_http(self, url: str) -> tuple[dict | None, bool]:
        payload, timed_out = await self._fetch_with_http(url)
        if not payload or not payload.get("html"):
            return None, timed_out
        raw = str(payload["html"]).strip()
        if not raw:
            return None, timed_out
        try:
            return json.loads(raw), timed_out
        except json.JSONDecodeError:
            self.metrics.record_skip("Invalid JSON response")
            return None, timed_out

    async def _search_nzbindex(
        self,
        *,
        indexer: PublicUsenetIndexerDefinition,
        query: str,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_nzb_keys: set[str],
        processed_nzb_keys_lock: asyncio.Lock | None,
    ) -> AsyncGenerator[UsenetStreamData, None]:
        stream_hits = 0
        request_succeeded = False
        timed_out = False
        health_bucket = self._health_bucket(catalog_type=catalog_type, is_anime=is_anime)
        origin = (indexer.site_origin or "").rstrip("/")
        if not origin:
            self.logger.warning("NZBIndex indexer missing site_origin")
            return

        for page_zero in range(max(1, indexer.search_pages_per_query)):
            api_url = f"{origin}/api/search?{urlencode({'q': query, 'page': str(page_zero)})}"
            data, request_timed_out = await self._fetch_json_from_http(api_url)
            timed_out = timed_out or request_timed_out
            if not data:
                self.metrics.record_skip(f"No JSON for {indexer.key} page {page_zero}")
                continue

            request_succeeded = True
            inner = data.get("data") if isinstance(data, dict) else None
            if not isinstance(inner, dict):
                self.metrics.record_skip("NZBIndex unexpected payload shape")
                break

            content = inner.get("content")
            if not isinstance(content, list) or not content:
                break

            self.metrics.record_found_items(len(content))
            for item_index, item in enumerate(content):
                if item_index >= settings.public_indexers_max_rows_per_page:
                    self.metrics.record_skip(
                        f"Row scan limit reached for {indexer.key}:{settings.public_indexers_max_rows_per_page}"
                    )
                    break
                if not isinstance(item, dict):
                    continue
                stream = await self._nzbindex_item_to_stream(
                    item=item,
                    origin=origin,
                    indexer=indexer,
                    metadata=metadata,
                    catalog_type=catalog_type,
                    season=season,
                    episode=episode,
                    is_anime=is_anime,
                    processed_nzb_keys=processed_nzb_keys,
                    processed_nzb_keys_lock=processed_nzb_keys_lock,
                )
                if stream:
                    stream_hits += 1
                    yield stream

        await record_source_outcome(
            indexer.key,
            success=stream_hits > 0 or request_succeeded,
            timed_out=timed_out,
            challenge_solved=False,
            health_bucket=health_bucket,
        )

    async def _nzbindex_item_to_stream(
        self,
        *,
        item: dict,
        origin: str,
        indexer: PublicUsenetIndexerDefinition,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_nzb_keys: set[str],
        processed_nzb_keys_lock: asyncio.Lock | None,
    ) -> UsenetStreamData | None:
        release_id = str(item.get("id") or "").strip()
        if not release_id:
            return None

        title = self._unescape_title(str(item.get("name") or "").strip())
        if not title:
            return None
        if is_contain_18_plus_keywords(title):
            self.metrics.record_skip("Adult content")
            return None

        parsed_data = PTT.parse_title(title, True)
        if not self.validate_title_and_year(parsed_data, metadata, catalog_type, title):
            return None
        if catalog_type == "series" and not self._validate_series(
            parsed_data, season, episode, strict_season=not is_anime
        ):
            return None

        nzb_guid = hashlib.sha256(f"nzbindex:{release_id}".encode()).hexdigest()[:40]
        if not await self._mark_nzb_guid_if_new(
            nzb_guid,
            processed_nzb_keys=processed_nzb_keys,
            processed_nzb_keys_lock=processed_nzb_keys_lock,
        ):
            self.metrics.record_skip("Duplicate nzb_guid")
            return None

        nzb_url = sanitize_nzb_url(f"{origin}/api/download/{release_id}.nzb")

        size = 0
        try:
            raw_size = item.get("size")
            if raw_size is not None:
                size = int(raw_size)
        except (TypeError, ValueError):
            size = 0

        groups = item.get("groups")
        group_name = None
        if isinstance(groups, list) and groups:
            group_name = ", ".join(str(g) for g in groups[:3] if g)
        elif isinstance(groups, str) and groups:
            group_name = groups

        poster = str(item.get("poster") or "").strip() or None
        files_count = item.get("fileCount")
        try:
            files_count_int = int(files_count) if files_count is not None else None
        except (TypeError, ValueError):
            files_count_int = None

        posted_at: datetime | None = None
        try:
            posted_raw = item.get("posted")
            if posted_raw is not None:
                ts = int(posted_raw)
                if ts > 0:
                    posted_at = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            posted_at = None

        files = self._build_files(title, parsed_data, catalog_type, season, episode)
        for f in files:
            if f.size in (None, 0) and size > 0:
                f.size = size

        stream = UsenetStreamData(
            nzb_guid=nzb_guid,
            nzb_url=nzb_url,
            name=title,
            size=size,
            indexer=indexer.source_name,
            source=indexer.source_name,
            group_name=group_name,
            poster=poster,
            files_count=files_count_int,
            posted_at=posted_at,
            is_passworded=False,
            meta_id=metadata.get_canonical_id(),
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
            files=files,
        )
        self.metrics.record_processed_item()
        self.metrics.record_quality(stream.quality)
        self.metrics.record_source(stream.source)
        return stream

    async def _search_binsearch(
        self,
        *,
        indexer: PublicUsenetIndexerDefinition,
        query: str,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_nzb_keys: set[str],
        processed_nzb_keys_lock: asyncio.Lock | None,
    ) -> AsyncGenerator[UsenetStreamData, None]:
        stream_hits = 0
        request_succeeded = False
        challenge_solved = False
        timed_out = False
        health_bucket = self._health_bucket(catalog_type=catalog_type, is_anime=is_anime)
        encoded_query = quote_plus(query)
        adapter = self._fetch_adapter(indexer)

        for page in range(1, indexer.search_pages_per_query + 1):
            matched_template = False
            for template in indexer.query_url_templates:
                search_url = template.format(query=encoded_query, page=page)
                solved, request_timed_out = await self._fetch_page(adapter, search_url)
                timed_out = timed_out or request_timed_out
                if not solved:
                    continue
                request_succeeded = True
                challenge_solved = challenge_solved or bool(solved.get("challenge_solved"))

                selector = Selector(text=solved["html"])
                rows = self._extract_rows(selector, (_BINSEARCH_ROW,))
                if not rows:
                    continue
                matched_template = True
                self.metrics.record_found_items(len(rows))

                for row_index, row in enumerate(rows):
                    if row_index >= settings.public_indexers_max_rows_per_page:
                        self.metrics.record_skip(
                            f"Row scan limit reached for {indexer.key}:{settings.public_indexers_max_rows_per_page}"
                        )
                        break
                    if row.css("th").get():
                        continue
                    stream = await self._binsearch_row_to_stream(
                        row=row,
                        indexer=indexer,
                        metadata=metadata,
                        catalog_type=catalog_type,
                        season=season,
                        episode=episode,
                        is_anime=is_anime,
                        processed_nzb_keys=processed_nzb_keys,
                        processed_nzb_keys_lock=processed_nzb_keys_lock,
                    )
                    if stream:
                        stream_hits += 1
                        yield stream
                break
            if not matched_template:
                self.metrics.record_skip(f"No rows for {indexer.key} page {page}")

        await record_source_outcome(
            indexer.key,
            success=stream_hits > 0 or request_succeeded,
            timed_out=timed_out,
            challenge_solved=challenge_solved,
            health_bucket=health_bucket,
        )

    @staticmethod
    def _unescape_title(text: str | None) -> str:
        if not text:
            return ""
        cleaned = html_module.unescape(text)
        return re.sub(r"\s+", " ", cleaned).strip()

    async def _mark_nzb_guid_if_new(
        self,
        nzb_guid: str,
        *,
        processed_nzb_keys: set[str],
        processed_nzb_keys_lock: asyncio.Lock | None,
    ) -> bool:
        normalized = (nzb_guid or "").strip().lower()
        if not normalized:
            return False
        if processed_nzb_keys_lock is None:
            if normalized in processed_nzb_keys:
                return False
            processed_nzb_keys.add(normalized)
            return True
        async with processed_nzb_keys_lock:
            if normalized in processed_nzb_keys:
                return False
            processed_nzb_keys.add(normalized)
            return True

    async def _binsearch_row_to_stream(
        self,
        *,
        row,
        indexer: PublicUsenetIndexerDefinition,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        is_anime: bool,
        processed_nzb_keys: set[str],
        processed_nzb_keys_lock: asyncio.Lock | None,
    ) -> UsenetStreamData | None:
        guid = (row.css('input[type="checkbox"]::attr(name)').get() or "").strip()
        if not guid:
            return None

        raw_title = self._first(row, _BINSEARCH_TITLE)
        title = self._unescape_title(raw_title)
        if not title:
            return None
        if is_contain_18_plus_keywords(title):
            self.metrics.record_skip("Adult content")
            return None

        parsed_data = PTT.parse_title(title, True)
        if not self.validate_title_and_year(parsed_data, metadata, catalog_type, title):
            return None
        if catalog_type == "series" and not self._validate_series(
            parsed_data, season, episode, strict_season=not is_anime
        ):
            return None

        nzb_guid = hashlib.sha256(f"binsearch:{guid}".encode()).hexdigest()[:40]
        if not await self._mark_nzb_guid_if_new(
            nzb_guid,
            processed_nzb_keys=processed_nzb_keys,
            processed_nzb_keys_lock=processed_nzb_keys_lock,
        ):
            self.metrics.record_skip("Duplicate nzb_guid")
            return None

        params = {"name": title, "id": guid}
        nzb_url = sanitize_nzb_url(f"https://www.binsearch.info/nzb?{urlencode(params)}")
        size = self._extract_size_bytes(row, _BINSEARCH_SIZE) or 0
        group_name = self._first(row, _BINSEARCH_GROUP)

        files = self._build_files(title, parsed_data, catalog_type, season, episode)
        for f in files:
            if f.size in (None, 0) and size > 0:
                f.size = size

        stream = UsenetStreamData(
            nzb_guid=nzb_guid,
            nzb_url=nzb_url,
            name=title,
            size=size,
            indexer=indexer.source_name,
            source=indexer.source_name,
            group_name=group_name,
            meta_id=metadata.get_canonical_id(),
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
            files=files,
        )
        self.metrics.record_processed_item()
        self.metrics.record_quality(stream.quality)
        self.metrics.record_source(stream.source)
        return stream
