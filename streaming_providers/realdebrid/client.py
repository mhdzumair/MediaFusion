from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from typing import Any

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException

REALDEBRID_ERROR_CODE_MAP: dict[int, tuple[str, str]] = {
    -1: ("Real-Debrid internal error", "debrid_service_down_error.mp4"),
    1: ("Real-Debrid missing parameter", "api_error.mp4"),
    2: ("Real-Debrid bad parameter value", "transfer_error.mp4"),
    3: ("Real-Debrid unknown method", "api_error.mp4"),
    4: ("Real-Debrid method not allowed", "api_error.mp4"),
    5: ("Real-Debrid slow down", "too_many_requests.mp4"),
    6: ("Real-Debrid resource unreachable", "debrid_service_down_error.mp4"),
    7: ("Real-Debrid resource not found", "torrent_not_downloaded.mp4"),
    8: ("Real-Debrid bad token", "invalid_token.mp4"),
    9: ("Real-Debrid permission denied", "invalid_token.mp4"),
    10: ("Real-Debrid two-factor authentication needed", "invalid_token.mp4"),
    11: ("Real-Debrid two-factor authentication pending", "invalid_token.mp4"),
    12: ("Real-Debrid invalid login", "invalid_token.mp4"),
    13: ("Real-Debrid invalid password", "invalid_token.mp4"),
    14: ("Real-Debrid account locked", "invalid_token.mp4"),
    15: ("Real-Debrid account not activated", "invalid_token.mp4"),
    16: ("Real-Debrid unsupported hoster", "transfer_error.mp4"),
    17: ("Real-Debrid hoster in maintenance", "debrid_service_down_error.mp4"),
    18: ("Real-Debrid hoster limit reached", "exceed_remote_traffic_limit.mp4"),
    19: ("Real-Debrid hoster temporarily unavailable", "debrid_service_down_error.mp4"),
    20: ("Real-Debrid hoster not available for free users", "need_premium.mp4"),
    21: ("Real-Debrid too many active downloads", "torrent_limit.mp4"),
    22: ("Real-Debrid IP address not allowed", "ip_not_allowed.mp4"),
    23: ("Real-Debrid traffic exhausted", "exceed_remote_traffic_limit.mp4"),
    24: ("Real-Debrid file unavailable", "torrent_not_downloaded.mp4"),
    25: ("Real-Debrid service unavailable", "debrid_service_down_error.mp4"),
    26: ("Real-Debrid upload too big", "transfer_error.mp4"),
    27: ("Real-Debrid upload error", "transfer_error.mp4"),
    28: ("Real-Debrid file not allowed", "transfer_error.mp4"),
    29: ("Real-Debrid torrent too big", "transfer_error.mp4"),
    30: ("Real-Debrid torrent file invalid", "transfer_error.mp4"),
    31: ("Real-Debrid action already done", "transfer_error.mp4"),
    32: ("Real-Debrid image resolution error", "api_error.mp4"),
    33: ("Real-Debrid torrent already active", "torrent_not_downloaded.mp4"),
    34: ("Real-Debrid too many requests", "too_many_requests.mp4"),
    35: ("Real-Debrid infringing file", "content_infringing.mp4"),
    36: ("Real-Debrid fair usage limit reached", "exceed_remote_traffic_limit.mp4"),
    37: ("Real-Debrid disabled endpoint", "api_error.mp4"),
}


class RealDebrid(DebridClient):
    BASE_URL = "https://api.real-debrid.com/rest/1.0"
    OAUTH_URL = "https://api.real-debrid.com/oauth/v2"
    OPENSOURCE_CLIENT_ID = "X245A4XAIBGVM"

    def __init__(self, token: str | None = None, user_ip: str | None = None):
        self.user_ip = user_ip
        super().__init__(token)

    @staticmethod
    def _raise_mapped_error(error_data: dict):
        error_code = error_data.get("error_code")
        if error_code is None:
            return
        try:
            error_code = int(error_code)
        except (TypeError, ValueError):
            return

        mapped_error = REALDEBRID_ERROR_CODE_MAP.get(error_code)
        if not mapped_error:
            return

        message, video_file_name = mapped_error
        error_detail = error_data.get("error_details") or error_data.get("error")
        if isinstance(error_detail, str):
            stripped_detail = error_detail.strip()
            if stripped_detail and stripped_detail.lower() not in message.lower():
                message = f"{message}: {stripped_detail}"
        raise ProviderException(message, video_file_name)

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        self._raise_mapped_error(error_data)

    async def _make_request(
        self,
        method: str,
        url: str,
        data: dict | bytes | None = None,
        **kwargs,
    ) -> dict:
        if method in ["POST", "PUT"] and self.user_ip:
            data = data or {}
            if isinstance(data, dict):
                data["ip"] = self.user_ip
        return await super()._make_request(method=method, url=url, data=data, **kwargs)

    async def initialize_headers(self):
        if self.token:
            token_data = self.decode_token_str(self.token)
            if "private_token" in token_data:
                self.headers = {"Authorization": f"Bearer {token_data['private_token']}"}
                self.is_private_token = True
            else:
                access_token_data = await self.get_token(
                    token_data["client_id"],
                    token_data["client_secret"],
                    token_data["code"],
                )
                self.headers = {"Authorization": f"Bearer {access_token_data['access_token']}"}

    @staticmethod
    def encode_token_data(code: str, client_id: str = None, client_secret: str = None, *args, **kwargs):
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

        token_data = await self.get_token(response_data["client_id"], response_data["client_secret"], device_code)

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
        return await self._make_request("POST", f"{self.BASE_URL}/torrents/addMagnet", data={"magnet": magnet_link})

    async def add_torrent_file(self, torrent_file: bytes):
        return await self._make_request(
            "PUT",
            f"{self.BASE_URL}/torrents/addTorrent",
            data=torrent_file,
        )

    async def get_active_torrents(self):
        return await self._make_request("GET", f"{self.BASE_URL}/torrents/activeCount")

    async def get_user_torrent_list(self, page: int | None = None, limit: int | None = None):
        params = {}
        if page is not None:
            params["page"] = page
        if limit is not None:
            params["limit"] = limit
        return await self._make_request("GET", f"{self.BASE_URL}/torrents", params=params or None)

    async def get_user_downloads(self):
        return await self._make_request("GET", f"{self.BASE_URL}/downloads")

    async def get_torrent_info(self, torrent_id):
        return await self._make_request("GET", f"{self.BASE_URL}/torrents/info/{torrent_id}")

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

    async def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
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
        if isinstance(response, dict) and "download" in response:
            return response

        if isinstance(response, dict):
            self._raise_mapped_error(response)

        raise ProviderException(f"Failed to create download link. response: {response}", "api_error.mp4")

    async def delete_torrent(self, torrent_id) -> dict:
        return await self._make_request(
            "DELETE",
            f"{self.BASE_URL}/torrents/delete/{torrent_id}",
            is_return_none=True,
        )

    async def get_user_info(self) -> dict:
        return await self._make_request("GET", f"{self.BASE_URL}/user")
