import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from seedrcc import AsyncSeedr
from seedrcc.exceptions import APIError, AuthenticationError, NetworkError, ServerError

from db.schemas import AuthorizeData
from streaming_providers.exceptions import ProviderException
from utils import const

router = APIRouter()
logger = logging.getLogger(__name__)


def _map_seedr_error(error: Exception) -> ProviderException:
    if isinstance(error, AuthenticationError):
        return ProviderException("Invalid Seedr token", "invalid_token.mp4")
    if isinstance(error, NetworkError):
        return ProviderException("Seedr request timed out", "debrid_service_down_error.mp4")
    return ProviderException("Seedr authorization failed", "api_error.mp4")


def _oauth_error_response(error: ProviderException) -> JSONResponse:
    return JSONResponse(
        content={"error": error.message, "message": error.message},
        headers=const.NO_CACHE_HEADERS,
    )


@router.get("/get-device-code")
async def get_device_code():
    try:
        device_code = await AsyncSeedr.get_device_code()
        return JSONResponse(content=device_code.get_raw(), headers=const.NO_CACHE_HEADERS)
    except (APIError, ServerError) as error:
        logger.warning("Seedr get-device-code error: %s", error)
        return _oauth_error_response(
            ProviderException("Seedr service error. Please try again.", "debrid_service_down_error.mp4")
        )
    except NetworkError as error:
        logger.warning("Seedr get-device-code network error: %s", error)
        return _oauth_error_response(ProviderException("Seedr request failed. Please try again.", "api_error.mp4"))
    except (asyncio.TimeoutError, TimeoutError) as error:
        logger.warning("Seedr get-device-code timeout: %s", error)
        return _oauth_error_response(ProviderException("Seedr request timed out", "debrid_service_down_error.mp4"))


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    try:
        client = await AsyncSeedr.from_device_code(data.device_code)
        token_b64 = client.token.to_base64()
        await client.close()
        return JSONResponse(content={"token": token_b64}, headers=const.NO_CACHE_HEADERS)
    except AuthenticationError as error:
        error_type = error.error_type or ""
        if error_type in ("authorization_pending", "slow_down"):
            return JSONResponse(
                content={"result": error_type},
                headers=const.NO_CACHE_HEADERS,
            )
        logger.warning("Seedr authorize auth error: %s", error)
        return _oauth_error_response(ProviderException("Invalid Seedr token", "invalid_token.mp4"))
    except (APIError, ServerError) as error:
        logger.warning("Seedr authorize error: %s", error)
        return _oauth_error_response(
            ProviderException("Seedr service error. Please try again.", "debrid_service_down_error.mp4")
        )
    except NetworkError as error:
        logger.warning("Seedr authorize network error: %s", error)
        return _oauth_error_response(ProviderException("Seedr request failed. Please try again.", "api_error.mp4"))
    except (asyncio.TimeoutError, TimeoutError) as error:
        logger.warning("Seedr authorize timeout: %s", error)
        return _oauth_error_response(ProviderException("Seedr request timed out", "debrid_service_down_error.mp4"))
