"""Stremio configuration routes."""

import asyncio
import math
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from api.routers.user.profiles import unmask_config_update
from db import schemas
from db.config import settings
from streaming_providers.validator import validate_provider_credentials
from utils import const, wrappers
from utils.crypto import UserFacingSecretError, crypto_utils
from utils.network import get_user_data
from utils.profile_crypto import profile_crypto
from utils.validation_helper import (
    validate_mdblist_token,
    validate_mediaflow_proxy_credentials,
    validate_rpdb_token,
)

router = APIRouter()


def _make_json_safe(value: Any) -> Any:
    """Recursively normalize non-finite float values for JSON responses."""
    if isinstance(value, float):
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        if math.isnan(value):
            return None
        return value
    if isinstance(value, dict):
        return {key: _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_make_json_safe(item) for item in value]
    return value


@router.get("/configure", tags=["configure"])
@router.get("/{secret_str}/configure", tags=["configure"])
async def configure(
    response: Response,
    request: Request,
    secret_str: str = None,
):
    """Redirect to the React configuration page."""
    response.headers.update(const.NO_CACHE_HEADERS)
    redirect_url = "/app/configure"
    if secret_str:
        redirect_url = f"{redirect_url}?secret_str={quote(secret_str, safe='')}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/decrypt-user-data/{secret_str}", tags=["user_data"])
@wrappers.auth_required
@wrappers.rate_limit(30, 60 * 5, "user_data")
async def decrypt_user_data(
    response: Response,
    user_data: schemas.UserData = Depends(get_user_data),
):
    """Return masked user config for anonymous update flow."""
    response.headers.update(const.NO_CACHE_HEADERS)
    masked_config = profile_crypto.mask_secrets_for_display(user_data.model_dump(by_alias=True, exclude_none=True))
    return {
        "status": "success",
        "config": _make_json_safe(masked_config),
    }


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
        except UserFacingSecretError as e:
            return {"status": "error", "message": str(e)}
        except ValueError:
            existing_config = schemas.UserData()

        try:
            merged_config = unmask_config_update(
                user_data.model_dump(by_alias=True, exclude_none=True),
                existing_config.model_dump(by_alias=True, exclude_none=True),
            )
            user_data = schemas.UserData.model_validate(merged_config)
        except ValidationError as e:
            return {
                "status": "error",
                "message": f"Invalid configuration update: {str(e)}",
            }

    validation_result = await _validate_all_config()
    if validation_result["status"] == "error":
        return validation_result

    try:
        encrypted_str = await crypto_utils.process_user_data(user_data)
    except UserFacingSecretError as e:
        return {"status": "error", "message": str(e)}
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    return {"status": "success", "encrypted_str": encrypted_str}
