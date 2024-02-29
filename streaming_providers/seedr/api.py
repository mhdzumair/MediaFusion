from fastapi import APIRouter
from fastapi.responses import JSONResponse
from seedrcc import Login

from db.schemas import AuthorizeData
from utils import const

router = APIRouter()


@router.get("/get-device-code")
async def get_device_code():
    seedr = Login()
    device_code = seedr.getDeviceCode()
    return JSONResponse(content=device_code, headers=const.NO_CACHE_HEADERS)


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    seedr = Login()
    response = seedr.authorize(data.device_code)

    if "access_token" in response:
        return JSONResponse(
            content={"token": seedr.token}, headers=const.NO_CACHE_HEADERS
        )
    else:
        return JSONResponse(content=response, headers=const.NO_CACHE_HEADERS)
