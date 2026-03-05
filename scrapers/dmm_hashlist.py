import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import PTT
import dramatiq
import httpx

from db import crud
from db.config import settings
from db.crud.media import get_all_external_ids_batch, search_media
from db.database import get_background_session
from db.enums import MediaType
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import StreamFileData, TorrentStreamData
from scrapers.scraper_tasks import meta_fetcher
from utils.parser import calculate_max_similarity_ratio, is_contain_18_plus_keywords
from utils.lzstring import decompress_from_encoded_uri_component
from utils.wrappers import minimum_run_interval

logger = logging.getLogger(__name__)

GITHUB_API_BASE_URL = "https://api.github.com"
INFO_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
IFRAME_HASH_FRAGMENT_PATTERN = re.compile(r'<iframe\s+src="https://debridmediamanager\.com/hashlist#([^"]+)"></iframe>')
SPORTS_BROADCASTER_PATTERN = re.compile(
    r"Sky\s*F1(?:UHD|HD)?|Sky\s*Sports|F1TV|V\s*Sport|MotoGP\s*VideoPass",
    re.IGNORECASE,
)
SPORTS_DOMAIN_PATTERN = re.compile(
    r"\b(UFC|WWE|AEW|NBA|NFL|MLB|NHL|F1|Formula\s*1|MotoGP|WRC|Premier\s*League|La\s*Liga|Bundesliga|Serie\s*A)\b",
    re.IGNORECASE,
)
SPORTS_EVENT_PATTERN = re.compile(
    r"\b(Grand\s*Prix|Prelims|Main\s*Card|Qualifying|Race\s*Day|vs\.?|Playoffs?|R\d{1,2}|Raw|SmackDown)\b",
    re.IGNORECASE,
)
DATE_STAMP_PATTERN = re.compile(r"\b20\d{2}[.\-_ ]\d{2}[.\-_ ]\d{2}\b")
ANIME_RELEASE_GROUP_PATTERN = re.compile(
    r"\[(subsplease|erai-raws|horriblesubs|judas|ember|anime-time|nyaa)\]",
    re.IGNORECASE,
)
ANIME_KEYWORD_PATTERN = re.compile(r"\b(anime|ova|ona|vostfr|dual\s?audio)\b", re.IGNORECASE)
SEASON_EPISODE_PATTERN = re.compile(r"\bS\d{1,2}E\d{1,3}\b", re.IGNORECASE)
ANIME_EPISODE_NUMBER_PATTERN = re.compile(r"\b\d{3,4}\b")
COMMON_VIDEO_NUMBER_TOKENS = {480, 540, 576, 720, 1080, 1440, 2160}

LATEST_COMMIT_SHA_KEY = "dmm_hashlist_scraper:latest_commit_sha"
BACKFILL_NEXT_COMMIT_SHA_KEY = "dmm_hashlist_scraper:backfill_next_commit_sha"
PROCESSED_FILE_SHA_KEY = "dmm_hashlist_scraper:processed_file_shas"
BACKFILL_DONE_SENTINEL = "__done__"
DEFAULT_FULL_INGEST_INCREMENTAL_COMMITS = 100
DEFAULT_FULL_INGEST_BACKFILL_COMMITS = 100
DEFAULT_FULL_INGEST_MAX_ITERATIONS = 200

# Keep title matching strict to avoid wrong cross-title links.
DMM_METADATA_MIN_SIMILARITY = 87
DMM_METADATA_SEARCH_TIMEOUT_SECONDS = 8
DMM_METADATA_RESOLVE_CONCURRENCY = 8


def _decode_redis_value(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _parse_iso_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)

    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def extract_hash_fragment_from_html(html: str) -> str | None:
    """Extract hash fragment from DMM iframe HTML wrapper."""
    match = IFRAME_HASH_FRAGMENT_PATTERN.search(html)
    if not match:
        return None
    return match.group(1)


@dataclass(slots=True)
class HashlistTorrentEntry:
    filename: str
    info_hash: str
    size: int


