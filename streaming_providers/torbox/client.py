from typing import Any, Optional

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class Torbox(DebridClient):
    BASE_URL = "https://api.torbox.app/v1/api"

    async def initialize_headers(self):
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def disable_access_token(self):
        pass

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        pass

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

    async def add_magnet_link(self, magnet_link):
        response_data = await self._make_request(
            "POST",
            "/torrents/createtorrent",
            data={"magnet": magnet_link},
            is_expected_to_fail=True,
        )

        if response_data.get("detail") is False:
            raise ProviderException(
                f"Failed to add magnet link to Torbox {response_data}",
                "transfer_error.mp4",
            )
        return response_data

    async def get_user_torrent_list(self):
        return await self._make_request(
            "GET", "/torrents/mylist", params={"bypass_cache": "true"}
        )

    async def get_torrent_info(self, magnet_id):
        response = await self.get_user_torrent_list()
        torrent_list = response.get("data", [])
        for torrent in torrent_list:
            if torrent.get("magnet", "") == magnet_id:
                return torrent
        return {}

    async def get_torrent_instant_availability(self, torrent_hashes: list[str]):
        response = await self._make_request(
            "GET",
            "/torrents/checkcached",
            params={"hash": torrent_hashes, "format": "object"},
        )
        return response.get("data", [])

    async def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
        response = await self.get_user_torrent_list()
        torrent_list = response.get("data", [])
        for torrent in torrent_list:
            if torrent.get("hash", "") == info_hash:
                return torrent
        return {}

    async def create_download_link(self, torrent_id, filename):
        response = await self._make_request(
            "GET",
            "/torrents/requestdl",
            params={"token": self.token, "torrent_id": torrent_id, "file_id": filename},
            is_expected_to_fail=True,
        )
        if "successfully" in response.get("detail"):
            return response
        raise ProviderException(
            f"Failed to create download link from Torbox {response}",
            "transfer_error.mp4",
        )

    async def delete_torrent(self, torrent_id):
        return await self._make_request(
            "POST",
            "/torrents/controltorrent",
            json={"torrent_id": torrent_id, "operation": "delete"},
        )
