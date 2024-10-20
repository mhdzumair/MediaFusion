import asyncio
from os import path
from typing import Optional, List

import httpx

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent


class OffCloud(DebridClient):
    BASE_URL = "https://offcloud.com/api"
    DELETE_URL = "https://offcloud.com"

    async def initialize_headers(self):
        pass

    async def disable_access_token(self):
        pass

    async def _handle_service_specific_errors(self, error: httpx.HTTPStatusError):
        if error.response.status_code == 403:
            raise ProviderException("Invalid OffCloud API key", "invalid_token.mp4")
        if error.response.status_code == 429:
            raise ProviderException(
                "OffCloud rate limit exceeded", "too_many_requests.mp4"
            )

    async def _make_request(
        self,
        method: str,
        url: str,
        data: Optional[dict | str] = None,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        is_return_none: bool = False,
        is_expected_to_fail: bool = False,
        delete: bool = False,
    ) -> dict | list:
        params = params or {}
        params["key"] = self.token
        full_url = (self.DELETE_URL if delete else self.BASE_URL) + url
        return await super()._make_request(
            method, full_url, data, json, params, is_return_none, is_expected_to_fail
        )

    async def add_magnet_link(self, magnet_link: str) -> dict:
        response_data = await self._make_request(
            "POST", "/cloud", data={"url": magnet_link}
        )

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

    async def get_user_torrent_list(self) -> List[dict]:
        return await self._make_request("GET", "/cloud/history")

    async def get_torrent_info(self, request_id: str) -> dict:
        response = await self._make_request(
            "POST", "/cloud/status", data={"requestIds": [request_id]}
        )
        return response.get("requests", [{}])[0]

    async def get_torrent_instant_availability(self, magnet_links: List[str]) -> dict:
        response = await self._make_request(
            "POST", "/cache", data={"hashes": magnet_links}
        )
        return response.get("cachedItems", {})

    async def get_available_torrent(self, info_hash: str) -> Optional[dict]:
        available_torrents = await self.get_user_torrent_list()
        return next(
            (
                torrent
                for torrent in available_torrents
                if info_hash.casefold() in torrent["originalLink"].casefold()
            ),
            None,
        )

    async def explore_folder_links(self, request_id: str) -> List[str]:
        return await self._make_request("GET", f"/cloud/explore/{request_id}")

    @staticmethod
    async def get_file_sizes(files_data: list[dict]) -> list[dict]:
        async with httpx.AsyncClient() as client:
            file_sizes = await asyncio.gather(
                *[client.head(file_data["link"]) for file_data in files_data]
            )
        return [
            {**file_data, "size": int(response.headers["Content-Length"])}
            for file_data, response in zip(files_data, file_sizes)
        ]

    async def create_download_link(
        self,
        request_id: str,
        torrent_info: dict,
        filename: Optional[str],
        episode: Optional[int],
    ) -> str:
        if not torrent_info["isDirectory"]:
            return f"https://{torrent_info['server']}.offcloud.com/cloud/download/{request_id}/{torrent_info['fileName']}"

        links = await self.explore_folder_links(request_id)
        files_data = [{"name": path.basename(link), "link": link} for link in links]

        file_index = select_file_index_from_torrent(
            {"files": files_data},
            filename=filename,
            file_index=None,  # File index is not equal to OC file index
            episode=episode,
            file_size_callback=self.get_file_sizes,
        )
        selected_file = links[file_index]
        return selected_file

    async def delete_torrent(self, request_id: str) -> dict:
        return await self._make_request(
            "GET", f"/cloud/remove/{request_id}", delete=True
        )
