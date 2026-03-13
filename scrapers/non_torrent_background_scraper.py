import asyncio
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha1
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
import PTT
import yt_dlp
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from api.task_queue import actor
from db import crud
from db.config import settings
from db.database import get_background_session
from db.schemas import MetadataData
from db.models import AceStreamStream, Stream, StreamMediaLink, StreamType, YouTubeStream
from db.redis_database import REDIS_ASYNC_CLIENT
from scrapers.base_scraper import BackgroundScraperManager
from scrapers.telegram import telegram_scraper
from utils.config import config_manager
from utils.parser import is_contain_18_plus_keywords
from utils.youtube import analyze_youtube_video

logger = logging.getLogger(__name__)

YOUTUBE_BACKGROUND_MAX_RESULTS_PER_QUERY = 25
YOUTUBE_BACKGROUND_DEDUPE_TTL_HOURS = 72
YOUTUBE_BACKGROUND_MIN_MOVIE_DURATION_SECONDS = 45 * 60
YOUTUBE_BACKGROUND_MIN_SERIES_DURATION_SECONDS = 8 * 60
YOUTUBE_BACKGROUND_MAX_MOVIES_PER_RUN = 10
YOUTUBE_BACKGROUND_MAX_SERIES_PER_RUN = 10
YOUTUBE_BACKGROUND_MAX_AKA_TITLES_PER_MEDIA = 2
YOUTUBE_BACKGROUND_MAX_QUERIES_PER_MEDIA = 8
YOUTUBE_BACKGROUND_MAX_TOTAL_QUERIES = 120
YOUTUBE_BACKGROUND_BLOCKED_TITLE_PATTERNS = (
    re.compile(r"\btop\s*\d+\b", flags=re.IGNORECASE),
    re.compile(r"\bbest\b", flags=re.IGNORECASE),
    re.compile(r"\branked\b", flags=re.IGNORECASE),
    re.compile(r"\branking\b", flags=re.IGNORECASE),
    re.compile(r"\bexplained\b", flags=re.IGNORECASE),
    re.compile(r"\brecap\b", flags=re.IGNORECASE),
    re.compile(r"\breview\b", flags=re.IGNORECASE),
    re.compile(r"\breaction\b", flags=re.IGNORECASE),
    re.compile(r"\btrailer\b", flags=re.IGNORECASE),
    re.compile(r"\bteaser\b", flags=re.IGNORECASE),
    re.compile(r"\bclip\b", flags=re.IGNORECASE),
    re.compile(r"\bshorts?\b", flags=re.IGNORECASE),
    re.compile(r"\breleased in \d{4}\b", flags=re.IGNORECASE),
)
YOUTUBE_BACKGROUND_DEFAULT_LANGUAGE_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "english",
        "region_code": "US",
        "movie_terms": ["full movie", "full length movie"],
        "series_terms": ["full episode", "full tv episode"],
    },
    {
        "name": "hindi",
        "region_code": "IN",
        "movie_terms": ["hindi full movie", "bollywood full movie"],
        "series_terms": ["hindi serial full episode", "hindi full episode"],
    },
    {
        "name": "tamil",
        "region_code": "IN",
        "movie_terms": ["tamil full movie"],
        "series_terms": ["tamil full episode"],
    },
    {
        "name": "spanish",
        "region_code": "ES",
        "movie_terms": ["pelicula completa"],
        "series_terms": ["serie completa capitulo"],
    },
    {
        "name": "korean",
        "region_code": "KR",
        "movie_terms": ["korean full movie"],
        "series_terms": ["korean full episode"],
    },
)

ACESTREAM_BACKGROUND_DEFAULT_QUERY_TERMS = ["live sports", "movies", "series"]
ACESTREAM_BACKGROUND_FETCH_TIMEOUT_SECONDS = 20
ACESTREAM_BACKGROUND_MAX_ITEMS_PER_SOURCE = 50
ACESTREAM_BACKGROUND_MAX_PAGES_PER_SOURCE = 2
ACESTREAM_BACKGROUND_DEDUPE_TTL_HOURS = 168

TELEGRAM_BACKGROUND_DEDUPE_TTL_HOURS = 168
TELEGRAM_BACKGROUND_MAX_CHANNELS_PER_RUN = 100
TELEGRAM_BACKGROUND_INDEXER_QUERY_TERMS = ["movies", "series", "anime", "sports"]
TELEGRAM_BACKGROUND_INDEXER_MIN_MEMBERS = 1000
TELEGRAM_BACKGROUND_INDEXER_MAX_CHANNELS = 200
TELEGRAM_BACKGROUND_INDEXER_TIMEOUT_SECONDS = 20
TELEGRAM_BACKGROUND_INDEXER_CACHE_TTL_HOURS = 24

ACESTREAM_URI_PATTERN = re.compile(r"acestream://([a-fA-F0-9]{40})", flags=re.IGNORECASE)
ACESTREAM_ANCHOR_PATTERN = re.compile(
    r"""<a[^>]*href=["']acestream://(?P<content_id>[a-fA-F0-9]{40})["'][^>]*>(?P<title>[^<]+)</a>""",
    flags=re.IGNORECASE,
)
ACESTREAM_INFOHASH_PARAM_PATTERN = re.compile(r"(?:infohash|info_hash)=([a-fA-F0-9]{40})", flags=re.IGNORECASE)
ACESTREAM_LABELED_SERVER_PATTERN = re.compile(
    r"""Server\s*(?P<server_no>\d+)\s*:\s*(?P<label>[^\n\r<]+).*?acestream://(?P<content_id>[a-fA-F0-9]{40})""",
    flags=re.IGNORECASE | re.DOTALL,
)
RESOLUTION_PATTERN = re.compile(r"\b(2160p|1080p|720p|480p)\b", flags=re.IGNORECASE)
HEX_40_FULL_PATTERN = re.compile(r"^[a-fA-F0-9]{40}$")
TELEGRAM_PUBLIC_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,})", flags=re.IGNORECASE
)
TELEGRAM_HANDLE_PATTERN = re.compile(r"^@?[A-Za-z0-9_]{4,}$")


@dataclass(slots=True)
class YouTubeCandidate:
    video_id: str
    default_media_type: str


@dataclass(slots=True)
class YouTubeSearchSeed:
    query: str
    default_media_type: str
    region_code: str
    max_results: int
    name: str


@dataclass(slots=True)
class AceStreamCandidate:
    content_id: str | None
    info_hash: str | None
    title: str | None
    default_media_type: str
    source_name: str
    channel_key: str | None = None
    upsert_by_channel: bool = False
    server_slot: str | None = None
    metadata_title: str | None = None
    metadata_external_id: str | None = None
    metadata_media_type: str | None = None
    metadata_poster: str | None = None
    metadata_country: str | None = None
    metadata_tv_language: str | None = None


@dataclass(slots=True)
class TelegramCandidate:
    chat_id: str
    message_id: int
    chat_username: str | None
    file_unique_id: str | None
    file_id: str | None
    file_name: str | None
    mime_type: str | None
    size: int | None
    posted_at: datetime | None
    caption: str | None
    name: str
    resolution: str | None
    codec: str | None
    quality: str | None
    bit_depth: str | None
    uploader: str | None
    release_group: str | None
    is_remastered: bool
    is_proper: bool
    is_repack: bool
    is_extended: bool
    is_dubbed: bool
    is_subbed: bool
    season_number: int | None
    episode_number: int | None
    episode_end: int | None
    languages: list[str]
    inferred_title: str
    inferred_year: int | None
    imdb_id: str | None

    @property
    def dedupe_key(self) -> str:
        return f"{self.chat_id}:{self.message_id}"

    @property
    def inferred_media_type(self) -> str:
        if self.season_number is not None or self.episode_number is not None:
            return "series"
        return "movie"


