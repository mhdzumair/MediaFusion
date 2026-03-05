"""Stremio manifest route."""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, Response

from db import crud, schemas
from db.config import settings
from db.database import get_async_session_context, get_read_session_context
from db.redis_database import REDIS_ASYNC_CLIENT
from db.retry_utils import run_db_read_with_primary_fallback
from utils import const, wrappers
from utils.network import get_user_data
from utils.parser import generate_manifest

router = APIRouter()
MANIFEST_CACHE_TTL_SECONDS = 300
logger = logging.getLogger(__name__)


def _get_manifest_cache_key(user_data: schemas.UserData) -> str:
    user_data_payload = user_data.model_dump_json(
        exclude_none=True,
        exclude_defaults=True,
        exclude_unset=True,
        by_alias=True,
        round_trip=True,
    )
    # Use HMAC (server-secret keyed hash) so cache keys do not expose deterministic
    # plain SHA digests of user configuration payloads.
    user_data_hash = hmac.new(
        settings.secret_key.encode("utf-8"),
        user_data_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"manifest:response:{settings.version}:{user_data_hash}"


@router.get("/manifest.json", tags=["manifest"])
@router.get("/{secret_str}/manifest.json", tags=["manifest"])
@wrappers.auth_required
async def get_manifest(
    response: Response,
    user_data: schemas.UserData = Depends(get_user_data),
):
    """Get the Stremio addon manifest."""
    response.headers.update(const.NO_CACHE_HEADERS)
    manifest_cache_ttl = (
        min(settings.meta_cache_ttl, MANIFEST_CACHE_TTL_SECONDS)
        if settings.meta_cache_ttl > 0
        else MANIFEST_CACHE_TTL_SECONDS
    )
    manifest_cache_key = _get_manifest_cache_key(user_data)
    cached_manifest = await REDIS_ASYNC_CLIENT.get(manifest_cache_key)
    if cached_manifest:
        return Response(
            content=cached_manifest,
            media_type="application/json",
            headers=dict(response.headers),
        )

    # Fetch all genres in a single efficient query
    async def _fetch_genres_from_read_replica() -> dict[str, list[str]]:
        async with get_read_session_context() as session:
            return await crud.get_all_genres_by_type(session)

    async def _fetch_genres_from_primary() -> dict[str, list[str]]:
        async with get_async_session_context() as session:
            return await crud.get_all_genres_by_type(session)

    genres_fetch_failed = False
    try:
        genres = await run_db_read_with_primary_fallback(
            _fetch_genres_from_read_replica,
            _fetch_genres_from_primary,
            operation_name="stremio manifest genre fetch",
            on_fallback=lambda exc: logger.warning(
                "Read replica conflict for manifest genres, retrying on primary: %s",
                exc,
            ),
        )
    except Exception as exc:
        logger.exception(
            "Error fetching genres for manifest from both replica and primary: %s",
            exc,
        )
        genres = {"movie": [], "series": [], "tv": []}
        genres_fetch_failed = True

    manifest = await generate_manifest(user_data, genres)
    manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    if not genres_fetch_failed:
        await REDIS_ASYNC_CLIENT.set(manifest_cache_key, manifest_json, ex=manifest_cache_ttl)
    return Response(
        content=manifest_json,
        media_type="application/json",
        headers=dict(response.headers),
    )
