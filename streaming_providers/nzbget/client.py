"""NZBGet JSON-RPC API client for Usenet downloads."""

import base64
from typing import Any

import aiohttp

from streaming_providers.exceptions import ProviderException


class NZBGet:
    """NZBGet JSON-RPC API client for managing Usenet downloads."""

    def __init__(self, url: str, username: str, password: str):
        """Initialize NZBGet client.

        Args:
            url: NZBGet server URL
            username: NZBGet username
            password: NZBGet password
        """
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        auth = aiohttp.BasicAuth(self.username, self.password)
        self.session = aiohttp.ClientSession(auth=auth)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _make_request(self, method: str, params: list[Any] | None = None) -> Any:
        """Make a JSON-RPC request to NZBGet.

        Args:
            method: RPC method name
            params: Method parameters

        Returns:
            Response result
        """
        if not self.session:
            raise ProviderException("NZBGet client not initialized", "provider_error.mp4")

        payload = {
            "method": method,
            "params": params or [],
            "id": 1,
        }

        url = f"{self.base_url}/jsonrpc"

        try:
            async with self.session.post(url, json=payload) as response:
                response.raise_for_status()
                data = await response.json()

                if "error" in data and data["error"]:
                    raise ProviderException(
                        f"NZBGet API error: {data['error']}",
                        "provider_error.mp4",
                    )

                return data.get("result")
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                raise ProviderException("Invalid NZBGet credentials", "invalid_token.mp4")
            raise ProviderException(f"NZBGet API error: {e}", "provider_error.mp4")
        except aiohttp.ClientError as e:
            raise ProviderException(f"Failed to connect to NZBGet: {e}", "provider_error.mp4")

    async def get_version(self) -> str:
        """Get NZBGet version.

        Returns:
            Version string
        """
        return await self._make_request("version")

    async def get_status(self) -> dict:
        """Get NZBGet server status.

        Returns:
            Status dict
        """
        return await self._make_request("status")

    async def get_groups(self) -> list[dict]:
        """Get current download queue (groups).

        Returns:
            List of download groups
        """
        return await self._make_request("listgroups") or []

    async def get_history(self, hidden: bool = False) -> list[dict]:
        """Get download history.

        Args:
            hidden: Include hidden items

        Returns:
            List of history items
        """
        return await self._make_request("history", [hidden]) or []

    async def add_nzb_by_url(
        self,
        nzb_url: str,
        category: str = "MediaFusion",
        name: str | None = None,
        priority: int = 0,
    ) -> int:
        """Add NZB download by URL.

        Args:
            nzb_url: URL to NZB file
            category: Download category
            name: Optional custom name
            priority: Download priority (-100 to 100, 0 = normal)

        Returns:
            NZB ID of added download
        """
        # Use URL method instead of NZBContent-based append
        result = await self._make_request(
            "append",
            [
                name or nzb_url,  # NZBFilename
                nzb_url,  # URL
                category,
                priority,
                False,  # AddToTop
                False,  # AddPaused
                "",  # DupeKey
                0,  # DupeScore
                "SCORE",  # DupeMode
                [{"*Unpack:": "yes"}],  # PPParameters
            ],
        )

        if not result or result <= 0:
            raise ProviderException("Failed to add NZB URL to NZBGet", "transfer_error.mp4")

        return result

    async def add_nzb_by_content(
        self,
        nzb_content: bytes,
        filename: str,
        category: str = "MediaFusion",
        priority: int = 0,
    ) -> int:
        """Add NZB download by file content.

        Args:
            nzb_content: NZB file content as bytes
            filename: Name for the NZB file
            category: Download category
            priority: Download priority

        Returns:
            NZB ID of added download
        """
        # NZBGet expects base64 encoded content
        nzb_base64 = base64.b64encode(nzb_content).decode("utf-8")

        result = await self._make_request(
            "append",
            [
                f"{filename}.nzb",  # NZBFilename
                nzb_base64,  # NZBContent (base64)
                category,
                priority,
                False,  # AddToTop
                False,  # AddPaused
                "",  # DupeKey
                0,  # DupeScore
                "SCORE",  # DupeMode
                [{"*Unpack:": "yes"}],  # PPParameters
            ],
        )

        if not result or result <= 0:
            raise ProviderException("Failed to add NZB to NZBGet", "transfer_error.mp4")

        return result

    async def get_nzb_status(self, nzb_id: int) -> dict | None:
        """Get status of a specific NZB download.

        Args:
            nzb_id: NZB ID of the download

        Returns:
            Download status dict or None if not found
        """
        # Check queue first
        groups = await self.get_groups()
        for group in groups:
            if group.get("NZBID") == nzb_id:
                total_size = group.get("FileSizeMB", 0)
                remaining_size = group.get("RemainingSizeMB", 0)
                progress = ((total_size - remaining_size) / total_size * 100) if total_size > 0 else 0

                return {
                    "status": "downloading",
                    "progress": progress,
                    "filename": group.get("NZBName", ""),
                    "size": total_size * 1024 * 1024,
                    "nzb_id": nzb_id,
                    "dest_dir": group.get("DestDir", ""),
                }

        # Check history
        history = await self.get_history()
        for item in history:
            if item.get("NZBID") == nzb_id:
                status = item.get("Status", "UNKNOWN")
                is_success = status.startswith("SUCCESS")

                return {
                    "status": "completed" if is_success else status.lower(),
                    "progress": 100.0 if is_success else 0.0,
                    "filename": item.get("NZBName", ""),
                    "size": item.get("FileSizeMB", 0) * 1024 * 1024,
                    "nzb_id": nzb_id,
                    "dest_dir": item.get("DestDir", ""),
                }

        return None

    async def get_files(self, nzb_id: int) -> list[dict]:
        """Get files for a specific download.

        Args:
            nzb_id: NZB ID of the download

        Returns:
            List of file info dicts
        """
        result = await self._make_request("listfiles", [0, 0, nzb_id])
        return result or []

    async def delete_nzb(self, nzb_id: int, delete_files: bool = False) -> bool:
        """Delete an NZB download.

        Args:
            nzb_id: NZB ID to delete
            delete_files: Whether to delete downloaded files

        Returns:
            True if successful
        """
        # Try to delete from queue
        result = await self._make_request("editqueue", ["GroupDelete", "", [nzb_id]])
        if result:
            return True

        # Try to delete from history
        if delete_files:
            result = await self._make_request("editqueue", ["HistoryDelete", "", [nzb_id]])
        else:
            result = await self._make_request("editqueue", ["HistoryMarkBad", "", [nzb_id]])

        return bool(result)

    async def pause_nzb(self, nzb_id: int) -> bool:
        """Pause an NZB download.

        Args:
            nzb_id: NZB ID to pause

        Returns:
            True if successful
        """
        result = await self._make_request("editqueue", ["GroupPause", "", [nzb_id]])
        return bool(result)

    async def resume_nzb(self, nzb_id: int) -> bool:
        """Resume a paused NZB download.

        Args:
            nzb_id: NZB ID to resume

        Returns:
            True if successful
        """
        result = await self._make_request("editqueue", ["GroupResume", "", [nzb_id]])
        return bool(result)

    async def get_config(self) -> list[dict]:
        """Get NZBGet configuration.

        Returns:
            List of config options
        """
        return await self._make_request("config") or []

    async def get_all_downloads(self) -> list[dict]:
        """Get all downloads (queue + history).

        Returns:
            List of all download info dicts
        """
        downloads = []

        # Get queue items
        groups = await self.get_groups()
        for group in groups:
            total_size = group.get("FileSizeMB", 0)
            remaining_size = group.get("RemainingSizeMB", 0)
            progress = ((total_size - remaining_size) / total_size * 100) if total_size > 0 else 0

            downloads.append(
                {
                    "nzb_id": group.get("NZBID"),
                    "filename": group.get("NZBName", ""),
                    "status": "downloading",
                    "progress": progress,
                    "size": total_size * 1024 * 1024,
                    "dest_dir": group.get("DestDir", ""),
                }
            )

        # Get history items
        history = await self.get_history()
        for item in history:
            status = item.get("Status", "UNKNOWN")
            is_success = status.startswith("SUCCESS")

            downloads.append(
                {
                    "nzb_id": item.get("NZBID"),
                    "filename": item.get("NZBName", ""),
                    "status": "completed" if is_success else status.lower(),
                    "progress": 100.0 if is_success else 0.0,
                    "size": item.get("FileSizeMB", 0) * 1024 * 1024,
                    "dest_dir": item.get("DestDir", ""),
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
