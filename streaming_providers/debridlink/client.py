import PTN
import traceback

from requests import RequestException, JSONDecodeError
from typing import Any

import requests
from base64 import b64encode, b64decode

from streaming_providers.exceptions import ProviderException


class DebridLink:
    BASE_URL = "https://debrid-link.com/api/v2"
    OAUTH_URL = "https://debrid-link.com/api/oauth"
    OPENSOURCE_CLIENT_ID = "RyrV22FOg30DsxjYPziRKA"

    def __init__(self, encoded_token=None):
        self.encoded_token = encoded_token
        self.headers = {}
        self.initialize_headers()

    def __del__(self):
        if self.encoded_token:
            self.disable_access_token()

    def _make_request(
        self,
        method: str,
        url: str,
        data=None,
        params=None,
        is_return_none=False,
        is_expected_to_fail=False,
    ) -> dict:
        if method == "GET":
            response = requests.get(url, params=params, headers=self.headers)
        elif method == "POST":
            response = requests.post(url, data=data, headers=self.headers)
        elif method == "DELETE":
            response = requests.delete(url, headers=self.headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        try:
            response.raise_for_status()
        except RequestException as error:
            if is_expected_to_fail:
                pass
            elif error.response.status_code == 401:
                raise ProviderException("Invalid token", "invalid_token.mp4")
            elif (
                error.response.status_code == 400
                and response.json().get("error") == "freeServerOverload"
            ):
                raise ProviderException(
                    "Debrid-Link free servers are overloaded", "need_premium.mp4"
                )
            else:
                formatted_traceback = "".join(traceback.format_exception(error))
                raise ProviderException(
                    f"status code: {error.response.status_code}, data: {error.response.content}, trace log:\n {formatted_traceback}",
                    "api_error.mp4",
                )

        if is_return_none:
            return {}
        try:
            return response.json()
        except JSONDecodeError as error:
            formatted_traceback = "".join(traceback.format_exception(error))
            raise ProviderException(
                f"Failed to parse response. content: {response.text}, trace log:\n {formatted_traceback}",
                "api_error.mp4",
            )

    def initialize_headers(self):
        if self.encoded_token:
            token_data = self.decode_token_str(self.encoded_token)
            access_token_data = self.refresh_token(
                token_data["client_id"], token_data["code"]
            )
            self.headers = {
                "Authorization": f"Bearer {access_token_data['access_token']}"
            }

    @staticmethod
    def encode_token_data(client_id: str, code: str):
        token = f"{client_id}:{code}"
        return b64encode(str(token).encode()).decode()

    @staticmethod
    def decode_token_str(token: str) -> dict[str, str]:
        try:
            client_id, code = b64decode(token).decode().split(":")
        except ValueError:
            raise ProviderException("Invalid token", "invalid_token.mp4")
        return {"client_id": client_id, "code": code}

    def get_device_code(self):
        return self._make_request(
            "POST",
            f"{self.OAUTH_URL}/device/code",
            data={
                "client_id": self.OPENSOURCE_CLIENT_ID,
                "scope": "get.post.downloader get.post.seedbox get.account get.files get.post.stream",
            },
        )

    def get_token(self, client_id, device_code):
        return self._make_request(
            "POST",
            f"{self.OAUTH_URL}/token",
            data={
                "client_id": client_id,
                "code": device_code,
                "grant_type": "http://oauth.net/grant_type/device/1.0",
            },
            is_expected_to_fail=True,
        )

    def refresh_token(self, client_id, refresh_token):
        return self._make_request(
            "POST",
            f"{self.OAUTH_URL}/token",
            data={
                "client_id": client_id,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    def authorize(self, device_code):
        token_data = self.get_token(self.OPENSOURCE_CLIENT_ID, device_code)

        if "error" in token_data:
            return token_data

        if "access_token" in token_data:
            token = self.encode_token_data(
                self.OPENSOURCE_CLIENT_ID, token_data["refresh_token"]
            )
            return {"token": token}
        else:
            return token_data

    def add_magent_link(self, magnet_link):
        return self._make_request(
            "POST", f"{self.BASE_URL}/seedbox/add", data={"url": magnet_link}
        )

    def get_user_torrent_list(self):
        return self._make_request("GET", f"{self.BASE_URL}/seedbox/list")

    def get_torrent_info(self, torrent_id):
        return self._make_request(
            "GET", f"{self.BASE_URL}/seedbox/list", data={"url": torrent_id}
        )

    def get_torrent_files_list(self, torrent_id):
        return self._make_request("GET", f"{self.BASE_URL}/files/{torrent_id}/list")

    def get_torrent_instant_availability(self, torrent_hash):
        return self._make_request(
            "GET", f"{self.BASE_URL}/seedbox/cached/", data={"url": torrent_hash}
        )

    def disable_access_token(self):
        return self._make_request(
            "GET", f"{self.OAUTH_URL}/revoke", is_return_none=True
        )

    def get_available_torrent(self, info_hash: str) -> dict[str, Any] | None:
        torrent_list_response = self.get_user_torrent_list()
        if "error" in torrent_list_response:
            raise ProviderException(
                "Failed to get torrent info from Debrid-Link", "transfer_error.mp4"
            )

        available_torrents = torrent_list_response["value"]
        for torrent in available_torrents:
            if torrent["hashString"] == info_hash:
                return torrent
