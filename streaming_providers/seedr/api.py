from fastapi import APIRouter
from fastapi.responses import JSONResponse
from aioseedrcc import Login

from db.schemas import AuthorizeData
from utils import const

router = APIRouter()


@router.get("/get-device-code")
async def get_device_code():
    async with Login() as seedr_login:
        device_code = await seedr_login.get_device_code()
        return JSONResponse(content=device_code, headers=const.NO_CACHE_HEADERS)


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    async with Login() as seedr_login:
        response = await seedr_login.authorize(data.device_code)
        if "access_token" in response:
            return JSONResponse(
                content={"token": seedr_login.token}, headers=const.NO_CACHE_HEADERS
            )
        else:
            return JSONResponse(content=response, headers=const.NO_CACHE_HEADERS)
