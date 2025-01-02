from typing import Optional

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class EasyDebrid(DebridClient):
    BASE_URL = "https://easydebrid.com/api/v1"

    def __init__(self, token: Optional[str] = None, user_ip: Optional[str] = None):
        self.user_ip = user_ip
        super().__init__(token)

    async def initialize_headers(self):
        self.headers = {"Authorization": f"Bearer {self.token}"}
        if self.user_ip:
            self.headers['X-Forwarded-For'] = self.user_ip

    async def disable_access_token(self):
        pass

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        error_code = error_data.get("error")
        match error_code:
            case "Unsupported link for direct download.":
                raise ProviderException(
                    "Unsupported link for direct download.",
                    "unsupported_direct_download.mp4",
                )

    async def _make_request(
        self,
        method: str,
        url: str,
        data: Optional[dict | str] = None,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        is_return_none: bool = False,
        is_expected_to_fail: bool = False,
        retry_count: int = 0,
    ) -> dict:
        params = params or {}
        url = self.BASE_URL + url
        return await super()._make_request(
            method, url, data, json, params, is_return_none, is_expected_to_fail
        )

    async def get_torrent_instant_availability(self, urls: list[str]):
        response = await self._make_request(
            "POST",
            "/link/lookup",
            json={"urls": urls},
        )
        return response.get("cached", [])

    async def create_download_link(self, magnet):
        response = await self._make_request(
            "POST",
            "/link/generate",
            json={"url": magnet},
        )
        return response

    async def get_torrent_info(self, torrent_id: str) -> dict:
        pass

    async def get_user_info(self):
        return await self._make_request("GET", "/user/details")
