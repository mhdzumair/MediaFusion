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
PendingContributionHandler = Callable[[dict[str, Any]], Awaitable[Any]]
PendingStreamSuggestionHandler = Callable[[dict[str, Any]], Awaitable[Any]]
PendingMetadataSuggestionHandler = Callable[[dict[str, Any]], Awaitable[Any]]
PendingEpisodeSuggestionHandler = Callable[[dict[str, Any]], Awaitable[Any]]

_file_annotation_handlers: list[FileAnnotationHandler] = []
_pending_contribution_handlers: list[PendingContributionHandler] = []
_pending_stream_suggestion_handlers: list[PendingStreamSuggestionHandler] = []
_pending_metadata_suggestion_handlers: list[PendingMetadataSuggestionHandler] = []
_pending_episode_suggestion_handlers: list[PendingEpisodeSuggestionHandler] = []


async def _dispatch_payload(
    handlers: list[Callable[[dict[str, Any]], Awaitable[Any]]],
    payload: dict[str, Any],
    event_name: str,
) -> None:
    """Dispatch a payload to registered handlers with per-handler isolation."""
    if not handlers:
        logger.debug("No %s handlers registered, skipping notification", event_name)
        return

    for handler in handlers:
        try:
            await handler(payload)
        except Exception as e:
            logger.exception("%s handler %s failed: %s", event_name, handler.__qualname__, e)


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


def register_pending_contribution_handler(handler: PendingContributionHandler) -> None:
    """Register a handler for pending contribution notifications."""
    if handler not in _pending_contribution_handlers:
        _pending_contribution_handlers.append(handler)
        logger.debug("Registered pending contribution handler: %s", handler.__qualname__)


def unregister_pending_contribution_handler(handler: PendingContributionHandler) -> None:
    """Unregister a pending contribution handler."""
    if handler in _pending_contribution_handlers:
        _pending_contribution_handlers.remove(handler)


async def send_pending_contribution_notification(payload: dict[str, Any]) -> None:
    """Notify handlers about a newly created pending contribution."""
    await _dispatch_payload(_pending_contribution_handlers, payload, "Pending contribution")


def register_pending_stream_suggestion_handler(handler: PendingStreamSuggestionHandler) -> None:
    """Register a handler for pending stream suggestion notifications."""
    if handler not in _pending_stream_suggestion_handlers:
        _pending_stream_suggestion_handlers.append(handler)
        logger.debug("Registered pending stream suggestion handler: %s", handler.__qualname__)


def unregister_pending_stream_suggestion_handler(handler: PendingStreamSuggestionHandler) -> None:
    """Unregister a pending stream suggestion handler."""
    if handler in _pending_stream_suggestion_handlers:
        _pending_stream_suggestion_handlers.remove(handler)


async def send_pending_stream_suggestion_notification(payload: dict[str, Any]) -> None:
    """Notify handlers about a newly created pending stream suggestion."""
    await _dispatch_payload(_pending_stream_suggestion_handlers, payload, "Pending stream suggestion")


def register_pending_metadata_suggestion_handler(handler: PendingMetadataSuggestionHandler) -> None:
    """Register a handler for pending metadata suggestion notifications."""
    if handler not in _pending_metadata_suggestion_handlers:
        _pending_metadata_suggestion_handlers.append(handler)
        logger.debug("Registered pending metadata suggestion handler: %s", handler.__qualname__)


def unregister_pending_metadata_suggestion_handler(handler: PendingMetadataSuggestionHandler) -> None:
    """Unregister a pending metadata suggestion handler."""
    if handler in _pending_metadata_suggestion_handlers:
        _pending_metadata_suggestion_handlers.remove(handler)


async def send_pending_metadata_suggestion_notification(payload: dict[str, Any]) -> None:
    """Notify handlers about a newly created pending metadata suggestion."""
    await _dispatch_payload(_pending_metadata_suggestion_handlers, payload, "Pending metadata suggestion")


def register_pending_episode_suggestion_handler(handler: PendingEpisodeSuggestionHandler) -> None:
    """Register a handler for pending episode suggestion notifications."""
    if handler not in _pending_episode_suggestion_handlers:
        _pending_episode_suggestion_handlers.append(handler)
        logger.debug("Registered pending episode suggestion handler: %s", handler.__qualname__)


def unregister_pending_episode_suggestion_handler(handler: PendingEpisodeSuggestionHandler) -> None:
    """Unregister a pending episode suggestion handler."""
    if handler in _pending_episode_suggestion_handlers:
        _pending_episode_suggestion_handlers.remove(handler)


async def send_pending_episode_suggestion_notification(payload: dict[str, Any]) -> None:
    """Notify handlers about a newly created pending episode suggestion."""
    await _dispatch_payload(_pending_episode_suggestion_handlers, payload, "Pending episode suggestion")
