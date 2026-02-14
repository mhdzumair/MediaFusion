"""
Simkl sync service implementation.

Simkl API documentation: https://simkl.docs.apiary.io/
"""

import logging
from datetime import datetime
from typing import Any

import httpx

from api.services.sync.base import BaseSyncService, WatchedItem
from db.config import settings
from db.enums import IntegrationType
from db.schemas.config import SimklConfig

logger = logging.getLogger(__name__)

# Simkl API endpoints
SIMKL_API_URL = "https://api.simkl.com"
SIMKL_AUTH_URL = "https://simkl.com/oauth"

# Default client ID
DEFAULT_SIMKL_CLIENT_ID = getattr(settings, "simkl_client_id", None)
DEFAULT_SIMKL_CLIENT_SECRET = getattr(settings, "simkl_client_secret", None)


class SimklSyncService(BaseSyncService[SimklConfig]):
    """Simkl sync service implementation."""

    platform = IntegrationType.SIMKL

    def __init__(self, config: SimklConfig, profile_id: int):
        super().__init__(config, profile_id)
        self._client: httpx.AsyncClient | None = None

    @property
    def client_id(self) -> str:
        """Get Simkl client ID."""
        return self.config.client_id or DEFAULT_SIMKL_CLIENT_ID or ""

    @property
    def client_secret(self) -> str:
        """Get Simkl client secret."""
        return self.config.client_secret or DEFAULT_SIMKL_CLIENT_SECRET or ""

    @property
    def headers(self) -> dict[str, str]:
        """Get API headers."""
        return {
            "Content-Type": "application/json",
            "simkl-api-key": self.client_id,
            "Authorization": f"Bearer {self.config.access_token}",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=SIMKL_API_URL,
                headers=self.headers,
                timeout=30.0,
            )
        return self._client

    async def _close_client(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # Abstract method implementations
    # =========================================================================

    async def validate_credentials(self) -> bool:
        """Validate Simkl credentials."""
        try:
            client = await self._get_client()
            response = await client.get("/users/settings")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Simkl credential validation failed: {e}")
            return False

    async def refresh_token(self) -> SimklConfig | None:
        """Refresh Simkl OAuth token."""
        if not self.config.refresh_token:
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{SIMKL_AUTH_URL}/token",
                    json={
                        "refresh_token": self.config.refresh_token,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                        "grant_type": "refresh_token",
                    },
                )

                if response.status_code != 200:
                    logger.error(f"Simkl token refresh failed: {response.text}")
                    return None

                data = response.json()

                # Preserve user's custom credentials
                return SimklConfig(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token", self.config.refresh_token),
                    expires_at=data.get("expires_in"),
                    client_id=self.config.client_id,
                    client_secret=self.config.client_secret,
                    sync_enabled=self.config.sync_enabled,
                    sync_direction=self.config.sync_direction,
                )

        except Exception as e:
            logger.exception(f"Simkl token refresh error: {e}")
            return None

    async def fetch_watch_history(
        self,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[WatchedItem]:
        """Fetch watch history from Simkl."""
        items = []

        try:
            client = await self._get_client()

            # Fetch all watched items
            params = {"extended": "full"}
            if since:
                params["date_from"] = since.strftime("%Y-%m-%d")

            # Fetch movies
            response = await client.get("/sync/all-items/movies", params=params)
            if response.status_code == 200:
                data = response.json()
                for entry in data.get("movies", []):
                    item = self._parse_movie(entry)
                    if item:
                        items.append(item)

            # Fetch shows
            response = await client.get("/sync/all-items/shows", params=params)
            if response.status_code == 200:
                data = response.json()
                for entry in data.get("shows", []):
                    show_items = self._parse_show(entry)
                    items.extend(show_items)

            # Fetch anime
            response = await client.get("/sync/all-items/anime", params=params)
            if response.status_code == 200:
                data = response.json()
                for entry in data.get("anime", []):
                    anime_items = self._parse_anime(entry)
                    items.extend(anime_items)

            if limit:
                items = items[:limit]

            logger.info(f"Fetched {len(items)} items from Simkl")

        except Exception as e:
            logger.exception(f"Failed to fetch Simkl history: {e}")
        finally:
            await self._close_client()

        return items

    def _parse_movie(self, entry: dict) -> WatchedItem | None:
        """Parse Simkl movie entry."""
        movie = entry.get("movie", {})
        ids = movie.get("ids", {})

        if entry.get("status") != "completed":
            return None

        watched_at = None
        if entry.get("last_watched_at"):
            watched_at = datetime.fromisoformat(entry["last_watched_at"].replace("Z", "+00:00"))

        return WatchedItem(
            imdb_id=ids.get("imdb"),
            tmdb_id=ids.get("tmdb"),
            title=movie.get("title", ""),
            year=movie.get("year"),
            media_type="movie",
            watched_at=watched_at,
            platform_id=str(ids.get("simkl")),
        )

    def _parse_show(self, entry: dict) -> list[WatchedItem]:
        """Parse Simkl show entry with episodes."""
        items = []
        show = entry.get("show", {})
        ids = show.get("ids", {})

        for season in entry.get("seasons", []):
            season_num = season.get("number")
            for episode in season.get("episodes", []):
                if episode.get("watched"):
                    watched_at = None
                    if episode.get("watched_at"):
                        watched_at = datetime.fromisoformat(episode["watched_at"].replace("Z", "+00:00"))

                    items.append(
                        WatchedItem(
                            imdb_id=ids.get("imdb"),
                            tmdb_id=ids.get("tmdb"),
                            tvdb_id=ids.get("tvdb"),
                            title=show.get("title", ""),
                            year=show.get("year"),
                            media_type="series",
                            season=season_num,
                            episode=episode.get("number"),
                            watched_at=watched_at,
                            platform_id=str(ids.get("simkl")),
                        )
                    )

        return items

    def _parse_anime(self, entry: dict) -> list[WatchedItem]:
        """Parse Simkl anime entry."""
        items = []
        anime = entry.get("show", {})
        ids = anime.get("ids", {})

        for episode in entry.get("episodes", []):
            if episode.get("watched"):
                watched_at = None
                if episode.get("watched_at"):
                    watched_at = datetime.fromisoformat(episode["watched_at"].replace("Z", "+00:00"))

                items.append(
                    WatchedItem(
                        imdb_id=ids.get("imdb"),
                        tmdb_id=ids.get("tmdb"),
                        mal_id=ids.get("mal"),
                        title=anime.get("title", ""),
                        year=anime.get("year"),
                        media_type="series",
                        episode=episode.get("number"),
                        watched_at=watched_at,
                        platform_id=str(ids.get("simkl")),
                    )
                )

        return items

    async def push_watch_history(
        self,
        items: list[WatchedItem],
    ) -> tuple[int, int]:
        """Push watch history to Simkl."""
        success = 0
        errors = 0

        if not items:
            return success, errors

        try:
            client = await self._get_client()

            # Build sync data
            movies = []
            shows = []

            for item in items:
                entry = self._create_simkl_entry(item)
                if item.media_type == "movie":
                    movies.append(entry)
                else:
                    shows.append(entry)

            # Sync movies
            if movies:
                response = await client.post(
                    "/sync/history",
                    json={"movies": movies},
                )
                if response.status_code in (200, 201):
                    result = response.json()
                    success += result.get("added", {}).get("movies", 0)
                else:
                    errors += len(movies)

            # Sync shows
            if shows:
                response = await client.post(
                    "/sync/history",
                    json={"shows": shows},
                )
                if response.status_code in (200, 201):
                    result = response.json()
                    success += result.get("added", {}).get("shows", 0)
                else:
                    errors += len(shows)

        except Exception as e:
            logger.exception(f"Failed to push to Simkl: {e}")
            errors += len(items)
        finally:
            await self._close_client()

        return success, errors

    def _create_simkl_entry(self, item: WatchedItem) -> dict[str, Any]:
        """Create Simkl API entry from WatchedItem."""
        ids = {}
        if item.imdb_id:
            ids["imdb"] = item.imdb_id
        if item.tmdb_id:
            ids["tmdb"] = item.tmdb_id
        if item.mal_id:
            ids["mal"] = item.mal_id

        entry: dict[str, Any] = {
            "ids": ids,
            "to": "completed",
        }

        if item.watched_at:
            entry["watched_at"] = item.watched_at.isoformat()

        if item.media_type == "series":
            entry["seasons"] = [
                {
                    "number": item.season or 1,
                    "episodes": [{"number": item.episode or 1}],
                }
            ]

        return entry

    async def get_platform_id_for_media(
        self,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        title: str | None = None,
        year: int | None = None,
    ) -> str | None:
        """Get Simkl ID for a media item."""
        try:
            client = await self._get_client()

            # Try IMDb ID first
            if imdb_id:
                response = await client.get("/search/id", params={"imdb": imdb_id})
                if response.status_code == 200:
                    results = response.json()
                    if results:
                        return str(results[0].get("ids", {}).get("simkl"))

            # Try TMDB ID
            if tmdb_id:
                response = await client.get("/search/id", params={"tmdb": str(tmdb_id)})
                if response.status_code == 200:
                    results = response.json()
                    if results:
                        return str(results[0].get("ids", {}).get("simkl"))

            # Fallback to text search
            if title:
                query = title
                response = await client.get("/search/movie,show,anime", params={"q": query})
                if response.status_code == 200:
                    results = response.json()
                    if results:
                        return str(results[0].get("ids", {}).get("simkl"))

        except Exception as e:
            logger.exception(f"Failed to get Simkl ID: {e}")
        finally:
            await self._close_client()

        return None


# =========================================================================
# OAuth helper functions
# =========================================================================


def get_simkl_auth_url(client_id: str | None = None) -> str:
    """Get Simkl OAuth authorization URL."""
    cid = client_id or DEFAULT_SIMKL_CLIENT_ID
    return f"{SIMKL_AUTH_URL}/authorize?response_type=code&client_id={cid}&redirect_uri=urn:ietf:wg:oauth:2.0:oob"


async def exchange_simkl_code(
    code: str,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> SimklConfig | None:
    """Exchange authorization code for access token."""
    # Use provided credentials or fall back to server defaults
    cid = client_id or DEFAULT_SIMKL_CLIENT_ID
    secret = client_secret or DEFAULT_SIMKL_CLIENT_SECRET

    # Track if user provided custom credentials
    is_custom_app = bool(client_id and client_secret)

    if not cid or not secret:
        logger.error("Simkl client ID and secret are required")
        return None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{SIMKL_AUTH_URL}/token",
                json={
                    "code": code,
                    "client_id": cid,
                    "client_secret": secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "authorization_code",
                },
            )

            if response.status_code != 200:
                logger.error(f"Simkl token exchange failed: {response.text}")
                return None

            data = response.json()

            # Only store custom credentials if user provided them
            return SimklConfig(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expires_at=data.get("expires_in"),
                client_id=client_id if is_custom_app else None,
                client_secret=client_secret if is_custom_app else None,
            )

    except Exception as e:
        logger.exception(f"Simkl token exchange error: {e}")
        return None
