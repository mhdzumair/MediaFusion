"""Kodi device pairing and setup routes.

Handles the 6-digit code pairing flow between Kodi addon and the web UI:
1. Kodi addon calls generate-setup-code to get a 6-digit code
2. User enters that code on the MediaFusion web UI
3. Web UI calls associate-manifest with the code + manifest URL
4. Kodi addon polls get-manifest to retrieve the config

Authentication:
- generate-setup-code: Kodi addon can authenticate via X-API-Key header
  (recommended for private instances) or via secret_str (legacy encrypted
  user data containing api_password).
- associate-manifest: Called from the web UI which sends X-API-Key header
  (handled by APIKeyMiddleware).
- get-manifest / qr-code: Secured by short-lived Redis codes (5 min TTL,
  one-time use). Exempt from API key middleware.
"""

import secrets
from io import BytesIO
from typing import Annotated
from urllib.parse import quote, urlencode

import qrcode
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import RadialGradiantColorMask
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
from starlette.responses import StreamingResponse

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import KodiConfig
from utils import const
from utils.crypto import crypto_utils

router = APIRouter(prefix="/api/v1/kodi", tags=["Kodi"])


async def _validate_generate_setup_auth(request: Request, secret_str: str | None) -> None:
    """Validate authentication for generate-setup-code on private instances.

    Supports either:
    - X-API-Key header (preferred for Kodi addon private-instance setup), or
    - legacy secret_str body payload with encrypted UserData.api_password.
    """
    if settings.is_public_instance:
        return

    api_key = request.headers.get("X-API-Key")
    if api_key:
        if api_key != settings.api_password:
            raise HTTPException(status_code=401, detail="Invalid API password")
        return

    if not secret_str:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        user_data = await crypto_utils.decrypt_user_data(secret_str)
    except (ValueError, Exception):
        raise HTTPException(status_code=401, detail="Invalid configuration data")

    if user_data.api_password != settings.api_password:
        raise HTTPException(status_code=401, detail="Invalid API password")


def _validate_api_key_from_request(request: Request) -> None:
    """Validate the X-API-Key header on private instances.

    On public instances this is a no-op.
    """
    if settings.is_public_instance:
        return

    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key != settings.api_password:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.post("/generate-setup-code")
async def generate_setup_code(request: Request, secret_str: Annotated[str | None, Body()] = None):
    """Generate a 6-digit setup code for Kodi device pairing.

    Called by the Kodi addon. On private instances, authentication can be done
    via X-API-Key header or via legacy secret_str payload.
    """
    await _validate_generate_setup_auth(request, secret_str)

    code = secrets.token_hex(3)  # 6-digit hex code
    configure_query_params = {"kodi_code": code}
    if secret_str:
        configure_query_params["secret_str"] = secret_str
    configure_url = f"{settings.host_url}/app/configure?{urlencode(configure_query_params)}"
    qr_code_url = (
        f"{settings.host_url}/api/v1/kodi/qr-code/{quote(secret_str, safe='')}/{code}"
        if secret_str
        else f"{settings.host_url}/api/v1/kodi/qr-code/{code}"
    )

    # Store code in Redis
    await REDIS_ASYNC_CLIENT.set(f"setup_code:{code}", "1", ex=300)  # 5 minutes expiration

    return JSONResponse(
        content={
            "code": code,
            "configure_url": configure_url,
            "qr_code_url": qr_code_url,
            "expires_in": 300,  # 5 minutes in seconds
        },
        headers=const.NO_CACHE_HEADERS,
    )


@router.get("/qr-code/{code}")
@router.get("/qr-code/{secret_str}/{code}")
async def get_qr_code(code: str, secret_str: str = None):
    """Generate a QR code image for the setup URL.

    Secured by the short-lived setup code (5 min TTL in Redis).
    """
    if not await REDIS_ASYNC_CLIENT.exists(f"setup_code:{code}"):
        raise HTTPException(status_code=404, detail="Invalid setup code")

    configure_query_params = {"kodi_code": code}
    if secret_str:
        configure_query_params["secret_str"] = secret_str
    configure_url = f"{settings.host_url}/app/configure?{urlencode(configure_query_params)}"

    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=5,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
    )
    qr.add_data(configure_url)
    qr.make(fit=True)

    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        color_mask=RadialGradiantColorMask(center_color=(255, 0, 0), edge_color=(0, 0, 255)),
    )

    # Resize the image to 300x300
    img = img.resize((300, 300))

    buffered = BytesIO()
    img.save(buffered, format="PNG")
    qr_code_bytes = buffered.getvalue()

    response = StreamingResponse(BytesIO(qr_code_bytes), media_type="image/png")
    response.headers.update(const.NO_CACHE_HEADERS)
    return response


@router.post("/associate-manifest")
async def associate_manifest(request: Request, kodi_config: KodiConfig):
    """Associate a manifest URL with a setup code (called from web UI).

    On private instances, the X-API-Key header is validated.
    """
    _validate_api_key_from_request(request)

    if not await REDIS_ASYNC_CLIENT.exists(f"setup_code:{kodi_config.code}"):
        raise HTTPException(status_code=404, detail="Invalid setup code")

    await REDIS_ASYNC_CLIENT.set(
        f"manifest:{kodi_config.code}", str(kodi_config.manifest_url), ex=300
    )  # 5 minutes expiration
    response = JSONResponse(content={"status": "success"})
    response.headers.update(const.NO_CACHE_HEADERS)
    return response


@router.get("/get-manifest/{code}")
async def get_manifest(code: str):
    """Retrieve the manifest for a setup code (polled by Kodi addon).

    Secured by the short-lived setup code (5 min TTL, deleted after retrieval).
    """
    manifest_url = await REDIS_ASYNC_CLIENT.get(f"manifest:{code}")
    if not manifest_url:
        raise HTTPException(status_code=404, detail="Manifest URL not found")

    # Delete the code and manifest URL after retrieval
    await REDIS_ASYNC_CLIENT.delete(f"setup_code:{code}", f"manifest:{code}")

    secret_string = manifest_url.decode("utf-8").split("/")[-2]

    response = JSONResponse(content={"secret_string": secret_string})
    response.headers.update(const.NO_CACHE_HEADERS)
    return response
