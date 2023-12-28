from typing import Any

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


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
        response_data = self._make_request(
            "POST", "/cloud", data={"url": magnet_link}
        )

        if response_data is None:
            raise ProviderException(
                f"Failed to add magnet link to OffCloud {response_data}",
                "transfer_error.mp4",
            )
        return response_data

    def get_user_torrent_list(self):
        return self._make_request("GET", "/cloud/history")

    def get_torrent_info(self, magnet_id):
        response = self._make_request("POST", "/cloud/status", data={"requestIds": [magnet_id]})
        return response.get("requests", {})[0]

    def get_torrent_instant_availability(self, magnet_links: list[str]):
        response = self._make_request(
            "POST", "/cache", data={"hashes": magnet_links}
        )
        return response.get("cachedItems", {})

    def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
        available_torrents = self.get_user_torrent_list()
        for torrent in available_torrents:
            if info_hash in torrent["originalLink"]:
                return torrent

    def create_download_link(self, link):
        response = self._make_request(
            "GET",
            f"/cloud/explore/{link}",
            is_expected_to_fail=True,
        )
        # Offcloud returns bad archive when the file is a mkv file, it's annoying since it does not
        # return the URL for download and has to be constructed manually in code.
        # Furthermore, the response is a dict with this response, but a list with the URLs, so it's a
        # special case handling here.
        if type(response) is dict and response.get("error") == "Bad archive":
            return "Bad archive"
        return response

    def delete_torrent(self, magnet_id):
        raise NotImplementedError
