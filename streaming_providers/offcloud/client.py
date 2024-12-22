import asyncio
from os import path
from typing import Optional, List

import aiohttp


from db.models import TorrentStreams
from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import (
    select_file_index_from_torrent,
    update_torrent_streams_metadata,
)


class OffCloud(DebridClient):
    BASE_URL = "https://offcloud.com"

    async def initialize_headers(self):
        pass

    async def disable_access_token(self):
        pass

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        if status_code == 403:
            raise ProviderException("Invalid OffCloud API key", "invalid_token.mp4")
        if status_code == 429:
            raise ProviderException(
                "OffCloud rate limit exceeded", "too_many_requests.mp4"
            )

    async def _make_request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        **kwargs,
    ) -> dict | list:
        params = params or {}
        params["key"] = self.token
        full_url = self.BASE_URL + url
        return await super()._make_request(
            method=method, url=full_url, params=params, **kwargs
        )

    async def add_magnet_link(self, magnet_link: str) -> dict:
        response_data = await self._make_request(
            "POST", "/api/cloud", data={"url": magnet_link}
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

    async def add_torrent_file(
        self, torrent_file: bytes, torrent_name: Optional[str]
    ) -> dict:
        data = aiohttp.FormData()
        data.add_field(
            "file",
            torrent_file,
            filename=torrent_name,
            content_type="application/x-bittorrent",
        )
        response_data = await self._make_request("POST", "/torrent/upload", data=data)
        if response_data.get("success") is False:
            raise ProviderException(
                f"Failed to add torrent file to OffCloud {response_data}",
                "transfer_error.mp4",
            )
        return await self.add_magnet_link(response_data["url"])

    async def get_user_torrent_list(self) -> List[dict]:
        return await self._make_request("GET", "/api/cloud/history")

    async def get_torrent_info(self, request_id: str) -> dict:
        response = await self._make_request(
            "POST", "/api/cloud/status", data={"requestIds": [request_id]}
        )
        return response.get("requests", [{}])[0]

    async def get_torrent_instant_availability(self, magnet_links: List[str]) -> dict:
        response = await self._make_request(
            "POST", "/api/cache", data={"hashes": magnet_links}
        )
        return response.get("cachedItems", {})

    async def get_available_torrent(self, info_hash: str) -> Optional[dict]:
        available_torrents = await self.get_user_torrent_list()
        return next(
            (
                torrent
                for torrent in available_torrents
                if info_hash.casefold() in torrent.get("originalLink", "").casefold()
            ),
            None,
        )

    async def explore_folder_links(self, request_id: str) -> List[str]:
        return await self._make_request("GET", f"/api/cloud/explore/{request_id}")

    async def update_file_sizes(self, files_data: list[dict]):
        """
        Update file sizes for a list of files by making HEAD requests.

        Args:
            files_data (list[dict]): List of file data dictionaries containing 'link' keys

        Note:
            This method modifies the input files_data list in-place, adding 'size' keys
            where the HEAD request was successful.
        """

        async def get_file_size(file_data: dict) -> tuple[dict, Optional[int]]:
            """Helper function to get file size for a single file."""
            try:
                async with self.session.head(
                    file_data["link"],
                    timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=True,
                ) as response:
                    if response.status == 200:
                        return file_data, int(response.headers.get("Content-Length", 0))
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            return file_data, 0

        # Gather all HEAD requests with proper concurrency
        tasks = [get_file_size(file_data) for file_data in files_data]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Update file sizes in the original data
        for file_data, size in results:
            file_data["size"] = size

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
        return await self._make_request("GET", f"/cloud/remove/{request_id}")
