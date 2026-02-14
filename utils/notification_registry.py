"""
Notification registry for decoupling modules.

This module provides a registry pattern so that high-level components
(like the Telegram bot) can be notified of events without creating
circular import dependencies.

Usage:
    # In streaming_providers/parser.py (or other callers):
    from utils.notification_registry import send_file_annotation_request
    await send_file_annotation_request(info_hash, name)

    # In utils/telegram_bot.py (at startup):
    from utils.notification_registry import register_file_annotation_handler
    register_file_annotation_handler(telegram_notifier.send_file_annotation_request)
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Type for async handler: (info_hash: str, name: str) -> None
FileAnnotationHandler = Callable[[str, str], Awaitable[Any]]

_file_annotation_handlers: list[FileAnnotationHandler] = []


def register_file_annotation_handler(handler: FileAnnotationHandler) -> None:
    """Register a handler to be called when file annotation is requested."""
    if handler not in _file_annotation_handlers:
        _file_annotation_handlers.append(handler)
        logger.debug("Registered file annotation handler: %s", handler.__qualname__)


def unregister_file_annotation_handler(handler: FileAnnotationHandler) -> None:
    """Unregister a previously registered handler."""
    if handler in _file_annotation_handlers:
        _file_annotation_handlers.remove(handler)


async def send_file_annotation_request(info_hash: str, name: str) -> None:
    """
    Notify all registered handlers that a file annotation was requested.

    Called when a torrent stream needs contributor annotation (e.g. to map
    files to episodes). Handlers may send Telegram messages to request input.

    Args:
        info_hash: Torrent info hash
        name: Display name for the torrent
    """
    if not _file_annotation_handlers:
        logger.debug("No file annotation handlers registered, skipping notification")
        return

    for handler in _file_annotation_handlers:
        try:
            await handler(info_hash, name)
        except Exception as e:
            logger.exception("File annotation handler %s failed: %s", handler.__qualname__, e)
