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
    REDIRECT_URI = f"{settings.host_url}/streaming_provider/premiumize/oauth2_redirect"

    OAUTH_CLIENT_ID = settings.premiumize_oauth_client_id
    OAUTH_CLIENT_SECRET = settings.premiumize_oauth_client_secret

    async def _handle_service_specific_errors(self, error):
        pass

    async def initialize_headers(self):
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

    async def get_token(self, code):
        return await self._make_request(
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

    async def add_magnet_link(self, magnet_link: str, folder_id: str = None):
        return await self._make_request(
            "POST",
            f"{self.BASE_URL}/transfer/create",
            data={"src": magnet_link, "folder_id": folder_id},
        )

    async def create_folder(self, name, parent_id=None):
        return await self._make_request(
            "POST",
            f"{self.BASE_URL}/folder/create",
            data={"name": name, "parent_id": parent_id},
        )

    async def get_transfer_list(self):
        return await self._make_request("GET", f"{self.BASE_URL}/transfer/list")

    async def get_torrent_info(self, torrent_id):
        transfer_list = await self.get_transfer_list()
        torrent_info = next(
            (
                torrent
                for torrent in transfer_list["transfers"]
                if torrent["id"] == torrent_id
            ),
            None,
        )
        return torrent_info

    async def get_folder_list(self, folder_id: str = None):
        return await self._make_request(
            "GET",
            f"{self.BASE_URL}/folder/list",
            params={"id": folder_id} if folder_id else None,
        )

    async def delete_folder(self, folder_id: str):
        return await self._make_request(
            "POST", f"{self.BASE_URL}/folder/delete", data={"id": folder_id}
        )

    async def delete_torrent(self, torrent_id):
        return await self._make_request(
            "POST", f"{self.BASE_URL}/transfer/delete", data={"id": torrent_id}
        )

    async def get_torrent_instant_availability(self, torrent_hashes: list[str]):
        results = await self._make_request(
            "GET", f"{self.BASE_URL}/cache/check", params={"items[]": torrent_hashes}
        )
        if results.get("status") != "success":
            raise ProviderException(
                "Failed to get instant availability from Premiumize",
                "transfer_error.mp4",
            )
        return results

    async def disable_access_token(self):
        pass

    async def get_available_torrent(
        self, info_hash: str, torrent_name
    ) -> dict[str, Any] | None:
        torrent_list_response = await self.get_transfer_list()
        if torrent_list_response.get("status") != "success":
            if torrent_list_response.get("message") == "Not logged in.":
                raise ProviderException(
                    "Premiumize is not logged in.", "invalid_token.mp4"
                )
            raise ProviderException(
                "Failed to get torrent info from Premiumize", "transfer_error.mp4"
            )

        available_torrents = torrent_list_response["transfers"]
        for torrent in available_torrents:
            src = torrent.get("src")
            if (
                (src and info_hash in src)
                or info_hash == torrent["name"]
                or torrent_name == torrent["name"]
            ):
                return torrent
