"""Moderator routes package."""

from fastapi import APIRouter

_router = None


def get_router() -> APIRouter:
    """Create and return the combined moderator router."""
    global _router
    if _router is not None:
        return _router

    from api.routers.moderator.metadata import router as metadata_router

    combined = APIRouter()
    combined.include_router(metadata_router)
    _router = combined
    return _router


__all__ = ["get_router"]
