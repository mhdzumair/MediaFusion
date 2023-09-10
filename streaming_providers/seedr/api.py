from fastapi import APIRouter
from fastapi.responses import JSONResponse
from seedrcc import Login

from db.schemas import AuthorizeData

router = APIRouter()
headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}


@router.get("/get-device-code")
async def get_device_code():
    seedr = Login()
    device_code = seedr.getDeviceCode()
    return JSONResponse(content=device_code, headers=headers)


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    seedr = Login()
    response = seedr.authorize(data.device_code)

    if "access_token" in response:
        return JSONResponse(content={"token": seedr.token}, headers=headers)
    else:
        return JSONResponse(content=response, headers=headers)
