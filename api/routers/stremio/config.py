"""Stremio configuration routes."""

import asyncio

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from db import schemas
from db.config import settings
from streaming_providers.validator import validate_provider_credentials
from utils import const, wrappers
from utils.crypto import crypto_utils
from utils.validation_helper import (
    validate_mdblist_token,
    validate_mediaflow_proxy_credentials,
    validate_rpdb_token,
)

router = APIRouter()


@router.get("/configure", tags=["configure"])
@router.get("/{secret_str}/configure", tags=["configure"])
async def configure(
    response: Response,
    request: Request,
    secret_str: str = None,
):
    """Redirect to the React configuration page."""
    response.headers.update(const.NO_CACHE_HEADERS)
    # Redirect to React SPA configure page
    return RedirectResponse(url="/app/configure", status_code=302)


@router.post("/encrypt-user-data", tags=["user_data"])
@router.post("/encrypt-user-data/{existing_secret_str}", tags=["user_data"])
@wrappers.rate_limit(30, 60 * 5, "user_data")
async def encrypt_user_data(
    user_data: schemas.UserData,
    request: Request,
    existing_secret_str: str | None = None,
):
    """Encrypt user configuration data."""

    async def _validate_all_config() -> dict:
        if "p2p" in settings.disabled_providers and not user_data.has_any_provider():
            return {
                "status": "error",
                "message": "Direct torrent has been disabled by the administrator. You must select a streaming provider.",
            }

        if not settings.is_public_instance and (
            not user_data.api_password or user_data.api_password != settings.api_password
        ):
            return {
                "status": "error",
                "message": "Invalid MediaFusion API Password. Make sure to enter the correct password which is configured in environment variables.",
            }
        try:
            validation_tasks = [
                validate_provider_credentials(request, user_data),
                validate_mediaflow_proxy_credentials(user_data),
                validate_rpdb_token(user_data),
                validate_mdblist_token(user_data),
            ]

            results = await asyncio.gather(*validation_tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    return {
                        "status": "error",
                        "message": f"Validation failed: {str(result)}",
                    }
                if isinstance(result, dict) and result["status"] == "error":
                    return result

            return {"status": "success"}
        except Exception as e:
            return {
                "status": "error",
                "message": f"Unexpected error during validation: {str(e)}",
            }

    if existing_secret_str:
        try:
            existing_config = await crypto_utils.decrypt_user_data(existing_secret_str)
        except ValueError:
            existing_config = schemas.UserData()

        # Restore masked passwords for streaming_providers
        if user_data.streaming_providers and existing_config.streaming_providers:
            existing_providers_by_service = {p.service: p for p in existing_config.streaming_providers}
            for provider in user_data.streaming_providers:
                existing_provider = existing_providers_by_service.get(provider.service)
                if existing_provider:
                    if provider.password == "••••••••":
                        provider.password = existing_provider.password
                    if provider.token == "••••••••":
                        provider.token = existing_provider.token
                    if provider.qbittorrent_config and existing_provider.qbittorrent_config:
                        if provider.qbittorrent_config.qbittorrent_password == "••••••••":
                            provider.qbittorrent_config.qbittorrent_password = (
                                existing_provider.qbittorrent_config.qbittorrent_password
                            )
                        if provider.qbittorrent_config.webdav_password == "••••••••":
                            provider.qbittorrent_config.webdav_password = (
                                existing_provider.qbittorrent_config.webdav_password
                            )

        if user_data.mediaflow_config and existing_config.mediaflow_config:
            if user_data.mediaflow_config.api_password == "••••••••":
                user_data.mediaflow_config.api_password = existing_config.mediaflow_config.api_password

        if user_data.rpdb_config and existing_config.rpdb_config:
            if user_data.rpdb_config.api_key == "••••••••":
                user_data.rpdb_config.api_key = existing_config.rpdb_config.api_key

    validation_result = await _validate_all_config()
    if validation_result["status"] == "error":
        return validation_result

    encrypted_str = await crypto_utils.process_user_data(user_data)
    return {"status": "success", "encrypted_str": encrypted_str}
