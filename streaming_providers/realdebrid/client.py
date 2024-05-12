from base64 import b64encode, b64decode
from typing import Any

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class RealDebrid(DebridClient):
    BASE_URL = "https://api.real-debrid.com/rest/1.0"
    OAUTH_URL = "https://api.real-debrid.com/oauth/v2"
    OPENSOURCE_CLIENT_ID = "X245A4XAIBGVM"

    def __init__(self, token: str | None = None, user_ip: str | None = None):
        self.user_ip = user_ip
        super().__init__(token)

    def _handle_service_specific_errors(self, error):
        if (
            error.response.status_code == 403
            and error.response.json().get("error_code") == 9
        ):
            raise ProviderException(
                "Real-Debrid Permission denied for free account", "need_premium.mp4"
            )

    def _make_request(
        self,
        method: str,
        url: str,
        data=None,
        params=None,
        is_return_none=False,
        is_expected_to_fail=False,
    ) -> dict:
        if method == "POST" and self.user_ip:
            data = data or {}
            data["ip"] = self.user_ip
        return super()._make_request(
            method, url, data, params, is_return_none, is_expected_to_fail
        )

    def initialize_headers(self):
        if self.token:
            token_data = self.decode_token_str(self.token)
            access_token_data = self.get_token(
                token_data["client_id"], token_data["client_secret"], token_data["code"]
            )
            self.headers = {
                "Authorization": f"Bearer {access_token_data['access_token']}"
            }

    @staticmethod
    def encode_token_data(client_id: str, client_secret: str, code: str):
        token = f"{client_id}:{client_secret}:{code}"
        return b64encode(str(token).encode()).decode()

    @staticmethod
    def decode_token_str(token: str) -> dict[str, str]:
        try:
            client_id, client_secret, code = b64decode(token).decode().split(":")
        except ValueError:
            raise ProviderException("Invalid token", "invalid_token.mp4")
        return {"client_id": client_id, "client_secret": client_secret, "code": code}

    def get_device_code(self):
        return self._make_request(
            "GET",
            f"{self.OAUTH_URL}/device/code",
            params={"client_id": self.OPENSOURCE_CLIENT_ID, "new_credentials": "yes"},
        )

    def get_token(self, client_id, client_secret, device_code):
        return self._make_request(
            "POST",
            f"{self.OAUTH_URL}/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": device_code,
                "grant_type": "http://oauth.net/grant_type/device/1.0",
            },
        )

    def authorize(self, device_code):
        response_data = self._make_request(
            "GET",
            f"{self.OAUTH_URL}/device/credentials",
            params={"client_id": self.OPENSOURCE_CLIENT_ID, "code": device_code},
            is_expected_to_fail=True,
        )

        if "client_secret" not in response_data:
            return response_data

        token_data = self.get_token(
            response_data["client_id"], response_data["client_secret"], device_code
        )

        if "access_token" in token_data:
            token = self.encode_token_data(
                response_data["client_id"],
                response_data["client_secret"],
                token_data["refresh_token"],
            )
            return {"token": token}
        else:
            return token_data

    def add_magent_link(self, magnet_link):
        return self._make_request(
            "POST", f"{self.BASE_URL}/torrents/addMagnet", data={"magnet": magnet_link}
        )

    def get_active_torrents(self):
        return self._make_request("GET", f"{self.BASE_URL}/torrents/activeCount")

    def get_user_torrent_list(self):
        return self._make_request("GET", f"{self.BASE_URL}/torrents")

    def get_user_downloads(self):
        return self._make_request("GET", f"{self.BASE_URL}/downloads")

    def get_torrent_info(self, torrent_id):
        return self._make_request("GET", f"{self.BASE_URL}/torrents/info/{torrent_id}")

    def get_torrent_instant_availability(self, torrent_hashes: list[str]):
        return self._make_request(
            "GET",
            f"{self.BASE_URL}/torrents/instantAvailability/{'/'.join(torrent_hashes)}",
        )

    def disable_access_token(self):
        return self._make_request(
            "GET",
            f"{self.BASE_URL}/disable_access_token",
            is_return_none=True,
            is_expected_to_fail=True,
        )

    def start_torrent_download(self, torrent_id, file_ids="all"):
        return self._make_request(
            "POST",
            f"{self.BASE_URL}/torrents/selectFiles/{torrent_id}",
            data={"files": file_ids},
            is_return_none=True,
        )

    def get_available_torrent(self, info_hash) -> dict[str, Any] | None:
        available_torrents = self.get_user_torrent_list()
        for torrent in available_torrents:
            if torrent["hash"] == info_hash:
                return torrent

    def create_download_link(self, link):
        response = self._make_request(
            "POST",
            f"{self.BASE_URL}/unrestrict/link",
            data={"link": link},
            is_expected_to_fail=True,
        )
        if "download" in response:
            return response

        if "error_code" in response:
            if response["error_code"] == 23:
                raise ProviderException(
                    "Exceed remote traffic limit", "exceed_remote_traffic_limit.mp4"
                )
        raise ProviderException(
            f"Failed to create download link. response: {response}", "api_error.mp4"
        )

    def delete_torrent(self, torrent_id):
        return self._make_request(
            "DELETE",
            f"{self.BASE_URL}/torrents/delete/{torrent_id}",
            is_return_none=True,
        )
