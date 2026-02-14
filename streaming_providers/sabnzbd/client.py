"""SABnzbd API client for Usenet downloads."""

import base64
from typing import Any

import aiohttp

from streaming_providers.exceptions import ProviderException


class SABnzbd:
    """SABnzbd API client for managing Usenet downloads."""

    def __init__(self, url: str, api_key: str):
        """Initialize SABnzbd client.

        Args:
            url: SABnzbd server URL
            api_key: SABnzbd API key
        """
        self.base_url = url.rstrip("/")
        self.api_key = api_key
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _make_request(
        self,
        mode: str,
        params: dict[str, Any] | None = None,
        data: aiohttp.FormData | None = None,
    ) -> dict:
        """Make a request to SABnzbd API.

        Args:
            mode: API mode/command
            params: Additional query parameters
            data: Form data for POST requests

        Returns:
            JSON response data
        """
        if not self.session:
            raise ProviderException("SABnzbd client not initialized", "provider_error.mp4")

        query_params = {
            "mode": mode,
            "apikey": self.api_key,
            "output": "json",
            **(params or {}),
        }

        url = f"{self.base_url}/api"

        try:
            if data:
                async with self.session.post(url, params=query_params, data=data) as response:
                    response.raise_for_status()
                    return await response.json()
            else:
                async with self.session.get(url, params=query_params) as response:
                    response.raise_for_status()
                    return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 401 or e.status == 403:
                raise ProviderException("Invalid SABnzbd API key", "invalid_token.mp4")
            raise ProviderException(f"SABnzbd API error: {e}", "provider_error.mp4")
        except aiohttp.ClientError as e:
            raise ProviderException(f"Failed to connect to SABnzbd: {e}", "provider_error.mp4")

    async def get_version(self) -> str:
        """Get SABnzbd version.

        Returns:
            Version string
        """
        response = await self._make_request("version")
        return response.get("version", "")

    async def get_queue(self) -> dict:
        """Get current download queue.

        Returns:
            Queue data with slots
        """
        response = await self._make_request("queue")
        return response.get("queue", {})

    async def get_history(self, limit: int = 50) -> dict:
        """Get download history.

        Args:
            limit: Maximum number of history items

        Returns:
            History data with slots
        """
        response = await self._make_request("history", {"limit": limit})
        return response.get("history", {})

    async def add_nzb_by_url(self, nzb_url: str, category: str = "MediaFusion", name: str | None = None) -> str:
        """Add NZB download by URL.

        Args:
            nzb_url: URL to NZB file
            category: Download category
            name: Optional custom name

        Returns:
            NZO ID of added download
        """
        params = {
            "name": nzb_url,
            "cat": category,
        }
        if name:
            params["nzbname"] = name

        response = await self._make_request("addurl", params)

        if response.get("status") is False:
            raise ProviderException(
                f"Failed to add NZB URL to SABnzbd: {response.get('error', 'Unknown error')}",
                "transfer_error.mp4",
            )

        nzo_ids = response.get("nzo_ids", [])
        if not nzo_ids:
            raise ProviderException("No NZO ID returned from SABnzbd", "transfer_error.mp4")

        return nzo_ids[0]

    async def add_nzb_by_content(self, nzb_content: bytes, filename: str, category: str = "MediaFusion") -> str:
        """Add NZB download by file content.

        Args:
            nzb_content: NZB file content as bytes
            filename: Name for the NZB file
            category: Download category

        Returns:
            NZO ID of added download
        """
        # SABnzbd accepts NZB content as base64 encoded
        nzb_base64 = base64.b64encode(nzb_content).decode("utf-8")

        params = {
            "name": nzb_base64,
            "nzbname": filename,
            "cat": category,
        }

        response = await self._make_request("addlocalfile", params)

        if response.get("status") is False:
            # Try alternative method using form upload
            data = aiohttp.FormData()
            data.add_field(
                "nzbfile",
                nzb_content,
                filename=f"{filename}.nzb",
                content_type="application/x-nzb",
            )

            response = await self._make_request("addfile", {"cat": category}, data=data)

            if response.get("status") is False:
                raise ProviderException(
                    f"Failed to add NZB to SABnzbd: {response.get('error', 'Unknown error')}",
                    "transfer_error.mp4",
                )

        nzo_ids = response.get("nzo_ids", [])
        if not nzo_ids:
            raise ProviderException("No NZO ID returned from SABnzbd", "transfer_error.mp4")

        return nzo_ids[0]

    async def get_nzb_status(self, nzo_id: str) -> dict | None:
        """Get status of a specific NZB download.

        Args:
            nzo_id: NZO ID of the download

        Returns:
            Download status dict or None if not found
        """
        # Check queue first
        queue = await self.get_queue()
        for slot in queue.get("slots", []):
            if slot.get("nzo_id") == nzo_id:
                return {
                    "status": "downloading",
                    "progress": float(slot.get("percentage", 0)),
                    "filename": slot.get("filename", ""),
                    "size": slot.get("mb", 0) * 1024 * 1024,
                    "eta": slot.get("timeleft", ""),
                    "nzo_id": nzo_id,
                }

        # Check history
        history = await self.get_history()
        for slot in history.get("slots", []):
            if slot.get("nzo_id") == nzo_id:
                return {
                    "status": "completed" if slot.get("status") == "Completed" else slot.get("status", "unknown"),
                    "progress": 100.0,
                    "filename": slot.get("name", ""),
                    "size": slot.get("bytes", 0),
                    "storage": slot.get("storage", ""),
                    "nzo_id": nzo_id,
                }

        return None

    async def get_completed_files(self, nzo_id: str) -> list[dict]:
        """Get files from a completed download.

        Args:
            nzo_id: NZO ID of the completed download

        Returns:
            List of file info dicts
        """
        response = await self._make_request("get_files", {"value": nzo_id})

        files = response.get("files", [])
        return [
            {
                "id": i,
                "filename": f.get("filename", ""),
                "size": f.get("bytes", 0),
                "status": f.get("status", ""),
            }
            for i, f in enumerate(files)
        ]

    async def delete_nzb(self, nzo_id: str, delete_files: bool = False) -> bool:
        """Delete an NZB download.

        Args:
            nzo_id: NZO ID to delete
            delete_files: Whether to delete downloaded files

        Returns:
            True if successful
        """
        # Try to delete from queue
        response = await self._make_request("queue", {"name": "delete", "value": nzo_id})
        if response.get("status") is True:
            return True

        # Try to delete from history
        mode = "delete" if delete_files else "remove"
        response = await self._make_request("history", {"name": mode, "value": nzo_id})
        return response.get("status", False)

    async def pause_nzb(self, nzo_id: str) -> bool:
        """Pause an NZB download.

        Args:
            nzo_id: NZO ID to pause

        Returns:
            True if successful
        """
        response = await self._make_request("queue", {"name": "pause", "value": nzo_id})
        return response.get("status", False)

    async def resume_nzb(self, nzo_id: str) -> bool:
        """Resume a paused NZB download.

        Args:
            nzo_id: NZO ID to resume

        Returns:
            True if successful
        """
        response = await self._make_request("queue", {"name": "resume", "value": nzo_id})
        return response.get("status", False)

    async def get_categories(self) -> list[str]:
        """Get available download categories.

        Returns:
            List of category names
        """
        response = await self._make_request("get_cats")
        return response.get("categories", [])

    async def get_all_downloads(self) -> list[dict]:
        """Get all downloads (queue + history).

        Returns:
            List of all download info dicts
        """
        downloads = []

        # Get queue items
        queue = await self.get_queue()
        for slot in queue.get("slots", []):
            downloads.append(
                {
                    "nzo_id": slot.get("nzo_id"),
                    "filename": slot.get("filename", ""),
                    "status": "downloading",
                    "progress": float(slot.get("percentage", 0)),
                    "size": slot.get("mb", 0) * 1024 * 1024,
                }
            )

        # Get history items
        history = await self.get_history()
        for slot in history.get("slots", []):
            downloads.append(
                {
                    "nzo_id": slot.get("nzo_id"),
                    "filename": slot.get("name", ""),
                    "status": "completed" if slot.get("status") == "Completed" else slot.get("status", "unknown"),
                    "progress": 100.0,
                    "size": slot.get("bytes", 0),
                    "storage": slot.get("storage", ""),
                }
            )

        return downloads

    async def find_download_by_name(self, name: str) -> dict | None:
        """Find a download by name (partial match).

        Args:
            name: Name to search for

        Returns:
            Download info dict or None
        """
        downloads = await self.get_all_downloads()
        name_lower = name.lower()

        for download in downloads:
            if name_lower in download.get("filename", "").lower():
                return download

        return None
