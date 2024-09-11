import secrets
from io import BytesIO
from typing import Annotated

import qrcode
from fastapi import APIRouter, HTTPException, Body
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
from qrcode.image.styles.colormasks import RadialGradiantColorMask
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from db.config import settings
from db.schemas import KodiConfig
from utils import const
from utils.runtime_const import REDIS_ASYNC_CLIENT


kodi_router = APIRouter()


@kodi_router.post("/generate_setup_code")
async def generate_setup_code(secret_str: Annotated[str, Body()]):
    code = secrets.token_hex(3)  # 6-digit hex code
    configure_url = f"{settings.host_url}{'/' + secret_str if secret_str else ''}/configure?kodi_code={code}"
    qr_code_url = f"http://mediafusion.local:8000/kodi/qr_code/{code}"

    # Store code in Redis
    await REDIS_ASYNC_CLIENT.set(
        f"setup_code:{code}", "1", ex=300
    )  # 5 minutes expiration

    return JSONResponse(
        content={
            "code": code,
            "configure_url": configure_url,
            "qr_code_url": qr_code_url,
            "expires_in": 300,  # 5 minutes in seconds
        },
        headers=const.NO_CACHE_HEADERS,
    )


@kodi_router.get("/qr_code/{code}")
async def get_qr_code(code: str):
    if not await REDIS_ASYNC_CLIENT.exists(f"setup_code:{code}"):
        raise HTTPException(status_code=404, detail="Invalid setup code")

    configure_url = f"{settings.host_url}/configure/{code}"

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
        color_mask=RadialGradiantColorMask(
            center_color=(255, 0, 0), edge_color=(0, 0, 255)
        ),
    )

    # Resize the image to 300x300
    img = img.resize((300, 300))

    buffered = BytesIO()
    img.save(buffered, format="PNG")
    qr_code_bytes = buffered.getvalue()

    response = StreamingResponse(BytesIO(qr_code_bytes), media_type="image/png")
    response.headers.update(const.NO_CACHE_HEADERS)
    return response


@kodi_router.post("/associate_manifest")
async def associate_manifest(kodi_config: KodiConfig):
    if not await REDIS_ASYNC_CLIENT.exists(f"setup_code:{kodi_config.code}"):
        raise HTTPException(status_code=404, detail="Invalid setup code")

    await REDIS_ASYNC_CLIENT.set(
        f"manifest:{kodi_config.code}", str(kodi_config.manifest_url), ex=300
    )  # 5 minutes expiration
    response = JSONResponse(content={"status": "success"})
    response.headers.update(const.NO_CACHE_HEADERS)
    return response


@kodi_router.get("/get_manifest/{code}")
async def get_manifest(code: str):
    manifest_url = await REDIS_ASYNC_CLIENT.get(f"manifest:{code}")
    if not manifest_url:
        raise HTTPException(status_code=404, detail="Manifest URL not found")

    # Delete the code and manifest URL after retrieval
    await REDIS_ASYNC_CLIENT.delete(f"setup_code:{code}", f"manifest:{code}")

    secret_string = manifest_url.decode("utf-8").split("/")[-2]

    response = JSONResponse(content={"secret_string": secret_string})
    response.headers.update(const.NO_CACHE_HEADERS)
    return response
