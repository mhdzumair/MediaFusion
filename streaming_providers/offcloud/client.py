import asyncio
from os import path

import aiohttp

from db.schemas import TorrentStreamData
from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import (
    select_file_index_from_torrent,
)


class OffCloud(DebridClient):
    BASE_URL = "https://offcloud.com"

    async def initialize_headers(self):
        self.headers = {
            "Authorization": f"Bearer {self.token}",
        }

    async def disable_access_token(self):
        pass

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        error_message = str(error_data.get("error", "")).casefold()
        if "not premium" in error_message or "premium required" in error_message:
            raise ProviderException("Need premium OffCloud account", "need_premium.mp4")
        if status_code == 403:
            raise ProviderException("Invalid OffCloud API key", "invalid_token.mp4")
        if status_code == 402:
            raise ProviderException("Need premium OffCloud account", "need_premium.mp4")
        if status_code == 429:
            raise ProviderException("OffCloud rate limit exceeded", "too_many_requests.mp4")

    async def _make_request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        **kwargs,
    ) -> dict | list:
        params = params or {}
        # Keep legacy key query parameter for backward compatibility.
        params["key"] = self.token
        full_url = self.BASE_URL + url
        return await super()._make_request(method=method, url=full_url, params=params, **kwargs)

    async def add_magnet_link(self, magnet_link: str) -> dict:
        response_data = await self._make_request("POST", "/api/cloud", data={"url": magnet_link})

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

    async def get_user_torrent_list(self) -> list[dict]:
        return await self._make_request("GET", "/api/cloud/history")

    async def get_torrent_info(self, request_id: str) -> dict:
        response = await self._make_request("POST", "/api/cloud/status", data={"requestId": request_id})
        if not isinstance(response, dict):
            return {}
        if "requests" in response:
            return response.get("requests", [{}])[0]
        # New API wraps the torrent info inside {"status": {…}}
        if "status" in response and isinstance(response["status"], dict):
            return response["status"]
        return response

    async def get_torrent_instant_availability(self, magnet_links: list[str]) -> list:
        response = await self._make_request("POST", "/api/cache", data={"hashes": magnet_links})
        return response.get("cachedItems", [])

    async def get_available_torrent(self, info_hash: str) -> dict | None:
        available_torrents = await self.get_user_torrent_list()
        return next(
            (
                torrent
                for torrent in available_torrents
                if info_hash.casefold() in torrent.get("originalLink", "").casefold()
            ),
            None,
        )

    async def explore_folder_links(self, request_id: str) -> list[str]:
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

        async def get_file_size(file_data: dict) -> tuple[dict, int | None]:
            """Helper function to get file size for a single file."""
            try:
                async with self.session.head(
                    file_data["link"],
                    timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=True,
                ) as response:
                    if response.status == 200:
                        return file_data, int(response.headers.get("Content-Length", 0))
            except (TimeoutError, aiohttp.ClientError):
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
        stream: TorrentStreamData,
        filename: str | None,
        season: int | None,
        episode: int | None,
    ) -> str:
        if torrent_info.get("url"):
            return torrent_info["url"]

        if torrent_info.get("isDirectory") is False and torrent_info.get("server") and torrent_info.get("fileName"):
            return (
                f"https://{torrent_info['server']}.offcloud.com/cloud/download/{request_id}/{torrent_info['fileName']}"
            )

        links = await self.explore_folder_links(request_id)
        if isinstance(links, dict):
            raise ProviderException(
                f"Failed to explore OffCloud files: {links.get('error', 'unknown error')}",
                "transfer_error.mp4",
            )
        if not links:
            raise ProviderException("No matching file available", "no_matching_file.mp4")
        files_data = [{"name": path.basename(link), "link": link} for link in links]

        file_index = await select_file_index_from_torrent(
            torrent_info={"files": files_data},
            torrent_stream=stream,
            filename=filename,
            season=season,
            episode=episode,
            file_size_callback=self.update_file_sizes,
        )

        selected_file_url = links[file_index]
        return selected_file_url

    async def delete_torrent(self, request_id: str) -> dict:
        return await self._make_request("GET", f"/cloud/remove/{request_id}")
