"""
Telegram Content Scraper

Scrapes video content from Telegram channels/groups and stores them as TelegramStream records.

Currently supported content types:
- Direct video files uploaded to channels

Planned content types (detection implemented, processing NOT implemented):
- Magnet links shared in messages (detected but skipped)
- NZB links shared in messages (detected but skipped)

Note: To contribute magnet links, NZB URLs, and other content types, use the
MediaFusion Telegram bot's interactive contribution flow instead of channel scraping.
The bot provides a full wizard experience for contributing all content types.

Metadata matching:
- PTT parsing of filenames
- Caption parsing for IMDb IDs
- Title/year extraction from captions

Uses Telethon library for Telegram MTProto API access.
"""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from typing import Any

import PTT

from db import crud
from db.config import settings
from db.database import get_background_session
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import MetadataData, UserData
from db.schemas.media import TelegramStreamData
from utils.parser import calculate_max_similarity_ratio, is_contain_18_plus_keywords
from utils.runtime_const import TELEGRAM_SEARCH_TTL

# Optional dependency - Telethon for Telegram scraping
try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

    TELETHON_AVAILABLE = True
except ImportError:
    TelegramClient = None
    StringSession = None
    DocumentAttributeFilename = None
    DocumentAttributeVideo = None
    TELETHON_AVAILABLE = False

logger = logging.getLogger(__name__)

# Regex patterns for content extraction
IMDB_PATTERN = re.compile(r"tt\d{7,8}")
MAGNET_PATTERN = re.compile(r"magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}[^\s]*")
NZB_PATTERN = re.compile(r"https?://[^\s]+\.nzb(?:\?[^\s]*)?")
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".webm", ".mov", ".flv", ".wmv", ".m4v"}


@dataclass
class TelegramScraperMetrics:
    """Metrics for Telegram scraping operations."""

    scraper_name: str = "telegram"
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    channels_scraped: int = 0
    messages_processed: int = 0
    videos_found: int = 0
    magnets_found: int = 0
    nzbs_found: int = 0
    streams_created: int = 0
    errors: int = 0
    skip_reasons: dict = field(default_factory=dict)

    def start(self):
        self.start_time = datetime.now()
        self.end_time = None
        self.channels_scraped = 0
        self.messages_processed = 0
        self.videos_found = 0
        self.magnets_found = 0
        self.nzbs_found = 0
        self.streams_created = 0
        self.errors = 0
        self.skip_reasons = {}

    def stop(self):
        self.end_time = datetime.now()

    def record_skip(self, reason: str):
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1

    def get_summary(self) -> dict:
        duration = (self.end_time or datetime.now()) - self.start_time
        return {
            "scraper_name": self.scraper_name,
            "duration_seconds": duration.total_seconds(),
            "channels_scraped": self.channels_scraped,
            "messages_processed": self.messages_processed,
            "content_found": {
                "videos": self.videos_found,
                "magnets": self.magnets_found,
                "nzbs": self.nzbs_found,
            },
            "streams_created": self.streams_created,
            "errors": self.errors,
            "skip_reasons": self.skip_reasons,
        }


