"""
Streaming routes package - user-facing playback, cache, and provider auth endpoints.
"""

from fastapi import APIRouter

# Create main streaming router
router = APIRouter()


def get_router() -> APIRouter:
    """Get streaming router with all sub-routers included.

    This router handles:
    - Playback routes: /{secret_str}/playback/... (no prefix needed)
    - Cache routes: /cache/...
    - Provider auth: /streaming_provider/{provider}/... (needs prefix)
    """
    # Import sub-routers lazily to avoid circular imports
    from api.routers.streaming.cache import router as cache_router
    from api.routers.streaming.playback import router as playback_router

    # Main router for playback (no prefix - these are at root level)
    _router = APIRouter()
    _router.include_router(playback_router, tags=["streaming_provider"])
    _router.include_router(cache_router, prefix="/cache", tags=["cache"])

    return _router


def get_provider_router() -> APIRouter:
    """Get provider-specific router for auth endpoints.

    These routes are under /streaming_provider/{provider}/...
    Includes OAuth/device code authorization for supported providers.
    """
    from streaming_providers.debridlink.api import router as debridlink_router
    from streaming_providers.premiumize.api import router as premiumize_router
    from streaming_providers.realdebrid.api import router as realdebrid_router
    from streaming_providers.seedr.api import router as seedr_router

    _router = APIRouter()
    _router.include_router(seedr_router, prefix="/seedr", tags=["seedr"])
    _router.include_router(realdebrid_router, prefix="/realdebrid", tags=["realdebrid"])
    _router.include_router(debridlink_router, prefix="/debridlink", tags=["debridlink"])
    _router.include_router(premiumize_router, prefix="/premiumize", tags=["premiumize"])

    # Note: Other providers (alldebrid, torbox, offcloud, pikpak, easydebrid)
    # don't have API routers yet. They use direct token authentication.

    return _router


__all__ = ["router", "get_router", "get_provider_router"]
