"""
External platform sync services.

This module provides bidirectional synchronization between MediaFusion
watch history and external tracking platforms like Trakt, Simkl, etc.
"""

from api.services.sync.base import BaseSyncService, SyncResult, WatchedItem
from api.services.sync.simkl import SimklSyncService, exchange_simkl_code, get_simkl_auth_url
from api.services.sync.trakt import TraktSyncService, exchange_trakt_code, get_trakt_auth_url

__all__ = [
    # Base
    "BaseSyncService",
    "SyncResult",
    "WatchedItem",
    # Trakt
    "TraktSyncService",
    "get_trakt_auth_url",
    "exchange_trakt_code",
    # Simkl
    "SimklSyncService",
    "get_simkl_auth_url",
    "exchange_simkl_code",
]
