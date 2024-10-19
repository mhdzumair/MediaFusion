import asyncio
from typing import Optional, List

import PTT
import httpx
from thefuzz import fuzz

from streaming_providers.debrid_client import DebridClient
from streaming_providers.exceptions import ProviderException
from utils.validation_helper import is_video_file


class OffCloud(DebridClient):
    BASE_URL = "https://offcloud.com/api"
    DELETE_URL = "https://offcloud.com"

    async def initialize_headers(self):
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

    async def create_download_link(
        self, request_id: str, torrent_info: dict, filename: str, episode: Optional[int]
    ) -> str:
        if not torrent_info["isDirectory"]:
            return f"https://{torrent_info['server']}.offcloud.com/cloud/download/{request_id}/{torrent_info['fileName']}"

        links = await self.explore_folder_links(request_id)

        if filename:
            exact_match = next((link for link in links if filename in link), None)
            if exact_match:
                return exact_match

        # Fuzzy matching as a fallback
        fuzzy_matches = [(link, fuzz.ratio(filename, link)) for link in links]
        selected_file, fuzzy_ratio = max(fuzzy_matches, key=lambda x: x[1])

        if fuzzy_ratio < 50:
            # If the fuzzy ratio is less than 50, then select the largest file
            async with httpx.AsyncClient() as client:
                file_sizes = await asyncio.gather(
                    *[client.head(link) for link in links]
                )
                selected_file = max(
                    zip(links, file_sizes),
                    key=lambda x: int(x[1].headers.get("Content-Length", 0)),
                )[0]

        if episode:
            # Select the file with the matching episode number
            episode_match = next(
                (
                    link
                    for link in links
                    if episode in PTT.parse_title(link).get("episodes", [])
                ),
                None,
            )
            if episode_match:
                return episode_match

        if not is_video_file(selected_file):
            raise ProviderException(
                "No matching video file available for this torrent",
                "no_matching_file.mp4",
            )

        return selected_file

    async def delete_torrent(self, request_id: str) -> dict:
        return await self._make_request(
            "GET", f"/cloud/remove/{request_id}", delete=True
        )
