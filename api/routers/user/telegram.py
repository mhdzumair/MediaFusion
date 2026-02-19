"""
User Telegram Channel Management API endpoints.

Provides endpoints for managing user-configured Telegram channels for scraping.
"""

import logging
import re

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import select

from api.routers.user.auth import require_auth
from db.config import settings
from db.database import get_async_session_context
from db.models import User
from db.schemas.config import TelegramChannelConfig, TelegramConfig
from utils.profile_context import ProfileDataProvider
from utils.telegram_bot import ContentType, telegram_content_bot

logger = logging.getLogger(__name__)

# Maps settings.disabled_content_types values to bot ContentType enum members.
# Used to gate content detection and filter help/welcome messages.
_CONFIG_TO_CONTENT_TYPES: dict[str, set[ContentType]] = {
    "magnet": {ContentType.MAGNET},
    "torrent": {ContentType.TORRENT_FILE, ContentType.TORRENT_URL},
    "nzb": {ContentType.NZB},
    "youtube": {ContentType.YOUTUBE},
    "http": {ContentType.HTTP},
    "acestream": {ContentType.ACESTREAM},
    "telegram": {ContentType.VIDEO},
}

# (config_key, emoji, label) — used to build the dynamic supported-types list.
_CONTENT_TYPE_DISPLAY = [
    ("magnet", "🧲", "Magnet links"),
    ("torrent", "📦", "Torrent files (.torrent)"),
    ("nzb", "📰", "NZB URLs"),
    ("youtube", "▶️", "YouTube URLs"),
    ("http", "🔗", "HTTP direct links"),
    ("acestream", "📡", "AceStream IDs"),
    ("telegram", "🎬", "Video files"),
]

# (config_key, emoji, label, detail) — richer format for /help.
_CONTENT_TYPE_HELP = [
    ("magnet", "🧲", "Magnet Link", "`magnet:?xt=urn:btih:...`"),
    ("torrent", "📦", "Torrent File", "Upload a .torrent file"),
    ("nzb", "📰", "NZB URL", "`https://example.com/file.nzb`"),
    ("youtube", "▶️", "YouTube", "`https://youtube.com/watch?v=...`\n`https://youtu.be/...`"),
    ("http", "🔗", "HTTP Direct Link", "`https://example.com/movie.mkv`"),
    ("acestream", "📡", "AceStream ID", "40-character hex ID"),
    ("telegram", "🎬", "Video File", "Forward or upload a video"),
]

# (config_key, label) — compact format for unknown-message hints.
_CONTENT_TYPE_HINTS = [
    ("magnet", "Magnet links (`magnet:?xt=...`)"),
    ("torrent", "Torrent files (`.torrent`)"),
    ("torrent", "Torrent URLs (https://...)"),
    ("nzb", "NZB URLs (`.nzb`)"),
    ("youtube", "YouTube URLs"),
    ("http", "HTTP direct links"),
    ("acestream", "AceStream IDs (40-char hex)"),
    ("telegram", "Video files"),
]


def _get_disabled_content_types() -> set[ContentType]:
    disabled: set[ContentType] = set()
    for cfg_key in settings.disabled_content_types:
        disabled |= _CONFIG_TO_CONTENT_TYPES.get(cfg_key, set())
    return disabled


router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram"])


# ============================================
# Pydantic Schemas
# ============================================


class TelegramChannelInput(BaseModel):
    """Input schema for adding a Telegram channel."""

    id: str = Field(..., description="Unique identifier (username or chat_id)")
    name: str = Field(..., description="Display name for the channel")
    username: str | None = Field(default=None, description="Channel @username (without @)")
    chat_id: str | None = Field(default=None, description="Numeric chat ID")
    enabled: bool = Field(default=True, description="Enable this channel")
    priority: int = Field(default=1, description="Priority (lower = higher priority)")


class TelegramChannelResponse(BaseModel):
    """Response schema for a Telegram channel."""

    id: str
    name: str
    username: str | None = None
    chat_id: str | None = None
    enabled: bool = True
    priority: int = 1


