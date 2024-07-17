import json

from typing import Any

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class Torbox(DebridClient):
    BASE_URL = "https://api.torbox.app/v1/api"

    def initialize_headers(self):
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def __del__(self):
        pass

    def _handle_service_specific_errors(self, error):
        pass

    def _make_request(
            self,
            method: str,
            url: str,
            data=None,
            params=None,
            is_return_none=False,
            is_expected_to_fail=False,
    ) -> dict:
        params = params or {}
        url = self.BASE_URL + url
        return super()._make_request(
            method, url, data, params, is_return_none, is_expected_to_fail
        )

    def add_magnet_link(self, magnet_link):
        response_data = self._make_request(
            "POST",
            "/torrents/createtorrent",
            data={"magnet": magnet_link},
            is_expected_to_fail=True
        )

        if response_data.get("detail") is False:
            raise ProviderException(
                f"Failed to add magnet link to Torbox {response_data}",
                "transfer_error.mp4",
            )
        return response_data

    def get_user_torrent_list(self):
        return self._make_request("GET", "/torrents/mylist", params={"bypass_cache": "true"})

    def get_torrent_info(self, magnet_id):
        response = self.get_user_torrent_list()
        torrent_list = response.get("data", [])
        for torrent in torrent_list:
            if torrent.get("magnet", "") == magnet_id:
                return torrent
        return {}

    def get_torrent_instant_availability(self, torrent_hashes: list[str]):
        response = self._make_request(
            "GET", "/torrents/checkcached", params={"hash": torrent_hashes, "format": "object"}
        )
        return response.get("data", {})

    def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
        response = self.get_user_torrent_list()
        torrent_list = response.get("data", [])
        for torrent in torrent_list:
            if torrent.get("hash", "") == info_hash:
                return torrent
        return {}

    def create_download_link(self, torrent_id, filename):
        response = self._make_request(
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

    def delete_torrent(self, torrent_id):
        return self._make_request(
            "POST",
            "/torrents/controltorrent",
            data=json.dumps({"torrent_id": torrent_id, "operation": "delete"})
        )

