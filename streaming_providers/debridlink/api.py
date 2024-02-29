from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.schemas import AuthorizeData
from streaming_providers.debridlink.client import DebridLink
from utils import const

router = APIRouter()


@router.get("/get-device-code")
async def get_device_code():
    dl_client = DebridLink()
    return JSONResponse(
        content=dl_client.get_device_code(), headers=const.NO_CACHE_HEADERS
    )


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    dl_client = DebridLink()
    response = dl_client.authorize(data.device_code)
    return JSONResponse(content=response, headers=const.NO_CACHE_HEADERS)
