import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse, RedirectResponse

from db import schemas
from streaming_providers.exceptions import ProviderException
from streaming_providers.premiumize.client import Premiumize
from utils import const
from utils.crypto import UserFacingSecretError, crypto_utils

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/authorize")
async def authorize():
    if not Premiumize.OAUTH_CLIENT_ID or not Premiumize.OAUTH_CLIENT_SECRET:
        return {"error": "Premiumize OAuth not configured"}

    async with Premiumize() as pm_client:
        return RedirectResponse(pm_client.get_authorization_url(), headers=const.NO_CACHE_HEADERS)


@router.get("/oauth2_redirect")
async def oauth2_redirect(code: str):
    try:
        async with Premiumize() as pm_client:
            token_data = await pm_client.get_token(code)
            token = pm_client.encode_token_data(token_data["access_token"])
            user_data = schemas.UserData(
                streaming_provider=schemas.StreamingProvider(service="premiumize", token=token)
            )
            try:
                encrypted_str = await crypto_utils.process_user_data(user_data)
            except UserFacingSecretError as e:
                return JSONResponse({"status": "error", "message": str(e)}, status_code=200)
            except ValueError as e:
                return JSONResponse({"status": "error", "message": str(e)}, status_code=200)
            return RedirectResponse(f"/{encrypted_str}/configure", headers=const.NO_CACHE_HEADERS)
    except ProviderException as e:
        logger.warning("Premiumize OAuth token exchange failed: %s", e.message)
        return JSONResponse(
            {"status": "error", "message": e.message},
            status_code=200,
            headers=const.NO_CACHE_HEADERS,
        )
