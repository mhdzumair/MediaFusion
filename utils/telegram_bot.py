import hashlib
import json
import logging
import os
import random
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp
import PTT
import pytz
from sqlmodel import func, select

from db import crud
from db.config import settings
from db.crud.reference import get_or_create_catalog, get_or_create_genre
from db.crud.streams import (
    create_acestream_stream,
    create_http_stream,
    create_torrent_stream,
    create_usenet_stream,
    create_youtube_stream,
)
from db.database import get_async_session_context
from db.enums import MediaType
from db.models import User
from db.models.links import MediaCatalogLink, MediaGenreLink
from db.models.media import Media
from db.models.streams import (
    AceStreamStream,
    HTTPStream,
    TelegramStream,
    TorrentStream,
    UsenetStream,
    YouTubeStream,
)
from db.models.providers import MediaImage, MetadataProvider
from utils.sports_parser import (
    SPORTS_CATEGORIES,
    detect_sports_category,
    parse_sports_title,
)
from utils.runtime_const import SPORTS_ARTIFACTS
from db.redis_database import REDIS_SYNC_CLIENT
from api.routers.content.torrent_import import fetch_and_create_media_from_external
from scrapers.imdb_data import get_imdb_title_data, search_multiple_imdb
from scrapers.scraper_tasks import meta_fetcher
from utils.notification_registry import register_file_annotation_handler
from utils import const, torrent
from utils.parser import convert_bytes_to_readable
from utils.youtube import analyze_youtube_video

logger = logging.getLogger(__name__)


# ============================================
# Content Type Detection Patterns
# ============================================

MAGNET_PATTERN = re.compile(r"magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}[^\s]*", re.IGNORECASE)
NZB_URL_PATTERN = re.compile(r"https?://[^\s]+\.nzb(?:\?[^\s]*)?", re.IGNORECASE)
# Torrent URL pattern - URLs that likely return torrent file content
# Matches: .torrent files, /torrent endpoints, /torrents/xxx paths, /download paths
TORRENT_URL_PATTERN = re.compile(
    r"https?://[^\s]*(?:"
    r"\.torrent(?:\?[^\s]*)?"  # URLs ending in .torrent (with optional query)
    r"|/torrent(?:\?[^\s]+|/[^\s]+)"  # /torrent?params or /torrent/xxx paths
    r"|/torrents/[^\s]+"  # /torrents/id/... paths
    r")",
    re.IGNORECASE,
)
YOUTUBE_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)
ACESTREAM_PATTERN = re.compile(r"(?:acestream://)?([a-fA-F0-9]{40})", re.IGNORECASE)
HTTP_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
IMDB_PATTERN = re.compile(r"tt\d{7,8}")
# Accepts any supported external ID format: tt1234567, tmdb:123, tvdb:456, mal:789, kitsu:012
EXTERNAL_ID_PATTERN = re.compile(
    r"(?:tt\d{7,8}|(?:tmdb|tvdb|mal|kitsu):\d+)",
    re.IGNORECASE,
)

# Video MIME types
VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/x-matroska",
    "video/webm",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-flv",
    "video/mpeg",
    "video/3gpp",
    "video/x-ms-wmv",
}


# ============================================
# Conversation State Management
# ============================================


class ConversationStep(str, Enum):
    """Steps in the contribution wizard."""

    IDLE = "idle"
    AWAITING_MEDIA_TYPE = "awaiting_media_type"
    ANALYZING = "analyzing"
    AWAITING_MATCH = "awaiting_match"
    AWAITING_MANUAL_IMDB = "awaiting_manual_imdb"
    AWAITING_SPORTS_CATEGORY = "awaiting_sports_category"
    AWAITING_METADATA_REVIEW = "awaiting_metadata_review"
    AWAITING_FIELD_EDIT = "awaiting_field_edit"
    AWAITING_POSTER_INPUT = "awaiting_poster_input"  # User is providing poster image
    AWAITING_CONFIRM = "awaiting_confirm"
    IMPORTING = "importing"


class ContentType(str, Enum):
    """Types of content that can be contributed."""

    MAGNET = "magnet"
    TORRENT_FILE = "torrent_file"
    TORRENT_URL = "torrent_url"  # URL that returns torrent file content
    NZB = "nzb"
    YOUTUBE = "youtube"
    HTTP = "http"
    ACESTREAM = "acestream"
    VIDEO = "video"


@dataclass
class ConversationState:
    """State for a user's contribution wizard conversation."""

    user_id: int
    chat_id: int
    step: ConversationStep = ConversationStep.IDLE
    content_type: ContentType | None = None
    raw_input: str | dict | None = None  # magnet link, URL, file_id, torrent bytes, etc.
    media_type: str | None = None  # movie, series, sports
    sports_category: str | None = None  # football, basketball, etc.
    analysis_result: dict | None = None  # from analyze step
    matches: list[dict] | None = None  # search matches
    selected_match: dict | None = None  # selected media match (optional for sports)
    metadata_overrides: dict = field(default_factory=dict)  # user edits
    editing_field: str | None = None  # field currently being edited
    message_id: int | None = None  # the wizard message being edited
    original_message_id: int | None = None  # original content message ID
    custom_poster_url: str | None = None  # user-provided poster URL
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def touch(self):
        """Update the last activity timestamp."""
        self.updated_at = datetime.now()

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """Check if the conversation has timed out."""
        return (datetime.now() - self.updated_at) > timedelta(minutes=timeout_minutes)


# ============================================
# Metadata Options for Edit Keyboards
# ============================================

# Flatten quality groups into a flat list for the picker (exclude None values)
QUALITY_OPTIONS = [q for qs in const.QUALITY_GROUPS.values() for q in qs if q]
# Filter out None from resolutions
RESOLUTION_OPTIONS = [r for r in const.RESOLUTIONS if r]
# Use the full languages list from const
LANGUAGE_OPTIONS = [lang for lang in const.LANGUAGES_FILTERS if lang]

# These don't exist in const — keep locally
CODEC_OPTIONS = ["x265", "HEVC", "x264", "AVC", "AV1", "VP9", "MPEG-4"]
AUDIO_OPTIONS = ["AAC", "AC3", "DTS", "DTS-HD MA", "TrueHD", "Atmos", "FLAC", "MP3", "EAC3"]
HDR_OPTIONS = ["HDR10", "HDR10+", "Dolby Vision", "HLG", "SDR"]


