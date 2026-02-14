"""Content-related routes package.

Import the router via get_router() to avoid circular imports.
"""

from fastapi import APIRouter

_router = None


def get_router() -> APIRouter:
    """Create and return the combined content router.

    Uses lazy imports to avoid circular dependencies.
    """
    global _router
    if _router is not None:
        return _router

    from api.routers.content.catalog import router as catalog_router
    from api.routers.content.contributions import router as contributions_router
    from api.routers.content.episode_suggestions import (
        router as episode_suggestions_router,
    )
    from api.routers.content.iptv_sources import router as iptv_sources_router
    from api.routers.content.m3u_import import router as m3u_import_router
    from api.routers.content.metadata import router as metadata_router
    from api.routers.content.nzb_import import router as nzb_import_router
    from api.routers.content.scraping import router as scraping_router
    from api.routers.content.stream_linking import router as stream_linking_router
    from api.routers.content.stream_suggestions import (
        router as stream_suggestions_router,
    )
    from api.routers.content.suggestions import router as suggestions_router
    from api.routers.content.torrent_import import router as torrent_import_router
    from api.routers.content.user_metadata import router as user_metadata_router
    from api.routers.content.voting import router as voting_router
    from api.routers.content.xtream_import import router as xtream_import_router
    from api.routers.content.acestream_import import router as acestream_import_router
    from api.routers.content.http_import import router as http_import_router
    from api.routers.content.youtube_import import router as youtube_import_router

    combined = APIRouter()
    combined.include_router(catalog_router)
    combined.include_router(contributions_router)
    combined.include_router(torrent_import_router)
    combined.include_router(nzb_import_router)
    combined.include_router(m3u_import_router)
    combined.include_router(xtream_import_router)
    combined.include_router(iptv_sources_router)
    combined.include_router(acestream_import_router)
    combined.include_router(http_import_router)
    combined.include_router(youtube_import_router)
    combined.include_router(voting_router)
    combined.include_router(suggestions_router)
    combined.include_router(stream_suggestions_router)
    combined.include_router(episode_suggestions_router)
    combined.include_router(stream_linking_router)
    combined.include_router(metadata_router)
    combined.include_router(user_metadata_router)
    combined.include_router(scraping_router)
    _router = combined
    return _router


# Don't auto-create router at import time to avoid circular imports
# Use get_router() instead when registering routes

__all__ = ["get_router"]