def decode_hashlist_payload(encoded_payload: str) -> list[HashlistTorrentEntry]:
    """Decode DMM hashlist payload and normalize torrent rows."""
    decoded_json = decompress_from_encoded_uri_component(encoded_payload)
    if not decoded_json:
        return []

    payload = json_loads(decoded_json)
    if isinstance(payload, dict):
        torrent_rows = payload.get("torrents", [])
    elif isinstance(payload, list):
        torrent_rows = payload
    else:
        return []

    entries: list[HashlistTorrentEntry] = []
    for row in torrent_rows:
        if not isinstance(row, dict):
            continue

        filename = row.get("filename")
        info_hash = row.get("hash")
        size = row.get("bytes")

        if not filename or not info_hash:
            continue
        if not INFO_HASH_PATTERN.fullmatch(str(info_hash)):
            continue

        try:
            size_value = int(size or 0)
        except (TypeError, ValueError):
            size_value = 0

        entries.append(
            HashlistTorrentEntry(
                filename=str(filename),
                info_hash=str(info_hash).lower(),
                size=max(size_value, 0),
            )
        )

    return entries


def json_loads(value: str) -> Any:
    # Wrapper keeps decode entrypoint easy to monkeypatch in tests.
    return json.loads(value)


def deduplicate_entries_by_info_hash(entries: list[HashlistTorrentEntry]) -> list[HashlistTorrentEntry]:
    """Keep first occurrence for each info_hash."""
    unique_entries: list[HashlistTorrentEntry] = []
    seen_hashes: set[str] = set()
    for entry in entries:
        if entry.info_hash in seen_hashes:
            continue
        seen_hashes.add(entry.info_hash)
        unique_entries.append(entry)
    return unique_entries


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_likely_sports_broadcast_title(title: str) -> bool:
    if SPORTS_BROADCASTER_PATTERN.search(title):
        return True
    if not SPORTS_DOMAIN_PATTERN.search(title):
        return False
    return bool(SPORTS_EVENT_PATTERN.search(title) or DATE_STAMP_PATTERN.search(title))


def is_likely_anime_title(title: str, media_type: str | None = None) -> bool:
    if ANIME_RELEASE_GROUP_PATTERN.search(title):
        return True
    if ANIME_KEYWORD_PATTERN.search(title):
        return True
    if media_type == "movie":
        return False
    if SEASON_EPISODE_PATTERN.search(title):
        return False
    for numeric_token in ANIME_EPISODE_NUMBER_PATTERN.findall(title):
        try:
            numeric_value = int(numeric_token)
        except ValueError:
            continue
        if numeric_value in COMMON_VIDEO_NUMBER_TOKENS:
            continue
        if 1900 <= numeric_value <= 2099:
            continue
        if numeric_value >= 100:
            return True
    return False


def is_valid_metadata_match(
    *,
    parsed_title: str,
    parsed_year: int | None,
    media_type: str,
    candidate: dict[str, Any],
    min_similarity: int = DMM_METADATA_MIN_SIMILARITY,
    torrent_title: str | None = None,
) -> bool:
    """
    Validate search candidate quality before linking DMM stream to metadata.

    Uses the same core checks used by other scrapers: strict title similarity and
    year sanity for movie/series where parsed year exists.
    """
    candidate_title = candidate.get("title")
    if not candidate_title:
        return False
    if candidate.get("adult") is True:
        return False
    if media_type == "movie" and torrent_title and is_likely_sports_broadcast_title(torrent_title):
        return False

    candidate_type = candidate.get("type")
    if candidate_type in {"movie", "series"} and candidate_type != media_type:
        return False

    max_similarity = calculate_max_similarity_ratio(parsed_title, str(candidate_title))
    if max_similarity < min_similarity:
        return False

    parsed_year_int = _to_int_or_none(parsed_year)
    if parsed_year_int is None:
        return True

    candidate_year = _to_int_or_none(candidate.get("year"))
    candidate_end_year = _to_int_or_none(candidate.get("end_year"))

    if media_type == "movie":
        if candidate_year is not None and candidate_year != parsed_year_int:
            return False
        return True

    if candidate_year is None:
        return True

    if candidate_end_year is not None:
        return candidate_year <= parsed_year_int <= candidate_end_year
    return parsed_year_int >= candidate_year


