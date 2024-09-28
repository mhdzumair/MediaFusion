from typing import Any

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class AllDebrid(DebridClient):
    BASE_URL = "https://api.alldebrid.com/v4"
    AGENT = "mediafusion"

    def __init__(self, token: str, user_ip: str | None = None):
        self.user_ip = user_ip
        super().__init__(token)

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
        params["agent"] = self.AGENT
        if self.user_ip:
            params["ip"] = self.user_ip
        url = self.BASE_URL + url
        return super()._make_request(
            method, url, data, params, is_return_none, is_expected_to_fail
        )

    @staticmethod
    def _validate_error_response(response_data):
        if response_data.get("status") != "success":
            error_code = response_data.get("error", {}).get("code")
            match error_code:
                case "AUTH_BAD_APIKEY":
                    raise ProviderException(
                        f"Invalid AllDebrid API key", "invalid_token.mp4"
                    )
                case "NO_SERVER":
                    raise ProviderException(
                        f"Failed to add magnet link to AllDebrid {response_data}",
                        "transfer_error.mp4",
                    )
                case "AUTH_BLOCKED":
                    raise ProviderException(
                        f"API got blocked on AllDebrid", "alldebrid_api_blocked.mp4"
                    )
                case "MAGNET_MUST_BE_PREMIUM":
                    raise ProviderException(
                        f"Torrent must be premium on AllDebrid", "need_premium.mp4"
                    )
                case "MAGNET_TOO_MANY_ACTIVE":
                    raise ProviderException(
                        f"Too many active torrents on AllDebrid", "torrent_limit.mp4"
                    )
                case _:
                    raise ProviderException(
                        f"Failed to add magnet link to AllDebrid {response_data}",
                        "transfer_error.mp4",
                    )

    def add_magnet_link(self, magnet_link):
        response_data = self._make_request(
            "POST", f"/magnet/upload", data={"magnets[]": magnet_link}
        )
        self._validate_error_response(response_data)
        return response_data

    def get_user_torrent_list(self):
        return self._make_request("GET", "/magnet/status")

    def get_torrent_info(self, magnet_id):
        response = self._make_request("GET", "/magnet/status", params={"id": magnet_id})
        return response.get("data", {}).get("magnets")

    def get_torrent_instant_availability(self, magnet_links: list[str]):
        response = self._make_request(
            "POST", "/magnet/instant", data={"magnets[]": magnet_links}
        )
        return response.get("data", {}).get("magnets", [])

    def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
        available_torrents = self.get_user_torrent_list()
        self._validate_error_response(available_torrents)
        if not available_torrents.get("data"):
            return None
        for torrent in available_torrents["data"]["magnets"]:
            if torrent["hash"] == info_hash:
                return torrent

    def create_download_link(self, link):
        response = self._make_request(
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

    def delete_torrent(self, magnet_id):
        return self._make_request("GET", "/magnet/delete", params={"id": magnet_id})
