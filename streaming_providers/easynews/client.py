"""Easynews API client for direct Usenet streaming."""

from typing import Any
from urllib.parse import quote

import aiohttp

from streaming_providers.exceptions import ProviderException


class Easynews:
    """Easynews API client for searching and streaming Usenet content.

    Easynews provides direct HTTP streaming of Usenet content without
    requiring a separate download client.
    """

    BASE_URL = "https://members.easynews.com"
    SEARCH_URL = "https://members.easynews.com/2.0/search/solr-search/advanced"

    def __init__(self, username: str, password: str):
        """Initialize Easynews client.

        Args:
            username: Easynews account username
            password: Easynews account password
        """
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

    async def _make_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict | list:
        """Make a request to Easynews API.

        Args:
            url: Request URL
            params: Query parameters

        Returns:
            JSON response data
        """
        if not self.session:
            raise ProviderException("Easynews client not initialized", "provider_error.mp4")

        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 401:
                    raise ProviderException("Invalid Easynews credentials", "invalid_token.mp4")
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                raise ProviderException("Invalid Easynews credentials", "invalid_token.mp4")
            raise ProviderException(f"Easynews API error: {e}", "provider_error.mp4")
        except aiohttp.ClientError as e:
            raise ProviderException(f"Failed to connect to Easynews: {e}", "provider_error.mp4")

    async def search(
        self,
        query: str,
        max_results: int = 50,
        sort: str = "relevance",
        video_only: bool = True,
    ) -> list[dict]:
        """Search for content on Easynews.

        Args:
            query: Search query
            max_results: Maximum number of results
            sort: Sort order (relevance, date, size)
            video_only: Only return video files

        Returns:
            List of search results
        """
        params = {
            "gps": query,
            "sbj": 1,  # Search in subject
            "from": 0,
            "pno": 1,
            "s1": sort,
            "s1d": "-",  # Descending
            "s2": "relevance",
            "s2d": "-",
            "s3": "dsize",
            "s3d": "-",
            "pby": max_results,
            "u": 1,  # Return URLs
            "st": "adv",
            "saession": 0,
            "fex": "mkv,mp4,avi,mov,wmv,webm" if video_only else "",
            "spamf": 1,
            "sb": 1,
        }

        response = await self._make_request(self.SEARCH_URL, params)

        results = []
        data = response.get("data", [])

        for item in data:
            # Parse the result
            result = {
                "id": item.get("0"),  # File ID
                "hash": item.get("hash", item.get("0")),
                "filename": item.get("10", item.get("fn", "")),  # Filename
                "subject": item.get("2", ""),  # Subject line
                "size": int(item.get("4", 0)),  # Size in bytes
                "posted_at": item.get("5"),  # Post date
                "group": item.get("6"),  # Newsgroup
                "extension": item.get("11", ""),  # File extension
                "duration": item.get("14"),  # Video duration
                "resolution": item.get("15"),  # Video resolution
                "codec": item.get("16"),  # Video codec
                "sig": item.get("sig"),  # Signature for download
            }
            results.append(result)

        return results

    def generate_download_url(self, file_id: str, filename: str, sig: str | None = None) -> str:
        """Generate a download/streaming URL for a file.

        Args:
            file_id: Easynews file ID
            filename: Original filename
            sig: Optional signature from search

        Returns:
            Download URL with authentication
        """
        # URL encode the filename
        encoded_filename = quote(filename)

        # Build the URL with credentials embedded
        base_url = f"https://{quote(self.username)}:{quote(self.password)}@members.easynews.com"

        if sig:
            return f"{base_url}/dl/{file_id}/{encoded_filename}?sig={sig}"
        else:
            return f"{base_url}/dl/{file_id}/{encoded_filename}"

    async def get_file_info(self, file_id: str) -> dict | None:
        """Get information about a specific file.

        Args:
            file_id: Easynews file ID

        Returns:
            File info dict or None
        """
        # Search for the specific file
        results = await self.search(file_id, max_results=10, video_only=False)

        for result in results:
            if result.get("id") == file_id or result.get("hash") == file_id:
                return result

        return None

    async def search_movie(
        self,
        title: str,
        year: int | None = None,
        imdb_id: str | None = None,
    ) -> list[dict]:
        """Search for a movie.

        Args:
            title: Movie title
            year: Release year
            imdb_id: IMDb ID

        Returns:
            List of matching results
        """
        query_parts = [title]

        if year:
            query_parts.append(str(year))

        if imdb_id:
            query_parts.append(imdb_id)

        query = " ".join(query_parts)
        return await self.search(query, video_only=True)

    async def search_episode(
        self,
        title: str,
        season: int,
        episode: int,
        imdb_id: str | None = None,
    ) -> list[dict]:
        """Search for a TV episode.

        Args:
            title: Series title
            season: Season number
            episode: Episode number
            imdb_id: IMDb ID of the series

        Returns:
            List of matching results
        """
        # Format season/episode in common patterns
        se_formats = [
            f"S{season:02d}E{episode:02d}",
            f"s{season:02d}e{episode:02d}",
            f"{season}x{episode:02d}",
        ]

        all_results = []
        seen_ids = set()

        for se_format in se_formats:
            query = f"{title} {se_format}"
            if imdb_id:
                query += f" {imdb_id}"

            results = await self.search(query, max_results=20, video_only=True)

            for result in results:
                if result["id"] not in seen_ids:
                    seen_ids.add(result["id"])
                    all_results.append(result)

        return all_results

    async def verify_credentials(self) -> bool:
        """Verify that the credentials are valid.

        Returns:
            True if credentials are valid
        """
        try:
            # Try a simple search to verify credentials
            await self.search("test", max_results=1)
            return True
        except ProviderException:
            return False
