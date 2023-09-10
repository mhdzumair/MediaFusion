from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.schemas import AuthorizeData
from streaming_providers.realdebrid.client import RealDebrid

router = APIRouter()
headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}


@router.get("/get-device-code")
async def get_device_code():
    rd_client = RealDebrid()
    return JSONResponse(content=rd_client.get_device_code(), headers=headers)


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    rd_client = RealDebrid()
    response = rd_client.authorize(data.device_code)
    return JSONResponse(content=response, headers=headers)