class ProcessedItemTracker:
    def __init__(self, key: str, ttl_hours: int):
        self.key = key
        self.ttl_seconds = max(1, int(timedelta(hours=ttl_hours).total_seconds()))

    async def is_processed(self, item_id: str) -> bool:
        return bool(await REDIS_ASYNC_CLIENT.sismember(self.key, item_id))

    async def mark_processed(self, item_id: str) -> None:
        await REDIS_ASYNC_CLIENT.sadd(self.key, item_id)
        await REDIS_ASYNC_CLIENT.expire(self.key, self.ttl_seconds)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_media_type(raw_value: str | None, *, fallback: str = "movie") -> str:
    value = (raw_value or "").strip().lower()
    if value in {"movie", "series", "tv"}:
        return value
    return fallback


def _coerce_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) == 4 and stripped.isdigit():
            return int(stripped)
    return None


def _coerce_bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(coerced, maximum))


def _parse_comma_terms(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _normalize_hex_40(value: Any) -> str | None:
    if not value:
        return None
    raw_value = str(value).strip()
    if not raw_value:
        return None
    uri_match = ACESTREAM_URI_PATTERN.search(raw_value)
    if uri_match:
        return uri_match.group(1).lower()
    infohash_match = ACESTREAM_INFOHASH_PARAM_PATTERN.search(raw_value)
    if infohash_match:
        return infohash_match.group(1).lower()
    if HEX_40_FULL_PATTERN.match(raw_value):
        return raw_value.lower()
    return None


def _normalize_channel_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or None


def _acestream_source_identifier(source_name: str, channel_key: str | None = None) -> str:
    _ = channel_key
    return source_name.strip() or "acestream"


def _legacy_acestream_source_identifier(source_name: str, channel_key: str | None = None) -> str:
    if channel_key:
        return f"acestream_background:channel:{channel_key}"
    return f"acestream_background:{source_name}"


def _extract_json_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("results", "items", "data", "channels", "hits"):
        nested = payload.get(key)
        items = _extract_json_items(nested)
        if items:
            return items

    if any(key in payload for key in ("title", "name", "content_id", "infohash", "username", "channel_username")):
        return [payload]
    return []


def _extract_member_count(item: dict[str, Any], configured_keys: list[str] | None = None) -> int | None:
    candidate_keys = configured_keys or [
        "members",
        "member_count",
        "subscribers",
        "subscriber_count",
        "participants_count",
    ]
    for key in candidate_keys:
        raw_value = item.get(key)
        if raw_value is None:
            continue
        if isinstance(raw_value, (int, float)):
            return int(raw_value)
        if isinstance(raw_value, str):
            digits = re.sub(r"[^\d]", "", raw_value)
            if digits:
                return int(digits)
    return None


def _normalize_telegram_channel_identifier(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None

    url_match = TELEGRAM_PUBLIC_URL_PATTERN.search(text)
    if url_match:
        handle = url_match.group(1)
        if handle and handle.lower() not in {"joinchat", "s"}:
            return f"@{handle.lower()}"

    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc and parsed.path:
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            candidate = parts[0]
            if candidate.lower() == "s" and len(parts) > 1:
                candidate = parts[1]
            if (
                candidate
                and not candidate.startswith("+")
                and candidate.lower() not in {"joinchat", "c", "addstickers"}
            ):
                return f"@{candidate.lower()}"

    if TELEGRAM_HANDLE_PATTERN.match(text):
        normalized = text[1:] if text.startswith("@") else text
        return f"@{normalized.lower()}"

    if text.startswith("-") and text[1:].isdigit():
        return text
    if text.isdigit():
        return text
    return None


def _extract_channel_identifiers(item: dict[str, Any], configured_keys: list[str] | None = None) -> set[str]:
    identifiers: set[str] = set()
    candidate_keys = configured_keys or [
        "username",
        "channel_username",
        "handle",
        "slug",
        "url",
        "link",
        "telegram_url",
        "invite_link",
        "chat_id",
    ]
    for key in candidate_keys:
        raw_value = item.get(key)
        if raw_value is None:
            continue
        if isinstance(raw_value, str):
            normalized = _normalize_telegram_channel_identifier(raw_value)
            if normalized:
                identifiers.add(normalized)
        elif isinstance(raw_value, list):
            for list_item in raw_value:
                if isinstance(list_item, str):
                    normalized = _normalize_telegram_channel_identifier(list_item)
                    if normalized:
                        identifiers.add(normalized)

    for nested_key in ("channel", "telegram", "data"):
        nested_value = item.get(nested_key)
        if isinstance(nested_value, dict):
            identifiers.update(_extract_channel_identifiers(nested_value, configured_keys))
    return identifiers


def _tmp_external_id(prefix: str, item_key: str) -> str:
    digest = sha1(item_key.encode("utf-8")).hexdigest()[:20]
    return f"mf_tmp_{prefix}_{digest}"


async def _resolve_metadata(
    *,
    session,
    title: str,
    media_type: str,
    year: int | None,
    external_id: str | None,
    poster: str | None = None,
    extra_fields: dict[str, Any] | None = None,
):
    if not title:
        return None
    metadata_payload: dict[str, Any] = {
        "title": title,
        "year": year,
        "id": external_id or _tmp_external_id("bg", f"{media_type}:{title}:{year or 'na'}"),
    }
    if poster:
        metadata_payload["poster"] = poster
    if extra_fields:
        metadata_payload.update(extra_fields)
    return await crud.get_or_create_metadata(
        session,
        metadata_payload,
        media_type,
        is_search_imdb_title=True,
        is_imdb_only=True,
    )


def _parse_title_data(title: str) -> dict[str, Any]:
    try:
        return PTT.parse_title(title, True) or {}
    except Exception:
        return {}


def _is_adult_title(title: str) -> bool:
    return bool(title and is_contain_18_plus_keywords(title))


def _is_blocked_youtube_title(title: str) -> bool:
    if not title:
        return True
    return any(pattern.search(title) for pattern in YOUTUBE_BACKGROUND_BLOCKED_TITLE_PATTERNS)


def _passes_youtube_duration_gate(duration_seconds: int, media_type: str) -> bool:
    if duration_seconds <= 0:
        return False
    if media_type == "series":
        return duration_seconds >= YOUTUBE_BACKGROUND_MIN_SERIES_DURATION_SECONDS
    return duration_seconds >= YOUTUBE_BACKGROUND_MIN_MOVIE_DURATION_SECONDS


def _yt_dlp_collect_video_ids(info: Any, *, max_results: int) -> list[str]:
    if not isinstance(info, dict):
        return []

    extracted_ids: list[str] = []
    entries = info.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            video_id = entry.get("id")
            if isinstance(video_id, str) and len(video_id) == 11:
                extracted_ids.append(video_id)
            if len(extracted_ids) >= max_results:
                break
    else:
        video_id = info.get("id")
        if isinstance(video_id, str) and len(video_id) == 11:
            extracted_ids.append(video_id)

    return extracted_ids[:max_results]


def _yt_dlp_extract_video_ids_sync(
    query: str,
    max_results: int,
    region_code: str | None = None,
) -> list[str]:
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": max_results,
        "default_search": "ytsearch",
    }
    if region_code:
        ydl_opts["geo_bypass_country"] = region_code

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=False)
    return _yt_dlp_collect_video_ids(info, max_results=max_results)


async def _yt_dlp_extract_video_ids(
    query: str,
    *,
    max_results: int,
    region_code: str | None = None,
) -> list[str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _yt_dlp_extract_video_ids_sync,
        query,
        max_results,
        region_code,
    )


def _normalize_search_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _collect_metadata_titles(metadata: MetadataData, *, max_aka_titles: int) -> list[str]:
    title_candidates: list[str] = []
    title_candidates.append(_normalize_search_text(metadata.title))
    if metadata.original_title:
        title_candidates.append(_normalize_search_text(metadata.original_title))

    aka_count = 0
    for aka_title in metadata.aka_titles:
        if aka_count >= max_aka_titles:
            break
        if isinstance(aka_title, str):
            cleaned = _normalize_search_text(aka_title)
            if cleaned:
                title_candidates.append(cleaned)
                aka_count += 1

    unique_titles: list[str] = []
    seen: set[str] = set()
    for title in title_candidates:
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_titles.append(title)
    return unique_titles


