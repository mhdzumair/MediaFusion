from aioseedrcc import Login
from aioseedrcc.exception import SeedrException
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.schemas import AuthorizeData
from streaming_providers.exceptions import ProviderException
from utils import const

router = APIRouter()


def _map_seedr_oauth_error(error: SeedrException) -> ProviderException:
    """Map Seedr OAuth SDK errors to ProviderException."""
    normalized = str(error).lower()
    if "unauthorized" in normalized or "forbidden" in normalized:
        return ProviderException("Invalid Seedr token", "invalid_token.mp4")
    if "timeout" in normalized or "timed out" in normalized:
        return ProviderException("Seedr request timed out", "debrid_service_down_error.mp4")
    return ProviderException("Seedr authorization failed", "api_error.mp4")


@router.get("/get-device-code")
async def get_device_code():
    try:
        async with Login() as seedr_login:
            device_code = await seedr_login.get_device_code()
            return JSONResponse(content=device_code, headers=const.NO_CACHE_HEADERS)
    except SeedrException as error:
        raise _map_seedr_oauth_error(error) from error


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    try:
        async with Login() as seedr_login:
            response = await seedr_login.authorize(data.device_code)
            if "access_token" in response:
                return JSONResponse(content={"token": seedr_login.token}, headers=const.NO_CACHE_HEADERS)
            else:
                return JSONResponse(content=response, headers=const.NO_CACHE_HEADERS)
    except SeedrException as error:
        raise _map_seedr_oauth_error(error) from error