class DMMHashlistScraper:
    SOURCE_NAME = "DMM Hashlist"

    def __init__(self):
        self.owner = settings.dmm_hashlist_repo_owner
        self.repo = settings.dmm_hashlist_repo_name
        self.branch = settings.dmm_hashlist_branch
        self.max_incremental_commits = max(settings.dmm_hashlist_commits_per_run, 0)
        self.max_backfill_commits = max(settings.dmm_hashlist_backfill_commits_per_run, 0)
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(40.0, connect=10.0),
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "MediaFusion-DMMHashlistScraper/1.0",
            },
        )

    async def close(self):
        await self.http_client.aclose()

    async def run(self) -> dict[str, int]:
        incremental_stats = await self._process_incremental_commits()
        backfill_stats = await self._process_backfill_commits()

        return {
            "incremental_commits": incremental_stats["commits_processed"],
            "incremental_files": incremental_stats["files_processed"],
            "incremental_streams": incremental_stats["streams_created"],
            "backfill_commits": backfill_stats["commits_processed"],
            "backfill_files": backfill_stats["files_processed"],
            "backfill_streams": backfill_stats["streams_created"],
        }

    async def _request_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = await self.http_client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def _request_text(self, url: str) -> str:
        response = await self.http_client.get(url)
        response.raise_for_status()
        return response.text

    async def _get_latest_commit_sha(self) -> str | None:
        commits = await self._request_json(
            f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/commits",
            params={"sha": self.branch, "per_page": 1, "page": 1},
        )
        if not commits:
            return None
        return commits[0].get("sha")

    async def _get_redis_str(self, key: str) -> str | None:
        return _decode_redis_value(await REDIS_ASYNC_CLIENT.get(key))

    async def _set_redis_str(self, key: str, value: str):
        await REDIS_ASYNC_CLIENT.set(key, value)

    async def _process_incremental_commits(self) -> dict[str, int]:
        stats = {"commits_processed": 0, "files_processed": 0, "streams_created": 0}
        if self.max_incremental_commits <= 0:
            return stats

        commits = await self._request_json(
            f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/commits",
            params={"sha": self.branch, "per_page": min(self.max_incremental_commits, 100), "page": 1},
        )
        if not commits:
            return stats

        latest_known_sha = await self._get_redis_str(LATEST_COMMIT_SHA_KEY)
        head_sha = commits[0].get("sha")

        commits_to_process: list[str] = []
        for commit in commits:
            commit_sha = commit.get("sha")
            if not commit_sha:
                continue
            if latest_known_sha and commit_sha == latest_known_sha:
                break
            commits_to_process.append(commit_sha)

        if latest_known_sha and len(commits_to_process) == len(commits):
            logger.info(
                "DMM incremental capped at %s commits; remaining history will be picked by backfill",
                self.max_incremental_commits,
            )

        for commit_sha in reversed(commits_to_process):
            commit_stats = await self._process_commit(commit_sha)
            stats["commits_processed"] += 1
            stats["files_processed"] += commit_stats["files_processed"]
            stats["streams_created"] += commit_stats["streams_created"]

        if head_sha:
            await self._set_redis_str(LATEST_COMMIT_SHA_KEY, head_sha)

        return stats

    async def _process_backfill_commits(self) -> dict[str, int]:
        stats = {"commits_processed": 0, "files_processed": 0, "streams_created": 0}
        if self.max_backfill_commits <= 0:
            return stats

        next_commit_sha = await self._get_redis_str(BACKFILL_NEXT_COMMIT_SHA_KEY)
        if next_commit_sha == BACKFILL_DONE_SENTINEL:
            return stats

        if not next_commit_sha:
            latest_sha = await self._get_redis_str(LATEST_COMMIT_SHA_KEY) or await self._get_latest_commit_sha()
            if not latest_sha:
                return stats

            latest_commit = await self._request_json(
                f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/commits/{latest_sha}"
            )
            parents = latest_commit.get("parents", [])
            if not parents:
                return stats
            next_commit_sha = parents[0].get("sha")
            if not next_commit_sha:
                return stats
            await self._set_redis_str(BACKFILL_NEXT_COMMIT_SHA_KEY, next_commit_sha)

        current_sha = next_commit_sha
        while current_sha and stats["commits_processed"] < self.max_backfill_commits:
            commit_stats = await self._process_commit(current_sha)
            stats["commits_processed"] += 1
            stats["files_processed"] += commit_stats["files_processed"]
            stats["streams_created"] += commit_stats["streams_created"]
            current_sha = commit_stats["next_parent_sha"]

        await self._set_redis_str(BACKFILL_NEXT_COMMIT_SHA_KEY, current_sha or BACKFILL_DONE_SENTINEL)
        return stats

    async def _process_commit(self, commit_sha: str) -> dict[str, int | str]:
        commit_data = await self._request_json(
            f"{GITHUB_API_BASE_URL}/repos/{self.owner}/{self.repo}/commits/{commit_sha}"
        )
        commit_date = _parse_iso_datetime(commit_data.get("commit", {}).get("author", {}).get("date"))
        parents = commit_data.get("parents", [])
        next_parent_sha = parents[0].get("sha") if parents else ""

        files_processed = 0
        streams_created = 0

        for file_data in commit_data.get("files", []):
            file_path = str(file_data.get("filename", ""))
            if not file_path.endswith(".html"):
                continue

            blob_sha = file_data.get("sha")
            if blob_sha and await REDIS_ASYNC_CLIENT.sismember(PROCESSED_FILE_SHA_KEY, blob_sha):
                continue

            raw_url = file_data.get("raw_url")
            if not raw_url:
                raw_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/{file_path}"

            try:
                html_content = await self._request_text(raw_url)
            except httpx.HTTPError as exc:
                logger.warning("Failed to fetch DMM hashlist file %s: %s", file_path, exc)
                continue

            encoded_payload = extract_hash_fragment_from_html(html_content)
            if not encoded_payload:
                logger.debug("Skipping DMM file without hash fragment: %s", file_path)
                if blob_sha:
                    await REDIS_ASYNC_CLIENT.sadd(PROCESSED_FILE_SHA_KEY, blob_sha)
                continue

            try:
                entries = decode_hashlist_payload(encoded_payload)
            except Exception as exc:
                logger.warning("Failed to decode DMM payload for %s: %s", file_path, exc)
                continue

            created = await self._store_entries(entries, commit_date)
            files_processed += 1
            streams_created += created

            if blob_sha:
                await REDIS_ASYNC_CLIENT.sadd(PROCESSED_FILE_SHA_KEY, blob_sha)

        return {
            "files_processed": files_processed,
            "streams_created": streams_created,
            "next_parent_sha": next_parent_sha,
        }

    async def _store_entries(self, entries: list[HashlistTorrentEntry], created_at: datetime) -> int:
        if not entries:
            return 0

        unique_entries = deduplicate_entries_by_info_hash(entries)

        metadata_keys: dict[tuple[str, int | None, str], tuple[str, int | None, str, str]] = {}
        stream_payloads: list[TorrentStreamData] = []
        parsed_entry_rows: list[tuple[HashlistTorrentEntry, dict[str, Any], tuple[str, int | None, str], str]] = []
        skipped_adult = 0
        skipped_sports = 0

        for entry in unique_entries:
            if is_contain_18_plus_keywords(entry.filename):
                skipped_adult += 1
                continue

            parsed = PTT.parse_title(entry.filename, True)
            parsed_title = parsed.get("title") or entry.filename
            parsed_year = parsed.get("year")
            media_type = "series" if parsed.get("seasons") or parsed.get("episodes") else "movie"
            if media_type == "movie" and is_likely_sports_broadcast_title(entry.filename):
                skipped_sports += 1
                continue

            metadata_cache_key = (parsed_title.lower(), parsed_year, media_type)
            metadata_keys.setdefault(metadata_cache_key, (parsed_title, parsed_year, media_type, entry.filename))
            parsed_entry_rows.append((entry, parsed, metadata_cache_key, media_type))

        metadata_cache: dict[tuple[str, int | None, str], str | None] = {}

        resolve_semaphore = asyncio.Semaphore(DMM_METADATA_RESOLVE_CONCURRENCY)

        async def _resolve_cache_key(
            cache_key: tuple[str, int | None, str],
            parsed_title: str,
            parsed_year: int | None,
            media_type: str,
            torrent_title: str,
        ) -> tuple[tuple[str, int | None, str], str | None]:
            async with resolve_semaphore:
                async with get_background_session() as session:
                    meta_id = await self._resolve_meta_id(
                        session,
                        parsed_title,
                        parsed_year,
                        media_type,
                        torrent_title=torrent_title,
                    )
                    # Persist metadata created during external resolution even if no stream is stored.
                    await session.commit()
                    return cache_key, meta_id

        if metadata_keys:
            resolved_pairs = await asyncio.gather(
                *(
                    _resolve_cache_key(cache_key, title, year, media_type, torrent_title)
                    for cache_key, (title, year, media_type, torrent_title) in metadata_keys.items()
                )
            )
            metadata_cache = dict(resolved_pairs)

        for entry, parsed, metadata_cache_key, media_type in parsed_entry_rows:
            meta_id = metadata_cache.get(metadata_cache_key)
            if not meta_id:
                continue
            stream_payloads.append(self._build_torrent_stream(entry, parsed, meta_id, created_at, media_type))

        if not stream_payloads:
            logger.info(
                "DMM store summary: total=%s unique=%s adult_skipped=%s sports_skipped=%s metadata_keys=%s resolved=%s payloads=0 stored=0",
                len(entries),
                len(unique_entries),
                skipped_adult,
                skipped_sports,
                len(metadata_keys),
                sum(1 for meta_id in metadata_cache.values() if meta_id),
            )
            return 0

        async with get_background_session() as session:
            stored_count = await crud.store_new_torrent_streams(
                session,
                [stream.model_dump(by_alias=True) for stream in stream_payloads],
            )
            await session.commit()
            logger.info(
                "DMM store summary: total=%s unique=%s adult_skipped=%s sports_skipped=%s metadata_keys=%s resolved=%s payloads=%s stored=%s",
                len(entries),
                len(unique_entries),
                skipped_adult,
                skipped_sports,
                len(metadata_keys),
                sum(1 for meta_id in metadata_cache.values() if meta_id),
                len(stream_payloads),
                stored_count,
            )
            return stored_count

    async def _resolve_meta_id_from_existing_db(
        self,
        session,
        title: str,
        year: int | None,
        media_type: str,
        torrent_title: str | None = None,
    ) -> str | None:
        db_media_type = MediaType.SERIES if media_type == "series" else MediaType.MOVIE
        try:
            db_results = await search_media(
                session,
                query_text=title,
                media_type=db_media_type,
                limit=10,
            )
        except Exception as exc:
            logger.debug("DB metadata search failed for DMM entry '%s': %s", title, exc)
            return None

        if not db_results:
            return None

        ext_ids_batch = await get_all_external_ids_batch(session, [media.id for media in db_results])
        for media in db_results:
            if media.adult:
                continue
            if not ext_ids_batch.get(media.id):
                continue

            candidate: dict[str, Any] = {
                "title": media.title,
                "year": media.year,
                "end_year": media.end_date.year if media.end_date else None,
                "type": media.type.value,
                "adult": media.adult,
            }
            if not is_valid_metadata_match(
                parsed_title=title,
                parsed_year=year,
                media_type=media_type,
                candidate=candidate,
                torrent_title=torrent_title,
            ):
                continue
            return await crud.get_canonical_external_id(session, media.id)

        return None

    async def _resolve_meta_id(
        self,
        session,
        title: str,
        year: int | None,
        media_type: str,
        torrent_title: str | None = None,
    ) -> str | None:
        existing_meta_id = await self._resolve_meta_id_from_existing_db(
            session,
            title,
            year,
            media_type,
            torrent_title=torrent_title,
        )
        if existing_meta_id:
            return existing_meta_id

        anime_search_enabled = is_likely_anime_title(torrent_title or title, media_type=media_type)
        try:
            search_results = await asyncio.wait_for(
                meta_fetcher.search_multiple_results(
                    title=title,
                    year=year,
                    media_type=media_type,
                    limit=5,
                    min_similarity=DMM_METADATA_MIN_SIMILARITY,
                    include_anime=anime_search_enabled,
                ),
                timeout=DMM_METADATA_SEARCH_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.debug("Metadata search timed out for DMM entry '%s'", title)
            search_results = []
        except Exception as exc:
            logger.debug("Metadata search failed for DMM entry '%s': %s", title, exc)
            search_results = []

        for best_match in search_results:
            if not is_valid_metadata_match(
                parsed_title=title,
                parsed_year=year,
                media_type=media_type,
                candidate=best_match,
                torrent_title=torrent_title,
            ):
                continue

            external_id = best_match.get("imdb_id") or best_match.get("id")
            if not external_id:
                continue

            metadata_payload: dict[str, Any] = {
                "id": external_id,
                "title": best_match.get("title", title),
                "year": best_match.get("year", year),
                "poster": best_match.get("poster"),
                "background": best_match.get("background"),
                "description": best_match.get("description"),
                "genres": best_match.get("genres", []),
                "imdb_id": best_match.get("imdb_id"),
                "tmdb_id": best_match.get("tmdb_id"),
                "tvdb_id": best_match.get("tvdb_id"),
            }
            metadata_result = await crud.get_or_create_metadata(
                session,
                metadata_payload,
                media_type,
                is_search_imdb_title=False,
                is_imdb_only=False,
            )
            if metadata_result:
                return await crud.get_canonical_external_id(session, metadata_result.id)

        logger.debug("Skipping DMM entry due to no confident metadata match: %s (%s)", title, media_type)
        return None

    def _build_torrent_stream(
        self,
        entry: HashlistTorrentEntry,
        parsed_data: dict[str, Any],
        meta_id: str,
        created_at: datetime,
        media_type: str,
    ) -> TorrentStreamData:
        files: list[StreamFileData] = []
        seasons = parsed_data.get("seasons") or []
        episodes = parsed_data.get("episodes") or []

        if media_type == "series" and seasons:
            if episodes:
                for episode_number in episodes:
                    files.append(
                        StreamFileData(
                            file_index=0,
                            filename=entry.filename,
                            file_type="video",
                            season_number=seasons[0],
                            episode_number=episode_number,
                        )
                    )
            else:
                for season_number in seasons:
                    files.append(
                        StreamFileData(
                            file_index=0,
                            filename=entry.filename,
                            file_type="video",
                            season_number=season_number,
                            episode_number=1,
                        )
                    )

        audio_formats = parsed_data.get("audio", [])
        channels = parsed_data.get("channels", [])
        hdr_formats = parsed_data.get("hdr", [])
        languages = parsed_data.get("languages", [])

        return TorrentStreamData(
            info_hash=entry.info_hash,
            meta_id=meta_id,
            name=entry.filename,
            announce_list=[],
            size=entry.size,
            source=self.SOURCE_NAME,
            seeders=0,
            created_at=created_at,
            resolution=parsed_data.get("resolution"),
            codec=parsed_data.get("codec"),
            quality=parsed_data.get("quality"),
            bit_depth=parsed_data.get("bit_depth"),
            release_group=parsed_data.get("group"),
            audio_formats=audio_formats if isinstance(audio_formats, list) else [],
            channels=channels if isinstance(channels, list) else [],
            hdr_formats=hdr_formats if isinstance(hdr_formats, list) else [],
            languages=languages if isinstance(languages, list) else [],
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


@dramatiq.actor(time_limit=60 * 60 * 1000, priority=5, queue_name="scrapy")
@minimum_run_interval(hours=settings.dmm_hashlist_sync_interval_hour)
async def run_dmm_hashlist_scraper(**kwargs):
    if not settings.is_scrap_from_dmm_hashlist:
        return {"status": "disabled"}

    scraper = DMMHashlistScraper()
    try:
        logger.info("Running DMM hashlist scraper")
        result = await scraper.run()
        logger.info("DMM hashlist scraper completed: %s", result)
        return result
    finally:
        await scraper.close()


async def run_dmm_hashlist_full_ingestion(
    *,
    max_iterations: int = DEFAULT_FULL_INGEST_MAX_ITERATIONS,
    incremental_commits: int = DEFAULT_FULL_INGEST_INCREMENTAL_COMMITS,
    backfill_commits: int = DEFAULT_FULL_INGEST_BACKFILL_COMMITS,
    reset_checkpoints: bool = False,
) -> dict[str, Any]:
    """
    Run DMM ingestion in a loop until backfill is complete or guardrails stop it.

    This is intended for one-time admin backfills.
    """
    if not settings.is_scrap_from_dmm_hashlist:
        return {
            "status": "disabled",
            "backfill_complete": False,
            "iterations_run": 0,
        }

    if reset_checkpoints:
        await REDIS_ASYNC_CLIENT.delete(
            LATEST_COMMIT_SHA_KEY,
            BACKFILL_NEXT_COMMIT_SHA_KEY,
            PROCESSED_FILE_SHA_KEY,
        )

    scraper = DMMHashlistScraper()
    scraper.max_incremental_commits = max(0, min(incremental_commits, 100))
    scraper.max_backfill_commits = max(0, min(backfill_commits, 100))

    totals = {
        "incremental_commits": 0,
        "incremental_files": 0,
        "incremental_streams": 0,
        "backfill_commits": 0,
        "backfill_files": 0,
        "backfill_streams": 0,
    }
    iterations_run = 0
    backfill_complete = False
    stopped_reason = "max_iterations_reached"

    try:
        for iteration in range(1, max(1, max_iterations) + 1):
            iteration_stats = await scraper.run()
            iterations_run = iteration

            for key in totals:
                totals[key] += int(iteration_stats.get(key, 0))

            backfill_next = _decode_redis_value(await REDIS_ASYNC_CLIENT.get(BACKFILL_NEXT_COMMIT_SHA_KEY))
            backfill_complete = backfill_next == BACKFILL_DONE_SENTINEL
            if backfill_complete:
                stopped_reason = "backfill_complete"
                break

            made_progress = any(int(iteration_stats.get(key, 0)) > 0 for key in totals)
            if not made_progress:
                stopped_reason = "no_progress"
                break
    finally:
        await scraper.close()

    return {
        "status": "success" if backfill_complete else "partial",
        "backfill_complete": backfill_complete,
        "stopped_reason": stopped_reason,
        "iterations_run": iterations_run,
        "max_iterations": max_iterations,
        "per_iteration_limits": {
            "incremental_commits": scraper.max_incremental_commits,
            "backfill_commits": scraper.max_backfill_commits,
        },
        "totals": totals,
        "latest_commit_sha": _decode_redis_value(await REDIS_ASYNC_CLIENT.get(LATEST_COMMIT_SHA_KEY)),
        "backfill_next_commit_sha": _decode_redis_value(await REDIS_ASYNC_CLIENT.get(BACKFILL_NEXT_COMMIT_SHA_KEY)),
        "processed_file_sha_count": await REDIS_ASYNC_CLIENT.scard(PROCESSED_FILE_SHA_KEY),
    }


@dramatiq.actor(time_limit=float("inf"), priority=5, queue_name="scrapy")
async def run_dmm_hashlist_full_ingestion_job(
    *,
    max_iterations: int = DEFAULT_FULL_INGEST_MAX_ITERATIONS,
    incremental_commits: int = DEFAULT_FULL_INGEST_INCREMENTAL_COMMITS,
    backfill_commits: int = DEFAULT_FULL_INGEST_BACKFILL_COMMITS,
    reset_checkpoints: bool = False,
):
    logger.info("Running full DMM hashlist ingestion")
    result = await run_dmm_hashlist_full_ingestion(
        max_iterations=max_iterations,
        incremental_commits=incremental_commits,
        backfill_commits=backfill_commits,
        reset_checkpoints=reset_checkpoints,
    )
    logger.info("Full DMM hashlist ingestion completed: %s", result)
    return result
