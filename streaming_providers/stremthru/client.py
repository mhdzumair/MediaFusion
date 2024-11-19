from typing import Any, Optional

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class StremThruError(Exception):
    def __init__(self, error: dict[str, Any]):
        self.type = error.get("type", "")
        self.code = error.get("code", "")
        self.message = error.get("message", "")
        self.store_name = error.get("store_name", "")


class StremThru(DebridClient):
    AGENT = "mediafusion"

    def __init__(self, url: str, token: str, **kwargs):
        self.BASE_URL = url
        super().__init__(token)

    async def initialize_headers(self):
        self.headers = {
            "Proxy-Authorization": f"Basic {self.token}",
            "User-Agent": self.AGENT,
        }

    def __del__(self):
        pass

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        pass

    async def disable_access_token(self):
        pass

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
    ) -> dict[str, Any]:
        params = params or {}
        url = self.BASE_URL + url
        response = await super()._make_request(
            method,
            url,
            data,
            json,
            params,
            is_return_none,
            is_expected_to_fail,
            retry_count,
        )
        if response.get("error"):
            if is_expected_to_fail:
                return response
            error_message = response.get("error", "unknown error")
            raise ProviderException(error_message, "api_error.mp4")
        return response.get("data")

    @staticmethod
    def _validate_error_response(response_data: dict):
        if response_data.get("error", None):
            raise ProviderException(
                f"Failed request to StremThru {response_data['error']}",
                "transfer_error.mp4",
            )

    async def add_magnet_link(self, magnet_link):
        response_data = await self._make_request(
            "POST", "/v0/store/magnets", data={"magnet": magnet_link}
        )
        self._validate_error_response(response_data)
        return response_data["data"]

    async def get_user_torrent_list(self):
        return await self._make_request("GET", "/v0/store/magnets")

    async def get_torrent_info(self, torrent_id):
        response = await self._make_request("GET", "/v0/store/magnets/" + torrent_id)
        return response.get("data", {})

    async def get_torrent_instant_availability(self, magnet_links: list[str]):
        return await self._make_request(
            "GET", "/v0/store/magnets/check", params={"magnet": ",".join(magnet_links)}
        )

    async def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
        available_torrents = await self.get_user_torrent_list()
        self._validate_error_response(available_torrents)
        if not available_torrents.get("data"):
            return None
        for torrent in available_torrents["data"]["items"]:
            if torrent["hash"] == info_hash:
                return torrent

    async def create_download_link(self, link):
        response = await self._make_request(
            "POST",
            "/v0/store/link/generate",
            data={"link": link},
            is_expected_to_fail=True,
        )
        if response.get("data"):
            return response["data"]
        error_message = response.get("error", "unknown error")
        raise ProviderException(
            f"Failed to create download link from StremThru {error_message}",
            "transfer_error.mp4",
        )

    async def delete_torrent(self, magnet_id):
        return await self._make_request(
            "DELETE",
            "/v0/store/magnets/" + magnet_id,
        )

    async def get_user_info(self):
        return await self._make_request("GET", "/v0/store/user")