class TelegramConfigResponse(BaseModel):
    """Response schema for Telegram configuration."""

    enabled: bool
    channels: list[TelegramChannelResponse]
    use_global_channels: bool
    global_channels_available: bool
    global_channel_count: int
    # Account linking status
    account_linked: bool = False
    telegram_user_id: str | None = None
    linked_at: str | None = None


class TelegramConfigUpdateInput(BaseModel):
    """Input schema for updating Telegram configuration."""

    enabled: bool = Field(default=False, description="Enable Telegram scraping")
    use_global_channels: bool = Field(default=True, description="Use admin-configured global channels")


class ChannelValidationResult(BaseModel):
    """Result of a channel validation."""

    success: bool
    message: str
    title: str | None = None
    username: str | None = None
    chat_id: str | None = None
    member_count: int | None = None
    is_channel: bool | None = None
    is_group: bool | None = None


class TelegramSetupStatus(BaseModel):
    """Status of Telegram scraping setup."""

    scraper_enabled: bool
    bot_configured: bool
    api_credentials_configured: bool
    global_channels_count: int
    message: str


# ============================================
# API Endpoints
# ============================================


@router.get("/status", response_model=TelegramSetupStatus)
async def get_telegram_status():
    """Get the setup status of Telegram scraping."""
    scraper_enabled = settings.is_scrap_from_telegram
    bot_configured = bool(settings.telegram_bot_token)
    api_configured = bool(settings.telegram_api_id and settings.telegram_api_hash and settings.telegram_session_string)
    global_channels_count = len(settings.telegram_scraping_channels) if settings.telegram_scraping_channels else 0

    if not scraper_enabled:
        message = "Telegram scraping is disabled by administrator"
    elif not api_configured:
        message = "Telegram API credentials are not configured"
    elif global_channels_count == 0:
        message = "No global channels configured. Users can add their own channels."
    else:
        message = f"Telegram scraping is enabled with {global_channels_count} global channel(s)"

    return TelegramSetupStatus(
        scraper_enabled=scraper_enabled,
        bot_configured=bot_configured,
        api_credentials_configured=api_configured,
        global_channels_count=global_channels_count,
        message=message,
    )


@router.get("/config", response_model=TelegramConfigResponse)
async def get_telegram_config(
    current_user: User = Depends(require_auth),
):
    """Get user's Telegram configuration."""
    channels = []
    enabled = False
    use_global = True
    account_linked = False
    telegram_user_id = None
    linked_at = None

    # Fetch fresh user data to get latest telegram_user_id
    async with get_async_session_context() as session:
        # Get fresh user data (in case it was just updated)
        query = select(User).where(User.id == current_user.id)
        result = await session.exec(query)
        fresh_user = result.first()

        if fresh_user:
            account_linked = bool(fresh_user.telegram_user_id)
            telegram_user_id = fresh_user.telegram_user_id
            linked_at = fresh_user.telegram_linked_at.isoformat() if fresh_user.telegram_linked_at else None

        # Get telegram channel config from ProfileContext (decrypted UserData)
        profile_ctx = await ProfileDataProvider.get_context(current_user.id, session)
        if profile_ctx.user_data.telegram_config:
            tg_config = profile_ctx.user_data.telegram_config
            enabled = tg_config.enabled
            use_global = tg_config.use_global_channels

            for channel in tg_config.channels:
                channels.append(
                    TelegramChannelResponse(
                        id=channel.id,
                        name=channel.name,
                        username=channel.username,
                        chat_id=channel.chat_id,
                        enabled=channel.enabled,
                        priority=channel.priority,
                    )
                )

    return TelegramConfigResponse(
        enabled=enabled,
        channels=channels,
        use_global_channels=use_global,
        global_channels_available=bool(settings.telegram_scraping_channels),
        global_channel_count=len(settings.telegram_scraping_channels) if settings.telegram_scraping_channels else 0,
        account_linked=account_linked,
        telegram_user_id=telegram_user_id,
        linked_at=linked_at,
    )


