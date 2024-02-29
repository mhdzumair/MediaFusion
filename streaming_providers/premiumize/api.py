from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from db import schemas
from streaming_providers.premiumize.client import Premiumize
from utils import crypto, const

router = APIRouter()


@router.get("/authorize")
async def authorize():
    if not Premiumize.OAUTH_CLIENT_ID or not Premiumize.OAUTH_CLIENT_SECRET:
        return {"error": "Premiumize OAuth not configured"}

    premiumize_client = Premiumize()
    return RedirectResponse(
        premiumize_client.get_authorization_url(), headers=const.NO_CACHE_HEADERS
    )


@router.get("/oauth2_redirect")
async def oauth2_redirect(code: str):
    premiumize_client = Premiumize()
    token_data = premiumize_client.get_token(code)
    token = premiumize_client.encode_token_data(token_data["access_token"])
    user_data = schemas.UserData(
        streaming_provider=schemas.StreamingProvider(service="premiumize", token=token)
    )
    encrypted_str = crypto.encrypt_user_data(user_data)
    return RedirectResponse(
        f"/{encrypted_str}/configure", headers=const.NO_CACHE_HEADERS
    )
