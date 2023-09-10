import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.schemas import AuthorizeData
from streaming_providers.realdebrid.token import encode_token_data

router = APIRouter()
headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}
OPENSOURCE_CLIENT_ID = "X245A4XAIBGVM"
API_OAUTH_URL = "https://api.real-debrid.com/oauth/v2"


@router.get("/get-device-code")
async def get_device_code():
    response = requests.get(
        f"{API_OAUTH_URL}/device/code", params={"client_id": OPENSOURCE_CLIENT_ID, "new_credentials": "yes"}
    )
    return JSONResponse(content=response.json(), headers=headers)


@router.post("/authorize")
async def authorize(data: AuthorizeData):
    response = requests.get(
        f"{API_OAUTH_URL}/device/credentials",
        params={"client_id": OPENSOURCE_CLIENT_ID, "code": data.device_code},
    )
    response_data = response.json()

    if "client_secret" not in response_data:
        return JSONResponse(content=response_data, headers=headers)

    response = requests.post(
        f"{API_OAUTH_URL}/token",
        data={
            "client_id": response_data["client_id"],
            "client_secret": response_data["client_secret"],
            "code": data.device_code,
            "grant_type": "http://oauth.net/grant_type/device/1.0",
        },
    )

    token_data = response.json()

    if "access_token" in token_data:
        token = encode_token_data(
            response_data["client_id"], response_data["client_secret"], token_data["refresh_token"]
        )
        return JSONResponse(content={"token": token}, headers=headers)
    else:
        return JSONResponse(content=response, headers=headers)
