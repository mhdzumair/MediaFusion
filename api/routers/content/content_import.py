"""
Content Import API endpoints - Combined router for backward compatibility.

This module combines all content import routers:
- torrent_import: Magnet and torrent file imports
- m3u_import: M3U playlist imports
- xtream_import: Xtream Codes server imports
- iptv_sources: IPTV source management
- youtube_import: YouTube video imports
- http_import: HTTP URL imports (with MediaFlow extractor support)
- acestream_import: AceStream content imports

For new code, import directly from the specific modules.
"""

from fastapi import APIRouter

from api.routers.content.acestream_import import router as acestream_import_router
from api.routers.content.http_import import router as http_import_router
from api.routers.content.iptv_sources import router as iptv_sources_router
from api.routers.content.m3u_import import router as m3u_import_router

# Re-export process_torrent_import for backward compatibility
from api.routers.content.torrent_import import process_torrent_import
from api.routers.content.torrent_import import router as torrent_import_router
from api.routers.content.xtream_import import router as xtream_import_router
from api.routers.content.youtube_import import router as youtube_import_router

# Combine all routers (they already have the same prefix, so we just merge them)
router = APIRouter()
router.include_router(torrent_import_router)
router.include_router(m3u_import_router)
router.include_router(xtream_import_router)
router.include_router(iptv_sources_router)
router.include_router(youtube_import_router)
router.include_router(http_import_router)
router.include_router(acestream_import_router)

__all__ = ["router", "process_torrent_import"]
