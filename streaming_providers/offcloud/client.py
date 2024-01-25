from typing import Any

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException
from utils.validation_helper import is_video_file


class OffCloud(DebridClient):
    BASE_URL = "https://offcloud.com/api"

    def initialize_headers(self):
        pass

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
        params["key"] = self.token
        url = self.BASE_URL + url
        return super()._make_request(
            method, url, data, params, is_return_none, is_expected_to_fail
        )

    def add_magent_link(self, magnet_link):
        response_data = self._make_request("POST", "/cloud", data={"url": magnet_link})

        if "requestId" not in response_data:
            if "not_available" in response_data:
                raise ProviderException(
                    "Need premium OffCloud account to add this torrent",
                    "need_premium.mp4",
                )
            raise ProviderException(
                f"Failed to add magnet link to OffCloud {response_data}",
                "transfer_error.mp4",
            )
        return response_data

    def get_user_torrent_list(self):
        return self._make_request("GET", "/cloud/history")

    def get_torrent_info(self, request_id):
        response = self._make_request(
            "POST", "/cloud/status", data={"requestIds": [request_id]}
        )
        return response.get("requests")[0] if response.get("requests") else {}

    def get_torrent_instant_availability(self, magnet_links: list[str]):
        response = self._make_request("POST", "/cache", data={"hashes": magnet_links})
        return response.get("cachedItems", {})

    def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
        available_torrents = self.get_user_torrent_list()
        for torrent in available_torrents:
            if info_hash in torrent["originalLink"]:
                return torrent

    def explore_folder_links(self, request_id):
        return self._make_request("GET", f"/cloud/explore/{request_id}")

    def create_download_link(self, request_id, torrent_info, filename):
        if torrent_info["isDirectory"] is False:
            return f"https://{torrent_info.get('server')}.offcloud.com/cloud/download/{request_id}/{torrent_info.get('fileName')}"

        response = self.explore_folder_links(request_id)
        for link in response:
            if filename is None:
                if is_video_file(link):
                    return link
            if filename is not None and filename in link:
                return link
        raise ProviderException(
            "No matching file available for this torrent", "api_error.mp4"
        )

    def delete_torrent(self, magnet_id):
        raise NotImplementedError
