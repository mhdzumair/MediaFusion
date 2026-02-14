from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class Debrider(DebridClient):
    BASE_URL = "https://debrider.app/api/v1"

    def __init__(self, token: str | None = None, user_ip: str | None = None):
        self.user_ip = user_ip
        super().__init__(token)

    async def initialize_headers(self):
        self.headers = {"Authorization": f"Bearer {self.token}"}
        if self.user_ip:
            self.headers["X-Forwarded-For"] = self.user_ip

    async def disable_access_token(self):
        pass

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        pass

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _make_request(
        self,
        method: str,
        url: str,
        data: dict | str | None = None,
        json: dict | None = None,
        params: dict | None = None,
        is_return_none: bool = False,
        is_expected_to_fail: bool = False,
        is_http_response: bool = False,
        retry_count: int = 0,
    ) -> dict:
        params = params or {}
        url = self.BASE_URL + url
        return await super()._make_request(method, url, data, json, params, is_return_none, is_expected_to_fail)

    async def get_torrent_instant_availability(self, urls: list[str]):
        response = await self._make_request(
            "POST",
            "/link/lookup",
            json={"data": urls},
        )
        return response.get("result", [])

    async def create_download_link(self, magnet):
        # Create download link call also adds the magnet if it's not cached
        response = await self._make_request("POST", "/link/generate", json={"data": magnet})
        return response

    async def add_torrent_file(self, magnet):
        response = await self._make_request(
            "POST",
            "/tasks",
            json={"type": "magnet", "data": magnet},
        )
        # Returns "message": "Task added successfully.", if added
        if not response.get("message", "") or "task added successfully" not in response.get("message").lower():
            raise ProviderException(
                f"Failed to add magnet link to Debrider {response}",
                "transfer_error.mp4",
            )
        return response

    async def get_torrent_info(self, torrent_id: str) -> dict:
        pass

    async def get_user_info(self):
        return await self._make_request("GET", "/account")

    # =========================================================================
    # Usenet/NZB API Methods
    # =========================================================================

    async def add_usenet_download(self, nzb_url: str) -> dict:
        """Add an NZB download to Debrider via URL.

        Args:
            nzb_url: URL to the NZB file

        Returns:
            Response data with download info
        """
        response = await self._make_request(
            "POST",
            "/tasks",
            json={"type": "nzb", "data": nzb_url},
        )
        if not response.get("message", "") or "task added successfully" not in response.get("message", "").lower():
            raise ProviderException(
                f"Failed to add NZB to Debrider: {response}",
                "transfer_error.mp4",
            )
        return response

    async def get_usenet_instant_availability(self, nzb_urls: list[str]) -> list[dict]:
        """Check instant availability for NZB URLs.

        Args:
            nzb_urls: List of NZB URLs to check

        Returns:
            List of availability data
        """
        response = await self._make_request(
            "POST",
            "/link/lookup",
            json={"data": nzb_urls},
        )
        return response.get("result", [])

    async def create_usenet_download_link(self, nzb_url: str) -> dict:
        """Create a download link for NZB content.

        Args:
            nzb_url: URL to the NZB file

        Returns:
            Response with download links for files
        """
        response = await self._make_request(
            "POST",
            "/link/generate",
            json={"data": nzb_url},
        )
        return response