@router.patch("/config", response_model=TelegramConfigResponse)
async def update_telegram_config(
    config: TelegramConfigUpdateInput,
    current_user: User = Depends(require_auth),
):
    """Update user's Telegram configuration (enabled status and global channel usage)."""
    # Check if Telegram scraping is enabled globally
    if config.enabled and not settings.is_scrap_from_telegram:
        raise HTTPException(
            status_code=400,
            detail="Telegram scraping is disabled by administrator",
        )

    async with get_async_session_context() as session:
        # Get user's active profile
        profile = current_user.get_active_profile()
        if not profile:
            raise HTTPException(status_code=400, detail="No active profile")

        # Get existing config or create new one
        profile_data = profile.data
        existing_channels = []

        if profile_data and profile_data.telegram_config:
            existing_channels = profile_data.telegram_config.channels

        # Update the config
        profile_data.telegram_config = TelegramConfig(
            enabled=config.enabled,
            channels=existing_channels,
            use_global_channels=config.use_global_channels,
        )

        # Save profile
        profile.data = profile_data
        session.add(profile)
        await session.commit()

    # Return updated config
    return await get_telegram_config(current_user)


@router.post("/channels", response_model=TelegramChannelResponse)
async def add_telegram_channel(
    channel: TelegramChannelInput,
    current_user: User = Depends(require_auth),
):
    """Add a Telegram channel to user's configuration."""
    # Check if Telegram scraping is enabled globally
    if not settings.is_scrap_from_telegram:
        raise HTTPException(
            status_code=400,
            detail="Telegram scraping is disabled by administrator",
        )

    async with get_async_session_context() as session:
        # Get user's active profile
        profile = current_user.get_active_profile()
        if not profile:
            raise HTTPException(status_code=400, detail="No active profile")

        # Get or create telegram config
        profile_data = profile.data
        if not profile_data.telegram_config:
            profile_data.telegram_config = TelegramConfig()

        tg_config = profile_data.telegram_config

        # Check if channel already exists
        for existing in tg_config.channels:
            if existing.id == channel.id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Channel '{channel.id}' already exists",
                )

        # Add new channel
        new_channel = TelegramChannelConfig(
            id=channel.id,
            name=channel.name,
            username=channel.username,
            chat_id=channel.chat_id,
            enabled=channel.enabled,
            priority=channel.priority,
        )
        tg_config.channels.append(new_channel)

        # Save profile
        profile.data = profile_data
        session.add(profile)
        await session.commit()

    return TelegramChannelResponse(
        id=new_channel.id,
        name=new_channel.name,
        username=new_channel.username,
        chat_id=new_channel.chat_id,
        enabled=new_channel.enabled,
        priority=new_channel.priority,
    )


@router.delete("/channels/{channel_id}")
async def remove_telegram_channel(
    channel_id: str,
    current_user: User = Depends(require_auth),
):
    """Remove a Telegram channel from user's configuration."""
    async with get_async_session_context() as session:
        # Get user's active profile
        profile = current_user.get_active_profile()
        if not profile:
            raise HTTPException(status_code=400, detail="No active profile")

        profile_data = profile.data
        if not profile_data.telegram_config:
            raise HTTPException(status_code=404, detail="Channel not found")

        # Find and remove channel
        tg_config = profile_data.telegram_config
        original_len = len(tg_config.channels)
        tg_config.channels = [c for c in tg_config.channels if c.id != channel_id]

        if len(tg_config.channels) == original_len:
            raise HTTPException(status_code=404, detail="Channel not found")

        # Save profile
        profile.data = profile_data
        session.add(profile)
        await session.commit()

    return {"message": f"Channel '{channel_id}' removed"}


@router.patch("/channels/{channel_id}", response_model=TelegramChannelResponse)
async def update_telegram_channel(
    channel_id: str,
    channel: TelegramChannelInput,
    current_user: User = Depends(require_auth),
):
    """Update a Telegram channel in user's configuration."""
    async with get_async_session_context() as session:
        # Get user's active profile
        profile = current_user.get_active_profile()
        if not profile:
            raise HTTPException(status_code=400, detail="No active profile")

        profile_data = profile.data
        if not profile_data.telegram_config:
            raise HTTPException(status_code=404, detail="Channel not found")

        # Find and update channel
        tg_config = profile_data.telegram_config
        found = False
        for i, existing in enumerate(tg_config.channels):
            if existing.id == channel_id:
                tg_config.channels[i] = TelegramChannelConfig(
                    id=channel.id,
                    name=channel.name,
                    username=channel.username,
                    chat_id=channel.chat_id,
                    enabled=channel.enabled,
                    priority=channel.priority,
                )
                found = True
                break

        if not found:
            raise HTTPException(status_code=404, detail="Channel not found")

        # Save profile
        profile.data = profile_data
        session.add(profile)
        await session.commit()

    return TelegramChannelResponse(
        id=channel.id,
        name=channel.name,
        username=channel.username,
        chat_id=channel.chat_id,
        enabled=channel.enabled,
        priority=channel.priority,
    )