def _resolve_youtube_language_profiles(youtube_config: dict[str, Any]) -> list[dict[str, Any]]:
    configured_profiles = [item for item in _as_list(youtube_config.get("language_profiles")) if isinstance(item, dict)]
    profile_source = configured_profiles or list(YOUTUBE_BACKGROUND_DEFAULT_LANGUAGE_PROFILES)
    default_max_results = _coerce_bounded_int(
        youtube_config.get("max_results_per_query"),
        default=YOUTUBE_BACKGROUND_MAX_RESULTS_PER_QUERY,
        minimum=1,
        maximum=50,
    )

    normalized_profiles: list[dict[str, Any]] = []
    for index, profile in enumerate(profile_source, start=1):
        name = _normalize_search_text(str(profile.get("name") or f"profile_{index}"))
        region_code = _normalize_search_text(str(profile.get("region_code") or "US")).upper()[:2] or "US"
        movie_terms = [
            _normalize_search_text(str(term))
            for term in _as_list(profile.get("movie_terms"))
            if _normalize_search_text(str(term))
        ]
        series_terms = [
            _normalize_search_text(str(term))
            for term in _as_list(profile.get("series_terms"))
            if _normalize_search_text(str(term))
        ]
        if not movie_terms:
            movie_terms = ["full movie"]
        if not series_terms:
            series_terms = ["full episode"]
        max_results = _coerce_bounded_int(
            profile.get("max_results"),
            default=default_max_results,
            minimum=1,
            maximum=50,
        )
        normalized_profiles.append(
            {
                "name": name or f"profile_{index}",
                "region_code": region_code,
                "movie_terms": movie_terms,
                "series_terms": series_terms,
                "max_results": max_results,
            }
        )

    return normalized_profiles


async def _load_youtube_background_targets(youtube_config: dict[str, Any]) -> list[dict[str, Any]]:
    manager = BackgroundScraperManager()
    pending_movies = await manager.get_pending_items("movie")
    pending_series = await manager.get_pending_items("series")
    if not pending_movies and not pending_series:
        return []

    max_movies = _coerce_bounded_int(
        youtube_config.get("max_movies_per_run"),
        default=YOUTUBE_BACKGROUND_MAX_MOVIES_PER_RUN,
        minimum=1,
        maximum=100,
    )
    max_series = _coerce_bounded_int(
        youtube_config.get("max_series_per_run"),
        default=YOUTUBE_BACKGROUND_MAX_SERIES_PER_RUN,
        minimum=1,
        maximum=100,
    )
    max_aka_titles = _coerce_bounded_int(
        youtube_config.get("max_aka_titles_per_media"),
        default=YOUTUBE_BACKGROUND_MAX_AKA_TITLES_PER_MEDIA,
        minimum=0,
        maximum=10,
    )

    targets: list[dict[str, Any]] = []
    async with get_background_session() as session:
        for item in pending_movies[:max_movies]:
            meta_id = _normalize_search_text(str(item.get("key") or ""))
            if not meta_id:
                continue
            media = await crud.get_movie_data_by_id(session, meta_id, load_relations=True)
            if not media:
                continue
            metadata = MetadataData.from_db(media)
            titles = _collect_metadata_titles(metadata, max_aka_titles=max_aka_titles)
            if not titles:
                continue
            targets.append(
                {
                    "target_key": meta_id,
                    "media_type": "movie",
                    "titles": titles,
                    "year": _coerce_year(metadata.year),
                    "season": None,
                    "episode": None,
                }
            )

        for item in pending_series[:max_series]:
            raw_key = _normalize_search_text(str(item.get("key") or ""))
            if not raw_key:
                continue
            parts = raw_key.rsplit(":", 2)
            if len(parts) != 3:
                continue
            meta_id, season_raw, episode_raw = parts
            try:
                season = int(season_raw)
                episode = int(episode_raw)
            except (TypeError, ValueError):
                continue
            if season <= 0 or episode <= 0:
                continue
            media = await crud.get_series_data_by_id(session, meta_id, load_relations=True)
            if not media:
                continue
            metadata = MetadataData.from_db(media)
            titles = _collect_metadata_titles(metadata, max_aka_titles=max_aka_titles)
            if not titles:
                continue
            targets.append(
                {
                    "target_key": raw_key,
                    "media_type": "series",
                    "titles": titles,
                    "year": _coerce_year(metadata.year),
                    "season": season,
                    "episode": episode,
                }
            )

    return targets


def _build_youtube_search_seeds(
    targets: list[dict[str, Any]],
    *,
    youtube_config: dict[str, Any],
) -> list[YouTubeSearchSeed]:
    if not targets:
        return []

    max_queries_per_media = _coerce_bounded_int(
        youtube_config.get("max_queries_per_media"),
        default=YOUTUBE_BACKGROUND_MAX_QUERIES_PER_MEDIA,
        minimum=1,
        maximum=40,
    )
    max_total_queries = _coerce_bounded_int(
        youtube_config.get("max_total_queries"),
        default=YOUTUBE_BACKGROUND_MAX_TOTAL_QUERIES,
        minimum=1,
        maximum=500,
    )
    profiles = _resolve_youtube_language_profiles(youtube_config)

    seeds: list[YouTubeSearchSeed] = []
    seen_queries: set[str] = set()

    for target in targets:
        media_type = _normalize_media_type(target.get("media_type"), fallback="movie")
        titles = [value for value in target.get("titles", []) if isinstance(value, str)]
        if not titles:
            continue
        year = _coerce_year(target.get("year"))
        season = target.get("season")
        episode = target.get("episode")

        media_query_count = 0
        for profile in profiles:
            terms = profile["movie_terms"] if media_type == "movie" else profile["series_terms"]
            if media_type == "movie":
                base_queries = [f"{title} {year}" if year else title for title in titles]
            else:
                if isinstance(season, int) and isinstance(episode, int):
                    base_queries = [f"{title} S{season:02d}E{episode:02d}" for title in titles] + [
                        f"{title} season {season} episode {episode}" for title in titles
                    ]
                else:
                    base_queries = list(titles)

            for base_query in base_queries:
                normalized_base = _normalize_search_text(base_query)
                if not normalized_base:
                    continue
                for term in terms:
                    query = _normalize_search_text(f"{normalized_base} {term}")
                    if not query:
                        continue
                    dedupe_key = f"{media_type}:{profile['region_code']}:{query.lower()}"
                    if dedupe_key in seen_queries:
                        continue
                    seen_queries.add(dedupe_key)
                    seeds.append(
                        YouTubeSearchSeed(
                            query=query,
                            default_media_type=media_type,
                            region_code=profile["region_code"],
                            max_results=profile["max_results"],
                            name=f"{profile['name']}:{target['target_key']}",
                        )
                    )
                    media_query_count += 1
                    if media_query_count >= max_queries_per_media:
                        break
                    if len(seeds) >= max_total_queries:
                        return seeds
                if media_query_count >= max_queries_per_media:
                    break
                if len(seeds) >= max_total_queries:
                    return seeds
            if media_query_count >= max_queries_per_media:
                break
            if len(seeds) >= max_total_queries:
                return seeds

    return seeds


async def _fetch_youtube_candidates() -> list[YouTubeCandidate]:
    raw_config = config_manager.get_config().get("youtube_background")
    youtube_config = raw_config if isinstance(raw_config, dict) else {}

    targets = await _load_youtube_background_targets(youtube_config)
    if not targets:
        logger.info("No pending background-search media items for youtube")
        return []

    search_seeds = _build_youtube_search_seeds(targets, youtube_config=youtube_config)
    if not search_seeds:
        logger.info("No youtube search seeds built from background-search media items")
        return []

    candidates: list[YouTubeCandidate] = []
    for seed in search_seeds:
        try:
            video_ids = await _yt_dlp_extract_video_ids(
                seed.query,
                max_results=seed.max_results,
                region_code=seed.region_code,
            )
        except Exception as exc:
            logger.warning("YouTube yt-dlp query failed for %s (%s): %s", seed.name, seed.query, exc)
            continue

        for video_id in video_ids:
            candidates.append(
                YouTubeCandidate(
                    video_id=video_id.strip(),
                    default_media_type=seed.default_media_type,
                )
            )
    logger.info(
        "YouTube search generated %d candidates from %d queue-driven queries",
        len(candidates),
        len(search_seeds),
    )
    return candidates


