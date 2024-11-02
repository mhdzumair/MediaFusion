from typing import Any, Optional

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class AllDebrid(DebridClient):
    BASE_URL = "https://api.alldebrid.com/v4"
    AGENT = "mediafusion"

    def __init__(self, token: str, user_ip: Optional[str] = None):
        self.user_ip = user_ip
        super().__init__(token)

    async def initialize_headers(self):
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def disable_access_token(self):
        pass

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
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
    ) -> dict:
        params = params or {}
        params["agent"] = self.AGENT
        if self.user_ip:
            params["ip"] = self.user_ip
        url = self.BASE_URL + url
        return await super()._make_request(
            method, url, data, json, params, is_return_none, is_expected_to_fail
        )

    @staticmethod
    def _validate_error_response(response_data):
        if response_data.get("status") != "success":
            error_code = response_data.get("error", {}).get("code")
            match error_code:
                case "AUTH_BAD_APIKEY":
                    raise ProviderException(
                        "Invalid AllDebrid API key", "invalid_token.mp4"
                    )
                case "NO_SERVER":
                    raise ProviderException(
                        f"Failed to add magnet link to AllDebrid {response_data}",
                        "transfer_error.mp4",
                    )
                case "AUTH_BLOCKED":
                    raise ProviderException(
                        "API got blocked on AllDebrid", "alldebrid_api_blocked.mp4"
                    )
                case "MAGNET_MUST_BE_PREMIUM":
                    raise ProviderException(
                        "Torrent must be premium on AllDebrid", "need_premium.mp4"
                    )
                case "MAGNET_TOO_MANY_ACTIVE":
                    raise ProviderException(
                        "Too many active torrents on AllDebrid", "torrent_limit.mp4"
                    )
                case _:
                    raise ProviderException(
                        f"Failed to add magnet link to AllDebrid {response_data}",
                        "transfer_error.mp4",
                    )

    async def add_magnet_link(self, magnet_link):
        response_data = await self._make_request(
            "POST", "/magnet/upload", data={"magnets[]": magnet_link}
        )
        self._validate_error_response(response_data)
        return response_data

    async def get_user_torrent_list(self):
        return await self._make_request("GET", "/magnet/status")

    async def get_torrent_info(self, magnet_id):
        response = await self._make_request(
            "GET", "/magnet/status", params={"id": magnet_id}
        )
        return response.get("data", {}).get("magnets")

    async def get_torrent_instant_availability(self, magnet_links: list[str]):
        response = await self._make_request(
            "POST", "/magnet/instant", data={"magnets[]": magnet_links}
        )
        return response.get("data", {}).get("magnets", [])

    async def get_available_torrent(self, info_hash) -> Optional[dict[str, Any]]:
        available_torrents = await self.get_user_torrent_list()
        self._validate_error_response(available_torrents)
        if not available_torrents.get("data"):
            return None
        for torrent in available_torrents["data"]["magnets"]:
            if torrent["hash"] == info_hash:
                return torrent
        return None

    async def create_download_link(self, link):
        response = await self._make_request(
            "GET",
            "/link/unlock",
            params={"link": link},
            is_expected_to_fail=True,
        )
        if response.get("status") == "success":
            return response
        raise ProviderException(
            f"Failed to create download link from AllDebrid {response}",
            "transfer_error.mp4",
        )

    async def delete_torrent(self, magnet_id):
        return await self._make_request(
            "GET", "/magnet/delete", params={"id": magnet_id}
        )

    async def get_user_info(self):
        return await self._make_request("GET", "/user")
