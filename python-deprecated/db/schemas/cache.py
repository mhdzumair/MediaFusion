"""Cache-related schemas for debrid services."""

from typing import Literal

from pydantic import BaseModel


class CacheStatusRequest(BaseModel):
    """Request model for checking cache status."""

    service: Literal[
        "realdebrid",
        "premiumize",
        "alldebrid",
        "debridlink",
        "offcloud",
        "seedr",
        "pikpak",
        "torbox",
        "easydebrid",
        "debrider",
    ]
    info_hashes: list[str]


class CacheStatusResponse(BaseModel):
    """Response model for cache status."""

    cached_status: dict[str, bool]


class CacheSubmitRequest(BaseModel):
    """Request model for submitting cached info hashes."""

    service: Literal[
        "realdebrid",
        "premiumize",
        "alldebrid",
        "debridlink",
        "offcloud",
        "seedr",
        "pikpak",
        "torbox",
        "easydebrid",
        "debrider",
    ]
    info_hashes: list[str]


class CacheSubmitResponse(BaseModel):
    """Response model for cache submission."""

    success: bool
    message: str
