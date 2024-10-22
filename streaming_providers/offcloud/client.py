import asyncio
from os import path
from typing import Optional, List

import httpx

from db.models import TorrentStreams
from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import (
    select_file_index_from_torrent,
    update_torrent_streams_metadata,
)


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
    async def update_file_sizes(files_data: list[dict]):
        async with httpx.AsyncClient() as client:
            file_sizes = await asyncio.gather(
                *[client.head(file_data["link"]) for file_data in files_data]
            )
        for file_data, file_size in zip(files_data, file_sizes):
            file_data["size"] = int(file_size.headers.get("Content-Length", 0))

    async def create_download_link(
        self,
        request_id: str,
        torrent_info: dict,
        stream: TorrentStreams,
        filename: Optional[str],
        season: Optional[int],
        episode: Optional[int],
        background_tasks,
    ) -> str:
        if not torrent_info["isDirectory"]:
            return f"https://{torrent_info['server']}.offcloud.com/cloud/download/{request_id}/{torrent_info['fileName']}"

        links = await self.explore_folder_links(request_id)
        files_data = [{"name": path.basename(link), "link": link} for link in links]

        file_index = await select_file_index_from_torrent(
            {"files": files_data},
            filename=filename,
            episode=episode,
            file_size_callback=self.update_file_sizes,
        )

        if filename is None:
            if "size" not in files_data[0]:
                await self.update_file_sizes(files_data)
            background_tasks.add_task(
                update_torrent_streams_metadata,
                torrent_stream=stream,
                torrent_info={"files": files_data},
                file_index=file_index,
                season=season,
                is_index_trustable=False,
            )
        selected_file_url = links[file_index]
        return selected_file_url

    async def delete_torrent(self, request_id: str) -> dict:
        return await self._make_request(
            "GET", f"/cloud/remove/{request_id}", delete=True
        )