class TelegramScraper:
    """
    Telegram content scraper using Telethon.

    Supports:
    - Public channel scraping by username
    - User-configured channel lists
    - Admin-configured global channels
    - Bot-forwarded content handling
    """

    CACHE_KEY_PREFIX = "telegram_scraper"
    DEFAULT_TTL = TELEGRAM_SEARCH_TTL

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.metrics = TelegramScraperMetrics()
        self._client = None
        self._client_lock = asyncio.Lock()

    async def get_client(self) -> TelegramClient | None:
        """Get or create Telethon client instance."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    if not TELETHON_AVAILABLE:
                        self.logger.error("Telethon not installed. Install with: pip install telethon")
                        return None

                    if not all(
                        [
                            settings.telegram_api_id,
                            settings.telegram_api_hash,
                            settings.telegram_session_string,
                        ]
                    ):
                        self.logger.warning(
                            "Telegram API credentials not configured. "
                            "Set telegram_api_id, telegram_api_hash, and telegram_session_string."
                        )
                        return None

                    try:
                        session = StringSession(settings.telegram_session_string)
                        self._client = TelegramClient(
                            session,
                            settings.telegram_api_id,
                            settings.telegram_api_hash,
                        )
                        await self._client.connect()

                        if not await self._client.is_user_authorized():
                            self.logger.error(
                                "Telethon session is not authorized. Please generate a new session string."
                            )
                            await self._client.disconnect()
                            self._client = None
                            return None

                        self.logger.info("Telethon client connected successfully")
                    except Exception as e:
                        self.logger.exception(f"Failed to initialize Telethon client: {e}")
                        self._client = None
                        return None
        return self._client

    async def close(self):
        """Close the Telethon client."""
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @staticmethod
    def cache(ttl: int = None):
        """Decorator for caching scraping status using Redis Sorted Sets."""

        def decorator(func):
            @wraps(func)
            async def wrapper(self, *args, **kwargs):
                # Resolve TTL at runtime to avoid class reference issues
                cache_ttl = ttl or self.DEFAULT_TTL
                cache_key = self._get_cache_key(*args, **kwargs)
                current_time = int(datetime.now().timestamp())

                # Check if recently scraped
                score = await REDIS_ASYNC_CLIENT.zscore(self.CACHE_KEY_PREFIX, cache_key)
                if score and current_time - score < cache_ttl:
                    self.logger.debug(f"Skipping {cache_key} - recently scraped")
                    return []

                result = await func(self, *args, **kwargs)

                # Mark as scraped
                await REDIS_ASYNC_CLIENT.zadd(self.CACHE_KEY_PREFIX, {cache_key: current_time})

                return result

            return wrapper

        return decorator

    def _get_cache_key(
        self,
        user_data: UserData | None,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
        **kwargs,
    ) -> str:
        """Generate cache key for scraping operations."""
        canonical_id = metadata.get_canonical_id()
        if catalog_type == "movie":
            return f"telegram:{catalog_type}:{canonical_id}"
        return f"telegram:{catalog_type}:{canonical_id}:{season}:{episode}"

    @cache()
    async def scrape_and_parse(
        self,
        user_data: UserData | None,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TelegramStreamData]:
        """
        Main entry point for Telegram scraping.

        Scrapes configured channels and returns matching content.
        """
        self.metrics.start()
        results = []

        try:
            # Get channels to scrape
            channels = self._get_channels_to_scrape(user_data)
            if not channels:
                self.logger.info("No Telegram channels configured for scraping")
                return results

            self.logger.info(f"Scraping {len(channels)} Telegram channels for {metadata.title} ({catalog_type})")

            # Scrape each channel
            for channel in channels:
                try:
                    async for stream in self._scrape_channel(
                        channel=channel,
                        metadata=metadata,
                        catalog_type=catalog_type,
                        season=season,
                        episode=episode,
                    ):
                        results.append(stream)
                        self.metrics.streams_created += 1
                    self.metrics.channels_scraped += 1
                except Exception as e:
                    self.logger.exception(f"Error scraping channel {channel}: {e}")
                    self.metrics.errors += 1

        except Exception as e:
            self.logger.exception(f"Error during Telegram scraping: {e}")
            self.metrics.errors += 1
        finally:
            self.metrics.stop()
            self._log_metrics()

        return results

    def _get_channels_to_scrape(self, user_data: UserData | None) -> list[str]:
        """Get list of channels to scrape based on configuration."""
        channels = []

        # Add admin-configured global channels
        if settings.telegram_scraping_channels:
            channels.extend(settings.telegram_scraping_channels)

        # Add user-configured channels
        if user_data and user_data.telegram_config:
            tg_config = user_data.telegram_config
            if tg_config.enabled:
                for channel_config in tg_config.channels:
                    if channel_config.enabled:
                        # Use username or chat_id
                        channel_id = channel_config.username or channel_config.chat_id
                        if channel_id and channel_id not in channels:
                            channels.append(channel_id)

        return channels

    async def _scrape_channel(
        self,
        channel: str,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> AsyncGenerator[TelegramStreamData, None]:
        """Scrape a single Telegram channel for matching content."""
        client = await self.get_client()
        if not client:
            self.logger.warning("Telegram client not available")
            return

        try:
            # Resolve channel entity
            try:
                entity = await client.get_entity(channel)
                chat_id = str(entity.id)
                chat_username = getattr(entity, "username", None)
                chat_title = getattr(entity, "title", None) or channel
            except Exception as e:
                self.logger.error(f"Failed to resolve channel {channel}: {e}")
                self.metrics.record_skip("channel_resolve_failed")
                return

            self.logger.info(f"Scraping channel: {chat_title} (ID: {chat_id})")

            # Iterate through recent messages
            message_count = 0
            async for message in client.iter_messages(
                entity,
                limit=settings.telegram_scrape_message_limit,
            ):
                message_count += 1
                self.metrics.messages_processed += 1

                # Process message content
                stream = await self._process_message(
                    message=message,
                    chat_id=chat_id,
                    chat_username=chat_username,
                    metadata=metadata,
                    catalog_type=catalog_type,
                    season=season,
                    episode=episode,
                )

                if stream:
                    yield stream

            self.logger.info(f"Processed {message_count} messages from {channel}")

        except Exception as e:
            self.logger.exception(f"Error scraping channel {channel}: {e}")
            raise

    async def _process_message(
        self,
        message: Any,
        chat_id: str,
        chat_username: str | None,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> TelegramStreamData | None:
        """Process a single Telegram message for content."""
        try:
            # Check for video/document content
            if message.video or message.document:
                return await self._process_video_message(
                    message=message,
                    chat_id=chat_id,
                    chat_username=chat_username,
                    metadata=metadata,
                    catalog_type=catalog_type,
                    season=season,
                    episode=episode,
                )

            # Check caption/text for magnet or NZB links
            text = message.message or ""
            if text:
                # Check for magnet links
                magnet_match = MAGNET_PATTERN.search(text)
                if magnet_match:
                    self.metrics.magnets_found += 1
                    # Magnet links should be processed as torrents
                    # This would need to call torrent scraper logic
                    self.logger.debug(f"Found magnet link in message {message.id}")
                    # For now, skip magnet handling - could be added later

                # Check for NZB links
                nzb_match = NZB_PATTERN.search(text)
                if nzb_match:
                    self.metrics.nzbs_found += 1
                    self.logger.debug(f"Found NZB link in message {message.id}")
                    # For now, skip NZB handling - could be added later

            return None

        except Exception as e:
            self.logger.debug(f"Error processing message {message.id}: {e}")
            return None

    async def _process_video_message(
        self,
        message: Any,
        chat_id: str,
        chat_username: str | None,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> TelegramStreamData | None:
        """Process a video/document message."""
        try:
            # Get file info from Telethon message
            media = message.video or message.document
            if not media:
                return None

            # Extract file attributes
            # Note: Telethon doesn't use file_id like Bot API - we use chat_id + message_id for streaming
            file_unique_id = str(media.id)  # Use document/video ID as unique identifier
            file_name = None
            mime_type = getattr(media, "mime_type", None) or "application/octet-stream"
            size = getattr(media, "size", None)

            # Get filename from attributes
            if hasattr(media, "attributes"):
                for attr in media.attributes:
                    if DocumentAttributeFilename and isinstance(attr, DocumentAttributeFilename):
                        file_name = attr.file_name
                        break

            # Default filename if not found
            if not file_name:
                if message.video:
                    file_name = f"video_{message.id}.mp4"
                else:
                    file_name = f"file_{message.id}"

            # Check if document is a video file
            is_video = message.video is not None
            if not is_video and message.document:
                ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
                if ext not in VIDEO_EXTENSIONS and not mime_type.startswith("video/"):
                    self.metrics.record_skip("not_video")
                    return None

            self.metrics.videos_found += 1

            # Check file size (skip small files)
            if size and size < settings.min_scraping_video_size:
                self.metrics.record_skip("file_too_small")
                return None

            # Check for adult content in filename
            if is_contain_18_plus_keywords(file_name):
                self.metrics.record_skip("adult_content")
                return None

            # Parse filename for metadata
            parsed_data = self._parse_filename(file_name)

            # Also check caption for IMDb ID and additional info
            caption = message.message or ""
            imdb_match = IMDB_PATTERN.search(caption)
            caption_imdb_id = imdb_match.group(0) if imdb_match else None

            # Validate content matches the requested metadata
            if not self._validate_content(
                parsed_data=parsed_data,
                metadata=metadata,
                catalog_type=catalog_type,
                file_name=file_name,
                caption_imdb_id=caption_imdb_id,
            ):
                return None

            # Extract episode info for series
            season_number = None
            episode_number = None
            episode_end = None

            if catalog_type == "series":
                seasons = parsed_data.get("seasons", [])
                episodes = parsed_data.get("episodes", [])

                if seasons and episodes:
                    season_number = seasons[0]
                    episode_number = episodes[0]
                    if len(episodes) > 1:
                        episode_end = episodes[-1]

                # Check if matches requested season/episode
                if season is not None and episode is not None:
                    if season_number != season:
                        self.metrics.record_skip("season_mismatch")
                        return None
                    if episode_number is not None and episode_number != episode:
                        # Allow if episode is in range
                        if episode_end is None or not (episode_number <= episode <= episode_end):
                            self.metrics.record_skip("episode_mismatch")
                            return None

            # Create TelegramStreamData
            # Note: Telethon doesn't provide file_id like Bot API
            # We need to store chat_id + message_id for MediaFlow to download
            stream = TelegramStreamData(
                chat_id=chat_id,
                chat_username=chat_username,
                message_id=message.id,
                file_id=None,  # Not available in Telethon, will be fetched by bot if needed
                file_unique_id=file_unique_id,
                file_name=file_name,
                mime_type=mime_type,
                size=size,
                posted_at=message.date,
                caption=caption[:500] if caption else None,  # Truncate long captions
                name=parsed_data.get("title", file_name),
                source="telegram",
                meta_id=metadata.get_canonical_id(),
                # Quality attributes from parsing
                resolution=parsed_data.get("resolution"),
                codec=parsed_data.get("codec"),
                quality=parsed_data.get("quality"),
                bit_depth=parsed_data.get("bit_depth"),
                uploader=chat_username or chat_id,
                release_group=parsed_data.get("group"),
                # Multi-value attributes
                audio_formats=parsed_data.get("audio", []) if isinstance(parsed_data.get("audio"), list) else [],
                channels=parsed_data.get("channels", []) if isinstance(parsed_data.get("channels"), list) else [],
                hdr_formats=parsed_data.get("hdr", []) if isinstance(parsed_data.get("hdr"), list) else [],
                languages=parsed_data.get("languages", []),
                # Release flags
                is_remastered=parsed_data.get("remastered", False),
                is_proper=parsed_data.get("proper", False),
                is_repack=parsed_data.get("repack", False),
                is_extended=parsed_data.get("extended", False),
                is_dubbed=parsed_data.get("dubbed", False),
                is_subbed=parsed_data.get("subbed", False),
                # Episode info
                season_number=season_number,
                episode_number=episode_number,
                episode_end=episode_end,
            )

            self.logger.info(f"Found matching content: {file_name} (chat: {chat_id}, msg: {message.id}, size: {size})")

            return stream

        except Exception as e:
            self.logger.debug(f"Error processing video message: {e}")
            self.metrics.record_skip("processing_error")
            return None

    @staticmethod
    def _parse_filename(filename: str) -> dict:
        """Parse filename using PTT for metadata extraction."""
        try:
            parsed = PTT.parse_title(filename, True)
            return {"filename": filename, **parsed}
        except Exception:
            return {"filename": filename, "title": filename}

    def _validate_content(
        self,
        parsed_data: dict,
        metadata: MetadataData,
        catalog_type: str,
        file_name: str,
        caption_imdb_id: str | None = None,
    ) -> bool:
        """Validate that the content matches the requested metadata."""
        # If we have IMDb ID in caption and it matches, accept
        meta_imdb_id = metadata.get_imdb_id()
        if caption_imdb_id and meta_imdb_id:
            if caption_imdb_id == meta_imdb_id:
                return True

        # Check title similarity
        parsed_title = parsed_data.get("title", "")
        if not parsed_title:
            self.metrics.record_skip("no_title_parsed")
            return False

        max_similarity = calculate_max_similarity_ratio(
            parsed_title,
            metadata.title,
            metadata.aka_titles or [],
        )

        if max_similarity < 80:  # 80% similarity threshold
            self.metrics.record_skip("title_mismatch")
            self.logger.debug(f"Title mismatch: '{parsed_title}' vs '{metadata.title}' (similarity: {max_similarity}%)")
            return False

        # Validate year for movies
        if catalog_type == "movie":
            parsed_year = parsed_data.get("year")
            if parsed_year and metadata.year:
                # Allow 1 year difference for release date variations
                if abs(parsed_year - metadata.year) > 1:
                    self.metrics.record_skip("year_mismatch")
                    return False

        # Validate year range for series
        if catalog_type == "series":
            parsed_year = parsed_data.get("year")
            if parsed_year and metadata.year:
                end_year = metadata.end_year or datetime.now().year
                if not (metadata.year <= parsed_year <= end_year + 1):
                    self.metrics.record_skip("year_mismatch")
                    return False

        return True

    def _log_metrics(self):
        """Log scraping metrics summary."""
        summary = self.metrics.get_summary()
        self.logger.info(
            f"Telegram scraping completed: "
            f"channels={summary['channels_scraped']}, "
            f"messages={summary['messages_processed']}, "
            f"videos={summary['content_found']['videos']}, "
            f"streams_created={summary['streams_created']}, "
            f"errors={summary['errors']}, "
            f"duration={summary['duration_seconds']:.2f}s"
        )
        if summary["skip_reasons"]:
            self.logger.debug(f"Skip reasons: {summary['skip_reasons']}")


async def store_telegram_streams(streams: list[TelegramStreamData]) -> int:
    """Store Telegram streams in the database.

    Args:
        streams: List of TelegramStreamData to store

    Returns:
        Number of streams successfully stored
    """
    stored_count = 0

    async with get_background_session() as session:
        for stream in streams:
            try:
                # Check if stream already exists
                existing = await crud.telegram_stream_exists(
                    session,
                    chat_id=stream.chat_id,
                    message_id=stream.message_id,
                )
                if existing:
                    logger.debug(f"Telegram stream already exists: {stream.chat_id}:{stream.message_id}")
                    continue

                # Resolve media_id from meta_id
                # This requires looking up the media by external ID
                media = await crud.get_media_by_external_id(session, stream.meta_id)
                if not media:
                    logger.warning(f"Media not found for meta_id: {stream.meta_id}")
                    continue

                # Create the Telegram stream
                await crud.create_telegram_stream(
                    session,
                    chat_id=stream.chat_id,
                    message_id=stream.message_id,
                    name=stream.name,
                    media_id=media.id,
                    chat_username=stream.chat_username,
                    file_id=stream.file_id,
                    file_unique_id=stream.file_unique_id,
                    file_name=stream.file_name,
                    mime_type=stream.mime_type,
                    size=stream.size,
                    posted_at=stream.posted_at,
                    source=stream.source,
                    resolution=stream.resolution,
                    codec=stream.codec,
                    quality=stream.quality,
                    bit_depth=stream.bit_depth,
                    uploader=stream.uploader,
                    release_group=stream.release_group,
                    is_remastered=stream.is_remastered,
                    is_proper=stream.is_proper,
                    is_repack=stream.is_repack,
                    is_extended=stream.is_extended,
                    is_dubbed=stream.is_dubbed,
                    is_subbed=stream.is_subbed,
                    season_number=stream.season_number,
                    episode_number=stream.episode_number,
                    episode_end=stream.episode_end,
                )
                stored_count += 1
                logger.info(f"Stored Telegram stream: {stream.name} ({stream.chat_id}:{stream.message_id})")

            except Exception as e:
                logger.exception(f"Error storing Telegram stream: {e}")

        await session.commit()

    return stored_count


# Singleton instance
telegram_scraper = TelegramScraper()