class TelegramNotifier:
    def __init__(self):
        self.bot_token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.enabled = bool(self.bot_token and self.chat_id)

    async def send_contribution_notification(
        self,
        meta_id: str,
        title: str,
        meta_type: str,
        poster: str,
        uploader: str,
        info_hash: str,
        torrent_type: str,
        size: str,
        torrent_name: str,
        seasons_and_episodes: dict | None = None,
        catalogs: list | None = None,
        languages: list | None = None,
    ):
        """Send notification about new contribution to Telegram channel"""
        if not self.enabled:
            logger.warning("Telegram notifications are disabled. Check bot token.")
            return

        # Create block URL - will open scraper page with block_torrent action and info hash
        block_url = f"{settings.host_url}/scraper?action=block_torrent&info_hash={info_hash}"
        meta_id_data = (
            f"*IMDb*: [{meta_id}](https://www.imdb.com/title/{meta_id}/)\n"
            if meta_id.startswith("tt")
            else f"Meta ID: `{meta_id}`\n"
        )

        # Build the message
        message = (
            f"🎬 New Contribution\n\n"
            f"*Title*: {title}\n"
            f"*Type*: {meta_type.title()}\n"
            f"{meta_id_data}"
            f"*Uploader*: {uploader}\n"
            f"*Size*: {size}\n"
            f"*Torrent Name*: `{torrent_name}`\n"
            f"*Info Hash*: `{info_hash}`\n"
            f"*Type*: {torrent_type}\n"
            f"*Poster*: [View]({poster})\n"
            f"*Stremio Links*:\n"
            f"   - *APP*: `stremio:///detail/{meta_type}/{meta_id}/{meta_id}`\n"
            f"   - *WEB*: [View](https://web.stremio.com/#/detail/{meta_type}/{meta_id}/{meta_id})"
        )

        if catalogs:
            # Escape underscores and use pipe with spaces
            escaped_catalogs = [cat.replace("_", "\\_") for cat in catalogs]
            message += f"\n*Catalogs*: {', '.join(escaped_catalogs)}"
        if languages:
            # Use pipe with spaces for languages
            message += f"\n*Languages*: {', '.join(languages)}"

        # Add season/episode info for series
        if meta_type == "series" and seasons_and_episodes:
            message += "\n*Seasons*: "
            for season, episodes in seasons_and_episodes.items():
                message += f"\n- Season {season}: "
                if len(episodes) == 1:
                    message += f"{episodes[0]}"
                else:
                    message += f"{min(episodes)} - {max(episodes)}"
            message += "\n"

        # Add block link
        message += f"\n\n[🚫 Block/Delete Torrent]({block_url})"

        await self._send_photo_message(poster, message)

    async def send_block_notification(
        self,
        info_hash: str,
        action: str,
        meta_id: str,
        title: str,
        meta_type: str,
        poster: str,
        torrent_name: str,
    ):
        """Send notification when a torrent is blocked"""
        if not self.enabled:
            logger.warning("Telegram notifications are disabled. Check bot token.")
            return

        meta_id_data = (
            f"*IMDb*: [{meta_id}](https://www.imdb.com/title/{meta_id}/)\n"
            if meta_id.startswith("tt")
            else f"Meta ID: {meta_id}\n"
        )

        message = (
            f"🚫 Torrent {'Blocked' if action == 'block' else 'Deleted'}\n\n"
            f"*Title*: {title}\n"
            f"*Type*: {meta_type.title()}\n"
            f"{meta_id_data}"
            f"*Torrent Name*: `{torrent_name}`\n"
            f"*Info Hash*: `{info_hash}`\n"
            f"*Poster*: [View]({poster})"
        )

        await self._send_photo_message(poster, message)

    async def send_migration_notification(
        self,
        old_id: str,
        new_id: str,
        title: str,
        meta_type: str,
        poster: str,
    ):
        """Send notification when an ID is migrated"""
        if not self.enabled:
            logger.warning("Telegram notifications are disabled. Check bot token.")
            return

        message = (
            f"🔄 ID Migration Complete\n\n"
            f"*Title*: {title}\n"
            f"*Type*: {meta_type.title()}\n"
            f"*Old ID*: `{old_id}`\n"
            f"*New IMDb ID*: [{new_id}](https://www.imdb.com/title/{new_id}/)\n"
            f"*Poster*: [View]({poster})"
        )

        await self._send_photo_message(poster, message)

    async def send_image_update_notification(
        self,
        meta_id: str,
        title: str,
        meta_type: str,
        poster: str,
        old_poster: str | None = None,
        new_poster: str | None = None,
        old_background: str | None = None,
        new_background: str | None = None,
        old_logo: str | None = None,
        new_logo: str | None = None,
    ):
        """Send notification when images are updated"""
        if not self.enabled:
            logger.warning("Telegram notifications are disabled. Check bot token.")
            return

        meta_id_data = (
            f"*IMDb*: [{meta_id}](https://www.imdb.com/title/{meta_id}/)\n"
            if meta_id.startswith("tt")
            else f"Meta ID: {meta_id}\n"
        )

        message = (
            f"🖼️ Images Updated\n\n"
            f"*Title*: {title}\n"
            f"*Type*: {meta_type.title()}\n"
            f"{meta_id_data}"
            f"*Old Poster*: [View]({old_poster})\n"
            f"*Old Background*: [View]({old_background})\n"
            f"*Old Logo*: [View]({old_logo})\n"
        )

        # Add details about what was updated
        if new_poster:
            message += f"\n*Poster*: Updated ✅ [View]({new_poster})"
        if new_background:
            message += f"\n*Background*: Updated ✅ [View]({new_background})"
        if new_logo:
            message += f"\n*Logo*: Updated ✅ [View]({new_logo})"

        message += f"\n\n*Preview*: [View]({poster})"

        await self._send_photo_message(poster, message)

    async def _send_photo_message(self, photo_url: str, message: str):
        """Send a message with photo, falling back to text-only if photo fails"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/sendPhoto",
                    json={
                        "chat_id": self.chat_id,
                        "photo": photo_url,
                        "caption": message,
                        "parse_mode": "Markdown",
                    },
                ) as response:
                    if not response.ok:
                        error_data = await response.json()
                        logger.error(f"Failed to send Telegram notification: {error_data}")
                        # Fallback to text-only message if photo fails
                        await self._send_text_only_message(message)
                    return await response.json()
        except Exception as e:
            logger.error(f"Error sending Telegram notification: {e}")
            # Fallback to text-only message if there's an error
            await self._send_text_only_message(message)

    async def _send_text_only_message(self, message: str):
        """Fallback method to send text-only message if photo sending fails"""
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": False,
                    },
                )
        except Exception as e:
            logger.error(f"Error sending fallback text message: {e}")

    async def send_file_annotation_request(self, info_hash: str, torrent_name: str):
        """Send notification to request episode annotations"""
        if not self.enabled:
            logger.warning("Telegram notifications are disabled. Check bot token.")
            return

        message = (
            f"📝 Failed to identify the episodes. Require to annotate.\n\n"
            f"*Info Hash*: `{info_hash}`\n"
            f"*Torrent Name*: `{torrent_name}`\n"
            f"Please review and annotate the episodes manually."
        )

        await self._send_text_only_message(message)

    async def send_content_received_notification(
        self,
        file_name: str,
        file_size: int,
        status: str = "received",
        meta_id: str | None = None,
        title: str | None = None,
        error_message: str | None = None,
    ):
        """Send notification about received content.

        Used for bot-forwarded content feature where users can send
        videos to the MediaFusion bot for processing.

        Args:
            file_name: Name of the received file
            file_size: Size of the file in bytes
            status: Status of processing (received, processing, stored, failed)
            meta_id: Matched media ID (if identified)
            title: Matched media title (if identified)
            error_message: Error message if processing failed
        """
        if not self.enabled:
            logger.warning("Telegram notifications are disabled. Check bot token.")
            return

        # Format file size
        if file_size >= 1024 * 1024 * 1024:
            size_str = f"{file_size / (1024 * 1024 * 1024):.2f} GB"
        elif file_size >= 1024 * 1024:
            size_str = f"{file_size / (1024 * 1024):.2f} MB"
        else:
            size_str = f"{file_size / 1024:.2f} KB"

        # Build status emoji
        status_emoji = {
            "received": "📥",
            "processing": "⏳",
            "stored": "✅",
            "failed": "❌",
        }.get(status, "📥")

        message = f"{status_emoji} Content {status.title()}\n\n*File*: `{file_name}`\n*Size*: {size_str}\n"

        if meta_id and title:
            message += f"*Matched*: [{title}](https://www.imdb.com/title/{meta_id}/)\n"
        elif status == "failed" and error_message:
            message += f"*Error*: {error_message}\n"

        await self._send_text_only_message(message)


class TelegramContentBot:
    """
    Interactive bot handler for content contribution.

    This class handles the full content contribution workflow via Telegram:
    - Detects content type (magnet, torrent, NZB, YouTube, HTTP, AceStream, video)
    - Guides users through a multi-step wizard using inline keyboards
    - Analyzes content and searches for metadata matches
    - Allows metadata review and editing before import
    - Creates streams and contributions in the database

    Supported content types:
    - Magnet links (magnet:?xt=urn:btih:...)
    - Torrent files (.torrent uploads)
    - NZB URLs (https://.../.nzb)
    - YouTube URLs (youtube.com/watch, youtu.be)
    - HTTP direct links (video URLs)
    - AceStream IDs (40-char hex)
    - Video files (forwarded videos/documents)

    Usage flow:
    1. User sends content (link, file, or video)
    2. Bot detects content type and shows media type selection
    3. User selects movie/series/sports
    4. Bot analyzes content and shows metadata matches
    5. User selects match or enters external ID manually
    6. Bot shows metadata review with edit options
    7. User confirms import
    8. Bot creates stream and shows success message
    """

    def __init__(self):
        self.bot_token = settings.telegram_bot_token
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None
        self.enabled = bool(self.bot_token)
        self._conversation_ttl_seconds = 30 * 60

    def _conversation_key(self, user_id: int) -> str:
        return f"telegram:conversation:{user_id}"

    def _pending_content_key(self, user_id: int) -> str:
        return f"telegram:pending_content:{user_id}"

    def _search_result_key(self, callback_data: str) -> str:
        return f"telegram:search_result:{callback_data}"

    def _user_mapping_key(self, telegram_user_id: int) -> str:
        return f"telegram:user_mapping:{telegram_user_id}"

    # ---- pending_content helpers ----

    def _get_pending_content(self, user_id: int) -> list:
        try:
            raw = REDIS_SYNC_CLIENT.get(self._pending_content_key(user_id))
            return json.loads(raw) if raw else []
        except Exception as e:
            logger.debug(f"Failed to load pending content from Redis for user {user_id}: {e}")
            return []

    def _set_pending_content(self, user_id: int, items: list):
        try:
            REDIS_SYNC_CLIENT.setex(
                self._pending_content_key(user_id),
                2 * 60 * 60,  # 2 hours
                json.dumps(items),
            )
        except Exception as e:
            logger.debug(f"Failed to persist pending content to Redis for user {user_id}: {e}")

    def _delete_pending_content(self, user_id: int):
        try:
            REDIS_SYNC_CLIENT.delete(self._pending_content_key(user_id))
        except Exception as e:
            logger.debug(f"Failed to delete pending content from Redis for user {user_id}: {e}")

    # ---- search_result helpers ----

    def _get_search_result(self, callback_data: str) -> dict | None:
        try:
            raw = REDIS_SYNC_CLIENT.get(self._search_result_key(callback_data))
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.debug(f"Failed to load search result from Redis: {e}")
            return None

    def _set_search_result(self, callback_data: str, data: dict):
        try:
            REDIS_SYNC_CLIENT.setex(
                self._search_result_key(callback_data),
                30 * 60,  # 30 minutes
                json.dumps(data),
            )
        except Exception as e:
            logger.debug(f"Failed to persist search result to Redis: {e}")

    def _delete_search_result(self, callback_data: str):
        try:
            REDIS_SYNC_CLIENT.delete(self._search_result_key(callback_data))
        except Exception as e:
            logger.debug(f"Failed to delete search result from Redis: {e}")

    def _to_json_safe(self, value: Any) -> Any:
        """Convert nested values to JSON-safe structures for Redis storage."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, bytes):
            return None
        if isinstance(value, list):
            return [self._to_json_safe(v) for v in value]
        if isinstance(value, dict):
            return {k: self._to_json_safe(v) for k, v in value.items()}
        return str(value)

    def _serialize_state(self, state: ConversationState) -> str:
        payload = {
            "user_id": state.user_id,
            "chat_id": state.chat_id,
            "step": state.step.value,
            "content_type": state.content_type.value if state.content_type else None,
            "raw_input": self._to_json_safe(state.raw_input),
            "media_type": state.media_type,
            "sports_category": state.sports_category,
            "analysis_result": self._to_json_safe(state.analysis_result),
            "matches": self._to_json_safe(state.matches),
            "selected_match": self._to_json_safe(state.selected_match),
            "metadata_overrides": self._to_json_safe(state.metadata_overrides),
            "editing_field": state.editing_field,
            "message_id": state.message_id,
            "original_message_id": state.original_message_id,
            "custom_poster_url": state.custom_poster_url,
            "created_at": state.created_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
        }
        return json.dumps(payload)

    def _deserialize_state(self, data: str | bytes) -> ConversationState | None:
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            payload = json.loads(data)
            return ConversationState(
                user_id=int(payload["user_id"]),
                chat_id=int(payload["chat_id"]),
                step=ConversationStep(payload.get("step", ConversationStep.IDLE.value)),
                content_type=ContentType(payload["content_type"]) if payload.get("content_type") else None,
                raw_input=payload.get("raw_input"),
                media_type=payload.get("media_type"),
                sports_category=payload.get("sports_category"),
                analysis_result=payload.get("analysis_result"),
                matches=payload.get("matches"),
                selected_match=payload.get("selected_match"),
                metadata_overrides=payload.get("metadata_overrides") or {},
                editing_field=payload.get("editing_field"),
                message_id=payload.get("message_id"),
                original_message_id=payload.get("original_message_id"),
                custom_poster_url=payload.get("custom_poster_url"),
                created_at=datetime.fromisoformat(payload.get("created_at"))
                if payload.get("created_at")
                else datetime.now(),
                updated_at=datetime.fromisoformat(payload.get("updated_at"))
                if payload.get("updated_at")
                else datetime.now(),
            )
        except Exception as e:
            logger.warning(f"Failed to deserialize conversation state: {e}")
            return None

    def _persist_conversation(self, state: ConversationState):
        """Persist state to Redis so it survives multi-worker routing."""
        try:
            REDIS_SYNC_CLIENT.setex(
                self._conversation_key(state.user_id),
                self._conversation_ttl_seconds,
                self._serialize_state(state),
            )
        except Exception as e:
            logger.debug(f"Failed to persist conversation to Redis for user {state.user_id}: {e}")

    # ============================================
    # Content Type Detection
    # ============================================

    @staticmethod
    def _extract_urls_from_entities(message: dict) -> list[str]:
        """Extract URLs from Telegram message entities.

        Telegram stores hyperlinks in two ways:
        - 'url' entity type: the URL is in the text itself
        - 'text_link' entity type: the URL is in entity.url (text shows display label)

        Also checks caption_entities for media messages.
        """
        urls: list[str] = []
        text = message.get("text", "") or ""
        caption = message.get("caption", "") or ""

        for entities_key, source_text in [("entities", text), ("caption_entities", caption)]:
            for entity in message.get(entities_key, []):
                entity_type = entity.get("type", "")
                if entity_type == "text_link":
                    # URL is in entity.url, not in the text
                    url = entity.get("url", "")
                    if url:
                        urls.append(url)
                elif entity_type == "url":
                    # URL is in the text itself
                    offset = entity.get("offset", 0)
                    length = entity.get("length", 0)
                    url = source_text[offset : offset + length]
                    if url:
                        urls.append(url)
        return urls

    def detect_content_type(self, message: dict) -> tuple[ContentType | None, Any]:
        """Detect content type from a Telegram message.

        Checks plain text, caption, and entity-embedded URLs (text_link).

        Args:
            message: Telegram message dict

        Returns:
            Tuple of (ContentType, raw_data) or (None, None) if not recognized
        """
        text = message.get("text", "") or message.get("caption", "") or ""
        document = message.get("document")
        video = message.get("video")

        # Extract URLs from entities (handles text_link markdown links)
        entity_urls = self._extract_urls_from_entities(message)

        # Check for video first
        if video:
            return ContentType.VIDEO, {
                "file_id": video.get("file_id"),
                "file_unique_id": video.get("file_unique_id"),
                "file_name": video.get("file_name"),
                "file_size": video.get("file_size"),
                "mime_type": video.get("mime_type"),
            }

        # Check for document (could be video or torrent file)
        if document:
            mime_type = document.get("mime_type", "")
            file_name = document.get("file_name", "")

            # Check for torrent file
            if mime_type == "application/x-bittorrent" or file_name.endswith(".torrent"):
                return ContentType.TORRENT_FILE, {
                    "file_id": document.get("file_id"),
                    "file_name": file_name,
                    "file_size": document.get("file_size"),
                }

            # Check for video document
            if mime_type in VIDEO_MIME_TYPES or any(
                file_name.lower().endswith(ext) for ext in [".mkv", ".mp4", ".avi", ".webm", ".mov"]
            ):
                return ContentType.VIDEO, {
                    "file_id": document.get("file_id"),
                    "file_unique_id": document.get("file_unique_id"),
                    "file_name": file_name,
                    "file_size": document.get("file_size"),
                    "mime_type": mime_type,
                }

        # Build a combined text that includes both plain text and entity URLs.
        # This ensures URLs from text_link entities (markdown-style links) are checked.
        all_texts = [text] + entity_urls

        for candidate in all_texts:
            if not candidate:
                continue

            # Magnet link
            magnet_match = MAGNET_PATTERN.search(candidate)
            if magnet_match:
                return ContentType.MAGNET, magnet_match.group(0)

            # NZB URL
            nzb_match = NZB_URL_PATTERN.search(candidate)
            if nzb_match:
                return ContentType.NZB, nzb_match.group(0)

            # Torrent URL (URLs that return torrent file content)
            # Check before generic HTTP - patterns like /torrent, /torrents/, .torrent
            torrent_url_match = TORRENT_URL_PATTERN.search(candidate)
            if torrent_url_match:
                return ContentType.TORRENT_URL, torrent_url_match.group(0)

            # YouTube URL
            youtube_match = YOUTUBE_PATTERN.search(candidate)
            if youtube_match:
                full_url = youtube_match.group(0)
                video_id = youtube_match.group(1)
                return ContentType.YOUTUBE, {"url": full_url, "video_id": video_id}

            # AceStream ID (40-char hex, but not a magnet hash)
            acestream_match = ACESTREAM_PATTERN.search(candidate)
            if acestream_match and "magnet:" not in candidate.lower():
                return ContentType.ACESTREAM, acestream_match.group(1)

            # Generic HTTP URL (check last, after more specific patterns)
            http_match = HTTP_URL_PATTERN.search(candidate)
            if http_match:
                url = http_match.group(0)
                # Make sure it's not an already-matched pattern
                if not any(
                    [
                        MAGNET_PATTERN.search(url),
                        NZB_URL_PATTERN.search(url),
                        YOUTUBE_PATTERN.search(url),
                        TORRENT_URL_PATTERN.search(url),
                    ]
                ):
                    return ContentType.HTTP, url

        return None, None

    def get_content_type_emoji(self, content_type: ContentType) -> str:
        """Get emoji for content type."""
        return {
            ContentType.MAGNET: "🧲",
            ContentType.TORRENT_FILE: "📦",
            ContentType.TORRENT_URL: "🔗📦",
            ContentType.NZB: "📰",
            ContentType.YOUTUBE: "▶️",
            ContentType.HTTP: "🔗",
            ContentType.ACESTREAM: "📡",
            ContentType.VIDEO: "🎬",
        }.get(content_type, "📁")

    def get_content_type_name(self, content_type: ContentType) -> str:
        """Get display name for content type."""
        return {
            ContentType.MAGNET: "Magnet Link",
            ContentType.TORRENT_FILE: "Torrent File",
            ContentType.TORRENT_URL: "Torrent URL",
            ContentType.NZB: "NZB URL",
            ContentType.YOUTUBE: "YouTube Video",
            ContentType.HTTP: "HTTP Link",
            ContentType.ACESTREAM: "AceStream",
            ContentType.VIDEO: "Video File",
        }.get(content_type, "Content")

    # ============================================
    # Conversation State Management
    # ============================================

    def get_conversation(self, user_id: int) -> ConversationState | None:
        """Get conversation state for a user.

        Always reads from Redis so all workers see the latest state.
        """
        try:
            redis_data = REDIS_SYNC_CLIENT.get(self._conversation_key(user_id))
            if not redis_data:
                return None
            state = self._deserialize_state(redis_data)
        except Exception as e:
            logger.debug(f"Failed to load conversation from Redis for user {user_id}: {e}")
            return None

        if state and state.is_expired():
            try:
                REDIS_SYNC_CLIENT.delete(self._conversation_key(user_id))
            except Exception:
                pass
            return None
        return state

    def create_conversation(
        self,
        user_id: int,
        chat_id: int,
        content_type: ContentType,
        raw_input: Any,
        message_id: int | None = None,
    ) -> ConversationState:
        """Create a new conversation state for a user."""
        state = ConversationState(
            user_id=user_id,
            chat_id=chat_id,
            content_type=content_type,
            raw_input=raw_input,
            original_message_id=message_id,
            step=ConversationStep.AWAITING_MEDIA_TYPE,
        )
        self._persist_conversation(state)
        return state

    def clear_conversation(self, user_id: int):
        """Clear conversation state for a user."""
        try:
            REDIS_SYNC_CLIENT.delete(self._conversation_key(user_id))
        except Exception:
            pass

    # ============================================
    # Authentication Check
    # ============================================

    async def check_user_linked(self, telegram_user_id: int) -> tuple[bool, int | None]:
        """Check if a Telegram user is linked to a MediaFusion account.

        Args:
            telegram_user_id: Telegram user ID

        Returns:
            Tuple of (is_linked, mediafusion_user_id)
        """
        mf_user_id = await self._get_mediafusion_user_id(telegram_user_id)
        return (mf_user_id is not None, mf_user_id)

    # ============================================
    # Contribution Wizard Flow Methods
    # ============================================

    async def start_contribution_flow(
        self,
        user_id: int,
        chat_id: int,
        content_type: ContentType,
        raw_input: Any,
        message_id: int | None = None,
    ) -> dict:
        """Start the contribution wizard flow.

        This is the entry point for all content contributions.
        It checks authentication and shows the media type selection.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            content_type: Detected content type
            raw_input: Raw input data (URL, file info, etc.)
            message_id: Original message ID

        Returns:
            Dict with result
        """
        # Check if user is linked
        is_linked, mf_user_id = await self.check_user_linked(user_id)
        if not is_linked:
            return {
                "success": False,
                "message": (
                    "🔐 *Account Required*\n\n"
                    "To contribute content, you need to link your MediaFusion account.\n\n"
                    "Send `/login` to get started."
                ),
                "requires_login": True,
            }

        # Create conversation state
        state = self.create_conversation(user_id, chat_id, content_type, raw_input, message_id)

        # Build media type selection message
        emoji = self.get_content_type_emoji(content_type)
        type_name = self.get_content_type_name(content_type)

        # Get content preview text
        preview = self._get_content_preview(content_type, raw_input)

        message = f"{emoji} *{type_name} Detected*\n\n{preview}\n\nSelect the content type:"

        keyboard = self._build_media_type_keyboard(user_id)

        return {
            "success": True,
            "message": message,
            "reply_markup": {"inline_keyboard": keyboard},
            "state": state,
        }

    def _get_content_preview(self, content_type: ContentType, raw_input: Any) -> str:
        """Get a preview string for the content."""
        if content_type == ContentType.MAGNET:
            # Extract info hash from magnet
            info_hash, _ = torrent.parse_magnet(raw_input)
            return f"*Info Hash:* `{info_hash[:16]}...`" if info_hash else "*Magnet Link*"
        elif content_type == ContentType.VIDEO:
            file_name = raw_input.get("file_name", "Unknown")
            file_size = raw_input.get("file_size", 0)
            size_mb = file_size // (1024 * 1024) if file_size else 0
            return f"*File:* `{file_name}`\n*Size:* {size_mb} MB"
        elif content_type == ContentType.TORRENT_FILE:
            file_name = raw_input.get("file_name", "Unknown")
            return f"*File:* `{file_name}`"
        elif content_type == ContentType.YOUTUBE:
            video_id = raw_input.get("video_id", "")
            return f"*Video ID:* `{video_id}`"
        elif content_type == ContentType.NZB:
            return f"*URL:* `{raw_input[:50]}...`" if len(raw_input) > 50 else f"*URL:* `{raw_input}`"
        elif content_type == ContentType.HTTP:
            return f"*URL:* `{raw_input[:50]}...`" if len(raw_input) > 50 else f"*URL:* `{raw_input}`"
        elif content_type == ContentType.ACESTREAM:
            return f"*Content ID:* `{raw_input}`"
        return "*Content received*"

    def _build_media_type_keyboard(self, user_id: int) -> list[list[dict]]:
        """Build the media type selection keyboard."""
        return [
            [
                {"text": "🎬 Movie", "callback_data": f"mtype:{user_id}:movie"},
                {"text": "📺 Series", "callback_data": f"mtype:{user_id}:series"},
                {"text": "🏆 Sports", "callback_data": f"mtype:{user_id}:sports"},
            ],
            [{"text": "❌ Cancel", "callback_data": f"cancel:{user_id}"}],
        ]

    @staticmethod
    def _collect_match_ids(match: dict) -> dict[str, str]:
        """Extract canonical external IDs from a metadata match dict."""
        ids: dict[str, str] = {}
        external = match.get("external_ids") or {}

        def _set_if(key: str, value: Any):
            if value:
                ids[key] = str(value)

        _set_if("imdb", match.get("imdb_id"))
        _set_if("tmdb", match.get("tmdb_id"))
        _set_if("tvdb", match.get("tvdb_id"))
        _set_if("mal", match.get("mal_id"))
        _set_if("kitsu", match.get("kitsu_id"))

        _set_if("imdb", external.get("imdb"))
        _set_if("imdb", external.get("imdb_id"))
        _set_if("tmdb", external.get("tmdb"))
        _set_if("tmdb", external.get("tmdb_id"))
        _set_if("tvdb", external.get("tvdb"))
        _set_if("tvdb", external.get("tvdb_id"))
        _set_if("mal", external.get("mal"))
        _set_if("mal", external.get("mal_id"))
        _set_if("kitsu", external.get("kitsu"))
        _set_if("kitsu", external.get("kitsu_id"))
        return ids

    @staticmethod
    def _get_canonical_external_id(match: dict) -> str | None:
        """Get the best canonical external ID for a match in a format compatible
        with ``get_media_by_external_id`` and ``fetch_and_create_media_from_external``.

        Priority order: IMDb > TMDB > TVDB > MAL > Kitsu.
        Returns prefixed strings like ``tt1234567``, ``tmdb:123``, ``tvdb:456``, etc.
        Falls back to ``match["id"]`` if nothing better is available.
        """
        external = match.get("external_ids") or {}

        # IMDb – already prefixed
        imdb = match.get("imdb_id") or external.get("imdb") or external.get("imdb_id")
        if imdb and str(imdb).startswith("tt"):
            return str(imdb)

        # TMDB
        tmdb = match.get("tmdb_id") or external.get("tmdb") or external.get("tmdb_id")
        if tmdb:
            return f"tmdb:{tmdb}"

        # TVDB
        tvdb = match.get("tvdb_id") or external.get("tvdb") or external.get("tvdb_id")
        if tvdb:
            return f"tvdb:{tvdb}"

        # MAL
        mal = match.get("mal_id") or external.get("mal") or external.get("mal_id")
        if mal:
            return f"mal:{mal}"

        # Kitsu
        kitsu = match.get("kitsu_id") or external.get("kitsu") or external.get("kitsu_id")
        if kitsu:
            return f"kitsu:{kitsu}"

        # Fallback to the generic "id" field (may already be prefixed)
        fallback = match.get("id")
        if fallback:
            return str(fallback)

        return None

    async def _add_custom_poster(self, session, media_id: int, poster_url: str) -> None:
        """Add a custom poster image provided by the user.

        Args:
            session: Database session
            media_id: Media ID to add image for
            poster_url: URL of the poster image
        """
        # Get or create the "mediafusion" provider for user-contributed images
        provider = await session.exec(select(MetadataProvider).where(MetadataProvider.name == "mediafusion"))
        provider = provider.first()
        if not provider:
            provider = MetadataProvider(name="mediafusion", display_name="MediaFusion")
            session.add(provider)
            await session.flush()

        session.add(
            MediaImage(
                media_id=media_id,
                provider_id=provider.id,
                image_type="poster",
                url=poster_url,
                is_primary=True,
                display_order=1,
            )
        )

    async def _add_sports_images(
        self, session, media_id: int, sports_category: str, custom_poster_url: str | None = None
    ) -> None:
        """Add poster and background images for sports content from SPORTS_ARTIFACTS.

        Maps the internal sports_category to the display name used in SPORTS_ARTIFACTS,
        then randomly selects poster and background images.

        Args:
            session: Database session
            media_id: Media ID to add images for
            sports_category: Sports category key (e.g. 'american_football')
            custom_poster_url: Optional custom poster URL provided by user
        """
        # Map category to SPORTS_ARTIFACTS key
        category_display_name = SPORTS_CATEGORIES.get(sports_category, "Sports")

        # Try exact match first, then fallback to generic sports
        artifacts = SPORTS_ARTIFACTS.get(category_display_name)
        if not artifacts:
            # Try some common variations
            for key in SPORTS_ARTIFACTS:
                if sports_category.replace("_", " ").lower() in key.lower():
                    artifacts = SPORTS_ARTIFACTS[key]
                    break

        if not artifacts:
            # Use generic sports artifacts if available
            artifacts = SPORTS_ARTIFACTS.get("Sports", {})

        # Get or create the "mediafusion" provider for user-contributed images
        provider = await session.exec(select(MetadataProvider).where(MetadataProvider.name == "mediafusion"))
        provider = provider.first()
        if not provider:
            provider = MetadataProvider(name="mediafusion", display_name="MediaFusion")
            session.add(provider)
            await session.flush()

        # Add poster image (prefer custom poster if provided)
        if custom_poster_url:
            session.add(
                MediaImage(
                    media_id=media_id,
                    provider_id=provider.id,
                    image_type="poster",
                    url=custom_poster_url,
                    is_primary=True,
                    display_order=1,
                )
            )
        elif artifacts:
            posters = artifacts.get("poster", [])
            if posters:
                poster_url = random.choice(posters)
                session.add(
                    MediaImage(
                        media_id=media_id,
                        provider_id=provider.id,
                        image_type="poster",
                        url=poster_url,
                        is_primary=True,
                        display_order=1,
                    )
                )

        # Add background image from sports artifacts
        if artifacts:
            backgrounds = artifacts.get("background", [])
            if backgrounds:
                background_url = random.choice(backgrounds)
                session.add(
                    MediaImage(
                        media_id=media_id,
                        provider_id=provider.id,
                        image_type="background",
                        url=background_url,
                        is_primary=True,
                        display_order=1,
                    )
                )

    async def _check_content_already_exists(self, state: ConversationState) -> tuple[bool, str]:
        """Check whether analyzed content already exists in DB.

        Returns:
            (exists, human_readable_reason)
        """
        analysis = state.analysis_result or {}
        content_type = state.content_type

        async with get_async_session_context() as session:
            if content_type in (ContentType.MAGNET, ContentType.TORRENT_FILE, ContentType.TORRENT_URL):
                info_hash = analysis.get("info_hash")
                if info_hash:
                    normalized_hash = str(info_hash).strip().lower()
                    existing = await session.exec(
                        select(TorrentStream).where(func.lower(TorrentStream.info_hash) == normalized_hash)
                    )
                    if existing.first():
                        return True, f"Torrent already exists (`{normalized_hash}`)"

            elif content_type == ContentType.YOUTUBE:
                video_id = analysis.get("video_id")
                if video_id:
                    existing = await session.exec(select(YouTubeStream).where(YouTubeStream.video_id == video_id))
                    if existing.first():
                        return True, f"YouTube video already exists (`{video_id}`)"

            elif content_type == ContentType.HTTP:
                url = analysis.get("url")
                if url:
                    existing = await session.exec(select(HTTPStream).where(HTTPStream.url == url))
                    if existing.first():
                        return True, "HTTP stream URL already exists"

            elif content_type == ContentType.NZB:
                nzb_url = analysis.get("nzb_url")
                if nzb_url:
                    nzb_guid = hashlib.sha256(nzb_url.encode()).hexdigest()[:32]
                    existing = await session.exec(select(UsenetStream).where(UsenetStream.nzb_guid == nzb_guid))
                    if existing.first():
                        return True, f"NZB already exists (`{nzb_guid}`)"

            elif content_type == ContentType.ACESTREAM:
                content_id = analysis.get("content_id")
                if content_id:
                    existing = await session.exec(
                        select(AceStreamStream).where(AceStreamStream.content_id == content_id)
                    )
                    if existing.first():
                        return True, f"AceStream already exists (`{content_id}`)"

            elif content_type == ContentType.VIDEO:
                file_unique_id = analysis.get("file_unique_id")
                if file_unique_id:
                    existing = await session.exec(
                        select(TelegramStream).where(TelegramStream.file_unique_id == file_unique_id)
                    )
                    if existing.first():
                        return True, "Telegram file already exists (same file_unique_id)"

        return False, ""

    async def handle_media_type_selection(self, user_id: int, chat_id: int, message_id: int, media_type: str) -> dict:
        """Handle media type selection and run analysis.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit
            media_type: Selected media type (movie, series, sports)

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            return {"success": False, "message": "Session expired. Please start over."}

        state.media_type = media_type
        state.message_id = message_id
        state.step = ConversationStep.ANALYZING
        state.touch()
        self._persist_conversation(state)

        # Show analyzing message
        emoji = self.get_content_type_emoji(state.content_type)
        await self.edit_message(
            chat_id,
            message_id,
            f"{emoji} *Analyzing content...*\n\n⏳ Please wait...",
        )

        # Run analysis
        try:
            analysis_result = await self.run_analysis(state)

            if not analysis_result.get("success"):
                state.step = ConversationStep.IDLE
                error_msg = analysis_result.get("error", "Unknown error")
                # Show error to user
                await self.edit_message(
                    chat_id,
                    message_id,
                    f"❌ *Analysis Failed*\n\n{error_msg}\n\n_Please try again with different content._",
                )
                self.clear_conversation(user_id)
                return {"success": False, "message": error_msg}

            state.analysis_result = analysis_result
            state.matches = analysis_result.get("matches", [])

            # Early duplicate check: stop here if already in DB
            exists, reason = await self._check_content_already_exists(state)
            if exists:
                await self.edit_message(
                    chat_id,
                    message_id,
                    f"ℹ️ *Already Available*\n\n{reason}\n\nThis content is already in MediaFusion, so import is skipped.",
                )
                self.clear_conversation(user_id)
                return {"success": True, "message": "Already exists"}

            # For sports: skip match selection and show sports category picker
            if media_type == "sports":
                return await self.show_sports_category_picker(state, chat_id, message_id)

            # Show matches for movie/series
            return await self.show_matches(state, chat_id, message_id)

        except Exception as e:
            logger.exception(f"Error during analysis: {e}")
            state.step = ConversationStep.IDLE
            # Show error to user
            await self.edit_message(
                chat_id,
                message_id,
                f"❌ *Analysis Error*\n\n{str(e)}\n\n_Please try again._",
            )
            self.clear_conversation(user_id)
            return {"success": False, "message": str(e)}

    async def run_analysis(self, state: ConversationState) -> dict:
        """Run content analysis based on content type.

        This calls the appropriate analyze function from the import modules.

        Args:
            state: Conversation state

        Returns:
            Analysis result dict
        """
        content_type = state.content_type
        raw_input = state.raw_input
        media_type = state.media_type

        try:
            if content_type == ContentType.MAGNET:
                return await self._analyze_magnet(raw_input, media_type)
            elif content_type == ContentType.TORRENT_FILE:
                return await self._analyze_torrent_file(raw_input, media_type)
            elif content_type == ContentType.TORRENT_URL:
                return await self._analyze_torrent_url(raw_input, media_type)
            elif content_type == ContentType.VIDEO:
                return await self._analyze_video(raw_input, media_type)
            elif content_type == ContentType.YOUTUBE:
                return await self._analyze_youtube(raw_input, media_type)
            elif content_type == ContentType.HTTP:
                return await self._analyze_http(raw_input, media_type)
            elif content_type == ContentType.NZB:
                return await self._analyze_nzb(raw_input, media_type)
            elif content_type == ContentType.ACESTREAM:
                return await self._analyze_acestream(raw_input, media_type)
            else:
                return {"success": False, "error": f"Unsupported content type: {content_type}"}
        except Exception as e:
            logger.exception(f"Analysis error for {content_type}: {e}")
            return {"success": False, "error": str(e)}

    async def _analyze_magnet(self, magnet_link: str, media_type: str) -> dict:
        """Analyze a magnet link."""
        info_hash, trackers = torrent.parse_magnet(magnet_link)
        if not info_hash:
            return {"success": False, "error": "Invalid magnet link format."}

        try:
            # Fetch torrent metadata from DHT
            torrent_data_list = await torrent.info_hashes_to_torrent_metadata(
                [info_hash], trackers, is_raise_error=True
            )

            if not torrent_data_list or not torrent_data_list[0]:
                return {"success": False, "error": "Failed to fetch torrent metadata from DHT network."}

            torrent_data = torrent_data_list[0]

            # Search for matches
            matches = []
            if torrent_data.get("title"):
                try:
                    matches = await meta_fetcher.search_multiple_results(
                        title=torrent_data["title"],
                        year=torrent_data.get("year"),
                        media_type=media_type,
                    )
                except Exception:
                    pass

            total_size = torrent_data.get("total_size", 0)
            return {
                "success": True,
                "info_hash": info_hash.lower(),
                "torrent_name": torrent_data.get("torrent_name"),
                "total_size": total_size,
                "total_size_readable": convert_bytes_to_readable(total_size) if total_size else "Unknown",
                "file_count": len(torrent_data.get("file_data", [])),
                "files": torrent_data.get("file_data", []),
                "parsed_title": torrent_data.get("title"),
                "year": torrent_data.get("year"),
                "resolution": torrent_data.get("resolution"),
                "quality": torrent_data.get("quality"),
                "codec": torrent_data.get("codec"),
                "audio": torrent_data.get("audio"),
                "matches": matches,
            }

        except Exception as e:
            return {"success": False, "error": f"DHT lookup failed: {str(e)}"}

    async def _analyze_torrent_file(self, file_info: dict, media_type: str) -> dict:
        """Analyze a torrent file by downloading and parsing it."""
        file_id = file_info.get("file_id")
        if not file_id:
            return {"success": False, "error": "No file ID provided."}

        # Download the torrent file
        try:
            file_data = await self.get_file_info(file_id)
            if not file_data:
                return {"success": False, "error": "Failed to get file info from Telegram."}

            file_path = file_data.get("file_path")
            if not file_path:
                return {"success": False, "error": "No file path in response."}

            # Download the file content
            async with aiohttp.ClientSession() as session:
                download_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
                async with session.get(download_url) as response:
                    if response.status != 200:
                        return {"success": False, "error": "Failed to download torrent file."}
                    torrent_content = await response.read()

            # Parse the torrent
            torrent_data = torrent.extract_torrent_metadata(torrent_content, is_raise_error=True)
            if not torrent_data:
                return {"success": False, "error": "Failed to parse torrent file."}

            # Store the torrent content for later import
            file_info["torrent_content"] = torrent_content

            # Search for matches
            matches = []
            if torrent_data.get("title"):
                try:
                    matches = await meta_fetcher.search_multiple_results(
                        title=torrent_data["title"],
                        year=torrent_data.get("year"),
                        media_type=media_type,
                    )
                except Exception:
                    pass

            total_size = torrent_data.get("total_size", 0)
            return {
                "success": True,
                "info_hash": torrent_data.get("info_hash", "").lower(),
                "torrent_name": torrent_data.get("torrent_name"),
                "total_size": total_size,
                "total_size_readable": convert_bytes_to_readable(total_size) if total_size else "Unknown",
                "file_count": len(torrent_data.get("file_data", [])),
                "files": torrent_data.get("file_data", []),
                "parsed_title": torrent_data.get("title"),
                "year": torrent_data.get("year"),
                "resolution": torrent_data.get("resolution"),
                "quality": torrent_data.get("quality"),
                "codec": torrent_data.get("codec"),
                "audio": torrent_data.get("audio"),
                "matches": matches,
            }

        except Exception as e:
            return {"success": False, "error": f"Torrent analysis failed: {str(e)}"}

    async def _analyze_torrent_url(self, torrent_url: str, media_type: str) -> dict:
        """Analyze a torrent file URL by fetching and parsing it.

        This handles URLs that return torrent file content directly, such as:
        - https://example.com/download.torrent
        - https://tracker.example.com/torrents/12345/download
        """
        try:
            # Fetch the torrent file from URL
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    torrent_url,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                ) as response:
                    if response.status != 200:
                        return {
                            "success": False,
                            "error": f"Failed to fetch torrent file (HTTP {response.status}).",
                        }

                    # Note: content_type not used currently, but could be validated
                    torrent_content = await response.read()

                    # Validate it looks like a torrent file
                    # Torrent files are bencoded and typically start with 'd' (dict)
                    if not torrent_content or torrent_content[0:1] != b"d":
                        # Check if it's HTML (error page)
                        if b"<html" in torrent_content[:100].lower() or b"<!doctype" in torrent_content[:100].lower():
                            return {
                                "success": False,
                                "error": "URL returned HTML instead of torrent file. The link may require authentication.",
                            }
                        return {
                            "success": False,
                            "error": "URL did not return valid torrent file content.",
                        }

            # Parse the torrent
            torrent_data = torrent.extract_torrent_metadata(torrent_content, is_raise_error=True)
            if not torrent_data:
                return {"success": False, "error": "Failed to parse torrent file content."}

            # Search for matches
            matches = []
            if torrent_data.get("title"):
                try:
                    matches = await meta_fetcher.search_multiple_results(
                        title=torrent_data["title"],
                        year=torrent_data.get("year"),
                        media_type=media_type,
                    )
                except Exception:
                    pass

            total_size = torrent_data.get("total_size", 0)
            return {
                "success": True,
                "info_hash": torrent_data.get("info_hash", "").lower(),
                "torrent_name": torrent_data.get("torrent_name"),
                "total_size": total_size,
                "total_size_readable": convert_bytes_to_readable(total_size) if total_size else "Unknown",
                "file_count": len(torrent_data.get("file_data", [])),
                "files": torrent_data.get("file_data", []),
                "parsed_title": torrent_data.get("title"),
                "year": torrent_data.get("year"),
                "resolution": torrent_data.get("resolution"),
                "quality": torrent_data.get("quality"),
                "codec": torrent_data.get("codec"),
                "audio": torrent_data.get("audio"),
                "matches": matches,
                "torrent_content": torrent_content,  # Store for import
                "source_url": torrent_url,
            }

        except aiohttp.ClientError as e:
            return {"success": False, "error": f"Network error fetching torrent: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Torrent URL analysis failed: {str(e)}"}

    async def _analyze_video(self, video_info: dict, media_type: str) -> dict:
        """Analyze a video file using filename parsing."""
        file_name = video_info.get("file_name") or "unknown_video"
        file_size = video_info.get("file_size", 0)

        # Validate file size
        min_size = settings.min_scraping_video_size
        if file_size and file_size < min_size:
            return {
                "success": False,
                "error": f"File too small (minimum {min_size // (1024 * 1024)} MB required).",
            }

        # Parse filename
        parsed = {}
        search_title = None
        search_year = None

        try:
            parsed = PTT.parse_title(file_name, True)
            search_title = parsed.get("title")
            search_year = parsed.get("year")
        except Exception as e:
            logger.debug(f"PTT parsing error: {e}")

        # Search for matches
        matches = []
        if search_title:
            try:
                matches = await search_multiple_imdb(
                    title=search_title,
                    limit=5,
                    year=search_year,
                    media_type=media_type,
                    min_similarity=60,
                )
            except Exception:
                pass

        return {
            "success": True,
            "file_name": file_name,
            "file_id": video_info.get("file_id"),
            "file_unique_id": video_info.get("file_unique_id"),
            "file_size": file_size,
            "file_size_readable": convert_bytes_to_readable(file_size) if file_size else "Unknown",
            "mime_type": video_info.get("mime_type"),
            "parsed_title": search_title,
            "year": search_year,
            "resolution": parsed.get("resolution"),
            "quality": parsed.get("quality"),
            "codec": parsed.get("codec"),
            "audio": parsed.get("audio"),
            "matches": matches,
        }

    async def _analyze_youtube(self, url_info: dict, media_type: str) -> dict:
        """Analyze a YouTube URL using the shared youtube module."""
        video_id = url_info.get("video_id")
        url = url_info.get("url")

        if not video_id:
            return {"success": False, "error": "Invalid YouTube URL."}

        try:
            # Use shared youtube module for metadata extraction
            video_info = await analyze_youtube_video(video_id)

            title = video_info.title
            duration = video_info.duration
            channel = video_info.channel or ""
            is_live = video_info.is_live

            # Search for matches
            matches = []
            if title:
                try:
                    matches = await meta_fetcher.search_multiple_results(
                        title=title,
                        media_type=media_type,
                    )
                except Exception:
                    pass

            return {
                "success": True,
                "video_id": video_id,
                "url": url,
                "title": title,
                "duration": duration,
                "channel": channel,
                "is_live": is_live,
                "parsed_title": title,
                "matches": matches,
                "thumbnail": video_info.thumbnail,
                "resolution": video_info.resolution,
            }

        except Exception as e:
            return {"success": False, "error": f"YouTube analysis failed: {str(e)}"}

    async def _analyze_http(self, url: str, media_type: str) -> dict:
        """Analyze an HTTP direct link."""
        try:
            # Parse URL to get filename
            parsed_url = urlparse(url)
            path = unquote(parsed_url.path)
            file_name = os.path.basename(path) or "unknown"

            # Try to get content info via HEAD request
            content_length = None
            content_type = None

            async with aiohttp.ClientSession() as session:
                try:
                    async with session.head(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                        content_length = response.headers.get("Content-Length")
                        content_type = response.headers.get("Content-Type", "")
                except Exception:
                    pass

            # Parse filename for metadata
            parsed = {}
            search_title = None
            try:
                parsed = PTT.parse_title(file_name, True)
                search_title = parsed.get("title")
            except Exception:
                search_title = file_name

            # Search for matches
            matches = []
            if search_title:
                try:
                    matches = await meta_fetcher.search_multiple_results(
                        title=search_title,
                        year=parsed.get("year"),
                        media_type=media_type,
                    )
                except Exception:
                    pass

            size = int(content_length) if content_length else None
            return {
                "success": True,
                "url": url,
                "file_name": file_name,
                "content_type": content_type,
                "file_size": size,
                "file_size_readable": convert_bytes_to_readable(size) if size else "Unknown",
                "parsed_title": search_title,
                "year": parsed.get("year"),
                "resolution": parsed.get("resolution"),
                "quality": parsed.get("quality"),
                "codec": parsed.get("codec"),
                "matches": matches,
            }

        except Exception as e:
            return {"success": False, "error": f"HTTP analysis failed: {str(e)}"}

    async def _analyze_nzb(self, nzb_url: str, media_type: str) -> dict:
        """Analyze an NZB URL."""
        try:
            # Parse URL to get filename
            parsed_url = urlparse(nzb_url)
            path = unquote(parsed_url.path)
            file_name = os.path.basename(path) or "unknown.nzb"
            nzb_name = file_name.replace(".nzb", "")

            # Parse filename for metadata
            parsed = {}
            search_title = None
            try:
                parsed = PTT.parse_title(nzb_name, True)
                search_title = parsed.get("title")
            except Exception:
                search_title = nzb_name

            # Search for matches
            matches = []
            if search_title:
                try:
                    matches = await meta_fetcher.search_multiple_results(
                        title=search_title,
                        year=parsed.get("year"),
                        media_type=media_type,
                    )
                except Exception:
                    pass

            return {
                "success": True,
                "nzb_url": nzb_url,
                "nzb_name": nzb_name,
                "parsed_title": search_title,
                "year": parsed.get("year"),
                "resolution": parsed.get("resolution"),
                "quality": parsed.get("quality"),
                "codec": parsed.get("codec"),
                "matches": matches,
            }

        except Exception as e:
            return {"success": False, "error": f"NZB analysis failed: {str(e)}"}

    async def _analyze_acestream(self, content_id: str, media_type: str) -> dict:
        """Analyze an AceStream content ID."""
        # AceStream doesn't provide much metadata, so we just validate the ID
        if not content_id or len(content_id) != 40:
            return {"success": False, "error": "Invalid AceStream content ID (must be 40 hex characters)."}

        # Try to verify it's valid hex
        try:
            int(content_id, 16)
        except ValueError:
            return {"success": False, "error": "Invalid AceStream content ID (not valid hex)."}

        return {
            "success": True,
            "content_id": content_id.lower(),
            "parsed_title": None,
            "matches": [],  # No matches without metadata
        }

    async def show_matches(self, state: ConversationState, chat_id: int, message_id: int) -> dict:
        """Show match selection keyboard after analysis.

        Args:
            state: Conversation state
            chat_id: Chat ID
            message_id: Message ID to edit

        Returns:
            Dict with result
        """
        state.step = ConversationStep.AWAITING_MATCH
        state.touch()
        self._persist_conversation(state)

        analysis = state.analysis_result
        matches = state.matches or []

        # Build analysis summary
        summary_parts = []
        if analysis.get("torrent_name"):
            summary_parts.append(f"*Name:* `{analysis['torrent_name'][:60]}`")
        elif analysis.get("file_name"):
            summary_parts.append(f"*File:* `{analysis['file_name'][:60]}`")
        elif analysis.get("nzb_name"):
            summary_parts.append(f"*NZB:* `{analysis['nzb_name'][:60]}`")
        elif analysis.get("title"):
            summary_parts.append(f"*Title:* {analysis['title'][:60]}")

        if analysis.get("total_size_readable"):
            summary_parts.append(f"*Size:* {analysis['total_size_readable']}")
        elif analysis.get("file_size_readable"):
            summary_parts.append(f"*Size:* {analysis['file_size_readable']}")

        if analysis.get("file_count") and analysis["file_count"] > 1:
            summary_parts.append(f"*Files:* {analysis['file_count']}")

        # Show parsed metadata
        meta_parts = []
        if analysis.get("resolution"):
            meta_parts.append(analysis["resolution"])
        if analysis.get("quality"):
            meta_parts.append(analysis["quality"])
        if analysis.get("codec"):
            meta_parts.append(analysis["codec"])
        if meta_parts:
            summary_parts.append(f"*Quality:* {' | '.join(meta_parts)}")

        summary = "\n".join(summary_parts) if summary_parts else "*Content analyzed*"

        # Build keyboard
        keyboard = []
        match_details = []
        if matches:
            for idx, match in enumerate(matches[:5], 1):
                canonical_id = self._get_canonical_external_id(match) or ""
                title = match.get("title", "Unknown")
                year = match.get("year", "")
                match_type = match.get("type", "movie")
                type_emoji = "🎬" if match_type == "movie" else "📺"
                ids = self._collect_match_ids(match)
                id_parts = []
                if ids.get("imdb"):
                    id_parts.append(f"IMDb `{ids['imdb']}`")
                if ids.get("tmdb"):
                    id_parts.append(f"TMDB `{ids['tmdb']}`")
                if ids.get("tvdb"):
                    id_parts.append(f"TVDB `{ids['tvdb']}`")
                if ids.get("mal"):
                    id_parts.append(f"MAL `{ids['mal']}`")
                if ids.get("kitsu"):
                    id_parts.append(f"Kitsu `{ids['kitsu']}`")

                # Show the most recognizable ID in the button text
                short_id = ""
                if ids.get("imdb"):
                    short_id = ids["imdb"]
                elif ids.get("tmdb"):
                    short_id = f"tmdb:{ids['tmdb']}"
                elif ids.get("tvdb"):
                    short_id = f"tvdb:{ids['tvdb']}"
                elif ids.get("mal"):
                    short_id = f"mal:{ids['mal']}"
                elif ids.get("kitsu"):
                    short_id = f"kitsu:{ids['kitsu']}"

                button_text = f"{idx}. {type_emoji} {title}"
                if year:
                    button_text += f" ({year})"
                if short_id:
                    button_text += f" [{short_id}]"
                if len(button_text) > 60:
                    button_text = button_text[:57] + "..."

                keyboard.append(
                    [
                        {
                            "text": button_text,
                            "callback_data": f"match:{state.user_id}:{canonical_id}",
                        }
                    ]
                )
                if id_parts:
                    match_details.append(f"{idx}. {title} ({year or 'N/A'}) — " + " | ".join(id_parts))
                else:
                    match_details.append(f"{idx}. {title} ({year or 'N/A'})")

        # Add manual entry and cancel
        keyboard.append([{"text": "📝 Enter external ID manually", "callback_data": f"manual:{state.user_id}"}])
        keyboard.append([{"text": "❌ Cancel", "callback_data": f"cancel:{state.user_id}"}])

        if matches:
            details_block = "\n".join(match_details) if match_details else ""
            message = (
                f"✅ *Analysis Complete*\n\n{summary}\n\n"
                f"🔍 *Found {len(matches)} match(es):*\n"
                f"{details_block}\n\n"
                f"Select a match:"
            )
        else:
            message = f"✅ *Analysis Complete*\n\n{summary}\n\n❌ No automatic matches found.\n\nEnter an external ID manually:"

        await self.edit_message(chat_id, message_id, message, {"inline_keyboard": keyboard})

        return {"success": True, "matches_count": len(matches)}

    # ============================================
    # Sports Category Flow
    # ============================================

    async def show_sports_category_picker(self, state: ConversationState, chat_id: int, message_id: int) -> dict:
        """Show sports category selection after analysis (for sports media type).

        Auto-detects the sports category from the content title and shows
        a confirmation option. User can confirm the detected category or
        select a different one.

        Args:
            state: Conversation state
            chat_id: Chat ID
            message_id: Message ID to edit

        Returns:
            Dict with result
        """
        state.step = ConversationStep.AWAITING_SPORTS_CATEGORY
        state.touch()
        self._persist_conversation(state)

        analysis = state.analysis_result or {}

        # Get the title for category detection
        name = analysis.get("torrent_name") or analysis.get("file_name") or analysis.get("title") or ""
        parsed_title = analysis.get("parsed_title") or name

        # Try to auto-detect the sports category
        detected_category = detect_sports_category(parsed_title) or detect_sports_category(name)

        # Build summary
        summary_parts = []
        if name:
            summary_parts.append(f"*Name:* `{name[:80]}`")
        if analysis.get("total_size_readable"):
            summary_parts.append(f"*Size:* {analysis['total_size_readable']}")
        elif analysis.get("file_size_readable"):
            summary_parts.append(f"*Size:* {analysis['file_size_readable']}")
        summary = "\n".join(summary_parts) if summary_parts else "*Content analyzed*"

        # Build keyboard based on whether we detected a category
        keyboard_rows = []

        if detected_category:
            # Show detected category with confirm button at top
            detected_label = SPORTS_CATEGORIES.get(detected_category, "Sports")
            keyboard_rows.append(
                [
                    {
                        "text": f"✅ Confirm: {detected_label}",
                        "callback_data": f"sport:{state.user_id}:{detected_category}",
                    }
                ]
            )

            # Add separator row with "Or select different:" label as a non-clickable display
            # Since Telegram doesn't support non-clickable buttons, we'll just list other categories

            # Build sports category keyboard (2 per row), excluding the detected one
            items = [(k, v) for k, v in SPORTS_CATEGORIES.items() if k != detected_category]
            for i in range(0, len(items), 2):
                row = []
                for key, label in items[i : i + 2]:
                    row.append({"text": f"🏆 {label}", "callback_data": f"sport:{state.user_id}:{key}"})
                keyboard_rows.append(row)

            message = (
                f"✅ *Analysis Complete*\n\n{summary}\n\n"
                f"🏆 *Detected Category:* {detected_label}\n\n"
                f"_Confirm the detected category or select a different one:_"
            )
        else:
            # No detection - show all categories as before
            items = list(SPORTS_CATEGORIES.items())
            for i in range(0, len(items), 2):
                row = []
                for key, label in items[i : i + 2]:
                    row.append({"text": f"🏆 {label}", "callback_data": f"sport:{state.user_id}:{key}"})
                keyboard_rows.append(row)

            message = f"✅ *Analysis Complete*\n\n{summary}\n\n🏆 *Select the sport category:*"

        keyboard_rows.append(
            [
                {"text": "⬅️ Back", "callback_data": f"back:{state.user_id}"},
                {"text": "❌ Cancel", "callback_data": f"cancel:{state.user_id}"},
            ]
        )

        await self.edit_message(chat_id, message_id, message, {"inline_keyboard": keyboard_rows})
        return {"success": True, "detected_category": detected_category}

    async def handle_sports_category_selection(
        self, user_id: int, chat_id: int, message_id: int, category_key: str
    ) -> dict:
        """Handle sports category selection and show metadata review.

        For sports, external ID is optional. The bot creates a media record
        using the event title from analysis and the selected sport catalog.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit
            category_key: Sports category key (e.g. 'football', 'basketball')

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            await self.edit_message(
                chat_id, message_id, "❌ *Session Expired*\n\nPlease start over by sending your content again."
            )
            return {"success": False, "message": "Session expired."}

        category_label = SPORTS_CATEGORIES.get(category_key, "Other Sports")
        state.sports_category = category_key

        # Build a selected_match from the analysis for sports
        # External ID is intentionally left empty — sports content doesn't always have one
        analysis = state.analysis_result or {}

        # Get the raw title for sports parsing
        raw_title = analysis.get("torrent_name") or analysis.get("file_name") or analysis.get("title") or ""

        # Use the sports parser to properly clean the title
        # (PTT doesn't handle sports content well)
        parsed = parse_sports_title(raw_title, category=category_key)
        event_title = parsed.title if parsed.title else "Sports Event"

        # Extract year from parsed date or fallback to analysis
        year = parsed.year or analysis.get("year")

        state.selected_match = {
            "title": event_title,
            "year": year,
            "type": "movie",  # Sports VOD content is stored as movie (events type is for live content only)
            "sports_category": category_key,
            "sports_category_label": category_label,
        }

        state.step = ConversationStep.AWAITING_METADATA_REVIEW
        state.touch()
        self._persist_conversation(state)

        return await self.show_metadata_review(state, chat_id, message_id)

    # ============================================
    # Match Selection (movie / series)
    # ============================================

    async def handle_match_selection(self, user_id: int, chat_id: int, message_id: int, external_id: str) -> dict:
        """Handle match selection and show metadata review.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit
            external_id: Selected external ID (e.g. tt1234567, tmdb:123, tvdb:456)

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            await self.edit_message(
                chat_id, message_id, "❌ *Session Expired*\n\nPlease start over by sending your content again."
            )
            return {"success": False, "message": "Session expired. Please start over."}

        # Find the selected match by comparing canonical IDs
        selected_match = None
        for match in state.matches or []:
            canonical = self._get_canonical_external_id(match) or ""
            if canonical == external_id:
                selected_match = match
                break

        if not selected_match:
            # Create a basic match from the provided external ID
            selected_match = {"id": external_id, "title": external_id, "year": None}
            # Populate the appropriate ID field based on prefix
            if external_id.startswith("tt"):
                selected_match["imdb_id"] = external_id
            elif ":" in external_id:
                provider, pid = external_id.split(":", 1)
                selected_match[f"{provider}_id"] = pid

        state.selected_match = selected_match
        state.step = ConversationStep.AWAITING_METADATA_REVIEW
        state.touch()
        self._persist_conversation(state)

        return await self.show_metadata_review(state, chat_id, message_id)

    async def handle_manual_imdb_input(self, user_id: int, chat_id: int, message_id: int) -> dict:
        """Show manual external ID input prompt.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            return {"success": False, "message": "Session expired. Please start over."}

        state.step = ConversationStep.AWAITING_MANUAL_IMDB
        state.touch()
        self._persist_conversation(state)

        message = (
            "📝 *Enter External ID*\n\n"
            "Please reply with the external ID for this content.\n\n"
            "*Supported formats:*\n"
            "• IMDb: `tt1234567`\n"
            "• TMDB: `tmdb:12345`\n"
            "• TVDB: `tvdb:12345`\n"
            "• MAL: `mal:12345`\n"
            "• Kitsu: `kitsu:12345`\n\n"
            "You can find these IDs on the respective metadata provider websites."
        )

        keyboard = [[{"text": "❌ Cancel", "callback_data": f"cancel:{user_id}"}]]

        await self.edit_message(chat_id, message_id, message, {"inline_keyboard": keyboard})

        return {"success": True, "awaiting_input": True}

    async def process_manual_imdb_input(self, user_id: int, chat_id: int, text: str) -> dict:
        """Process manually entered external ID (IMDb, TMDB, TVDB, MAL, Kitsu).

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            text: User's text input

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state or state.step != ConversationStep.AWAITING_MANUAL_IMDB:
            return {"success": False, "handled": False}

        # Try to extract any supported external ID format
        ext_match = EXTERNAL_ID_PATTERN.search(text)
        if not ext_match:
            return {
                "success": False,
                "handled": True,
                "message": (
                    "❌ Invalid external ID format.\n\n"
                    "*Supported formats:*\n"
                    "• IMDb: `tt1234567`\n"
                    "• TMDB: `tmdb:12345`\n"
                    "• TVDB: `tvdb:12345`\n"
                    "• MAL: `mal:12345`\n"
                    "• Kitsu: `kitsu:12345`"
                ),
            }

        external_id = ext_match.group(0)

        # Build the selected_match based on the ID type
        selected_match: dict[str, Any] = {"id": external_id, "title": external_id, "year": None}

        if external_id.startswith("tt"):
            selected_match["imdb_id"] = external_id
            # Try to fetch metadata from IMDb
            try:
                metadata = await get_imdb_title_data(external_id, state.media_type or "movie")
                if metadata:
                    selected_match["title"] = metadata.get("title", external_id)
                    selected_match["year"] = metadata.get("year")
                    selected_match["type"] = metadata.get("type", "movie")
            except Exception:
                pass
        elif ":" in external_id:
            provider, pid = external_id.split(":", 1)
            selected_match[f"{provider}_id"] = pid
            # Try to fetch metadata from the provider
            try:
                metadata = await meta_fetcher.get_metadata_from_provider(provider, pid, state.media_type or "movie")
                if metadata:
                    selected_match["title"] = metadata.get("title", external_id)
                    selected_match["year"] = metadata.get("year")
                    selected_match["type"] = metadata.get("type", "movie")
                    # Also copy over any external IDs from the fetched metadata
                    if metadata.get("imdb_id"):
                        selected_match["imdb_id"] = metadata["imdb_id"]
                    if metadata.get("tmdb_id"):
                        selected_match["tmdb_id"] = metadata["tmdb_id"]
                    if metadata.get("external_ids"):
                        selected_match["external_ids"] = metadata["external_ids"]
            except Exception:
                pass

        state.selected_match = selected_match
        state.step = ConversationStep.AWAITING_METADATA_REVIEW
        state.touch()
        self._persist_conversation(state)

        # Send metadata review (as new message since we're responding to user's text)
        result = await self.show_metadata_review(state, chat_id, None)
        return {
            "success": True,
            "handled": True,
            "message": result.get("message"),
            "reply_markup": result.get("reply_markup"),
        }

    async def show_metadata_review(self, state: ConversationState, chat_id: int, message_id: int | None) -> dict:
        """Show metadata review with edit options.

        Args:
            state: Conversation state
            chat_id: Chat ID
            message_id: Message ID to edit (or None to send new)

        Returns:
            Dict with result
        """
        analysis = state.analysis_result or {}
        match = state.selected_match or {}
        overrides = state.metadata_overrides
        is_sports = state.media_type == "sports"

        title = match.get("title", "Unknown")
        year = match.get("year")
        year_str = f" ({year})" if year else ""

        # Build ID display — optional for sports
        ids = self._collect_match_ids(match)
        if ids:
            id_parts = []
            for provider, pid in ids.items():
                label = provider.upper() if provider in ("imdb", "tmdb", "tvdb", "mal") else provider.title()
                id_parts.append(f"{label}: `{pid}`")
            id_display = " | ".join(id_parts)
        elif is_sports:
            id_display = "_None (optional for sports)_"
        else:
            canonical_id = self._get_canonical_external_id(match) or "Unknown"
            id_display = f"`{canonical_id}`"

        # Get metadata values (override > analysis > None)
        resolution = overrides.get("resolution") or analysis.get("resolution") or "Auto"
        quality = overrides.get("quality") or analysis.get("quality") or "Auto"
        codec = overrides.get("codec") or analysis.get("codec") or "Auto"
        audio = overrides.get("audio") or analysis.get("audio") or "Auto"
        languages = overrides.get("languages") or "English"

        # Build message
        size_str = analysis.get("total_size_readable") or analysis.get("file_size_readable") or "Unknown"

        # Custom poster info
        poster_line = ""
        if state.custom_poster_url:
            poster_line = "🖼️ *Poster:* Custom ✓\n"
        elif is_sports:
            poster_line = "🖼️ *Poster:* Auto (sports)\n"

        # Sports-specific header
        if is_sports:
            category_label = match.get("sports_category_label") or SPORTS_CATEGORIES.get(
                state.sports_category or "", "Sports"
            )
            message = (
                f"📋 *Review Import Details*\n\n"
                f"🏆 *{title}*{year_str}\n"
                f"⚽ *Category:* {category_label}\n"
                f"🆔 {id_display}\n\n"
                f"📦 *Size:* {size_str}\n"
                f"📐 *Resolution:* {resolution}\n"
                f"🎞 *Quality:* {quality}\n"
                f"💿 *Codec:* {codec}\n"
                f"🔊 *Audio:* {audio}\n"
                f"🌐 *Languages:* {languages}\n"
                f"{poster_line}\n"
                f"_Tap a field to edit, or confirm to import._"
            )
        else:
            message = (
                f"📋 *Review Import Details*\n\n"
                f"🎬 *{title}*{year_str}\n"
                f"🆔 {id_display}\n\n"
                f"📦 *Size:* {size_str}\n"
                f"📐 *Resolution:* {resolution}\n"
                f"🎞 *Quality:* {quality}\n"
                f"💿 *Codec:* {codec}\n"
                f"🔊 *Audio:* {audio}\n"
                f"🌐 *Languages:* {languages}\n"
                f"{poster_line}\n"
                f"_Tap a field to edit, or confirm to import._"
            )

        # Poster button text
        poster_btn_text = "🖼️ ✓ Poster" if state.custom_poster_url else "🖼️ Add Poster"

        # Build edit keyboard
        keyboard = [
            [
                {"text": f"📐 {resolution}", "callback_data": f"meta_edit:{state.user_id}:resolution"},
                {"text": f"🎞 {quality}", "callback_data": f"meta_edit:{state.user_id}:quality"},
            ],
            [
                {"text": f"💿 {codec}", "callback_data": f"meta_edit:{state.user_id}:codec"},
                {"text": f"🔊 {audio}", "callback_data": f"meta_edit:{state.user_id}:audio"},
            ],
            [
                {"text": f"🌐 {languages}", "callback_data": f"meta_edit:{state.user_id}:languages"},
                {"text": poster_btn_text, "callback_data": f"add_poster:{state.user_id}"},
            ],
            [
                {"text": "✅ Confirm Import", "callback_data": f"confirm:{state.user_id}"},
            ],
            [
                {"text": "⬅️ Back", "callback_data": f"back:{state.user_id}"},
                {"text": "❌ Cancel", "callback_data": f"cancel:{state.user_id}"},
            ],
        ]

        if message_id:
            await self.edit_message(chat_id, message_id, message, {"inline_keyboard": keyboard})
        else:
            state.message_id = None  # Will be set by caller

        return {
            "success": True,
            "message": message,
            "reply_markup": {"inline_keyboard": keyboard},
        }

    async def show_field_editor(self, user_id: int, chat_id: int, message_id: int, field: str) -> dict:
        """Show field value selection keyboard.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit
            field: Field name to edit

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            return {"success": False, "message": "Session expired. Please start over."}

        state.editing_field = field
        state.step = ConversationStep.AWAITING_FIELD_EDIT
        state.touch()
        self._persist_conversation(state)

        # Get options based on field
        field_config = {
            "resolution": ("📐 Resolution", RESOLUTION_OPTIONS),
            "quality": ("🎞 Quality", QUALITY_OPTIONS),
            "codec": ("💿 Codec", CODEC_OPTIONS),
            "audio": ("🔊 Audio", AUDIO_OPTIONS),
            "languages": ("🌐 Languages", LANGUAGE_OPTIONS),
        }

        field_name, options = field_config.get(field, (field.title(), []))

        message = f"*Select {field_name}:*"

        # Build options keyboard (2 per row)
        keyboard = []
        for i in range(0, len(options), 2):
            row = []
            for opt in options[i : i + 2]:
                row.append({"text": opt, "callback_data": f"meta_val:{user_id}:{field}:{opt}"})
            keyboard.append(row)

        # Add back button
        keyboard.append([{"text": "⬅️ Back to Review", "callback_data": f"back_review:{user_id}"}])

        await self.edit_message(chat_id, message_id, message, {"inline_keyboard": keyboard})

        return {"success": True}

    async def handle_field_value_selection(
        self, user_id: int, chat_id: int, message_id: int, field: str, value: str
    ) -> dict:
        """Handle field value selection.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit
            field: Field name
            value: Selected value

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            return {"success": False, "message": "Session expired. Please start over."}

        state.metadata_overrides[field] = value
        state.step = ConversationStep.AWAITING_METADATA_REVIEW
        state.touch()
        self._persist_conversation(state)

        return await self.show_metadata_review(state, chat_id, message_id)

    async def show_poster_input_prompt(self, user_id: int, chat_id: int, message_id: int) -> dict:
        """Show prompt for user to provide a poster image URL or upload an image.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            return {"success": False, "message": "Session expired. Please start over."}

        state.step = ConversationStep.AWAITING_POSTER_INPUT
        state.touch()
        self._persist_conversation(state)

        message = (
            "🖼️ *Add Custom Poster*\n\n"
            "You can provide a poster image in one of the following ways:\n\n"
            "• *Send an image URL* - Paste a direct link to an image (jpg, png, webp)\n"
            "• *Upload an image* - Send an image file directly\n\n"
            "_The image will be used as the poster for this content._"
        )

        keyboard = [
            [{"text": "⬅️ Back to Review", "callback_data": f"back_review:{user_id}"}],
        ]

        # Add clear button if there's already a custom poster
        if state.custom_poster_url:
            keyboard.insert(0, [{"text": "🗑️ Clear Custom Poster", "callback_data": f"clear_poster:{user_id}"}])

        await self.edit_message(chat_id, message_id, message, {"inline_keyboard": keyboard})
        return {"success": True}

    async def handle_poster_input(self, user_id: int, chat_id: int, poster_url: str) -> dict:
        """Handle poster URL or image upload from user.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            poster_url: URL of the poster image

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            return {"success": False, "message": "Session expired. Please start over."}

        # Validate URL looks like an image
        lower_url = poster_url.lower()
        valid_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif")
        is_valid_url = poster_url.startswith("http") and (
            any(lower_url.endswith(ext) or ext in lower_url for ext in valid_extensions)
            or "image" in lower_url
            or "/photo/" in lower_url
            or "imgur" in lower_url
            or "postimg" in lower_url
        )

        if not is_valid_url:
            await self.send_message(
                chat_id,
                "⚠️ *Invalid Image URL*\n\nPlease send a valid image URL (jpg, png, webp) or upload an image directly.",
            )
            return {"success": False, "message": "Invalid image URL"}

        state.custom_poster_url = poster_url
        state.step = ConversationStep.AWAITING_METADATA_REVIEW
        state.touch()
        self._persist_conversation(state)

        # Go back to review with confirmation
        await self.send_message(chat_id, "✅ *Poster Added*\n\nCustom poster has been set.")

        if state.message_id:
            await self.show_metadata_review(state, chat_id, state.message_id)

        return {"success": True}

    async def execute_import(self, user_id: int, chat_id: int, message_id: int) -> dict:
        """Execute the import and create the stream/contribution.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            return {"success": False, "message": "Session expired. Please start over."}

        state.step = ConversationStep.IMPORTING
        state.touch()
        self._persist_conversation(state)

        # Show importing message
        await self.edit_message(chat_id, message_id, "⏳ *Importing content...*\n\nPlease wait...")

        try:
            # Get MediaFusion user ID
            is_linked, mf_user_id = await self.check_user_linked(state.user_id)
            if not mf_user_id:
                return {"success": False, "message": "❌ Account not linked. Please run /login first."}

            # For sports: use the sports-specific import path
            content_type = state.content_type
            result = None

            if state.media_type == "sports":
                result = await self._import_sports(state, mf_user_id)
            elif content_type == ContentType.VIDEO:
                result = await self._import_video(state, mf_user_id)
            elif content_type == ContentType.MAGNET:
                result = await self._import_magnet(state, mf_user_id)
            elif content_type == ContentType.TORRENT_FILE:
                result = await self._import_torrent_file(state, mf_user_id)
            elif content_type == ContentType.TORRENT_URL:
                result = await self._import_torrent_url(state, mf_user_id)
            elif content_type == ContentType.YOUTUBE:
                result = await self._import_youtube(state, mf_user_id)
            elif content_type == ContentType.HTTP:
                result = await self._import_http(state, mf_user_id)
            elif content_type == ContentType.NZB:
                result = await self._import_nzb(state, mf_user_id)
            elif content_type == ContentType.ACESTREAM:
                result = await self._import_acestream(state, mf_user_id)
            else:
                result = {"success": False, "error": f"Unsupported content type: {content_type}"}

            if result and result.get("success"):
                match = state.selected_match or {}
                title = match.get("title", "Content")
                year = match.get("year")
                year_str = f" ({year})" if year else ""
                is_sports = state.media_type == "sports"

                # Build ID block — optional for sports
                ids = self._collect_match_ids(match)
                id_lines = []
                for provider, pid in ids.items():
                    label = provider.upper() if provider in ("imdb", "tmdb", "tvdb", "mal") else provider.title()
                    id_lines.append(f"  {label}: `{pid}`")

                if is_sports:
                    category_label = match.get("sports_category_label") or SPORTS_CATEGORIES.get(
                        state.sports_category or "", "Sports"
                    )
                    header = f"🏆 *{title}*{year_str}\n⚽ *Category:* {category_label}"
                    if id_lines:
                        header += "\n🆔 IDs:\n" + "\n".join(id_lines)
                else:
                    canonical_id = self._get_canonical_external_id(match) or "Unknown"
                    id_block = "\n".join(id_lines) if id_lines else f"  `{canonical_id}`"
                    header = f"🎬 *{title}*{year_str}\n🆔 IDs:\n{id_block}"

                success_message = f"✅ *Import Successful!*\n\n{header}\n\nYour content has been added to MediaFusion!"

                if result.get("auto_approved"):
                    success_message += "\n\n✓ _Auto-approved_"
                else:
                    success_message += "\n\n⏳ _Pending review_"

                await self.edit_message(chat_id, message_id, success_message)
                self.clear_conversation(user_id)
                return {"success": True, "message": "Import successful"}
            else:
                error = result.get("error", "Unknown error") if result else "Import failed"
                await self.edit_message(chat_id, message_id, f"❌ *Import Failed*\n\n{error}")
                state.step = ConversationStep.AWAITING_METADATA_REVIEW
                self._persist_conversation(state)
                return {"success": False, "message": error}

        except Exception as e:
            logger.exception(f"Import error: {e}")
            await self.edit_message(chat_id, message_id, f"❌ *Import Error*\n\n{str(e)}")
            state.step = ConversationStep.AWAITING_METADATA_REVIEW
            self._persist_conversation(state)
            return {"success": False, "message": str(e)}

    async def _import_video(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import a video file as TelegramStream."""
        analysis = state.analysis_result or {}
        match = state.selected_match or {}
        overrides = state.metadata_overrides

        external_id = self._get_canonical_external_id(match)
        if not external_id:
            return {"success": False, "error": "No external ID selected."}

        # Build content info for the existing store method
        content_info = {
            "user_id": state.user_id,
            "chat_id": state.chat_id,
            "message_id": state.original_message_id or 0,
            "file_id": analysis.get("file_id"),
            "file_unique_id": analysis.get("file_unique_id"),
            "file_name": analysis.get("file_name", "video"),
            "file_size": analysis.get("file_size"),
            "mime_type": analysis.get("mime_type"),
            "meta_id": external_id,
            "needs_linking": False,
            "resolution": overrides.get("resolution") or analysis.get("resolution"),
            "quality": overrides.get("quality") or analysis.get("quality"),
            "codec": overrides.get("codec") or analysis.get("codec"),
        }

        stored = await self._store_forwarded_content(content_info)
        return {"success": stored, "auto_approved": True, "error": "Failed to store video" if not stored else None}

    async def _import_magnet(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import a magnet link as TorrentStream."""
        analysis = state.analysis_result or {}
        match = state.selected_match or {}
        overrides = state.metadata_overrides

        external_id = self._get_canonical_external_id(match)
        if not external_id:
            return {"success": False, "error": "No external ID selected."}

        info_hash = analysis.get("info_hash")
        if not info_hash:
            return {"success": False, "error": "No info hash from analysis."}
        normalized_hash = str(info_hash).strip().lower()

        async with get_async_session_context() as session:
            # Get or create media
            media = await crud.get_media_by_external_id(session, external_id)
            if not media:
                media = await fetch_and_create_media_from_external(
                    session, external_id, state.media_type, analysis.get("parsed_title")
                )
                if not media:
                    return {"success": False, "error": f"Could not find or create media for {external_id}"}

            # Check if stream already exists
            existing = await session.exec(
                select(TorrentStream).where(func.lower(TorrentStream.info_hash) == normalized_hash)
            )
            if existing.first():
                return {"success": False, "error": "Stream with this info hash already exists."}

            # Get uploader name with anonymity preference
            user = await session.get(User, mf_user_id)
            if user and user.contribute_anonymously:
                uploader_name = "Anonymous"
                uploader_user_id = None
            else:
                uploader_name = user.username or user.email if user else f"User #{mf_user_id}"
                uploader_user_id = mf_user_id

            # Create the torrent stream
            await create_torrent_stream(
                session,
                info_hash=normalized_hash,
                name=analysis.get("torrent_name") or analysis.get("parsed_title") or "Unknown",
                size=analysis.get("total_size") or 0,
                source="telegram_bot",
                media_id=media.id,
                resolution=overrides.get("resolution") or analysis.get("resolution"),
                quality=overrides.get("quality") or analysis.get("quality"),
                codec=overrides.get("codec") or analysis.get("codec"),
                uploader=uploader_name,
                uploader_user_id=uploader_user_id,
            )

            await session.commit()

        return {"success": True, "auto_approved": True}

    async def _import_torrent_file(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import a torrent file."""
        # Similar to magnet import but uses torrent content
        return await self._import_magnet(state, mf_user_id)

    async def _import_torrent_url(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import a torrent from a URL.

        This uses the same logic as magnet/torrent file import since we already
        have the info_hash from the analysis step.
        """
        # The analysis step already fetched and parsed the torrent,
        # so we can reuse the magnet import logic which uses the info_hash
        return await self._import_magnet(state, mf_user_id)

    async def _import_youtube(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import a YouTube video."""
        analysis = state.analysis_result or {}
        match = state.selected_match or {}
        overrides = state.metadata_overrides

        external_id = self._get_canonical_external_id(match)
        if not external_id:
            return {"success": False, "error": "No external ID selected."}

        video_id = analysis.get("video_id")
        if not video_id:
            return {"success": False, "error": "No YouTube video ID."}

        async with get_async_session_context() as session:
            # Get or create media
            media = await crud.get_media_by_external_id(session, external_id)
            if not media:
                media = await fetch_and_create_media_from_external(
                    session, external_id, state.media_type, analysis.get("title")
                )
                if not media:
                    return {"success": False, "error": f"Could not find or create media for {external_id}"}

            # Check if stream already exists
            existing = await session.exec(select(YouTubeStream).where(YouTubeStream.video_id == video_id))
            if existing.first():
                return {"success": False, "error": "This YouTube video is already in the database."}

            # Create the YouTube stream (uses kwargs for extra fields)
            await create_youtube_stream(
                session,
                video_id=video_id,
                name=analysis.get("title") or "YouTube Video",
                media_id=media.id,
                source="telegram_bot",
                is_live=analysis.get("is_live", False),
                uploader_user_id=mf_user_id,
                resolution=overrides.get("resolution") or analysis.get("resolution"),
            )

            await session.commit()

        return {"success": True, "auto_approved": True}

    async def _import_http(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import an HTTP direct link."""
        analysis = state.analysis_result or {}
        match = state.selected_match or {}
        overrides = state.metadata_overrides

        external_id = self._get_canonical_external_id(match)
        if not external_id:
            return {"success": False, "error": "No external ID selected."}

        url = analysis.get("url")
        if not url:
            return {"success": False, "error": "No URL provided."}

        async with get_async_session_context() as session:
            # Get or create media
            media = await crud.get_media_by_external_id(session, external_id)
            if not media:
                media = await fetch_and_create_media_from_external(
                    session, external_id, state.media_type, analysis.get("parsed_title")
                )
                if not media:
                    return {"success": False, "error": f"Could not find or create media for {external_id}"}

            # Create the HTTP stream
            await create_http_stream(
                session,
                url=url,
                name=analysis.get("file_name") or analysis.get("parsed_title") or "HTTP Stream",
                media_id=media.id,
                source="telegram_bot",
                size=analysis.get("file_size"),
                format=analysis.get("content_type"),
                uploader_user_id=mf_user_id,
                resolution=overrides.get("resolution") or analysis.get("resolution"),
                quality=overrides.get("quality") or analysis.get("quality"),
                codec=overrides.get("codec") or analysis.get("codec"),
            )

            await session.commit()

        return {"success": True, "auto_approved": True}

    async def _import_nzb(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import an NZB URL."""
        analysis = state.analysis_result or {}
        match = state.selected_match or {}
        overrides = state.metadata_overrides

        external_id = self._get_canonical_external_id(match)
        if not external_id:
            return {"success": False, "error": "No external ID selected."}

        nzb_url = analysis.get("nzb_url")
        if not nzb_url:
            return {"success": False, "error": "No NZB URL provided."}

        async with get_async_session_context() as session:
            # Get or create media
            media = await crud.get_media_by_external_id(session, external_id)
            if not media:
                media = await fetch_and_create_media_from_external(
                    session, external_id, state.media_type, analysis.get("parsed_title")
                )
                if not media:
                    return {"success": False, "error": f"Could not find or create media for {external_id}"}

            # Generate a unique NZB GUID from the URL
            nzb_guid = hashlib.sha256(nzb_url.encode()).hexdigest()[:32]

            # Check if stream already exists
            existing = await session.exec(select(UsenetStream).where(UsenetStream.nzb_guid == nzb_guid))
            if existing.first():
                return {"success": False, "error": "This NZB is already in the database."}

            # Create the Usenet stream
            await create_usenet_stream(
                session,
                nzb_guid=nzb_guid,
                name=analysis.get("nzb_name") or "NZB Content",
                size=0,  # Size unknown from URL
                indexer="telegram_bot",
                media_id=media.id,
                nzb_url=nzb_url,
                resolution=overrides.get("resolution") or analysis.get("resolution"),
                quality=overrides.get("quality") or analysis.get("quality"),
                codec=overrides.get("codec") or analysis.get("codec"),
                uploader_user_id=mf_user_id,
            )

            await session.commit()

        return {"success": True, "auto_approved": True}

    async def _import_acestream(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import an AceStream content ID."""
        analysis = state.analysis_result or {}
        match = state.selected_match or {}
        overrides = state.metadata_overrides

        external_id = self._get_canonical_external_id(match)
        if not external_id:
            return {"success": False, "error": "No external ID selected."}

        content_id = analysis.get("content_id")
        if not content_id:
            return {"success": False, "error": "No AceStream content ID."}

        async with get_async_session_context() as session:
            # Get or create media
            media = await crud.get_media_by_external_id(session, external_id)
            if not media:
                media = await fetch_and_create_media_from_external(
                    session, external_id, state.media_type, match.get("title")
                )
                if not media:
                    return {"success": False, "error": f"Could not find or create media for {external_id}"}

            # Check if stream already exists
            existing = await session.exec(select(AceStreamStream).where(AceStreamStream.content_id == content_id))
            if existing.first():
                return {"success": False, "error": "This AceStream is already in the database."}

            # Create the AceStream
            await create_acestream_stream(
                session,
                content_id=content_id,
                name=match.get("title") or "AceStream",
                media_id=media.id,
                source="telegram_bot",
                uploader_user_id=mf_user_id,
                resolution=overrides.get("resolution") or analysis.get("resolution"),
            )

            await session.commit()

        return {"success": True, "auto_approved": True}

    async def _import_sports(self, state: ConversationState, mf_user_id: int) -> dict:
        """Import sports content.

        Sports content does not require an external ID (IMDb, TMDB, etc.).
        It creates a Media record with type=MOVIE (for VOD), links it to the
        sports catalog, and creates the appropriate stream type.
        """
        analysis = state.analysis_result or {}
        match = state.selected_match or {}
        overrides = state.metadata_overrides
        sports_category = state.sports_category or "other_sports"

        event_title = (
            match.get("title")
            or analysis.get("parsed_title")
            or analysis.get("torrent_name")
            or analysis.get("file_name")
            or "Sports Event"
        )

        # Get year from match (set in handle_sports_category_selection) or analysis
        year = match.get("year") or analysis.get("year")

        # Check if an external ID was (optionally) set
        external_id = self._get_canonical_external_id(match)

        async with get_async_session_context() as session:
            media = None

            # If we have an external ID, try to find existing media
            if external_id:
                media = await crud.get_media_by_external_id(session, external_id)
                if not media:
                    media = await fetch_and_create_media_from_external(session, external_id, "movie", event_title)

            # If no external ID or creation via external ID failed, create media directly
            # Sports VOD content is stored as movie (events type is for live content only)
            if not media:
                media = Media(
                    type=MediaType.MOVIE,
                    title=event_title,
                    year=year,
                    status="released",  # Set as released so it shows in catalog
                    description=f"{SPORTS_CATEGORIES.get(sports_category, 'Sports')} event",
                )
                session.add(media)
                await session.flush()

                # Add sport-specific genre
                genre = await get_or_create_genre(session, SPORTS_CATEGORIES.get(sports_category, "Sports"))
                session.add(MediaGenreLink(media_id=media.id, genre_id=genre.id))

                # Add sports poster/background from SPORTS_ARTIFACTS if no external ID
                # (external ID content gets images from metadata providers)
                await self._add_sports_images(
                    session, media.id, sports_category, custom_poster_url=state.custom_poster_url
                )

            # Always link to the sports catalog
            catalog = await get_or_create_catalog(session, sports_category)
            # Check if link already exists
            existing_link = await session.exec(
                select(MediaCatalogLink).where(
                    MediaCatalogLink.media_id == media.id,
                    MediaCatalogLink.catalog_id == catalog.id,
                )
            )
            if not existing_link.first():
                session.add(MediaCatalogLink(media_id=media.id, catalog_id=catalog.id))

            # Get uploader name with anonymity preference
            user = await session.get(User, mf_user_id)
            if user and user.contribute_anonymously:
                uploader_name = "Anonymous"
                uploader_user_id = None
            else:
                uploader_name = user.username or user.email if user else f"User #{mf_user_id}"
                uploader_user_id = mf_user_id

            # Create the appropriate stream based on content type
            content_type = state.content_type

            if content_type in (ContentType.MAGNET, ContentType.TORRENT_FILE, ContentType.TORRENT_URL):
                info_hash = analysis.get("info_hash")
                if not info_hash:
                    return {"success": False, "error": "No info hash from analysis."}
                normalized_hash = str(info_hash).strip().lower()

                existing = await session.exec(
                    select(TorrentStream).where(func.lower(TorrentStream.info_hash) == normalized_hash)
                )
                if existing.first():
                    return {"success": False, "error": "Stream with this info hash already exists."}

                await create_torrent_stream(
                    session,
                    info_hash=normalized_hash,
                    name=analysis.get("torrent_name") or event_title,
                    size=analysis.get("total_size") or 0,
                    source="telegram_bot",
                    media_id=media.id,
                    resolution=overrides.get("resolution") or analysis.get("resolution"),
                    quality=overrides.get("quality") or analysis.get("quality"),
                    codec=overrides.get("codec") or analysis.get("codec"),
                    uploader=uploader_name,
                    uploader_user_id=uploader_user_id,
                )

            elif content_type == ContentType.HTTP:
                url = analysis.get("url")
                if not url:
                    return {"success": False, "error": "No URL provided."}
                await create_http_stream(
                    session,
                    url=url,
                    name=analysis.get("file_name") or event_title,
                    media_id=media.id,
                    source="telegram_bot",
                    size=analysis.get("file_size"),
                    format=analysis.get("content_type"),
                    uploader=uploader_name,
                    uploader_user_id=uploader_user_id,
                    resolution=overrides.get("resolution") or analysis.get("resolution"),
                    quality=overrides.get("quality") or analysis.get("quality"),
                    codec=overrides.get("codec") or analysis.get("codec"),
                )

            elif content_type == ContentType.ACESTREAM:
                content_id = analysis.get("content_id")
                if not content_id:
                    return {"success": False, "error": "No AceStream content ID."}

                existing = await session.exec(select(AceStreamStream).where(AceStreamStream.content_id == content_id))
                if existing.first():
                    return {"success": False, "error": "This AceStream is already in the database."}

                await create_acestream_stream(
                    session,
                    content_id=content_id,
                    name=event_title,
                    media_id=media.id,
                    source="telegram_bot",
                    uploader=uploader_name,
                    uploader_user_id=uploader_user_id,
                    resolution=overrides.get("resolution") or analysis.get("resolution"),
                )

            elif content_type == ContentType.VIDEO:
                # Delegate to the video import which uses _store_forwarded_content
                content_info = {
                    "user_id": state.user_id,
                    "chat_id": state.chat_id,
                    "message_id": state.original_message_id or 0,
                    "file_id": analysis.get("file_id"),
                    "file_unique_id": analysis.get("file_unique_id"),
                    "file_name": analysis.get("file_name", "video"),
                    "file_size": analysis.get("file_size"),
                    "mime_type": analysis.get("mime_type"),
                    "meta_id": external_id or f"mf:{media.id}",
                    "needs_linking": False,
                    "resolution": overrides.get("resolution") or analysis.get("resolution"),
                    "quality": overrides.get("quality") or analysis.get("quality"),
                    "codec": overrides.get("codec") or analysis.get("codec"),
                }
                await session.commit()
                stored = await self._store_forwarded_content(content_info)
                return {
                    "success": stored,
                    "auto_approved": True,
                    "error": "Failed to store video" if not stored else None,
                }

            elif content_type == ContentType.YOUTUBE:
                video_id = analysis.get("video_id")
                if not video_id:
                    return {"success": False, "error": "No YouTube video ID."}
                existing = await session.exec(select(YouTubeStream).where(YouTubeStream.video_id == video_id))
                if existing.first():
                    return {"success": False, "error": "This YouTube video is already in the database."}
                await create_youtube_stream(
                    session,
                    video_id=video_id,
                    name=analysis.get("title") or event_title,
                    media_id=media.id,
                    source="telegram_bot",
                    is_live=analysis.get("is_live", False),
                    uploader_user_id=mf_user_id,
                    resolution=overrides.get("resolution") or analysis.get("resolution"),
                )

            elif content_type == ContentType.NZB:
                nzb_url = analysis.get("nzb_url")
                if not nzb_url:
                    return {"success": False, "error": "No NZB URL provided."}
                nzb_guid = hashlib.sha256(nzb_url.encode()).hexdigest()[:32]
                existing = await session.exec(select(UsenetStream).where(UsenetStream.nzb_guid == nzb_guid))
                if existing.first():
                    return {"success": False, "error": "This NZB is already in the database."}
                await create_usenet_stream(
                    session,
                    nzb_guid=nzb_guid,
                    name=analysis.get("nzb_name") or event_title,
                    size=0,
                    indexer="telegram_bot",
                    media_id=media.id,
                    nzb_url=nzb_url,
                    resolution=overrides.get("resolution") or analysis.get("resolution"),
                    quality=overrides.get("quality") or analysis.get("quality"),
                    codec=overrides.get("codec") or analysis.get("codec"),
                    uploader_user_id=mf_user_id,
                )

            else:
                return {"success": False, "error": f"Unsupported content type for sports: {content_type}"}

            await session.commit()

        return {"success": True, "auto_approved": True}

    async def handle_cancel(self, user_id: int, chat_id: int, message_id: int) -> dict:
        """Handle cancel action.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit

        Returns:
            Dict with result
        """
        self.clear_conversation(user_id)
        await self.edit_message(chat_id, message_id, "❌ *Cancelled*\n\nOperation cancelled.")
        return {"success": True}

    async def handle_back(self, user_id: int, chat_id: int, message_id: int) -> dict:
        """Handle back action to go to previous step.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID to edit

        Returns:
            Dict with result
        """
        state = self.get_conversation(user_id)
        if not state:
            return {"success": False, "message": "Session expired."}

        current_step = state.step

        if current_step == ConversationStep.AWAITING_METADATA_REVIEW:
            if state.media_type == "sports":
                # Go back to sports category selection
                return await self.show_sports_category_picker(state, chat_id, message_id)
            else:
                # Go back to match selection
                return await self.show_matches(state, chat_id, message_id)
        elif current_step in (ConversationStep.AWAITING_FIELD_EDIT, ConversationStep.AWAITING_POSTER_INPUT):
            # Go back to metadata review
            state.step = ConversationStep.AWAITING_METADATA_REVIEW
            return await self.show_metadata_review(state, chat_id, message_id)
        elif current_step in (ConversationStep.AWAITING_MATCH, ConversationStep.AWAITING_SPORTS_CATEGORY):
            # Go back to media type selection
            state.step = ConversationStep.AWAITING_MEDIA_TYPE
            emoji = self.get_content_type_emoji(state.content_type)
            type_name = self.get_content_type_name(state.content_type)
            preview = self._get_content_preview(state.content_type, state.raw_input)
            message = f"{emoji} *{type_name} Detected*\n\n{preview}\n\nSelect the content type:"
            keyboard = self._build_media_type_keyboard(user_id)
            await self.edit_message(chat_id, message_id, message, {"inline_keyboard": keyboard})
            return {"success": True}
        else:
            # Can't go back further
            return await self.handle_cancel(user_id, chat_id, message_id)

    # ============================================
    # Bot Commands Registration
    # ============================================

    async def register_bot_commands(self) -> bool:
        """Register bot commands with Telegram.

        This sets up the command menu that appears in Telegram.

        Returns:
            True if successful
        """
        if not self.enabled:
            return False

        commands = [
            {"command": "start", "description": "Welcome message and quick start guide"},
            {"command": "help", "description": "Show help and supported content types"},
            {"command": "login", "description": "Link your Telegram account to MediaFusion"},
            {"command": "status", "description": "Check your account status"},
            {"command": "cancel", "description": "Cancel current operation"},
        ]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/setMyCommands",
                    json={"commands": commands},
                ) as response:
                    if response.ok:
                        logger.info("Successfully registered bot commands")
                        return True
                    else:
                        error_data = await response.json()
                        logger.error(f"Failed to register bot commands: {error_data}")
                        return False
        except Exception as e:
            logger.error(f"Error registering bot commands: {e}")
            return False

    async def handle_status_command(self, telegram_user_id: int, chat_id: int) -> dict:
        """Handle /status command.

        Args:
            telegram_user_id: Telegram user ID
            chat_id: Chat ID

        Returns:
            Dict with result message
        """
        is_linked, mf_user_id = await self.check_user_linked(telegram_user_id)

        if is_linked:
            # Get user details
            async with get_async_session_context() as session:
                user = await session.get(User, mf_user_id)
                if user:
                    username = user.username or user.email or f"User #{mf_user_id}"
                    message = (
                        f"✅ *Account Status*\n\n"
                        f"*Status:* Linked\n"
                        f"*Username:* {username}\n"
                        f"*MediaFusion ID:* {mf_user_id}\n\n"
                        f"You can contribute content by sending:\n"
                        f"• Magnet links\n"
                        f"• Torrent files\n"
                        f"• YouTube URLs\n"
                        f"• HTTP direct links\n"
                        f"• Video files\n"
                        f"• NZB URLs\n"
                        f"• AceStream IDs"
                    )
                else:
                    message = "✅ *Account Status*\n\n*Status:* Linked\n\nReady to contribute content!"
        else:
            message = (
                "❌ *Account Status*\n\n"
                "*Status:* Not Linked\n\n"
                "Your Telegram account is not linked to MediaFusion.\n\n"
                "Send `/login` to link your account and start contributing content."
            )

        return {"success": True, "message": message}

    async def handle_cancel_command(self, user_id: int, chat_id: int) -> dict:
        """Handle /cancel command.

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID

        Returns:
            Dict with result message
        """
        state = self.get_conversation(user_id)
        if state:
            self.clear_conversation(user_id)
            return {
                "success": True,
                "message": "❌ *Operation Cancelled*\n\nYour current operation has been cancelled.",
            }
        else:
            return {"success": True, "message": "ℹ️ *No Active Operation*\n\nThere's nothing to cancel."}

    async def get_file_info(self, file_id: str) -> dict | None:
        """Get file information from Telegram Bot API.

        Args:
            file_id: Telegram file_id

        Returns:
            File info dict with file_path, or None if failed
        """
        if not self.enabled:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/getFile",
                    json={"file_id": file_id},
                ) as response:
                    data = await response.json()
                    if data.get("ok"):
                        return data.get("result")
                    logger.error(f"Failed to get file info: {data}")
                    return None
        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            return None

    async def send_reply(
        self,
        chat_id: int,
        message: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ):
        """Send a reply message to a user.

        Args:
            chat_id: Telegram chat ID (usually same as user ID for DMs)
            message: Message text
            reply_to_message_id: Optional message ID to reply to
            reply_markup: Optional inline keyboard markup

        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self.enabled:
            logger.warning("Telegram bot is not enabled. Cannot send reply.")
            return False

        try:
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }
            if reply_to_message_id:
                payload["reply_to_message_id"] = reply_to_message_id
            if reply_markup:
                payload["reply_markup"] = reply_markup

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/sendMessage",
                    json=payload,
                ) as response:
                    response_data = await response.json()
                    if response.ok and response_data.get("ok"):
                        logger.debug(f"Successfully sent Telegram message to chat {chat_id}")
                        return True
                    else:
                        error_description = response_data.get("description", "Unknown error")
                        logger.error(
                            f"Failed to send Telegram reply to chat {chat_id}: "
                            f"{error_description} (Status: {response.status})"
                        )
                        return False
        except Exception as e:
            logger.error(f"Error sending Telegram reply to chat {chat_id}: {e}", exc_info=True)
            return False

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
    ):
        """Edit an existing message.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to edit
            text: New message text
            reply_markup: Optional inline keyboard markup
        """
        if not self.enabled:
            return

        try:
            payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/editMessageText",
                    json=payload,
                ) as response:
                    if not response.ok:
                        error_data = await response.json()
                        logger.error(f"Failed to edit message: {error_data}")
        except Exception as e:
            logger.error(f"Error editing message: {e}")

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None, show_alert: bool = False):
        """Answer a callback query.

        Args:
            callback_query_id: Callback query ID
            text: Optional text to show to user
            show_alert: Whether to show as alert (modal) or notification
        """
        if not self.enabled:
            return

        try:
            payload = {
                "callback_query_id": callback_query_id,
            }
            if text:
                payload["text"] = text
            payload["show_alert"] = show_alert

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/answerCallbackQuery",
                    json=payload,
                ) as response:
                    if not response.ok:
                        error_data = await response.json()
                        logger.error(f"Failed to answer callback: {error_data}")
        except Exception as e:
            logger.error(f"Error answering callback: {e}")

    async def send_video_to_user(
        self,
        chat_id: int,
        file_id: str,
        caption: str | None = None,
    ) -> dict | None:
        """Send a video to a user's DM using file_id for MediaFlow streaming access.

        This uses the Telegram Bot API sendVideo method with a file_id to send
        a video to the user. The file_id can be reused across chats, so we don't
        need to know where the original message is - just the file_id.

        Args:
            chat_id: User's Telegram ID (bot DM chat_id)
            file_id: Telegram file_id of the video to send
            caption: Optional caption for the video

        Returns:
            Dict with {"message_id": new_message_id} on success, None on failure
        """
        if not self.enabled:
            logger.warning("Telegram bot is not enabled. Cannot send video.")
            return None

        try:
            payload = {
                "chat_id": chat_id,
                "video": file_id,
            }
            if caption:
                payload["caption"] = caption

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/sendVideo",
                    json=payload,
                ) as response:
                    response_data = await response.json()
                    if response.ok and response_data.get("ok"):
                        result = response_data.get("result", {})
                        new_message_id = result.get("message_id")
                        logger.info(
                            f"Successfully sent video (file_id={file_id[:20]}...) "
                            f"to chat {chat_id}, message_id={new_message_id}"
                        )
                        return {"message_id": new_message_id}
                    else:
                        error_description = response_data.get("description", "Unknown error")
                        logger.error(
                            f"Failed to send video to {chat_id}: {error_description} (Status: {response.status})"
                        )
                        return None
        except Exception as e:
            logger.error(f"Error sending video to {chat_id}: {e}", exc_info=True)
            return None

    async def process_forwarded_video(
        self,
        user_id: int,
        chat_id: int,
        message_id: int,
        file_id: str,
        file_unique_id: str | None,
        file_name: str | None,
        file_size: int | None,
        mime_type: str | None,
        caption: str | None = None,
    ) -> dict:
        """Process a video forwarded by a user.

        This method:
        1. Validates the file (size, type)
        2. Parses the filename for metadata
        3. Automatically searches for matching media
        4. Shows interactive search results with buttons

        Args:
            user_id: Telegram user ID
            chat_id: Chat ID (same as user_id for DMs)
            message_id: Message ID containing the video
            file_id: Telegram file_id (bot-specific)
            file_unique_id: Universal file identifier (same across all bots)
            file_name: Original filename
            file_size: File size in bytes
            mime_type: MIME type
            caption: Message caption (may contain IMDb ID)

        Returns:
            Dict with processing result
        """
        result = {
            "success": False,
            "message": "",
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "needs_linking": True,
            "meta_id": None,
            "search_results": None,
            "reply_markup": None,
        }

        # Validate file size (minimum 25MB by default)
        min_size = settings.min_scraping_video_size
        if file_size and file_size < min_size:
            result["message"] = f"File too small (min {min_size // (1024 * 1024)} MB)"
            return result

        # Check for IMDb ID in caption first
        imdb_pattern = re.compile(r"tt\d{7,8}")
        imdb_match = imdb_pattern.search(caption or "")
        if imdb_match:
            result["meta_id"] = imdb_match.group(0)
            result["needs_linking"] = False
            result["message"] = f"✅ Video received and matched to {result['meta_id']}. Processing..."
            # Store and process immediately
            content_info = {
                "user_id": user_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "file_name": file_name or f"video_{message_id}",
                "file_size": file_size,
                "mime_type": mime_type,
                "caption": caption,
                "meta_id": result["meta_id"],
                "needs_linking": False,
                "timestamp": datetime.now().isoformat(),
            }
            pending = self._get_pending_content(user_id)
            pending.append(content_info)
            self._set_pending_content(user_id, pending)
            result["success"] = True
            return result

        # Parse filename for metadata
        search_title = None
        search_year = None
        media_type = None

        if file_name:
            try:
                parsed = PTT.parse_title(file_name, True)
                search_title = parsed.get("title")
                search_year = parsed.get("year")
                # Determine media type from parsed data
                if parsed.get("seasons") or parsed.get("episodes"):
                    media_type = "series"
                else:
                    media_type = "movie"
            except Exception as e:
                logger.debug(f"Error parsing filename {file_name}: {e}")

        # Also check caption for title/year
        if caption and not search_title:
            # Try to extract title from caption
            caption_lines = caption.split("\n")
            for line in caption_lines:
                line = line.strip()
                if line and not imdb_pattern.search(line):
                    search_title = line[:100]  # Limit length
                    break

        # If we have a title, search for matches
        if search_title:
            try:
                logger.info(f"Searching IMDb for: {search_title} ({search_year})")
                search_results = await search_multiple_imdb(
                    title=search_title,
                    limit=5,
                    year=search_year,
                    media_type=media_type,
                    min_similarity=60,
                )

                if search_results:
                    result["search_results"] = search_results
                    # Create interactive keyboard
                    keyboard = []
                    for idx, match in enumerate(search_results[:5], 1):
                        imdb_id = match.get("imdb_id", "")
                        title = match.get("title", "Unknown")
                        year = match.get("year", "")
                        type_str = "🎬" if match.get("type") == "movie" else "📺"
                        button_text = f"{idx}. {type_str} {title}"
                        if year:
                            button_text += f" ({year})"
                        # Truncate if too long (Telegram limit is 64 chars)
                        if len(button_text) > 60:
                            button_text = button_text[:57] + "..."

                        callback_data = f"select:{user_id}:{message_id}:{imdb_id}"
                        keyboard.append([{"text": button_text, "callback_data": callback_data}])
                        # Store search result in Redis for callback (shared across all workers)
                        self._set_search_result(
                            callback_data,
                            {
                                "user_id": user_id,
                                "message_id": message_id,
                                "file_id": file_id,
                                "file_unique_id": file_unique_id,
                                "file_name": file_name,
                                "file_size": file_size,
                                "mime_type": mime_type,
                                "caption": caption,
                                "imdb_id": imdb_id,
                                "title": title,
                                "year": year,
                            },
                        )

                    # Add "Manual Entry" button
                    keyboard.append(
                        [{"text": "✏️ Enter IMDb ID manually", "callback_data": f"manual:{user_id}:{message_id}"}]
                    )
                    keyboard.append([{"text": "❌ Cancel", "callback_data": f"cancel:{user_id}:{message_id}"}])

                    result["reply_markup"] = {"inline_keyboard": keyboard}
                    result["message"] = (
                        f"📹 *Video Received*\n\n"
                        f"*File:* `{file_name or 'unnamed'}`\n"
                        f"*Size:* {file_size // (1024 * 1024) if file_size else 'Unknown'} MB\n\n"
                        f"🔍 *Found {len(search_results)} possible match(es):*\n\n"
                        f"Select a match or enter IMDb ID manually:"
                    )
                else:
                    # No matches found, ask for manual entry
                    keyboard = [[{"text": "✏️ Enter IMDb ID", "callback_data": f"manual:{user_id}:{message_id}"}]]
                    keyboard.append([{"text": "❌ Cancel", "callback_data": f"cancel:{user_id}:{message_id}"}])
                    result["reply_markup"] = {"inline_keyboard": keyboard}
                    result["message"] = (
                        f"📹 *Video Received*\n\n"
                        f"*File:* `{file_name or 'unnamed'}`\n"
                        f"*Size:* {file_size // (1024 * 1024) if file_size else 'Unknown'} MB\n\n"
                        f"❌ No automatic matches found.\n"
                        f"Please select an option:"
                    )
            except Exception as e:
                logger.exception(f"Error searching for matches: {e}")
                # Fallback to manual entry
                keyboard = [[{"text": "✏️ Enter IMDb ID", "callback_data": f"manual:{user_id}:{message_id}"}]]
                keyboard.append([{"text": "❌ Cancel", "callback_data": f"cancel:{user_id}:{message_id}"}])
                result["reply_markup"] = {"inline_keyboard": keyboard}
                result["message"] = (
                    f"📹 *Video Received*\n\n"
                    f"*File:* `{file_name or 'unnamed'}`\n"
                    f"*Size:* {file_size // (1024 * 1024) if file_size else 'Unknown'} MB\n\n"
                    f"Please select an option:"
                )
        else:
            # No title to search, ask for manual entry
            keyboard = [[{"text": "✏️ Enter IMDb ID", "callback_data": f"manual:{user_id}:{message_id}"}]]
            keyboard.append([{"text": "❌ Cancel", "callback_data": f"cancel:{user_id}:{message_id}"}])
            result["reply_markup"] = {"inline_keyboard": keyboard}
            result["message"] = (
                f"📹 *Video Received*\n\n"
                f"*File:* `{file_name or 'unnamed'}`\n"
                f"*Size:* {file_size // (1024 * 1024) if file_size else 'Unknown'} MB\n\n"
                f"Please select an option:"
            )

        # Store content info for later processing (ALWAYS store, even if we have search results)
        content_info = {
            "user_id": user_id,
            "chat_id": chat_id,
            "message_id": message_id,  # This is the original video message ID
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "file_name": file_name or f"video_{message_id}",
            "file_size": file_size,
            "mime_type": mime_type,
            "caption": caption,
            "meta_id": None,
            "needs_linking": True,
            "timestamp": datetime.now().isoformat(),
        }

        # Add to pending content queue (replace if exists for same message_id)
        pending = [p for p in self._get_pending_content(user_id) if p.get("message_id") != message_id]
        pending.append(content_info)
        self._set_pending_content(user_id, pending)

        # Also update search_results in Redis with file info for all callbacks for this message
        # This ensures we can find it even if pending_content is cleared
        try:
            pattern = f"telegram:search_result:select:{user_id}:{message_id}:*"
            matching_keys = REDIS_SYNC_CLIENT.keys(pattern) or []
            for raw_key in matching_keys:
                key_str = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
                raw_data = REDIS_SYNC_CLIENT.get(key_str)
                if raw_data:
                    data = json.loads(raw_data)
                    data.update(
                        {
                            "chat_id": chat_id,
                            "file_id": file_id,
                            "file_unique_id": file_unique_id,
                            "file_name": file_name,
                            "file_size": file_size,
                            "mime_type": mime_type,
                            "caption": caption,
                        }
                    )
                    REDIS_SYNC_CLIENT.setex(key_str, 30 * 60, json.dumps(data))
        except Exception as e:
            logger.debug(f"Failed to update search results in Redis for message {message_id}: {e}")

        result["success"] = True
        return result

    async def link_pending_content(
        self,
        user_id: int,
        meta_id: str,
    ) -> dict:
        """Link pending content to a media item.

        Args:
            user_id: Telegram user ID
            meta_id: IMDb ID or other external ID to link to

        Returns:
            Dict with result
        """
        pending = self._get_pending_content(user_id)
        if not pending:
            return {
                "success": False,
                "message": "No pending content to link. Please forward a video first.",
            }

        # Get the most recent pending content and save the updated list immediately
        content = pending.pop()
        self._set_pending_content(user_id, pending)
        content["meta_id"] = meta_id
        content["needs_linking"] = False

        # Store the stream
        try:
            stored = await self._store_forwarded_content(content)
            if stored:
                return {
                    "success": True,
                    "message": f"Content linked to {meta_id} and stored successfully!",
                }
            else:
                # Put it back in the queue
                pending.append(content)
                self._set_pending_content(user_id, pending)
                return {
                    "success": False,
                    "message": "Failed to store content. Please try again.",
                }
        except Exception as e:
            logger.exception(f"Error storing forwarded content: {e}")
            pending.append(content)
            self._set_pending_content(user_id, pending)
            return {
                "success": False,
                "message": f"Error: {str(e)}",
            }

    async def _get_mediafusion_user_id(self, telegram_user_id: int) -> int | None:
        """Get MediaFusion user ID for a Telegram user ID.

        Args:
            telegram_user_id: Telegram user ID

        Returns:
            MediaFusion user ID if linked, None otherwise
        """
        # Check Redis cache first (shared across all workers)
        try:
            cached = REDIS_SYNC_CLIENT.get(self._user_mapping_key(telegram_user_id))
            if cached:
                return int(cached)
        except Exception as e:
            logger.debug(f"Failed to read user mapping from Redis for {telegram_user_id}: {e}")

        # Check database for linked user (at user level, not profile level)
        async with get_async_session_context() as session:
            # Search in users table for telegram_user_id
            query = select(User).where(User.telegram_user_id == str(telegram_user_id))
            result = await session.exec(query)
            user = result.first()

            if user:
                try:
                    REDIS_SYNC_CLIENT.setex(self._user_mapping_key(telegram_user_id), 3600, str(user.id))
                except Exception as e:
                    logger.debug(f"Failed to cache user mapping in Redis: {e}")
                return user.id

        return None

    async def _store_forwarded_content(self, content: dict) -> bool:
        """Store forwarded content as a Telegram stream.

        Also forwards to backup channel if configured for redundancy.

        Args:
            content: Content info dict

        Returns:
            True if stored successfully
        """
        meta_id = content.get("meta_id")
        if not meta_id:
            logger.warning("No meta_id provided in content")
            return False

        async with get_async_session_context() as session:
            # Resolve media ID
            media = await crud.get_media_by_external_id(session, meta_id)
            if not media:
                logger.warning(f"Media not found for meta_id: {meta_id}. Make sure the media exists in the database.")
                return False

            # Check if stream already exists by file_unique_id (preferred) or file_id
            file_unique_id = content.get("file_unique_id")
            file_id = content.get("file_id")

            if file_unique_id:
                existing = await crud.get_telegram_stream_by_file_unique_id(session, file_unique_id)
                if existing:
                    logger.info(f"Telegram stream already exists (file_unique_id: {file_unique_id})")
                    return True
            elif file_id:
                existing = await crud.get_telegram_stream_by_file_id(session, file_id)
                if existing:
                    logger.info(f"Telegram stream already exists (file_id: {file_id[:20]}...)")
                    return True

            # Parse filename for quality attributes
            parsed = PTT.parse_title(content["file_name"], True)

            # Get MediaFusion user ID if linked
            mf_user_id = await self._get_mediafusion_user_id(content["user_id"])

            # Determine uploader name and user_id with anonymity preference
            uploader_name = None
            uploader_user_id = None

            if mf_user_id:
                # User is linked - check their anonymity preference
                user = await session.get(User, mf_user_id)
                if user:
                    if user.contribute_anonymously:
                        # User prefers anonymous contributions
                        uploader_name = "Anonymous"
                        uploader_user_id = None
                    else:
                        uploader_name = user.username or user.email or f"User #{mf_user_id}"
                        uploader_user_id = mf_user_id
            else:
                # Unlinked user - use Telegram user ID (masked)
                telegram_id_str = str(content["user_id"])
                masked_id = telegram_id_str[:4] + "xxxx" if len(telegram_id_str) > 4 else "xxxx"
                uploader_name = f"Telegram User {masked_id}"

            # Forward to backup channel if configured
            # For bot contributions, the backup channel becomes the canonical location
            # (we don't store contributor's chat_id/message_id since we use file_id for playback)
            backup_chat_id = None
            backup_message_id = None

            if settings.telegram_backup_channel_id and content.get("file_id"):
                backup_result = await self._forward_to_backup_channel(
                    file_id=content["file_id"],
                    file_name=content["file_name"],
                    meta_id=meta_id,
                    media_title=media.title,
                )
                if backup_result:
                    backup_chat_id = backup_result.get("chat_id")
                    backup_message_id = backup_result.get("message_id")
                    logger.info(f"Forwarded to backup channel: {backup_chat_id}/{backup_message_id}")

            # For bot contributions:
            # - Use backup channel as primary location if available
            # - Otherwise use a placeholder (file_id is what matters for playback)
            # We don't store contributor's DM chat_id/message_id since:
            # 1. Playback uses file_id via sendVideo, not chat location
            # 2. Contributor's user info is stored via uploader_user_id
            primary_chat_id = backup_chat_id or "bot_contribution"
            primary_message_id = backup_message_id or 0

            # Create the stream
            await crud.create_telegram_stream(
                session,
                chat_id=primary_chat_id,
                message_id=primary_message_id,
                name=parsed.get("title", content["file_name"]),
                media_id=media.id,
                file_id=content["file_id"],
                file_unique_id=content.get("file_unique_id"),
                file_name=content["file_name"],
                mime_type=content["mime_type"],
                size=content["file_size"],
                backup_chat_id=backup_chat_id,
                backup_message_id=backup_message_id,
                source="telegram_bot",
                uploader=uploader_name,
                uploader_user_id=uploader_user_id,
                resolution=parsed.get("resolution"),
                codec=parsed.get("codec"),
                quality=parsed.get("quality"),
                release_group=parsed.get("group"),
            )

            await session.commit()
            logger.info(f"Stored Telegram stream: {content['file_name']} -> {meta_id}")

            # Notify admin channel
            await telegram_notifier.send_content_received_notification(
                file_name=content["file_name"],
                file_size=content["file_size"],
                status="stored",
                meta_id=meta_id,
                title=media.title,
            )

            return True

    async def _forward_to_backup_channel(
        self,
        file_id: str,
        file_name: str,
        meta_id: str,
        media_title: str,
    ) -> dict | None:
        """Forward video to backup channel for redundancy.

        Uses sendVideo with file_id to copy the video to the backup channel.
        This ensures content survives even if the original contributor deletes
        their message or the bot gets suspended (with file_unique_id matching).

        Args:
            file_id: Telegram file_id of the video
            file_name: Original filename
            meta_id: IMDb/external ID for caption
            media_title: Human-readable title

        Returns:
            Dict with {"chat_id": str, "message_id": int} on success, None on failure
        """
        if not self.enabled or not settings.telegram_backup_channel_id:
            return None

        try:
            # Build informative caption for the backup
            caption = f"📁 {file_name}\n🎬 {media_title}\n🔗 {meta_id}"
            if len(caption) > 1024:  # Telegram caption limit
                caption = caption[:1021] + "..."

            payload = {
                "chat_id": settings.telegram_backup_channel_id,
                "video": file_id,
                "caption": caption,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/sendVideo",
                    json=payload,
                ) as response:
                    response_data = await response.json()
                    if response.ok and response_data.get("ok"):
                        result = response_data.get("result", {})
                        message_id = result.get("message_id")
                        logger.info(f"Forwarded to backup channel: {settings.telegram_backup_channel_id}/{message_id}")
                        return {
                            "chat_id": settings.telegram_backup_channel_id,
                            "message_id": message_id,
                        }
                    else:
                        error_description = response_data.get("description", "Unknown error")
                        logger.error(f"Failed to forward to backup channel: {error_description}")
                        return None
        except Exception as e:
            logger.error(f"Error forwarding to backup channel: {e}", exc_info=True)
            return None

    def get_pending_count(self, user_id: int) -> int:
        """Get count of pending content for a user."""
        return len(self._get_pending_content(user_id))

    def clear_pending(self, user_id: int):
        """Clear pending content for a user."""
        self._delete_pending_content(user_id)

    async def handle_login_command(self, telegram_user_id: int, chat_id: int) -> dict:
        """Handle /login command to link Telegram account to MediaFusion.

        Args:
            telegram_user_id: Telegram user ID
            chat_id: Chat ID

        Returns:
            Dict with result message
        """
        # Generate a secure login token
        login_token = secrets.token_urlsafe(32)

        login_data = {
            "telegram_user_id": telegram_user_id,
        }

        # Store in Redis so all workers share the same token state
        REDIS_SYNC_CLIENT.setex(
            f"telegram:login_token:{login_token}",
            24 * 60 * 60,  # 24 hours in seconds
            json.dumps(login_data),
        )

        # Use frontend URL for better UX - redirects to login if not authenticated
        login_url = f"{settings.host_url}/app/telegram/login?token={login_token}"

        message = (
            "🔐 *Link Your MediaFusion Account*\n\n"
            "To link your Telegram account to MediaFusion:\n\n"
            f"1. Click this link: [Login to MediaFusion]({login_url})\n"
            "2. Sign in to your MediaFusion account\n"
            "3. Your Telegram account will be linked automatically\n\n"
            f"*Login Token:* `{login_token}`\n"
            "*Expires in:* 24 hours\n\n"
            "After linking, your uploaded content will be stored with your MediaFusion account."
        )

        return {"success": True, "message": message}

    async def link_telegram_user(self, login_token: str, mediafusion_user_id: int) -> dict:
        """Link Telegram user to MediaFusion user account.

        Args:
            login_token: Login token from /login command
            mediafusion_user_id: MediaFusion user ID

        Returns:
            Dict with result
        """
        # Retrieve token data from Redis (shared across all workers)
        raw = REDIS_SYNC_CLIENT.get(f"telegram:login_token:{login_token}")
        if not raw:
            return {"success": False, "message": "Invalid login token"}

        login_data = json.loads(raw)
        telegram_user_id = login_data["telegram_user_id"]

        # Store mapping at user level (not profile level)
        async with get_async_session_context() as session:
            # Get the user
            query = select(User).where(User.id == mediafusion_user_id)
            result = await session.exec(query)
            user = result.first()

            if not user:
                REDIS_SYNC_CLIENT.delete(f"telegram:login_token:{login_token}")
                return {"success": False, "message": "User not found"}

            # Update user with Telegram linking info
            user.telegram_user_id = str(telegram_user_id)
            user.telegram_linked_at = datetime.now(pytz.UTC)

            session.add(user)
            await session.commit()

        # Update Redis cache
        try:
            REDIS_SYNC_CLIENT.setex(self._user_mapping_key(telegram_user_id), 3600, str(mediafusion_user_id))
        except Exception as e:
            logger.debug(f"Failed to update user mapping cache in Redis: {e}")

        # Get user details for notification
        async with get_async_session_context() as session:
            user_query = select(User).where(User.id == mediafusion_user_id)
            user_result = await session.exec(user_query)
            user = user_result.first()

        # Send notification to Telegram user
        try:
            if not self.enabled:
                logger.warning("Telegram bot is not enabled. Cannot send linking notification.")
            else:
                user_display_name = (
                    user.username
                    if user and user.username
                    else (user.email.split("@")[0] if user and user.email else f"User {mediafusion_user_id}")
                )
                notification_message = (
                    f"✅ *Account Linked Successfully!*\n\n"
                    f"👋 *Welcome, {user_display_name}!*\n\n"
                    f"Your Telegram account has been linked to your MediaFusion account:\n"
                    f"*Username:* {user_display_name}\n"
                    f"*Email:* {user.email if user and user.email else 'N/A'}\n\n"
                    f"🎬 *What's Next?*\n"
                    f"• Forward videos to me and they'll be stored with your account\n"
                    f"• Your uploaded content will show your name as the uploader\n"
                    f"• Manage your Telegram channels in MediaFusion profile settings\n\n"
                    f"Ready to add content? Just forward a video file! 🚀\n\n"
                    f"Type `/help` for more information or `/start` to see the welcome message."
                )
                success = await self.send_reply(telegram_user_id, notification_message)
                if success:
                    logger.info(f"Successfully sent Telegram linking notification to user {telegram_user_id}")
                else:
                    logger.warning(f"Failed to send Telegram linking notification to user {telegram_user_id}")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification after linking: {e}", exc_info=True)

        # Clean up login token from Redis
        REDIS_SYNC_CLIENT.delete(f"telegram:login_token:{login_token}")

        return {
            "success": True,
            "message": f"Telegram account linked successfully to MediaFusion user {mediafusion_user_id}",
        }

    async def handle_callback_query(
        self,
        callback_query_id: str,
        user_id: int,
        chat_id: int,
        message_id: int,
        callback_data: str,
    ) -> dict:
        """Handle callback query from inline keyboard buttons.

        Supports both new wizard flow callbacks and legacy callbacks for backward compatibility.

        New callback prefixes:
        - mtype:{user_id}:{media_type} - Media type selection (movie/series/sports)
        - match:{user_id}:{external_id} - Match selection (external_id may contain colons)
        - sport:{user_id}:{category_key} - Sports category selection
        - manual:{user_id} - Manual external ID entry
        - meta_edit:{user_id}:{field} - Open field editor
        - meta_val:{user_id}:{field}:{value} - Set field value
        - confirm:{user_id} - Confirm import
        - cancel:{user_id} - Cancel operation
        - back:{user_id} - Go back one step
        - back_review:{user_id} - Go back to review from field editor

        Legacy callbacks (for backward compatibility):
        - select:{user_id}:{msg_id}:{imdb_id} - Legacy video match selection

        Args:
            callback_query_id: Callback query ID for answering
            user_id: Telegram user ID
            chat_id: Chat ID
            message_id: Message ID containing the keyboard
            callback_data: Callback data from button

        Returns:
            Dict with result
        """
        result = {"success": False, "message": "", "action": None}

        try:
            # Parse callback data
            parts = callback_data.split(":")
            action = parts[0] if parts else None

            # ============================================
            # NEW WIZARD FLOW CALLBACKS
            # ============================================

            if action == "mtype":
                # Media type selection: mtype:{user_id}:{media_type}
                if len(parts) >= 3:
                    target_user_id = int(parts[1])
                    media_type = parts[2]

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)
                    await self.handle_media_type_selection(user_id, chat_id, message_id, media_type)
                    result["success"] = True
                    result["action"] = "media_type_selected"

            elif action == "match":
                # Match selection: match:{user_id}:{external_id}
                # external_id may contain colons (e.g. tmdb:123), so rejoin parts
                if len(parts) >= 3:
                    target_user_id = int(parts[1])
                    external_id = ":".join(parts[2:])

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)
                    await self.handle_match_selection(user_id, chat_id, message_id, external_id)
                    result["success"] = True
                    result["action"] = "match_selected"

            elif action == "sport":
                # Sports category selection: sport:{user_id}:{category_key}
                if len(parts) >= 3:
                    target_user_id = int(parts[1])
                    category_key = parts[2]

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)
                    await self.handle_sports_category_selection(user_id, chat_id, message_id, category_key)
                    result["success"] = True
                    result["action"] = "sports_category_selected"

            elif action == "manual":
                # Manual IMDb entry: manual:{user_id} (new) or manual:{user_id}:{msg_id} (legacy)
                if len(parts) >= 2:
                    target_user_id = int(parts[1])

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)

                    # Check if this is from the new wizard flow
                    state = self.get_conversation(user_id)
                    if state:
                        await self.handle_manual_imdb_input(user_id, chat_id, message_id)
                    else:
                        # Legacy behavior
                        await self.edit_message(
                            chat_id,
                            message_id,
                            "✏️ *Manual Entry*\n\nPlease reply with an external ID.\n\n"
                            "*Examples:* `tt1234567`, `tmdb:12345`, `tvdb:12345`",
                        )
                    result["success"] = True
                    result["action"] = "manual_entry"

            elif action == "meta_edit":
                # Field editor: meta_edit:{user_id}:{field}
                if len(parts) >= 3:
                    target_user_id = int(parts[1])
                    field = parts[2]

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)
                    await self.show_field_editor(user_id, chat_id, message_id, field)
                    result["success"] = True
                    result["action"] = "field_editor_shown"

            elif action == "meta_val":
                # Field value selection: meta_val:{user_id}:{field}:{value}
                if len(parts) >= 4:
                    target_user_id = int(parts[1])
                    field = parts[2]
                    value = ":".join(parts[3:])  # Value might contain colons

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id, f"✓ {field}: {value}")
                    await self.handle_field_value_selection(user_id, chat_id, message_id, field, value)
                    result["success"] = True
                    result["action"] = "field_value_set"

            elif action == "confirm":
                # Confirm import: confirm:{user_id}
                if len(parts) >= 2:
                    target_user_id = int(parts[1])

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id, "⏳ Importing...")
                    await self.execute_import(user_id, chat_id, message_id)
                    result["success"] = True
                    result["action"] = "import_executed"

            elif action == "cancel":
                # Cancel: cancel:{user_id} (new) or cancel:{user_id}:{msg_id} (legacy)
                if len(parts) >= 2:
                    target_user_id = int(parts[1])

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)

                    # Handle new wizard flow
                    state = self.get_conversation(user_id)
                    if state:
                        await self.handle_cancel(user_id, chat_id, message_id)
                    else:
                        # Legacy behavior
                        await self.edit_message(chat_id, message_id, "❌ *Cancelled*\n\nOperation cancelled.")
                        # Remove pending content if msg_id provided
                        if len(parts) >= 3:
                            target_message_id = int(parts[2])
                            pending = self._get_pending_content(user_id)
                            updated = [p for p in pending if p.get("message_id") != target_message_id]
                            if len(updated) != len(pending):
                                self._set_pending_content(user_id, updated)

                    result["success"] = True
                    result["action"] = "cancelled"

            elif action == "back":
                # Back: back:{user_id}
                if len(parts) >= 2:
                    target_user_id = int(parts[1])

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)
                    await self.handle_back(user_id, chat_id, message_id)
                    result["success"] = True
                    result["action"] = "back"

            elif action == "back_review":
                # Back to review from field editor: back_review:{user_id}
                if len(parts) >= 2:
                    target_user_id = int(parts[1])

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)
                    state = self.get_conversation(user_id)
                    if state:
                        state.step = ConversationStep.AWAITING_METADATA_REVIEW
                        await self.show_metadata_review(state, chat_id, message_id)
                    result["success"] = True
                    result["action"] = "back_to_review"

            elif action == "add_poster":
                # Add poster: add_poster:{user_id}
                if len(parts) >= 2:
                    target_user_id = int(parts[1])

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id)
                    await self.show_poster_input_prompt(user_id, chat_id, message_id)
                    result["success"] = True
                    result["action"] = "add_poster_shown"

            elif action == "clear_poster":
                # Clear custom poster: clear_poster:{user_id}
                if len(parts) >= 2:
                    target_user_id = int(parts[1])

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    await self.answer_callback_query(callback_query_id, "🗑️ Poster cleared")
                    state = self.get_conversation(user_id)
                    if state:
                        state.custom_poster_url = None
                        state.step = ConversationStep.AWAITING_METADATA_REVIEW
                        state.touch()
                        self._persist_conversation(state)
                        await self.show_metadata_review(state, chat_id, message_id)
                    result["success"] = True
                    result["action"] = "poster_cleared"

            # ============================================
            # LEGACY CALLBACKS (backward compatibility)
            # ============================================

            elif action == "select":
                # Legacy video match selection: select:{user_id}:{msg_id}:{imdb_id}
                if len(parts) >= 4:
                    target_user_id = int(parts[1])
                    target_message_id = int(parts[2])
                    imdb_id = parts[3]

                    if target_user_id != user_id:
                        await self.answer_callback_query(callback_query_id, "❌ Unauthorized", show_alert=True)
                        return result

                    # Try to get data from search_results first (shared across all workers via Redis)
                    search_data = self._get_search_result(callback_data)

                    # Find pending content
                    pending_content = None
                    for pending in self._get_pending_content(user_id):
                        if pending.get("message_id") == target_message_id:
                            pending_content = pending.copy()
                            break

                    if search_data:
                        content_info = {
                            "user_id": user_id,
                            "chat_id": search_data.get("chat_id", chat_id),
                            "message_id": target_message_id,
                            "file_id": search_data.get("file_id"),
                            "file_name": search_data.get("file_name"),
                            "file_size": search_data.get("file_size"),
                            "mime_type": search_data.get("mime_type"),
                            "caption": search_data.get("caption"),
                            "meta_id": imdb_id,
                            "needs_linking": False,
                            "timestamp": datetime.now().isoformat(),
                        }
                        title = search_data.get("title", "Unknown")
                        year = search_data.get("year", "")
                    elif pending_content:
                        content_info = pending_content.copy()
                        content_info["meta_id"] = imdb_id
                        content_info["needs_linking"] = False
                        try:
                            if content_info.get("file_name"):
                                parsed = PTT.parse_title(content_info["file_name"], True)
                                title = parsed.get("title", "Unknown")
                                year = parsed.get("year", "")
                            else:
                                title = "Unknown"
                                year = ""
                        except Exception:
                            title = "Unknown"
                            year = ""
                    else:
                        await self.answer_callback_query(
                            callback_query_id, "❌ Content expired. Please forward the video again.", show_alert=True
                        )
                        return result

                    if not content_info.get("file_id"):
                        await self.answer_callback_query(
                            callback_query_id, "❌ Missing file information.", show_alert=True
                        )
                        return result

                    # Store the stream
                    stored = await self._store_forwarded_content(content_info)
                    if stored:
                        year_str = f" ({year})" if year else ""
                        await self.answer_callback_query(callback_query_id, f"✅ Linked to {title}{year_str}!")
                        await self.edit_message(
                            chat_id,
                            message_id,
                            f"✅ *Content Linked*\n\n*Title:* {title}{year_str}\n*IMDb:* `{imdb_id}`\n\nContent stored successfully!",
                        )
                        self._delete_search_result(callback_data)
                        result["success"] = True
                        result["action"] = "stored"
                    else:
                        await self.answer_callback_query(
                            callback_query_id, "❌ Failed to store content.", show_alert=True
                        )

            else:
                # Unknown callback
                logger.warning(f"Unknown callback action: {action} in {callback_data}")
                await self.answer_callback_query(callback_query_id, "❓ Unknown action", show_alert=True)

        except Exception as e:
            logger.exception(f"Error handling callback query: {e}")
            await self.answer_callback_query(callback_query_id, "❌ Error processing request", show_alert=True)

        return result


# Create singleton instances
telegram_notifier = TelegramNotifier()
telegram_content_bot = TelegramContentBot()

# Register with notification registry so streaming_providers can notify without importing us
register_file_annotation_handler(telegram_notifier.send_file_annotation_request)
