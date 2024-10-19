from base64 import b64encode, b64decode
from typing import Any, Optional

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException


class DebridLink(DebridClient):
    BASE_URL = "https://debrid-link.com/api/v2"
    OAUTH_URL = "https://debrid-link.com/api/oauth"
    OPENSOURCE_CLIENT_ID = "RyrV22FOg30DsxjYPziRKA"

    async def _handle_service_specific_errors(self, error):
        if (
            error.response.status_code == 400
            and error.response.json().get("error") == "freeServerOverload"
        ):
            raise ProviderException(
                "Debrid-Link free servers are overloaded", "need_premium.mp4"
            )

    async def initialize_headers(self):
        if self.token:
            token_data = self.decode_token_str(self.token)
            access_token_data = await self.refresh_token(
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

    async def get_device_code(self):
        return await self._make_request(
            "POST",
            f"{self.OAUTH_URL}/device/code",
            data={
                "client_id": self.OPENSOURCE_CLIENT_ID,
                "scope": "get.post.downloader get.post.seedbox get.account get.files get.post.stream",
            },
        )

    async def get_token(self, client_id, device_code):
        return await self._make_request(
            "POST",
            f"{self.OAUTH_URL}/token",
            data={
                "client_id": client_id,
                "code": device_code,
                "grant_type": "http://oauth.net/grant_type/device/1.0",
            },
            is_expected_to_fail=True,
        )

    async def refresh_token(self, client_id, refresh_token):
        return await self._make_request(
            "POST",
            f"{self.OAUTH_URL}/token",
            data={
                "client_id": client_id,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    async def authorize(self, device_code):
        token_data = await self.get_token(self.OPENSOURCE_CLIENT_ID, device_code)

        if "error" in token_data:
            return token_data

        if "access_token" in token_data:
            token = self.encode_token_data(
                self.OPENSOURCE_CLIENT_ID, token_data["refresh_token"]
            )
            return {"token": token}
        else:
            return token_data

    async def add_magnet_link(self, magnet_link):
        response = await self._make_request(
            "POST",
            f"{self.BASE_URL}/seedbox/add",
            data={"url": magnet_link},
            is_expected_to_fail=True,
        )
        if response.get("success") is False or response.get("error"):
            raise ProviderException(
                f"Failed to add magnet link to Debrid-Link: {response.get('error')}",
                "transfer_error.mp4",
            )
        return response.get("value", {})

    async def get_user_torrent_list(self):
        return await self._make_request("GET", f"{self.BASE_URL}/seedbox/list")

    async def get_torrent_info(self, torrent_id):
        response = await self._make_request(
            "GET", f"{self.BASE_URL}/seedbox/list", params={"ids": torrent_id}
        )
        if response.get("value"):
            return response.get("value")[0]
        raise ProviderException(
            "Failed to get torrent info from Debrid-Link", "transfer_error.mp4"
        )

    async def get_torrent_files_list(self, torrent_id):
        return await self._make_request(
            "GET", f"{self.BASE_URL}/files/{torrent_id}/list"
        )

    async def get_torrent_instant_availability(self, torrent_hash):
        return await self._make_request(
            "GET", f"{self.BASE_URL}/seedbox/cached", params={"url": torrent_hash}
        )

    async def delete_torrent(self, torrent_id):
        return await self._make_request(
            "DELETE", f"{self.BASE_URL}/seedbox/{torrent_id}/delete"
        )

    async def disable_access_token(self):
        return await self._make_request(
            "GET",
            f"{self.OAUTH_URL}/revoke",
            is_return_none=True,
            is_expected_to_fail=True,
        )

    async def get_available_torrent(self, info_hash: str) -> Optional[dict[str, Any]]:
        torrent_list_response = await self.get_user_torrent_list()
        if "error" in torrent_list_response:
            raise ProviderException(
                "Failed to get torrent info from Debrid-Link", "transfer_error.mp4"
            )

        available_torrents = torrent_list_response["value"]
        for torrent in available_torrents:
            if torrent["hashString"] == info_hash:
                return torrent
        return None
