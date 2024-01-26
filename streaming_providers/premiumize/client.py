from base64 import b64encode, b64decode
from typing import Any
from urllib.parse import quote_plus
from uuid import uuid4

from db.config import settings
from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class Premiumize(DebridClient):
    BASE_URL = "https://www.premiumize.me/api"
    OAUTH_TOKEN_URL = "https://www.premiumize.me/token"
    OAUTH_URL = "https://www.premiumize.me/authorize"
    REDIRECT_URI = f"{settings.host_url}/premiumize/oauth2_redirect"

    OAUTH_CLIENT_ID = settings.premiumize_oauth_client_id
    OAUTH_CLIENT_SECRET = settings.premiumize_oauth_client_secret

    def _handle_service_specific_errors(self, error):
        pass

    def initialize_headers(self):
        if self.token:
            token_data = self.decode_token_str(self.token)
            self.headers = {"Authorization": f"Bearer {token_data['access_token']}"}

    @staticmethod
    def encode_token_data(access_token: str):
        """
        Premiumize support grant_type device_code which has 10years of token expiration.
        """
        return b64encode(str(access_token).encode()).decode()

    @staticmethod
    def decode_token_str(token: str) -> dict[str, str]:
        try:
            access_token = b64decode(token).decode()
        except ValueError:
            raise ProviderException("Invalid token", "invalid_token.mp4")
        return {"access_token": access_token}

    def get_authorization_url(self) -> str:
        state = uuid4().hex
        return f"{self.OAUTH_URL}?client_id={self.OAUTH_CLIENT_ID}&response_type=code&redirect_uri={quote_plus(self.REDIRECT_URI)}&state={state}"

    def get_token(self, code):
        return self._make_request(
            "POST",
            self.OAUTH_TOKEN_URL,
            data={
                "client_id": self.OAUTH_CLIENT_ID,
                "client_secret": self.OAUTH_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.REDIRECT_URI,
            },
        )

    def add_magent_link(self, magnet_link: str, folder_id: str = None):
        return self._make_request(
            "POST",
            f"{self.BASE_URL}/transfer/create",
            data={"src": magnet_link, "folder_id": folder_id},
        )

    def create_folder(self, name, parent_id=None):
        return self._make_request(
            "POST",
            f"{self.BASE_URL}/folder/create",
            data={"name": name, "parent_id": parent_id},
        )

    def get_transfer_list(self):
        return self._make_request("GET", f"{self.BASE_URL}/transfer/list")

    def get_user_torrent_list(self):
        return self._make_request("GET", f"{self.BASE_URL}/item/listall")

    def get_torrent_info(self, torrent_id):
        transfer_list = self.get_transfer_list()
        torrent_info = next(
            (
                torrent
                for torrent in transfer_list["transfers"]
                if torrent["id"] == torrent_id
            ),
            None,
        )
        return torrent_info

    def get_folder_list(self, folder_id: str = None):
        return self._make_request(
            "GET",
            f"{self.BASE_URL}/folder/list",
            params={"id": folder_id} if folder_id else None,
        )

    def delete_torrent(self, torrent_id):
        return self._make_request(
            "POST", f"{self.BASE_URL}/transfer/delete", data={"id": torrent_id}
        )

    def get_torrent_instant_availability(self, torrent_hashes: list[str]):
        return self._make_request(
            "GET", f"{self.BASE_URL}/cache/check", params={"items[]": torrent_hashes}
        )

    def create_download_link(self, link):
        response = self._make_request(
            "POST",
            f"{self.BASE_URL}/transfer/directdl",
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

    def disable_access_token(self):
        pass

    def get_available_torrent(
        self, info_hash: str, torrent_name
    ) -> dict[str, Any] | None:
        torrent_list_response = self.get_transfer_list()
        if torrent_list_response.get("status") != "success":
            if torrent_list_response.get("message") == "Not logged in.":
                raise ProviderException("Premiumize is not logged in.", "ap.mp4")
            raise ProviderException(
                "Failed to get torrent info from Premiumize", "transfer_error.mp4"
            )

        available_torrents = torrent_list_response["transfers"]
        for torrent in available_torrents:
            if (
                info_hash in torrent["src"]
                or info_hash == torrent["name"]
                or torrent_name == torrent["name"]
            ):
                return torrent
