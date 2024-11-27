from typing import Any, Optional

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class EasyDebrid(DebridClient):
    BASE_URL = "https://easydebrid.com/api/v1"

    async def initialize_headers(self):
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def disable_access_token(self):
        pass

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        error_code = error_data.get("error")
        match error_code:
            case "BAD_TOKEN" | "AUTH_ERROR" | "OAUTH_VERIFICATION_ERROR":
                raise ProviderException(
                    "Invalid EasyDebrid token",
                    "invalid_token.mp4",
                )
            case "DOWNLOAD_TOO_LARGE":
                raise ProviderException(
                    "Download size too large for the user plan",
                    "not_enough_space.mp4",
                )
            case "ACTIVE_LIMIT" | "MONTHLY_LIMIT":
                raise ProviderException(
                    "Download limit exceeded",
                    "daily_download_limit.mp4",
                )
            case "DOWNLOAD_SERVER_ERROR" | "DATABASE_ERROR":
                raise ProviderException(
                    "EasyDebrid server error",
                    "debrid_service_down_error.mp4",
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

    async def create_download_link(self, magnet, filename):
        response = await self._make_request(
            "POST",
            "/link/generate",
            json={"url": magnet},
        )
        return response.get("files", [])


    async def delete_torrent(self, torrent_id):
        pass

    async def get_torrent_info(self, torrent_id: str) -> dict:
        pass

    async def get_user_info(self):
        return await self._make_request(
            "GET", "/user/details"
        )