def _extract_acestream_candidates_from_payload(
    payload_text: str,
    *,
    default_media_type: str,
    source_name: str,
) -> list[AceStreamCandidate]:
    candidates: list[AceStreamCandidate] = []

    for match in ACESTREAM_ANCHOR_PATTERN.finditer(payload_text):
        content_id = match.group("content_id").lower()
        title = re.sub(r"\s+", " ", (match.group("title") or "").strip())
        candidates.append(
            AceStreamCandidate(
                content_id=content_id,
                info_hash=None,
                title=title or None,
                default_media_type=default_media_type,
                source_name=source_name,
            )
        )

    for match in ACESTREAM_URI_PATTERN.finditer(payload_text):
        content_id = match.group(1).lower()
        candidates.append(
            AceStreamCandidate(
                content_id=content_id,
                info_hash=None,
                title=None,
                default_media_type=default_media_type,
                source_name=source_name,
            )
        )

    for match in ACESTREAM_INFOHASH_PARAM_PATTERN.finditer(payload_text):
        info_hash = match.group(1).lower()
        candidates.append(
            AceStreamCandidate(
                content_id=None,
                info_hash=info_hash,
                title=None,
                default_media_type=default_media_type,
                source_name=source_name,
            )
        )

    return candidates


def _extract_acestream_labeled_server_candidates(
    payload_text: str,
    *,
    default_media_type: str,
    source_name: str,
) -> list[AceStreamCandidate]:
    candidates: list[AceStreamCandidate] = []
    for match in ACESTREAM_LABELED_SERVER_PATTERN.finditer(payload_text):
        content_id = match.group("content_id").lower()
        server_no = str(match.group("server_no") or "").strip()
        raw_label = (match.group("label") or "").strip()
        label = re.sub(r"\s+", " ", raw_label).strip(":- ")
        display_title = f"Server {server_no}: {label}" if server_no else (label or None)
        candidates.append(
            AceStreamCandidate(
                content_id=content_id,
                info_hash=None,
                title=display_title,
                default_media_type=default_media_type,
                source_name=source_name,
                server_slot=server_no or None,
            )
        )
    return candidates


def _clean_acestream_stream_name(raw_name: str) -> tuple[str, str | None]:
    label = re.sub(r"^\s*Server\s*\d+\s*:\s*", "", raw_name or "", flags=re.IGNORECASE).strip()
    label = re.sub(r"/\s*\d+\s*fps\b", "", label, flags=re.IGNORECASE).strip()
    label = re.sub(r"\s+", " ", label)

    resolution_match = RESOLUTION_PATTERN.search(label)
    resolution = resolution_match.group(1).lower() if resolution_match else None

    lower_label = label.lower()
    if "f1tv" in lower_label and resolution:
        return f"F1TV {resolution}", resolution
    if "sky sport f1" in lower_label:
        return "Sky Sport F1", resolution
    if lower_label == "skyf1":
        return "SKYF1", resolution

    dazn_match = re.search(r"\bdanz\s+server\s+(\d+)\b", lower_label)
    if dazn_match:
        return f"DAZN {dazn_match.group(1)}", resolution

    return label or "F1 Live", resolution


def _select_acestream_candidates_for_source(
    candidates: list[AceStreamCandidate],
    *,
    preferred_label_patterns: list[str] | None = None,
    max_candidates: int | None = None,
) -> list[AceStreamCandidate]:
    if not candidates:
        return []

    selected = candidates
    patterns = [pattern.strip().lower() for pattern in preferred_label_patterns or [] if pattern and pattern.strip()]
    for pattern in patterns:
        matched = [candidate for candidate in selected if candidate.title and pattern in candidate.title.lower()]
        if matched:
            selected = matched
            break

    if max_candidates is not None:
        limited = max(1, int(max_candidates))
        return selected[:limited]
    return selected


def _extract_acestream_candidates_from_json_item(
    item: dict[str, Any],
    *,
    source_name: str,
    default_media_type: str,
    content_id_keys: list[str] | None,
    info_hash_keys: list[str] | None,
    title_keys: list[str] | None,
) -> list[AceStreamCandidate]:
    candidates: list[AceStreamCandidate] = []
    content_id_key_list = content_id_keys or ["content_id", "contentId", "acestream_id", "acestreamId", "id"]
    info_hash_key_list = info_hash_keys or ["infohash", "info_hash", "infoHash", "hash"]
    title_key_list = title_keys or ["title", "name", "channel_name", "channelName"]

    content_id = None
    info_hash = None
    title = None

    for key in content_id_key_list:
        content_id = _normalize_hex_40(item.get(key))
        if content_id:
            break
    for key in info_hash_key_list:
        info_hash = _normalize_hex_40(item.get(key))
        if info_hash:
            break
    for key in title_key_list:
        raw_title = item.get(key)
        if isinstance(raw_title, str) and raw_title.strip():
            title = re.sub(r"\s+", " ", raw_title.strip())
            break

    if not content_id and not info_hash:
        for value in item.values():
            if isinstance(value, str):
                possible_content = _normalize_hex_40(value)
                if possible_content:
                    if "acestream://" in value.lower():
                        content_id = possible_content
                    else:
                        info_hash = possible_content
                    break

    if content_id or info_hash:
        candidates.append(
            AceStreamCandidate(
                content_id=content_id,
                info_hash=info_hash,
                title=title,
                default_media_type=default_media_type,
                source_name=source_name,
            )
        )
    return candidates