@router.post("/validate", response_model=ChannelValidationResult)
async def validate_telegram_channel(
    channel: TelegramChannelInput,
):
    """Validate a Telegram channel by checking if it's accessible.

    This uses the Bot API to verify the channel exists and is accessible.
    The bot must be an admin in private channels to access them.
    """
    if not settings.telegram_bot_token:
        return ChannelValidationResult(
            success=False,
            message="Telegram bot token not configured",
        )

    # Determine chat identifier to use
    chat_id = channel.chat_id or (f"@{channel.username}" if channel.username else channel.id)

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getChat"
            async with session.post(url, json={"chat_id": chat_id}) as response:
                data = await response.json()

                if not data.get("ok"):
                    error_desc = data.get("description", "Unknown error")
                    return ChannelValidationResult(
                        success=False,
                        message=f"Channel validation failed: {error_desc}",
                    )

                result = data.get("result", {})
                chat_type = result.get("type", "")

                return ChannelValidationResult(
                    success=True,
                    message="Channel is accessible",
                    title=result.get("title"),
                    username=result.get("username"),
                    chat_id=str(result.get("id")),
                    member_count=result.get("member_count"),
                    is_channel=chat_type == "channel",
                    is_group=chat_type in ("group", "supergroup"),
                )

    except aiohttp.ClientError as e:
        logger.error(f"Network error validating Telegram channel: {e}")
        return ChannelValidationResult(
            success=False,
            message="Network error connecting to Telegram API",
        )
    except Exception as e:
        logger.exception(f"Error validating Telegram channel: {e}")
        return ChannelValidationResult(
            success=False,
            message=f"Validation error: {str(e)}",
        )


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram bot updates via webhook.

    This endpoint processes all bot interactions:
    - Commands (/start, /help, /login, /status, /cancel)
    - Content contributions (magnet links, torrent files, NZB URLs, YouTube URLs,
      HTTP links, AceStream IDs, video files)
    - Callback queries from inline keyboards
    - Manual IMDb ID input during wizard flow

    Security: Uses Telegram's secret token mechanism (X-Telegram-Bot-Api-Secret-Token header)
    instead of API key authentication, as Telegram sends webhook updates without API keys.
    """
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=400, detail="Telegram bot token not configured")

    # Verify secret token if configured (recommended for security)
    if settings.telegram_webhook_secret_token:
        secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not secret_token or secret_token != settings.telegram_webhook_secret_token:
            logger.warning(f"Invalid or missing Telegram webhook secret token from {request.client.host}")
            raise HTTPException(status_code=403, detail="Invalid secret token")

    try:
        update = await request.json()

        # Handle callback queries (button presses)
        if "callback_query" in update:
            callback_query = update["callback_query"]
            callback_query_id = callback_query.get("id")
            user_id = callback_query.get("from", {}).get("id")
            chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
            message_id = callback_query.get("message", {}).get("message_id")
            callback_data = callback_query.get("data")

            if callback_query_id and user_id and callback_data:
                result = await telegram_content_bot.handle_callback_query(
                    callback_query_id=str(callback_query_id),
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    callback_data=callback_data,
                )
                return {"ok": True, "result": result}

        # Handle message updates (also edited_message - user may edit to add IMDb ID)
        message = update.get("message") or update.get("edited_message")
        if message:
            chat_id = message.get("chat", {}).get("id")
            user_id = message.get("from", {}).get("id")
            message_id = message.get("message_id")
            text = message.get("text", "") or message.get("caption", "") or ""

            # Require user_id for all processing
            if not user_id:
                return {"ok": True}

            # ============================================
            # COMMANDS (highest priority)
            # ============================================

            if text.startswith("/start"):
                disabled_cfg = set(settings.disabled_content_types)
                types_lines = "\n".join(
                    f"{emoji} {label}" for cfg_key, emoji, label in _CONTENT_TYPE_DISPLAY if cfg_key not in disabled_cfg
                )
                welcome_message = (
                    "👋 *Welcome to MediaFusion Bot!*\n\n"
                    "I help you contribute content to your MediaFusion library.\n\n"
                    f"📋 *Supported Content Types:*\n{types_lines}\n\n"
                    "📋 *How to use:*\n"
                    "1️⃣ Send me any supported content\n"
                    "2️⃣ Select Movie, Series, or Sports\n"
                    "3️⃣ Choose a match or enter IMDb ID\n"
                    "4️⃣ Review metadata and confirm\n"
                    "5️⃣ Done! Your content is added.\n\n"
                    "⚠️ *Important:* You must link your MediaFusion account first.\n"
                    "Use `/login` to get started.\n\n"
                    "Type `/help` for more details."
                )
                await telegram_content_bot.send_reply(chat_id, welcome_message)
                return {"ok": True}

            if text.startswith("/help"):
                disabled_cfg = set(settings.disabled_content_types)
                types_sections = "\n\n".join(
                    f"{emoji} *{label}*\n{detail}"
                    for cfg_key, emoji, label, detail in _CONTENT_TYPE_HELP
                    if cfg_key not in disabled_cfg
                )
                help_message = (
                    "📖 *MediaFusion Bot Help*\n\n"
                    "🔹 *Commands:*\n"
                    "`/start` - Welcome message\n"
                    "`/help` - This help message\n"
                    "`/login` - Link your Telegram account\n"
                    "`/status` - Check account status\n"
                    "`/cancel` - Cancel current operation\n\n"
                    "🔹 *Contribute Content:*\n"
                    f"Just send me any of these:\n\n{types_sections}\n\n"
                    "🔹 *Contribution Flow:*\n"
                    "1. Send content → Select type\n"
                    "2. Review matches → Select one\n"
                    "3. Edit metadata if needed\n"
                    "4. Confirm → Done!\n\n"
                    "Need help? Contact your admin."
                )
                await telegram_content_bot.send_reply(chat_id, help_message)
                return {"ok": True}

            if text.startswith("/login"):
                login_result = await telegram_content_bot.handle_login_command(user_id, chat_id)
                await telegram_content_bot.send_reply(chat_id, login_result["message"])
                return {"ok": True}

            if text.startswith("/status"):
                status_result = await telegram_content_bot.handle_status_command(user_id, chat_id)
                await telegram_content_bot.send_reply(chat_id, status_result["message"])
                return {"ok": True}

            if text.startswith("/cancel"):
                cancel_result = await telegram_content_bot.handle_cancel_command(user_id, chat_id)
                await telegram_content_bot.send_reply(chat_id, cancel_result["message"])
                return {"ok": True}

            # ============================================
            # CHECK FOR ACTIVE CONVERSATION (manual IMDb input, poster input, or block new content)
            # ============================================
            # When a contribution flow is in progress, either process the message in context
            # or prompt the user to complete/cancel. Never treat new messages as new content.

            from utils.telegram_bot import ConversationStep

            state = telegram_content_bot.get_conversation(user_id)
            if state and text and not text.startswith("/"):
                # User has an active conversation and sent text (not a command)
                if state.step == ConversationStep.AWAITING_MANUAL_IMDB:
                    # User is providing manual external ID (IMDb, TMDB, etc.)
                    result = await telegram_content_bot.process_manual_imdb_input(user_id, chat_id, text)
                    if result.get("handled") and result.get("message"):
                        await telegram_content_bot.send_reply(
                            chat_id,
                            result["message"],
                            reply_to_message_id=message_id,
                            reply_markup=result.get("reply_markup"),
                        )
                    return {"ok": True}

                elif state.step == ConversationStep.AWAITING_TITLE_SEARCH:
                    # User typed a title to search for
                    result = await telegram_content_bot.process_title_search(user_id, chat_id, text)
                    if result.get("handled") and result.get("message"):
                        await telegram_content_bot.send_reply(
                            chat_id,
                            result["message"],
                            reply_to_message_id=message_id,
                            reply_markup=result.get("reply_markup"),
                        )
                    return {"ok": True}

                elif state.step == ConversationStep.AWAITING_POSTER_INPUT:
                    # User is providing poster image URL or uploading image
                    photo = message.get("photo")
                    if photo and len(photo) > 0:
                        largest_photo = photo[-1]
                        file_id = largest_photo.get("file_id")
                        try:
                            file_info = await telegram_content_bot.get_file_info(file_id)
                            if file_info and file_info.get("file_path"):
                                file_path = file_info["file_path"]
                                poster_url = (
                                    f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
                                )
                                await telegram_content_bot.handle_poster_input(user_id, chat_id, poster_url)
                                return {"ok": True}
                        except Exception as e:
                            logger.warning(f"Failed to get file from Telegram: {e}")
                            await telegram_content_bot.send_reply(
                                chat_id,
                                "⚠️ Failed to process uploaded image. Please try sending an image URL instead.",
                                reply_to_message_id=message_id,
                            )
                        return {"ok": True}
                    elif text:
                        await telegram_content_bot.handle_poster_input(user_id, chat_id, text.strip())
                        return {"ok": True}

                else:
                    # Active conversation in progress - user must use buttons or cancel
                    await telegram_content_bot.send_reply(
                        chat_id,
                        "⚠️ *Operation in Progress*\n\n"
                        "You have an active contribution flow. Use the buttons in the message above "
                        "to continue, or /cancel to cancel and start over.",
                        reply_to_message_id=message_id,
                    )
                    return {"ok": True}

            # ============================================
            # CONTENT DETECTION (new wizard flow)
            # ============================================

            # Detect content type from the message
            content_type, raw_input = telegram_content_bot.detect_content_type(message)

            if content_type and content_type in _get_disabled_content_types():
                type_label = content_type.value.replace("_", " ").title()
                await telegram_content_bot.send_reply(
                    chat_id,
                    f"🚫 *{type_label}* imports are currently disabled on this instance.\n\n"
                    "Type `/help` to see which content types are available.",
                    reply_to_message_id=message_id,
                )
                return {"ok": True}

            if content_type:
                # Start the contribution wizard flow
                result = await telegram_content_bot.start_contribution_flow(
                    user_id=user_id,
                    chat_id=chat_id,
                    content_type=content_type,
                    raw_input=raw_input,
                    message_id=message_id,
                )

                if result.get("requires_login"):
                    # User not linked - show login prompt
                    await telegram_content_bot.send_reply(chat_id, result["message"], reply_to_message_id=message_id)
                else:
                    # Show media type selection
                    await telegram_content_bot.send_reply(
                        chat_id,
                        result["message"],
                        reply_to_message_id=message_id,
                        reply_markup=result.get("reply_markup"),
                    )

                return {"ok": True}

            # ============================================
            # LEGACY: IMDb ID for linking pending content
            # ============================================

            imdb_pattern = re.compile(r"tt\d{7,8}")
            imdb_match = imdb_pattern.search(text)
            if imdb_match and user_id:
                meta_id = imdb_match.group(0)
                # Check if there's legacy pending content
                if telegram_content_bot.get_pending_count(user_id) > 0:
                    result = await telegram_content_bot.link_pending_content(user_id, meta_id)
                    await telegram_content_bot.send_reply(chat_id, result["message"], reply_to_message_id=message_id)
                    return {"ok": True}

            # ============================================
            # UNKNOWN MESSAGE
            # ============================================

            # Only show unknown message for non-empty text that's not a command
            if text and not text.startswith("/"):
                disabled_cfg = set(settings.disabled_content_types)
                hints = "\n".join(f"• {label}" for cfg_key, label in _CONTENT_TYPE_HINTS if cfg_key not in disabled_cfg)
                unknown_message = (
                    "❓ *Not Recognized*\n\n"
                    "I couldn't detect any supported content in your message.\n\n"
                    f"🔹 *Supported formats:*\n{hints}\n\n"
                    "Type `/help` for more info."
                )
                await telegram_content_bot.send_reply(chat_id, unknown_message, reply_to_message_id=message_id)

        return {"ok": True}

    except Exception as e:
        logger.exception(f"Error processing Telegram webhook: {e}")
        return {"ok": False, "error": str(e)}
