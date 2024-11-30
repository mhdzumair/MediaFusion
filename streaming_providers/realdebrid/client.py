from base64 import b64encode, b64decode
from binascii import Error as BinasciiError
from typing import Any, Optional

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class RealDebrid(DebridClient):
    BASE_URL = "https://api.real-debrid.com/rest/1.0"
    OAUTH_URL = "https://api.real-debrid.com/oauth/v2"
    OPENSOURCE_CLIENT_ID = "X245A4XAIBGVM"

    def __init__(self, token: Optional[str] = None, user_ip: Optional[str] = None):
        self.user_ip = user_ip
        super().__init__(token)

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        error_code = error_data.get("error_code")
        match error_code:
            case 9:
                raise ProviderException(
                    "Real-Debrid Permission denied", "invalid_token.mp4"
                )
            case 22:
                raise ProviderException("IP address not allowed", "ip_not_allowed.mp4")
            case 34:
                raise ProviderException("Too many requests", "too_many_requests.mp4")
            case 35:
                raise ProviderException(
                    "Content marked as infringing", "content_infringing.mp4"
                )
            case 21:
                raise ProviderException(
                    "Active torrents limit reached", "torrent_limit.mp4"
                )

    async def _make_request(
        self,
        method: str,
        url: str,
        data: Optional[dict] = None,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        is_return_none: bool = False,
        is_expected_to_fail: bool = False,
        retry_count: int = 0,
    ) -> dict:
        if method == "POST" and self.user_ip:
            data = data or {}
            data["ip"] = self.user_ip
        return await super()._make_request(
            method, url, data, json, params, is_return_none, is_expected_to_fail
        )

    async def initialize_headers(self):
        if self.token:
            token_data = self.decode_token_str(self.token)
            if "private_token" in token_data:
                self.headers = {
                    "Authorization": f"Bearer {token_data['private_token']}"
                }
                self.is_private_token = True
            else:
                access_token_data = await self.get_token(
                    token_data["client_id"],
                    token_data["client_secret"],
                    token_data["code"],
                )
                self.headers = {
                    "Authorization": f"Bearer {access_token_data['access_token']}"
                }

    @staticmethod
    def encode_token_data(
        code: str, client_id: str = None, client_secret: str = None, *args, **kwargs
    ):
        token = f"{client_id}:{client_secret}:{code}"
        return b64encode(str(token).encode()).decode()

    @staticmethod
    def decode_token_str(token: str) -> dict[str, str]:
        try:
            client_id, client_secret, code = b64decode(token).decode().split(":")
        except (ValueError, BinasciiError):
            return {"private_token": token}
        return {"client_id": client_id, "client_secret": client_secret, "code": code}

    async def get_device_code(self):
        return await self._make_request(
            "GET",
            f"{self.OAUTH_URL}/device/code",
            params={"client_id": self.OPENSOURCE_CLIENT_ID, "new_credentials": "yes"},
        )

    async def get_token(self, client_id, client_secret, device_code):
        return await self._make_request(
            "POST",
            f"{self.OAUTH_URL}/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": device_code,
                "grant_type": "http://oauth.net/grant_type/device/1.0",
            },
        )

    async def authorize(self, device_code):
        response_data = await self._make_request(
            "GET",
            f"{self.OAUTH_URL}/device/credentials",
            params={"client_id": self.OPENSOURCE_CLIENT_ID, "code": device_code},
            is_expected_to_fail=True,
        )

        if "client_secret" not in response_data:
            return response_data

        token_data = await self.get_token(
            response_data["client_id"], response_data["client_secret"], device_code
        )

        if "access_token" in token_data:
            token = self.encode_token_data(
                client_id=response_data["client_id"],
                client_secret=response_data["client_secret"],
                code=token_data["refresh_token"],
            )
            return {"token": token}
        else:
            return token_data

    async def add_magnet_link(self, magnet_link):
        return await self._make_request(
            "POST", f"{self.BASE_URL}/torrents/addMagnet", data={"magnet": magnet_link}
        )

    async def get_active_torrents(self):
        return await self._make_request("GET", f"{self.BASE_URL}/torrents/activeCount")

    async def get_user_torrent_list(self):
        return await self._make_request("GET", f"{self.BASE_URL}/torrents")

    async def get_user_downloads(self):
        return await self._make_request("GET", f"{self.BASE_URL}/downloads")

    async def get_torrent_info(self, torrent_id):
        return await self._make_request(
            "GET", f"{self.BASE_URL}/torrents/info/{torrent_id}"
        )

    async def disable_access_token(self):
        return await self._make_request(
            "GET",
            f"{self.BASE_URL}/disable_access_token",
            is_return_none=True,
            is_expected_to_fail=True,
        )

    async def start_torrent_download(self, torrent_id, file_ids="all"):
        return await self._make_request(
            "POST",
            f"{self.BASE_URL}/torrents/selectFiles/{torrent_id}",
            data={"files": file_ids},
            is_return_none=True,
        )

    async def get_available_torrent(self, info_hash) -> Optional[dict[str, Any]]:
        available_torrents = await self.get_user_torrent_list()
        for torrent in available_torrents:
            if torrent["hash"] == info_hash:
                return torrent
        return None

    async def create_download_link(self, link):
        response = await self._make_request(
            "POST",
            f"{self.BASE_URL}/unrestrict/link",
            data={"link": link},
            is_expected_to_fail=True,
        )
        if "download" in response:
            return response

        if "error_code" in response:
            if response["error_code"] == 23:
                raise ProviderException(
                    "Exceed remote traffic limit", "exceed_remote_traffic_limit.mp4"
                )
        raise ProviderException(
            f"Failed to create download link. response: {response}", "api_error.mp4"
        )

    async def delete_torrent(self, torrent_id) -> dict:
        return await self._make_request(
            "DELETE",
            f"{self.BASE_URL}/torrents/delete/{torrent_id}",
            is_return_none=True,
        )

    async def get_user_info(self) -> dict:
        return await self._make_request("GET", f"{self.BASE_URL}/user")