async def _fetch_acestream_search_api_candidates() -> list[AceStreamCandidate]:
    search_config = config_manager.get_scraper_config("acestream_background", "search_api")
    if not isinstance(search_config, dict):
        return []
    if not bool(search_config.get("enabled", False)):
        return []

    search_url = str(search_config.get("url") or "").strip()
    if not search_url:
        return []

    query_terms = _as_list(search_config.get("queries")) or ACESTREAM_BACKGROUND_DEFAULT_QUERY_TERMS
    query_terms = [str(term).strip() for term in query_terms if str(term).strip()]
    if not query_terms:
        return []

    query_param = str(search_config.get("query_param") or "query")
    page_param = str(search_config.get("page_param") or "page")
    limit_param = str(search_config.get("limit_param") or "limit")
    try:
        requested_max_results = int(search_config.get("max_results") or ACESTREAM_BACKGROUND_MAX_ITEMS_PER_SOURCE)
    except (TypeError, ValueError):
        requested_max_results = ACESTREAM_BACKGROUND_MAX_ITEMS_PER_SOURCE
    try:
        requested_max_pages = int(search_config.get("max_pages") or ACESTREAM_BACKGROUND_MAX_PAGES_PER_SOURCE)
    except (TypeError, ValueError):
        requested_max_pages = ACESTREAM_BACKGROUND_MAX_PAGES_PER_SOURCE

    max_results = min(ACESTREAM_BACKGROUND_MAX_ITEMS_PER_SOURCE, max(1, requested_max_results))
    max_pages = min(ACESTREAM_BACKGROUND_MAX_PAGES_PER_SOURCE, max(1, requested_max_pages))
    default_media_type = _normalize_media_type(search_config.get("media_type"), fallback="movie")
    source_name = str(search_config.get("name") or "acestream_search_api")
    raw_item_keys = search_config.get("item_keys")
    item_keys = raw_item_keys if isinstance(raw_item_keys, dict) else {}
    content_id_keys = _as_list(item_keys.get("content_id"))
    info_hash_keys = _as_list(item_keys.get("info_hash"))
    title_keys = _as_list(item_keys.get("title"))

    headers: dict[str, str] = {}
    configured_headers = search_config.get("headers")
    if isinstance(configured_headers, dict):
        for key, value in configured_headers.items():
            if isinstance(key, str) and isinstance(value, str):
                headers[key] = value

    if settings.acestream_background_search_api_key:
        api_key_header = str(search_config.get("api_key_header") or "").strip()
        api_key_prefix = str(search_config.get("api_key_prefix") or "")
        if api_key_header:
            headers[api_key_header] = f"{api_key_prefix}{settings.acestream_background_search_api_key}"

    extra_params = search_config.get("extra_params")
    default_params: dict[str, Any] = {}
    if isinstance(extra_params, dict):
        for key, value in extra_params.items():
            if isinstance(key, str):
                default_params[key] = value

    timeout = httpx.Timeout(ACESTREAM_BACKGROUND_FETCH_TIMEOUT_SECONDS)
    candidates: list[AceStreamCandidate] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for query in query_terms:
            for page in range(1, max_pages + 1):
                params: dict[str, Any] = dict(default_params)
                params[query_param] = query
                params[page_param] = page
                params[limit_param] = max_results

                api_key_param = str(search_config.get("api_key_param") or "").strip()
                if api_key_param and settings.acestream_background_search_api_key:
                    params[api_key_param] = settings.acestream_background_search_api_key

                try:
                    response = await client.get(search_url, params=params, headers=headers)
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    logger.warning("AceStream search API fetch failed for query=%s page=%s: %s", query, page, exc)
                    break

                items = _extract_json_items(payload)
                if not items:
                    break

                produced = 0
                for item in items:
                    for candidate in _extract_acestream_candidates_from_json_item(
                        item,
                        source_name=source_name,
                        default_media_type=default_media_type,
                        content_id_keys=[str(key) for key in content_id_keys if isinstance(key, str)] or None,
                        info_hash_keys=[str(key) for key in info_hash_keys if isinstance(key, str)] or None,
                        title_keys=[str(key) for key in title_keys if isinstance(key, str)] or None,
                    ):
                        candidates.append(candidate)
                        produced += 1
                        if produced >= max_results:
                            break
                    if produced >= max_results:
                        break
                if produced == 0:
                    break
    return candidates


async def _fetch_acestream_source_candidates() -> list[AceStreamCandidate]:
    source_config = config_manager.get_scraper_config("acestream_background", "sources")
    source_items = _as_list(source_config)
    if not source_items:
        logger.info("No acestream background sources configured")
        return []

    timeout = httpx.Timeout(ACESTREAM_BACKGROUND_FETCH_TIMEOUT_SECONDS)
    max_items_per_source = ACESTREAM_BACKGROUND_MAX_ITEMS_PER_SOURCE
    max_pages_per_source = ACESTREAM_BACKGROUND_MAX_PAGES_PER_SOURCE
    candidates: list[AceStreamCandidate] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for source_item in source_items:
            if not isinstance(source_item, dict):
                continue
            if source_item.get("enabled") is False:
                continue
            configured_urls = [
                str(url).strip()
                for url in _as_list(source_item.get("urls") or source_item.get("url"))
                if str(url).strip()
            ]
            if not configured_urls:
                continue

            source_name = (source_item.get("name") or configured_urls[0]).strip()
            media_type = _normalize_media_type(source_item.get("media_type"), fallback="movie")
            channel_key = _normalize_channel_key(source_item.get("channel_key"))
            channel_key_mode = str(source_item.get("channel_key_mode") or "").strip().lower()
            upsert_by_channel = bool(
                source_item.get("upsert_by_channel", False)
                and (channel_key or channel_key_mode in {"server_label", "server_number"})
            )
            source_channel_name = str(source_item.get("channel_name") or "").strip() or None
            preferred_label_patterns = [
                str(pattern)
                for pattern in _as_list(source_item.get("preferred_label_patterns"))
                if str(pattern).strip()
            ]
            parse_labeled_servers = bool(source_item.get("labeled_server_parser", False))
            target_metadata = source_item.get("target_metadata")
            metadata_config = target_metadata if isinstance(target_metadata, dict) else {}
            metadata_title = str(metadata_config.get("title") or "").strip() or None
            metadata_external_id = str(metadata_config.get("id") or "").strip() or None
            metadata_media_type = _normalize_media_type(metadata_config.get("media_type"), fallback=media_type)
            if str(metadata_config.get("media_type") or "").strip().lower() == "tv":
                metadata_media_type = "tv"
            metadata_poster = str(metadata_config.get("poster") or "").strip() or None
            metadata_country = str(metadata_config.get("country") or "").strip() or None
            metadata_tv_language = str(metadata_config.get("tv_language") or "").strip() or None
            per_source_limit = source_item.get("max_candidates")
            if per_source_limit is None:
                per_source_limit = 1 if upsert_by_channel else max_items_per_source
            try:
                per_source_limit = max(1, min(int(per_source_limit), max_items_per_source))
            except (TypeError, ValueError):
                per_source_limit = 1 if upsert_by_channel else max_items_per_source

            for source_url in configured_urls[:max_pages_per_source]:
                try:
                    response = await client.get(source_url)
                    response.raise_for_status()
                except Exception as exc:
                    logger.warning("AceStream source fetch failed for %s: %s", source_name, exc)
                    continue

                body = response.text or ""
                if parse_labeled_servers:
                    extracted = _extract_acestream_labeled_server_candidates(
                        body,
                        default_media_type=media_type,
                        source_name=source_name,
                    )
                else:
                    extracted = _extract_acestream_candidates_from_payload(
                        body,
                        default_media_type=media_type,
                        source_name=source_name,
                    )

                selected_candidates = _select_acestream_candidates_for_source(
                    extracted,
                    preferred_label_patterns=preferred_label_patterns,
                    max_candidates=per_source_limit,
                )
                for candidate in selected_candidates:
                    computed_channel_key = channel_key
                    if channel_key_mode == "server_label":
                        computed_channel_key = _normalize_channel_key(f"{source_name}:{candidate.title or 'stream'}")
                    elif channel_key_mode == "server_number":
                        slot = candidate.server_slot or "0"
                        computed_channel_key = _normalize_channel_key(f"{source_name}:server:{slot}")

                    candidate.channel_key = computed_channel_key
                    candidate.upsert_by_channel = bool(upsert_by_channel and computed_channel_key)
                    if source_channel_name:
                        candidate.title = source_channel_name
                    candidate.metadata_title = metadata_title
                    candidate.metadata_external_id = metadata_external_id
                    candidate.metadata_media_type = metadata_media_type
                    candidate.metadata_poster = metadata_poster
                    candidate.metadata_country = metadata_country
                    candidate.metadata_tv_language = metadata_tv_language
                    candidates.append(candidate)

    return candidates


async def _fetch_acestream_candidates() -> list[AceStreamCandidate]:
    api_candidates = await _fetch_acestream_search_api_candidates()
    source_candidates = await _fetch_acestream_source_candidates()
    return [*api_candidates, *source_candidates]


