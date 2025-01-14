import logging
from typing import Optional

import aiohttp

from db.config import settings

logger = logging.getLogger(__name__)


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
        seasons_and_episodes: Optional[dict] = None,
        catalogs: Optional[list] = None,
        languages: Optional[list] = None,
    ):
        """Send notification about new contribution to Telegram channel"""
        if not self.enabled:
            logger.warning("Telegram notifications are disabled. Check bot token.")
            return

        # Create block URL - will open scraper page with block_torrent action and info hash
        block_url = (
            f"{settings.host_url}/scraper?"
            f"action=block_torrent&"
            f"info_hash={info_hash}"
        )
        meta_id_data = (
            f"*IMDb*: [{meta_id}](https://www.imdb.com/title/{meta_id}/)\n"
            if meta_id.startswith("tt")
            else f"Meta ID: {meta_id}\n"
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
            f"*Poster*: [View]({poster})"
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
        message += f"\n\n[🚫 Block Torrent]({block_url})"

        await self._send_photo_message(poster, message)

    async def send_block_notification(
        self,
        info_hash: str,
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
            f"🚫 Torrent Blocked\n\n"
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
                        logger.error(
                            f"Failed to send Telegram notification: {error_data}"
                        )
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


# Create a singleton instance
telegram_notifier = TelegramNotifier()
