"""
Cache status and submission routes for streaming providers.
"""

from fastapi import APIRouter, HTTPException

from db.schemas import (
    CacheStatusRequest,
    CacheStatusResponse,
    CacheSubmitRequest,
    CacheSubmitResponse,
    StreamingProvider,
)
from streaming_providers.cache_helpers import (
    get_cached_status,
    store_cached_info_hashes,
)
from utils import wrappers

router = APIRouter()


@router.post("/status", response_model=CacheStatusResponse)
@wrappers.exclude_rate_limit
async def check_cache_status(request: CacheStatusRequest):
    """
    Check cache status for multiple info hashes.

    Args:
        request: CacheStatusRequest containing service name and list of info hashes

    Returns:
        Dictionary mapping info hashes to their cache status
    """
    if not request.info_hashes:
        return CacheStatusResponse(cached_status={})

    # Create streaming provider object
    provider = StreamingProvider(service=request.service, token="")

    try:
        # Get cache status using existing helper
        cached_status = await get_cached_status(provider, request.info_hashes)
        return CacheStatusResponse(cached_status=cached_status)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking cache status: {str(e)}")


@router.post("/submit", response_model=CacheSubmitResponse)
@wrappers.exclude_rate_limit
async def submit_cached_hashes(request: CacheSubmitRequest):
    """
    Submit cached info hashes to the central cache.

    Args:
        request: CacheSubmitRequest containing service name and list of cached info hashes

    Returns:
        Success status and message
    """
    if not request.info_hashes:
        return CacheSubmitResponse(success=True, message="No info hashes provided")

    provider = StreamingProvider(service=request.service, token="")
    try:
        # Store cache info using existing helper
        await store_cached_info_hashes(provider, request.info_hashes)
        return CacheSubmitResponse(
            success=True,
            message=f"Successfully stored {len(request.info_hashes)} cached info hashes",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error storing cached info hashes: {str(e)}")