def _build_telegram_candidate(raw_item: dict[str, Any]) -> TelegramCandidate | None:
    chat_id = str(raw_item.get("chat_id") or "").strip()
    message_id = raw_item.get("message_id")
    if not chat_id or not isinstance(message_id, int):
        return None

    inferred_title = str(raw_item.get("inferred_title") or raw_item.get("name") or "").strip()
    if not inferred_title:
        return None

    return TelegramCandidate(
        chat_id=chat_id,
        message_id=message_id,
        chat_username=raw_item.get("chat_username"),
        file_unique_id=raw_item.get("file_unique_id"),
        file_id=raw_item.get("file_id"),
        file_name=raw_item.get("file_name"),
        mime_type=raw_item.get("mime_type"),
        size=raw_item.get("size"),
        posted_at=raw_item.get("posted_at"),
        caption=raw_item.get("caption"),
        name=str(raw_item.get("name") or inferred_title),
        resolution=raw_item.get("resolution"),
        codec=raw_item.get("codec"),
        quality=raw_item.get("quality"),
        bit_depth=raw_item.get("bit_depth"),
        uploader=raw_item.get("uploader"),
        release_group=raw_item.get("release_group"),
        is_remastered=bool(raw_item.get("is_remastered")),
        is_proper=bool(raw_item.get("is_proper")),
        is_repack=bool(raw_item.get("is_repack")),
        is_extended=bool(raw_item.get("is_extended")),
        is_dubbed=bool(raw_item.get("is_dubbed")),
        is_subbed=bool(raw_item.get("is_subbed")),
        season_number=raw_item.get("season_number"),
        episode_number=raw_item.get("episode_number"),
        episode_end=raw_item.get("episode_end"),
        languages=[str(lang) for lang in raw_item.get("languages", []) if isinstance(lang, str)],
        inferred_title=inferred_title,
        inferred_year=_coerce_year(raw_item.get("inferred_year")),
        imdb_id=raw_item.get("imdb_id"),
    )


T = TypeVar("T")


def _iter_unique(items: Iterable[T], key_getter):
    seen: set[str] = set()
    for item in items:
        key = key_getter(item)
        if not key or key in seen:
            continue
        seen.add(key)
        yield item


async def _load_cached_telegram_indexer_channels(cache_key: str) -> list[str]:
    try:
        cached_values = await REDIS_ASYNC_CLIENT.smembers(cache_key)
    except Exception:
        return []
    channels: list[str] = []
    for value in cached_values:
        if isinstance(value, bytes):
            normalized = value.decode("utf-8", errors="ignore").strip()
        else:
            normalized = str(value).strip()
        if normalized:
            channels.append(normalized)
    return channels


async def _cache_telegram_indexer_channels(cache_key: str, channels: list[str], ttl_hours: int) -> None:
    if not channels:
        return
    await REDIS_ASYNC_CLIENT.sadd(cache_key, *channels)
    await REDIS_ASYNC_CLIENT.expire(
        cache_key,
        int(timedelta(hours=ttl_hours).total_seconds()),
    )


