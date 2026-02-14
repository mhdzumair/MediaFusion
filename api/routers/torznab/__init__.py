"""Torznab API router package."""

from fastapi import APIRouter

from api.routers.torznab.torznab import router as torznab_router


def get_router() -> APIRouter:
    """Get the combined Torznab router."""
    router = APIRouter()
    router.include_router(torznab_router)
    return router
