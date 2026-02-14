from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.schemas import AuthorizeData
from streaming_providers.debridlink.client import DebridLink
from utils import const

router = APIRouter()


@router.get("/get-device-code")
async def get_device_code():
    async with DebridLink() as dl_client:
        return JSONResponse(content=await dl_client.get_device_code(), headers=const.NO_CACHE_HEADERS)


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    async with DebridLink() as dl_client:
        return JSONResponse(
            content=await dl_client.authorize(data.device_code),
            headers=const.NO_CACHE_HEADERS,
        )