async def _discover_telegram_channels_from_indexers() -> list[str]:
    if not settings.telegram_background_use_indexers:
        return []

    indexer_config = config_manager.get_scraper_config("telegram_background", "indexers")
    indexers = [item for item in _as_list(indexer_config) if isinstance(item, dict) and item.get("enabled", False)]
    if not indexers:
        return []

    discovery_config = config_manager.get_scraper_config("telegram_background", "discovery")
    discovery = discovery_config if isinstance(discovery_config, dict) else {}

    query_terms = [str(term).strip() for term in _as_list(discovery.get("query_terms")) if str(term).strip()]
    if not query_terms:
        query_terms = TELEGRAM_BACKGROUND_INDEXER_QUERY_TERMS

    try:
        max_channels = max(1, int(discovery.get("max_channels") or TELEGRAM_BACKGROUND_INDEXER_MAX_CHANNELS))
    except (TypeError, ValueError):
        max_channels = TELEGRAM_BACKGROUND_INDEXER_MAX_CHANNELS
    try:
        min_members = max(0, int(discovery.get("min_members") or TELEGRAM_BACKGROUND_INDEXER_MIN_MEMBERS))
    except (TypeError, ValueError):
        min_members = TELEGRAM_BACKGROUND_INDEXER_MIN_MEMBERS
    try:
        timeout_seconds = max(5, int(discovery.get("timeout_seconds") or TELEGRAM_BACKGROUND_INDEXER_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        timeout_seconds = TELEGRAM_BACKGROUND_INDEXER_TIMEOUT_SECONDS
    try:
        cache_ttl_hours = max(1, int(discovery.get("cache_ttl_hours") or TELEGRAM_BACKGROUND_INDEXER_CACHE_TTL_HOURS))
    except (TypeError, ValueError):
        cache_ttl_hours = TELEGRAM_BACKGROUND_INDEXER_CACHE_TTL_HOURS

    timeout = httpx.Timeout(timeout_seconds)
    discovered: list[str] = []
    seen: set[str] = set()
    cache_key = "telegram_background_scraper:indexer_channels"

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for indexer in indexers:
            search_url = str(indexer.get("search_url") or "").strip()
            if not search_url:
                continue

            query_param = str(indexer.get("query_param") or "query")
            limit_param = str(indexer.get("limit_param") or "limit")
            page_param = str(indexer.get("page_param") or "page")
            limit_value = indexer.get("limit") or 25
            try:
                limit_value = max(1, min(int(limit_value), 100))
            except (TypeError, ValueError):
                limit_value = 25
            max_pages = indexer.get("max_pages") or 1
            try:
                max_pages = max(1, min(int(max_pages), 10))
            except (TypeError, ValueError):
                max_pages = 1

            default_params: dict[str, Any] = {}
            extra_params = indexer.get("extra_params")
            if isinstance(extra_params, dict):
                for key, value in extra_params.items():
                    if isinstance(key, str):
                        default_params[key] = value

            type_param = str(indexer.get("type_param") or "").strip()
            type_value = str(indexer.get("type_value") or "").strip()
            if type_param and type_value:
                default_params[type_param] = type_value

            headers: dict[str, str] = {}
            configured_headers = indexer.get("headers")
            if isinstance(configured_headers, dict):
                for key, value in configured_headers.items():
                    if isinstance(key, str) and isinstance(value, str):
                        headers[key] = value

            if settings.telegram_background_indexer_api_key:
                api_key_header = str(indexer.get("api_key_header") or "").strip()
                api_key_param = str(indexer.get("api_key_param") or "").strip()
                if api_key_header:
                    headers[api_key_header] = settings.telegram_background_indexer_api_key
                elif api_key_param:
                    default_params[api_key_param] = settings.telegram_background_indexer_api_key

            member_count_keys = [str(key) for key in _as_list(indexer.get("member_count_keys")) if isinstance(key, str)]
            channel_keys = [str(key) for key in _as_list(indexer.get("channel_keys")) if isinstance(key, str)]

            for query_term in query_terms:
                for page in range(1, max_pages + 1):
                    params = dict(default_params)
                    params[query_param] = query_term
                    params[limit_param] = limit_value
                    params[page_param] = page
                    try:
                        response = await client.get(search_url, params=params, headers=headers)
                        response.raise_for_status()
                        payload = response.json()
                    except Exception as exc:
                        logger.warning(
                            "Telegram indexer discovery failed indexer=%s query=%s page=%s: %s",
                            indexer.get("name") or search_url,
                            query_term,
                            page,
                            exc,
                        )
                        break

                    extracted_items = _extract_json_items(payload)
                    if not extracted_items:
                        break

                    for item in extracted_items:
                        member_count = _extract_member_count(item, member_count_keys or None)
                        if member_count is not None and member_count < min_members:
                            continue
                        channels = _extract_channel_identifiers(item, channel_keys or None)
                        for channel in channels:
                            if channel in seen:
                                continue
                            seen.add(channel)
                            discovered.append(channel)
                            if len(discovered) >= max_channels:
                                await _cache_telegram_indexer_channels(cache_key, discovered, cache_ttl_hours)
                                return discovered

    if discovered:
        await _cache_telegram_indexer_channels(cache_key, discovered, cache_ttl_hours)
        return discovered

    cached = await _load_cached_telegram_indexer_channels(cache_key)
    return cached[:max_channels]


async def _get_acestream_stream_by_source(
    session,
    *,
    source_identifier: str,
    channel_key: str | None = None,
) -> tuple[AceStreamStream, Stream] | None:
    query = (
        select(AceStreamStream, Stream)
        .join(Stream, AceStreamStream.stream_id == Stream.id)
        .where(
            Stream.stream_type == StreamType.ACESTREAM,
            Stream.source == source_identifier,
        )
    )
    if channel_key:
        query = query.where(Stream.release_group == channel_key)
    result = await session.exec(query)
    row = result.first()
    if not row:
        return None
    return row


async def _ensure_stream_media_link(
    session,
    *,
    stream_id: int,
    target_media_id: int,
) -> None:
    link_result = await session.exec(select(StreamMediaLink).where(StreamMediaLink.stream_id == stream_id))
    current_link = link_result.first()
    if not current_link:
        await crud.link_stream_to_media(session, stream_id, target_media_id)
        return
    if current_link.media_id == target_media_id:
        return
    await crud.unlink_stream_from_media(session, stream_id, current_link.media_id)
    await crud.link_stream_to_media(session, stream_id, target_media_id)


@actor(time_limit=60 * 60 * 1000, priority=5, queue_name="scrapy")
async def run_youtube_background_scraper(**kwargs):
    if not settings.is_scrap_from_youtube_background:
        logger.info("YouTube background scraping is disabled")
        return

    tracker = ProcessedItemTracker("youtube_background_scraper:processed_items", YOUTUBE_BACKGROUND_DEDUPE_TTL_HOURS)
    metrics = {"processed": 0, "created": 0, "skipped": 0, "errors": 0}
    candidates = await _fetch_youtube_candidates()

    async with get_background_session() as session:
        for candidate in _iter_unique(candidates, lambda item: item.video_id):
            item_key = f"youtube:{candidate.video_id}"
            if await tracker.is_processed(item_key):
                metrics["skipped"] += 1
                continue
            metrics["processed"] += 1

            try:
                existing = await session.exec(select(YouTubeStream).where(YouTubeStream.video_id == candidate.video_id))
                if existing.first():
                    await tracker.mark_processed(item_key)
                    metrics["skipped"] += 1
                    continue

                info = await analyze_youtube_video(candidate.video_id)
                if _is_adult_title(info.title):
                    metrics["skipped"] += 1
                    await tracker.mark_processed(item_key)
                    continue

                parsed = _parse_title_data(info.title)
                parsed_title = str(parsed.get("title") or info.title).strip()
                parsed_year = _coerce_year(parsed.get("year"))
                media_type = _normalize_media_type(
                    "series" if parsed.get("seasons") or parsed.get("episodes") else candidate.default_media_type,
                    fallback=candidate.default_media_type,
                )
                if _is_blocked_youtube_title(info.title):
                    metrics["skipped"] += 1
                    await tracker.mark_processed(item_key)
                    continue
                if not _passes_youtube_duration_gate(info.duration, media_type):
                    metrics["skipped"] += 1
                    await tracker.mark_processed(item_key)
                    continue
                metadata = await _resolve_metadata(
                    session=session,
                    title=parsed_title,
                    media_type=media_type,
                    year=parsed_year,
                    external_id=_tmp_external_id("youtube", candidate.video_id),
                    poster=info.thumbnail,
                )
                if not metadata:
                    metrics["skipped"] += 1
                    await tracker.mark_processed(item_key)
                    continue

                await crud.create_youtube_stream(
                    session,
                    video_id=candidate.video_id,
                    name=info.title or parsed_title,
                    media_id=metadata.id,
                    source="youtube_background",
                    is_live=info.is_live,
                    geo_restriction_type=info.geo_restriction_type,
                    geo_restriction_countries=info.geo_restriction_countries,
                    uploader=info.channel,
                )
                await session.commit()
                await tracker.mark_processed(item_key)
                metrics["created"] += 1
            except IntegrityError:
                await session.rollback()
                await tracker.mark_processed(item_key)
                metrics["skipped"] += 1
            except Exception as exc:
                await session.rollback()
                metrics["errors"] += 1
                logger.exception("Failed processing youtube candidate %s: %s", candidate.video_id, exc)

    logger.info("YouTube background scraper finished: %s", metrics)


@actor(time_limit=60 * 60 * 1000, priority=5, queue_name="scrapy")
async def run_acestream_background_scraper(**kwargs):
    if not settings.is_scrap_from_acestream_background:
        logger.info("AceStream background scraping is disabled")
        return

    tracker = ProcessedItemTracker(
        "acestream_background_scraper:processed_items",
        ACESTREAM_BACKGROUND_DEDUPE_TTL_HOURS,
    )
    metrics = {"processed": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}
    candidates = await _fetch_acestream_candidates()

    async with get_background_session() as session:
        for candidate in _iter_unique(
            candidates,
            lambda item: (
                f"channel:{item.channel_key}:{item.content_id or ''}:{item.info_hash or ''}"
                if item.upsert_by_channel and item.channel_key
                else f"{item.content_id or ''}:{item.info_hash or ''}"
            ),
        ):
            channel_identity = candidate.channel_key if candidate.upsert_by_channel else None
            item_key = (
                f"acestream:channel:{channel_identity}:{candidate.content_id or ''}:{candidate.info_hash or ''}"
                if channel_identity
                else f"acestream:{candidate.content_id or ''}:{candidate.info_hash or ''}"
            )
            if await tracker.is_processed(item_key):
                metrics["skipped"] += 1
                continue
            metrics["processed"] += 1

            try:
                source_identifier = _acestream_source_identifier(candidate.source_name, channel_identity)
                existing = await crud.get_acestream_by_identifier(
                    session,
                    content_id=candidate.content_id,
                    info_hash=candidate.info_hash,
                )

                fallback_title_id = candidate.content_id or candidate.info_hash or "stream"
                raw_title = (candidate.title or f"AceStream {fallback_title_id[:10]}").strip()
                title, resolution = _clean_acestream_stream_name(raw_title)
                if _is_adult_title(title):
                    metrics["skipped"] += 1
                    await tracker.mark_processed(item_key)
                    continue

                if channel_identity:
                    existing_channel = await _get_acestream_stream_by_source(
                        session,
                        source_identifier=source_identifier,
                        channel_key=channel_identity,
                    )
                    if not existing_channel:
                        legacy_channel_source_identifier = _legacy_acestream_source_identifier(
                            candidate.source_name,
                            channel_identity,
                        )
                        existing_channel = await _get_acestream_stream_by_source(
                            session,
                            source_identifier=legacy_channel_source_identifier,
                        )
                    if not existing_channel:
                        intermediate_channel_source_identifier = f"acestream_bg:{channel_identity}"
                        existing_channel = await _get_acestream_stream_by_source(
                            session,
                            source_identifier=intermediate_channel_source_identifier,
                        )
                    if not existing_channel:
                        # Backward-compat: migrate previously created source-based row
                        # into channel-based upsert identity on first successful refresh.
                        legacy_source_identifier = _legacy_acestream_source_identifier(candidate.source_name, None)
                        existing_channel = await _get_acestream_stream_by_source(
                            session,
                            source_identifier=legacy_source_identifier,
                        )
                    if not existing_channel:
                        intermediate_source_identifier = f"acestream_bg:{candidate.source_name}"
                        existing_channel = await _get_acestream_stream_by_source(
                            session,
                            source_identifier=intermediate_source_identifier,
                        )
                    metadata_item_key = f"channel:{channel_identity}"
                else:
                    existing_channel = None
                    metadata_item_key = item_key

                if existing_channel:
                    acestream_stream, stream = existing_channel
                    relinked = False
                    if _normalize_media_type(candidate.metadata_media_type, fallback="") == "tv":
                        tv_metadata_payload: dict[str, Any] = {
                            "id": candidate.metadata_external_id or _tmp_external_id("acestream", metadata_item_key),
                            "title": str(candidate.metadata_title or title).strip(),
                        }
                        if candidate.metadata_poster:
                            tv_metadata_payload["poster"] = candidate.metadata_poster
                        if candidate.metadata_country:
                            tv_metadata_payload["country"] = candidate.metadata_country
                        if candidate.metadata_tv_language:
                            tv_metadata_payload["tv_language"] = candidate.metadata_tv_language
                        target_media_id = await crud.save_tv_channel_metadata(session, tv_metadata_payload)
                        link_result = await session.exec(
                            select(StreamMediaLink).where(StreamMediaLink.stream_id == stream.id)
                        )
                        existing_link = link_result.first()
                        if not existing_link or existing_link.media_id != target_media_id:
                            await _ensure_stream_media_link(
                                session,
                                stream_id=stream.id,
                                target_media_id=target_media_id,
                            )
                            relinked = True
                    if (
                        acestream_stream.content_id == candidate.content_id
                        and acestream_stream.info_hash == candidate.info_hash
                        and stream.name == title
                        and stream.resolution == resolution
                        and not relinked
                    ):
                        await tracker.mark_processed(item_key)
                        metrics["skipped"] += 1
                        continue

                    acestream_stream.content_id = candidate.content_id
                    acestream_stream.info_hash = candidate.info_hash
                    stream.name = title
                    stream.resolution = resolution
                    stream.source = source_identifier
                    stream.release_group = channel_identity
                    await session.commit()
                    await tracker.mark_processed(item_key)
                    metrics["updated"] += 1
                    continue

                if existing and not channel_identity:
                    metrics["skipped"] += 1
                    await tracker.mark_processed(item_key)
                    continue

                parsed = _parse_title_data(title)
                parsed_title = str(parsed.get("title") or title).strip()
                parsed_year = _coerce_year(parsed.get("year"))
                inferred_media_type = "series" if parsed.get("seasons") or parsed.get("episodes") else "movie"
                configured_media_type = candidate.metadata_media_type or candidate.default_media_type
                media_type = _normalize_media_type(configured_media_type, fallback=inferred_media_type)
                metadata_title = str(candidate.metadata_title or parsed_title).strip()
                metadata_year = None if media_type == "tv" else parsed_year
                metadata_external_id = candidate.metadata_external_id or _tmp_external_id(
                    "acestream", metadata_item_key
                )
                metadata_extra_fields: dict[str, Any] = {}
                if media_type == "tv":
                    if candidate.metadata_country:
                        metadata_extra_fields["country"] = candidate.metadata_country
                    if candidate.metadata_tv_language:
                        metadata_extra_fields["tv_language"] = candidate.metadata_tv_language
                if media_type == "tv":
                    tv_metadata_payload: dict[str, Any] = {
                        "id": metadata_external_id,
                        "title": metadata_title,
                    }
                    if candidate.metadata_poster:
                        tv_metadata_payload["poster"] = candidate.metadata_poster
                    if candidate.metadata_country:
                        tv_metadata_payload["country"] = candidate.metadata_country
                    if candidate.metadata_tv_language:
                        tv_metadata_payload["tv_language"] = candidate.metadata_tv_language
                    media_id = await crud.save_tv_channel_metadata(session, tv_metadata_payload)
                else:
                    metadata = await _resolve_metadata(
                        session=session,
                        title=metadata_title,
                        media_type=media_type,
                        year=metadata_year,
                        external_id=metadata_external_id,
                        poster=candidate.metadata_poster,
                        extra_fields=metadata_extra_fields or None,
                    )
                    if not metadata:
                        metrics["skipped"] += 1
                        await tracker.mark_processed(item_key)
                        continue
                    media_id = metadata.id

                await crud.create_acestream_stream(
                    session,
                    name=title,
                    media_id=media_id,
                    content_id=candidate.content_id,
                    info_hash=candidate.info_hash,
                    source=source_identifier,
                    resolution=resolution,
                    release_group=channel_identity,
                )
                await session.commit()
                await tracker.mark_processed(item_key)
                metrics["created"] += 1
            except IntegrityError:
                await session.rollback()
                await tracker.mark_processed(item_key)
                metrics["skipped"] += 1
            except Exception as exc:
                await session.rollback()
                metrics["errors"] += 1
                logger.exception(
                    "Failed processing acestream candidate content_id=%s info_hash=%s: %s",
                    candidate.content_id,
                    candidate.info_hash,
                    exc,
                )

    logger.info("AceStream background scraper finished: %s", metrics)


@actor(time_limit=60 * 60 * 1000, priority=5, queue_name="scrapy")
async def run_telegram_background_scraper(**kwargs):
    if not settings.is_scrap_from_telegram_background:
        logger.info("Telegram background scraping is disabled")
        return

    tracker = ProcessedItemTracker(
        "telegram_background_scraper:processed_items",
        TELEGRAM_BACKGROUND_DEDUPE_TTL_HOURS,
    )
    metrics = {"processed": 0, "created": 0, "skipped": 0, "errors": 0}

    discovered_channels = await _discover_telegram_channels_from_indexers()
    if discovered_channels:
        logger.info("Telegram background discovery found %s channels from indexers", len(discovered_channels))

    max_channels_config = config_manager.get_scraper_config("telegram_background", "max_channels_per_run")
    try:
        max_channels_per_run = max(1, int(max_channels_config or TELEGRAM_BACKGROUND_MAX_CHANNELS_PER_RUN))
    except (TypeError, ValueError):
        max_channels_per_run = TELEGRAM_BACKGROUND_MAX_CHANNELS_PER_RUN

    raw_candidates = await telegram_scraper.scrape_feed_candidates(
        max_channels=max_channels_per_run,
        extra_channels=discovered_channels,
    )
    candidates = [
        candidate for candidate in (_build_telegram_candidate(item) for item in raw_candidates) if candidate is not None
    ]

    async with get_background_session() as session:
        for candidate in _iter_unique(candidates, lambda item: item.dedupe_key):
            if await tracker.is_processed(candidate.dedupe_key):
                metrics["skipped"] += 1
                continue
            metrics["processed"] += 1

            try:
                if await crud.telegram_stream_exists(
                    session,
                    chat_id=candidate.chat_id,
                    message_id=candidate.message_id,
                ):
                    await tracker.mark_processed(candidate.dedupe_key)
                    metrics["skipped"] += 1
                    continue

                if _is_adult_title(candidate.inferred_title):
                    await tracker.mark_processed(candidate.dedupe_key)
                    metrics["skipped"] += 1
                    continue

                media_type = candidate.inferred_media_type
                metadata_external_id = candidate.imdb_id or _tmp_external_id("telegram", candidate.dedupe_key)
                metadata = await _resolve_metadata(
                    session=session,
                    title=candidate.inferred_title,
                    media_type=media_type,
                    year=candidate.inferred_year,
                    external_id=metadata_external_id,
                )
                if not metadata:
                    await tracker.mark_processed(candidate.dedupe_key)
                    metrics["skipped"] += 1
                    continue

                await crud.create_telegram_stream(
                    session,
                    chat_id=candidate.chat_id,
                    message_id=candidate.message_id,
                    name=candidate.name,
                    media_id=metadata.id,
                    chat_username=candidate.chat_username,
                    file_id=candidate.file_id,
                    file_unique_id=candidate.file_unique_id,
                    file_name=candidate.file_name,
                    mime_type=candidate.mime_type,
                    size=candidate.size,
                    posted_at=candidate.posted_at,
                    source="telegram_background",
                    resolution=candidate.resolution,
                    codec=candidate.codec,
                    quality=candidate.quality,
                    bit_depth=candidate.bit_depth,
                    uploader=candidate.uploader,
                    release_group=candidate.release_group,
                    is_remastered=candidate.is_remastered,
                    is_proper=candidate.is_proper,
                    is_repack=candidate.is_repack,
                    is_extended=candidate.is_extended,
                    is_dubbed=candidate.is_dubbed,
                    is_subbed=candidate.is_subbed,
                    season_number=candidate.season_number,
                    episode_number=candidate.episode_number,
                    episode_end=candidate.episode_end,
                )
                await session.commit()
                await tracker.mark_processed(candidate.dedupe_key)
                metrics["created"] += 1
            except IntegrityError:
                await session.rollback()
                await tracker.mark_processed(candidate.dedupe_key)
                metrics["skipped"] += 1
            except Exception as exc:
                await session.rollback()
                metrics["errors"] += 1
                logger.exception(
                    "Failed processing telegram candidate %s: %s",
                    candidate.dedupe_key,
                    exc,
                )

    logger.info("Telegram background scraper finished: %s", metrics)
