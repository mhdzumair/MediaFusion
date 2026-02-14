"""
Xtream Codes API Client

Client for interacting with Xtream Codes IPTV servers.
Supports authentication, fetching categories, and retrieving streams for Live TV, VOD, and Series.
"""

import logging

import httpx

from utils import const

logger = logging.getLogger(__name__)


class XtreamError(Exception):
    """Base exception for Xtream client errors."""

    pass


class XtreamAuthError(XtreamError):
    """Authentication failed."""

    pass


class XtreamConnectionError(XtreamError):
    """Connection to server failed."""

    pass


class XtreamClient:
    """
    Client for Xtream Codes API.

    Xtream Codes is a popular IPTV middleware that provides:
    - Live TV channels
    - Video on Demand (VOD/Movies)
    - Series with episodes

    API Documentation:
    - Base URL format: http://server:port/player_api.php
    - Auth params: username, password
    - Actions: get_live_categories, get_live_streams, get_vod_categories,
               get_vod_streams, get_series, get_series_info
    """

    DEFAULT_TIMEOUT = 30.0

    def __init__(self, server_url: str, username: str, password: str):
        """
        Initialize Xtream client.

        Args:
            server_url: Base server URL (e.g., http://server.com:8080)
            username: Xtream username
            password: Xtream password
        """
        self.server_url = server_url.rstrip("/")
        self.username = username
        self.password = password

        # Construct API URL
        # Some servers use /player_api.php, others just /player_api
        if "/player_api" not in self.server_url:
            self.api_url = f"{self.server_url}/player_api.php"
        else:
            self.api_url = self.server_url

        self._account_info: dict | None = None

    def _get_base_params(self) -> dict:
        """Get base authentication parameters."""
        return {
            "username": self.username,
            "password": self.password,
        }

    async def _request(
        self,
        action: str | None = None,
        params: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """
        Make API request to Xtream server.

        Args:
            action: API action (e.g., get_live_streams)
            params: Additional parameters
            timeout: Request timeout

        Returns:
            JSON response dict
        """
        request_params = self._get_base_params()
        if action:
            request_params["action"] = action
        if params:
            request_params.update(params)

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers=const.UA_HEADER,
                verify=False,
            ) as client:
                response = await client.get(self.api_url, params=request_params)
                response.raise_for_status()

                # Handle empty response
                if not response.content:
                    return {}

                return response.json()
        except httpx.TimeoutException as e:
            logger.error(f"Xtream request timeout: {e}")
            raise XtreamConnectionError(f"Connection timeout to {self.server_url}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Xtream HTTP error: {e.response.status_code}")
            if e.response.status_code == 401:
                raise XtreamAuthError("Invalid username or password")
            raise XtreamConnectionError(f"HTTP error: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Xtream connection error: {e}")
            raise XtreamConnectionError(f"Failed to connect to server: {e}")
        except Exception as e:
            logger.exception(f"Xtream request failed: {e}")
            raise XtreamError(f"Request failed: {e}")

    async def authenticate(self) -> dict:
        """
        Test connection and get account info.

        Returns:
            Account info dict with keys like:
            - user_info: {username, status, exp_date, is_trial, active_cons, max_connections, ...}
            - server_info: {url, port, https_port, server_protocol, ...}

        Raises:
            XtreamAuthError: If authentication fails
            XtreamConnectionError: If connection fails
        """
        try:
            data = await self._request()

            # Check for authentication error
            if "user_info" not in data:
                raise XtreamAuthError("Invalid credentials or server response")

            user_info = data.get("user_info", {})

            # Check if account is active
            if user_info.get("status") == "Expired":
                logger.warning(f"Xtream account expired for {self.username}")

            if user_info.get("auth") == 0:
                raise XtreamAuthError("Authentication failed - account disabled")

            self._account_info = data
            return data

        except XtreamError:
            raise
        except Exception as e:
            logger.exception(f"Authentication failed: {e}")
            raise XtreamAuthError(f"Authentication failed: {e}")

    async def get_account_info(self) -> dict:
        """
        Get cached account info or fetch if not cached.

        Returns:
            Account info dict
        """
        if self._account_info is None:
            await self.authenticate()
        return self._account_info

    # =========================================================================
    # Live TV Methods
    # =========================================================================

    async def get_live_categories(self) -> list[dict]:
        """
        Get all live TV categories.

        Returns:
            List of category dicts with keys:
            - category_id: Category ID
            - category_name: Category display name
            - parent_id: Parent category ID (0 for root)
        """
        try:
            result = await self._request(action="get_live_categories")
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Failed to get live categories: {e}")
            return []

    async def get_live_streams(self, category_id: str | None = None) -> list[dict]:
        """
        Get live TV streams/channels.

        Args:
            category_id: Optional category ID to filter by

        Returns:
            List of stream dicts with keys:
            - num: Stream number
            - name: Channel name
            - stream_type: "live"
            - stream_id: Stream ID for URL construction
            - stream_icon: Logo URL
            - epg_channel_id: EPG channel ID
            - added: Timestamp when added
            - category_id: Category ID
            - tv_archive: Whether DVR is available
            - tv_archive_duration: DVR duration in days
        """
        try:
            params = {}
            if category_id:
                params["category_id"] = category_id

            result = await self._request(action="get_live_streams", params=params)
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Failed to get live streams: {e}")
            return []

    # =========================================================================
    # VOD (Movies) Methods
    # =========================================================================

    async def get_vod_categories(self) -> list[dict]:
        """
        Get all VOD (movie) categories.

        Returns:
            List of category dicts (same format as live categories)
        """
        try:
            result = await self._request(action="get_vod_categories")
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Failed to get VOD categories: {e}")
            return []

    async def get_vod_streams(self, category_id: str | None = None) -> list[dict]:
        """
        Get VOD (movie) streams.

        Args:
            category_id: Optional category ID to filter by

        Returns:
            List of VOD dicts with keys:
            - num: Stream number
            - name: Movie title
            - stream_type: "movie"
            - stream_id: Stream ID for URL construction
            - stream_icon: Poster URL
            - rating: IMDB/TMDB rating
            - rating_5based: Rating out of 5
            - added: Timestamp when added
            - category_id: Category ID
            - container_extension: File extension (mkv, mp4, etc.)
            - tmdb: TMDB ID (if available)
        """
        try:
            params = {}
            if category_id:
                params["category_id"] = category_id

            result = await self._request(action="get_vod_streams", params=params)
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Failed to get VOD streams: {e}")
            return []

    async def get_vod_info(self, vod_id: str) -> dict:
        """
        Get detailed info for a specific VOD item.

        Args:
            vod_id: VOD stream ID

        Returns:
            VOD info dict with movie_data, info, etc.
        """
        try:
            result = await self._request(action="get_vod_info", params={"vod_id": vod_id})
            return result if isinstance(result, dict) else {}
        except Exception as e:
            logger.error(f"Failed to get VOD info for {vod_id}: {e}")
            return {}

    # =========================================================================
    # Series Methods
    # =========================================================================

    async def get_series_categories(self) -> list[dict]:
        """
        Get all series categories.

        Returns:
            List of category dicts (same format as live categories)
        """
        try:
            result = await self._request(action="get_series_categories")
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Failed to get series categories: {e}")
            return []

    async def get_series(self, category_id: str | None = None) -> list[dict]:
        """
        Get series list.

        Args:
            category_id: Optional category ID to filter by

        Returns:
            List of series dicts with keys:
            - num: Series number
            - name: Series title
            - series_id: Series ID for getting episodes
            - cover: Poster URL
            - plot: Description
            - cast: Cast list
            - director: Director
            - genre: Genre
            - releaseDate: Release date
            - rating: Rating
            - rating_5based: Rating out of 5
            - category_id: Category ID
            - tmdb: TMDB ID (if available)
        """
        try:
            params = {}
            if category_id:
                params["category_id"] = category_id

            result = await self._request(action="get_series", params=params)
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Failed to get series: {e}")
            return []

    async def get_series_info(self, series_id: str) -> dict:
        """
        Get series details with episodes.

        Args:
            series_id: Series ID

        Returns:
            Dict with:
            - info: Series metadata (name, cover, plot, cast, etc.)
            - episodes: Dict of season_number -> list of episode dicts
              Each episode has: id, episode_num, title, container_extension, etc.
        """
        try:
            result = await self._request(action="get_series_info", params={"series_id": series_id})
            return result if isinstance(result, dict) else {}
        except Exception as e:
            logger.error(f"Failed to get series info for {series_id}: {e}")
            return {}

    # =========================================================================
    # URL Building
    # =========================================================================

    def build_stream_url(self, stream_type: str, stream_id: str, extension: str = "ts") -> str:
        """
        Build playback URL for a stream.

        Args:
            stream_type: "live", "movie", or "series"
            stream_id: Stream ID
            extension: File extension (ts for live, mkv/mp4 for vod/series)

        Returns:
            Full playback URL
        """
        # Map stream type to URL path
        type_paths = {
            "live": "live",
            "movie": "movie",
            "vod": "movie",
            "series": "series",
        }
        path = type_paths.get(stream_type, stream_type)

        # Default extensions by type
        if stream_type == "live" and extension == "ts":
            extension = "ts"  # or m3u8 for HLS
        elif stream_type in ("movie", "vod", "series") and extension == "ts":
            extension = "mkv"  # Most common

        return f"{self.server_url}/{path}/{self.username}/{self.password}/{stream_id}.{extension}"

    def build_live_url(self, stream_id: str, extension: str = "ts") -> str:
        """Build live TV stream URL."""
        return self.build_stream_url("live", stream_id, extension)

    def build_vod_url(self, stream_id: str, extension: str = "mkv") -> str:
        """Build VOD/movie stream URL."""
        return self.build_stream_url("movie", stream_id, extension)

    def build_series_url(self, stream_id: str, extension: str = "mkv") -> str:
        """Build series episode stream URL."""
        return self.build_stream_url("series", stream_id, extension)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    async def get_all_content_summary(self) -> dict:
        """
        Get summary of all available content.

        Returns:
            Dict with:
            - live_categories: List of live TV categories
            - vod_categories: List of VOD categories
            - series_categories: List of series categories
            - counts: {live: X, vod: Y, series: Z}
        """
        live_cats = await self.get_live_categories()
        vod_cats = await self.get_vod_categories()
        series_cats = await self.get_series_categories()

        # Get rough counts
        live_streams = await self.get_live_streams()
        vod_streams = await self.get_vod_streams()
        series_list = await self.get_series()

        return {
            "live_categories": live_cats,
            "vod_categories": vod_cats,
            "series_categories": series_cats,
            "counts": {
                "live": len(live_streams),
                "vod": len(vod_streams),
                "series": len(series_list),
            },
        }

    @staticmethod
    def parse_xtream_url(url: str) -> dict | None:
        """
        Parse Xtream URL to extract server, username, password.

        Supports formats:
        - http://server:port/get.php?username=X&password=Y&type=m3u_plus
        - http://server:port/player_api.php?username=X&password=Y
        - http://server:port/live/username/password/stream.ts

        Returns:
            Dict with server_url, username, password or None if invalid
        """
        from urllib.parse import parse_qs, urlparse

        try:
            parsed = urlparse(url)

            # Check for query string format
            if parsed.query:
                params = parse_qs(parsed.query)
                if "username" in params and "password" in params:
                    server_url = f"{parsed.scheme}://{parsed.netloc}"
                    return {
                        "server_url": server_url,
                        "username": params["username"][0],
                        "password": params["password"][0],
                    }

            # Check for path format (live/username/password/...)
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 3 and path_parts[0] in ("live", "movie", "series"):
                server_url = f"{parsed.scheme}://{parsed.netloc}"
                return {
                    "server_url": server_url,
                    "username": path_parts[1],
                    "password": path_parts[2],
                }

            return None
        except Exception:
            return None
