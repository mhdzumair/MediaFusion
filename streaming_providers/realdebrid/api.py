from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.schemas import AuthorizeData
from streaming_providers.exceptions import ProviderException
from streaming_providers.realdebrid.client import RealDebrid
from utils import const

router = APIRouter()


@router.get("/get-device-code")
async def get_device_code():
    try:
        async with RealDebrid() as rd_client:
            return JSONResponse(content=await rd_client.get_device_code(), headers=const.NO_CACHE_HEADERS)
    except ProviderException as error:
        # Keep OAuth polling flow stable: return provider error payload instead of raising a 500.
        return JSONResponse(
            content={"error": error.message, "message": error.message},
            headers=const.NO_CACHE_HEADERS,
        )


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    try:
        async with RealDebrid() as rd_client:
            response = await rd_client.authorize(data.device_code)
            return JSONResponse(content=response, headers=const.NO_CACHE_HEADERS)
    except ProviderException as error:
        # Return a structured error payload so frontend can decide whether to keep polling.
        return JSONResponse(
            content={"error": error.message, "message": error.message},
            headers=const.NO_CACHE_HEADERS,
        )
