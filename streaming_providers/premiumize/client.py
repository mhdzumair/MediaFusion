from urllib.parse import quote_plus
from uuid import uuid4

import aiohttp

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

    def __init__(self, token: str | None = None, user_ip: str | None = None):
        self.user_ip = user_ip
        super().__init__(token)

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        pass

    async def _make_request(self, method: str, url: str, params: dict | None = None, **kwargs) -> dict | list:
        params = params or {}
        if self.is_private_token:
            params["apikey"] = self.token
        return await super()._make_request(method=method, url=url, params=params, **kwargs)

    async def initialize_headers(self):
        self.headers = {}
        if self.token:
            access_token = self.decode_token_str(self.token)
            if access_token:
                self.headers["Authorization"] = f"Bearer {access_token}"
            else:
                self.is_private_token = True
        if self.user_ip:
            self.headers["X-Forwarded-For"] = self.user_ip

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

    async def add_torrent_file(self, torrent_file: bytes, torrent_name: str | None, folder_id: str = None):
        data = aiohttp.FormData()
        data.add_field(
            "file",
            torrent_file,
            filename=torrent_name,
            content_type="application/x-bittorrent",
        )
        data.add_field("folder_id", folder_id)
        return await self._make_request(
            "POST",
            f"{self.BASE_URL}/transfer/create",
            data={"file": torrent_file, "folder_id": folder_id},
        )

    async def create_direct_download(self, magnet_link: str):
        return await self._make_request("POST", f"{self.BASE_URL}/transfer/directdl", data={"src": magnet_link})

    async def create_folder(self, name, parent_id=None):
        data = {"name": name}
        if parent_id:
            data["parent_id"] = parent_id
        return await self._make_request(
            "POST",
            f"{self.BASE_URL}/folder/create",
            data=data,
        )

    async def get_transfer_list(self):
        return await self._make_request("GET", f"{self.BASE_URL}/transfer/list")

    async def get_torrent_info(self, torrent_id):
        transfer_list = await self.get_transfer_list()
        torrent_info = next(
            (torrent for torrent in transfer_list["transfers"] if torrent["id"] == torrent_id),
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
        return await self._make_request("POST", f"{self.BASE_URL}/folder/delete", data={"id": folder_id})

    async def delete_torrent(self, torrent_id):
        return await self._make_request("POST", f"{self.BASE_URL}/transfer/delete", data={"id": torrent_id})

    async def get_torrent_instant_availability(self, torrent_hashes: list[str]):
        results = await self._make_request("GET", f"{self.BASE_URL}/cache/check", params={"items[]": torrent_hashes})
        if results.get("status") != "success":
            raise ProviderException(
                "Failed to get instant availability from Premiumize",
                "transfer_error.mp4",
            )
        return results

    async def disable_access_token(self):
        pass

    async def get_account_info(self):
        return await self._make_request("GET", f"{self.BASE_URL}/account/info")
