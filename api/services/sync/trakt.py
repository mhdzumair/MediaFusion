"""
Trakt sync service implementation.

Trakt API documentation: https://trakt.docs.apiary.io/
"""

import logging
from datetime import datetime
from typing import Any

import httpx

from api.services.sync.base import BaseSyncService, WatchedItem
from db.config import settings
from db.enums import IntegrationType
from db.schemas.config import TraktConfig

logger = logging.getLogger(__name__)

# Trakt API endpoints
TRAKT_API_URL = "https://api.trakt.tv"
TRAKT_AUTH_URL = "https://trakt.tv/oauth"

# Default client ID (users can provide their own)
DEFAULT_TRAKT_CLIENT_ID = getattr(settings, "trakt_client_id", None)
DEFAULT_TRAKT_CLIENT_SECRET = getattr(settings, "trakt_client_secret", None)


class TraktSyncService(BaseSyncService[TraktConfig]):
    """Trakt sync service implementation."""

    platform = IntegrationType.TRAKT

    def __init__(self, config: TraktConfig, profile_id: int):
        super().__init__(config, profile_id)
        self._client: httpx.AsyncClient | None = None

    @property
    def client_id(self) -> str:
        """Get Trakt client ID."""
        return self.config.client_id or DEFAULT_TRAKT_CLIENT_ID or ""

    @property
    def client_secret(self) -> str:
        """Get Trakt client secret."""
        return self.config.client_secret or DEFAULT_TRAKT_CLIENT_SECRET or ""

    @property
    def headers(self) -> dict[str, str]:
        """Get API headers."""
        return {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
            "Authorization": f"Bearer {self.config.access_token}",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=TRAKT_API_URL,
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
        """Validate Trakt credentials by making a test API call."""
        try:
            client = await self._get_client()
            response = await client.get("/users/me")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Trakt credential validation failed: {e}")
            return False

    async def refresh_token(self) -> TraktConfig | None:
        """Refresh Trakt OAuth token."""
        if not self.config.refresh_token:
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{TRAKT_AUTH_URL}/token",
                    json={
                        "refresh_token": self.config.refresh_token,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                        "grant_type": "refresh_token",
                    },
                )

                if response.status_code != 200:
                    logger.error(f"Trakt token refresh failed: {response.text}")
                    return None

                data = response.json()

                # Return updated config - preserve user's custom credentials
                return TraktConfig(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token", self.config.refresh_token),
                    expires_at=data.get("created_at", 0) + data.get("expires_in", 0),
                    client_id=self.config.client_id,
                    client_secret=self.config.client_secret,
                    sync_enabled=self.config.sync_enabled,
                    sync_direction=self.config.sync_direction,
                    scrobble_enabled=self.config.scrobble_enabled,
                    min_watch_percent=self.config.min_watch_percent,
                )

        except Exception as e:
            logger.exception(f"Trakt token refresh error: {e}")
            return None

    async def fetch_watch_history(
        self,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[WatchedItem]:
        """Fetch watch history from Trakt."""
        items = []

        try:
            client = await self._get_client()

            # Fetch watched movies
            movies = await self._fetch_watched_movies(client, since)
            items.extend(movies)

            # Fetch watched shows (episodes)
            shows = await self._fetch_watched_shows(client, since)
            items.extend(shows)

            if limit:
                items = items[:limit]

            logger.info(f"Fetched {len(items)} items from Trakt")

        except Exception as e:
            logger.exception(f"Failed to fetch Trakt history: {e}")
        finally:
            await self._close_client()

        return items

    async def _fetch_watched_movies(
        self,
        client: httpx.AsyncClient,
        since: datetime | None = None,
    ) -> list[WatchedItem]:
        """Fetch watched movies from Trakt."""
        items = []

        try:
            response = await client.get("/sync/watched/movies")
            logger.info(f"Trakt movies response status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"Failed to fetch Trakt movies: {response.text}")
                return items

            movies_data = response.json()
            logger.info(f"Trakt returned {len(movies_data)} movies, filtering since={since}")
            for entry in movies_data:
                movie = entry.get("movie", {})
                ids = movie.get("ids", {})

                watched_at = None
                if entry.get("last_watched_at"):
                    watched_at = datetime.fromisoformat(entry["last_watched_at"].replace("Z", "+00:00"))

                # Skip if before since date
                if since and watched_at and watched_at < since:
                    continue

                items.append(
                    WatchedItem(
                        imdb_id=ids.get("imdb"),
                        tmdb_id=ids.get("tmdb"),
                        title=movie.get("title", ""),
                        year=movie.get("year"),
                        media_type="movie",
                        watched_at=watched_at,
                        platform_id=str(ids.get("trakt")),
                        platform_data={"plays": entry.get("plays", 1)},
                    )
                )

        except Exception as e:
            logger.exception(f"Error fetching Trakt movies: {e}")

        return items

    async def _fetch_watched_shows(
        self,
        client: httpx.AsyncClient,
        since: datetime | None = None,
    ) -> list[WatchedItem]:
        """Fetch watched show episodes from Trakt."""
        items = []

        try:
            response = await client.get("/sync/watched/shows")
            logger.info(f"Trakt shows response status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"Failed to fetch Trakt shows: {response.text}")
                return items

            shows_data = response.json()
            logger.info(f"Trakt returned {len(shows_data)} shows, filtering since={since}")
            for entry in shows_data:
                show = entry.get("show", {})
                show_ids = show.get("ids", {})

                # Process each season
                for season in entry.get("seasons", []):
                    season_num = season.get("number")

                    # Process each episode
                    for episode in season.get("episodes", []):
                        episode_num = episode.get("number")

                        watched_at = None
                        if episode.get("last_watched_at"):
                            watched_at = datetime.fromisoformat(episode["last_watched_at"].replace("Z", "+00:00"))

                        # Skip if before since date
                        if since and watched_at and watched_at < since:
                            continue

                        items.append(
                            WatchedItem(
                                imdb_id=show_ids.get("imdb"),
                                tmdb_id=show_ids.get("tmdb"),
                                tvdb_id=show_ids.get("tvdb"),
                                title=show.get("title", ""),
                                year=show.get("year"),
                                media_type="series",
                                season=season_num,
                                episode=episode_num,
                                watched_at=watched_at,
                                platform_id=str(show_ids.get("trakt")),
                                platform_data={"plays": episode.get("plays", 1)},
                            )
                        )

        except Exception as e:
            logger.exception(f"Error fetching Trakt shows: {e}")

        return items

    async def push_watch_history(
        self,
        items: list[WatchedItem],
    ) -> tuple[int, int]:
        """Push watch history to Trakt."""
        success = 0
        errors = 0

        if not items:
            return success, errors

        try:
            client = await self._get_client()

            # Separate movies and episodes
            movies = []
            episodes = []

            for item in items:
                entry = self._create_trakt_entry(item)
                if item.media_type == "movie":
                    movies.append(entry)
                else:
                    episodes.append(entry)

            # Sync movies
            if movies:
                movie_success, movie_errors = await self._sync_to_trakt(client, "/sync/history", {"movies": movies})
                success += movie_success
                errors += movie_errors

            # Sync episodes
            if episodes:
                ep_success, ep_errors = await self._sync_to_trakt(client, "/sync/history", {"episodes": episodes})
                success += ep_success
                errors += ep_errors

        except Exception as e:
            logger.exception(f"Failed to push to Trakt: {e}")
            errors += len(items)
        finally:
            await self._close_client()

        return success, errors

    def _create_trakt_entry(self, item: WatchedItem) -> dict[str, Any]:
        """Create Trakt API entry from WatchedItem."""
        ids = {}
        if item.imdb_id:
            ids["imdb"] = item.imdb_id
        if item.tmdb_id:
            ids["tmdb"] = item.tmdb_id
        if item.tvdb_id:
            ids["tvdb"] = item.tvdb_id

        entry: dict[str, Any] = {"ids": ids}

        if item.watched_at:
            entry["watched_at"] = item.watched_at.isoformat()

        if item.media_type == "series" and item.season and item.episode:
            # For episodes, we need show info + episode info
            return {
                "show": {"ids": ids},
                "seasons": [
                    {
                        "number": item.season,
                        "episodes": [{"number": item.episode}],
                    }
                ],
            }

        return entry

    async def _sync_to_trakt(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        data: dict,
    ) -> tuple[int, int]:
        """Make sync request to Trakt."""
        try:
            response = await client.post(endpoint, json=data)

            if response.status_code == 201:
                result = response.json()
                added = result.get("added", {})
                not_found = result.get("not_found", {})

                success = sum(added.values()) if isinstance(added, dict) else 0
                errors = sum(len(v) for v in not_found.values()) if isinstance(not_found, dict) else 0

                return success, errors
            else:
                logger.error(f"Trakt sync failed: {response.status_code} - {response.text}")
                return 0, 1

        except Exception as e:
            logger.exception(f"Trakt sync request error: {e}")
            return 0, 1

    async def get_platform_id_for_media(
        self,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        title: str | None = None,
        year: int | None = None,
    ) -> str | None:
        """Get Trakt ID for a media item."""
        try:
            client = await self._get_client()

            # Try IMDb ID first
            if imdb_id:
                response = await client.get(f"/search/imdb/{imdb_id}")
                if response.status_code == 200:
                    results = response.json()
                    if results:
                        item_type = results[0].get("type")
                        item = results[0].get(item_type, {})
                        return str(item.get("ids", {}).get("trakt"))

            # Try TMDB ID
            if tmdb_id:
                response = await client.get(f"/search/tmdb/{tmdb_id}")
                if response.status_code == 200:
                    results = response.json()
                    if results:
                        item_type = results[0].get("type")
                        item = results[0].get(item_type, {})
                        return str(item.get("ids", {}).get("trakt"))

            # Fallback to text search
            if title:
                query = title
                if year:
                    query += f" {year}"
                response = await client.get("/search/movie,show", params={"query": query})
                if response.status_code == 200:
                    results = response.json()
                    if results:
                        item_type = results[0].get("type")
                        item = results[0].get(item_type, {})
                        return str(item.get("ids", {}).get("trakt"))

        except Exception as e:
            logger.exception(f"Failed to get Trakt ID: {e}")
        finally:
            await self._close_client()

        return None

    # =========================================================================
    # Scrobbling support
    # =========================================================================

    async def scrobble_start(self, item: WatchedItem) -> bool:
        """Start scrobbling to Trakt."""
        if not self.config.scrobble_enabled:
            return True

        return await self._scrobble(item, "start", 0)

    async def scrobble_pause(self, item: WatchedItem) -> bool:
        """Pause scrobbling to Trakt."""
        if not self.config.scrobble_enabled:
            return True

        return await self._scrobble(item, "pause", 50)

    async def scrobble_stop(self, item: WatchedItem, progress_percent: float) -> bool:
        """Stop scrobbling to Trakt."""
        if not self.config.scrobble_enabled:
            return True

        return await self._scrobble(item, "stop", progress_percent)

    async def _scrobble(
        self,
        item: WatchedItem,
        action: str,
        progress: float,
    ) -> bool:
        """Make scrobble request to Trakt."""
        try:
            client = await self._get_client()

            # Build scrobble data
            data: dict[str, Any] = {"progress": progress}

            ids = {}
            if item.imdb_id:
                ids["imdb"] = item.imdb_id
            if item.tmdb_id:
                ids["tmdb"] = item.tmdb_id

            if item.media_type == "movie":
                data["movie"] = {"ids": ids}
            else:
                data["show"] = {"ids": ids}
                data["episode"] = {
                    "season": item.season,
                    "number": item.episode,
                }

            response = await client.post(f"/scrobble/{action}", json=data)
            return response.status_code in (200, 201)

        except Exception as e:
            logger.warning(f"Trakt scrobble failed: {e}")
            return False
        finally:
            await self._close_client()


# =========================================================================
# OAuth helper functions
# =========================================================================


def get_trakt_auth_url(client_id: str | None = None) -> str:
    """Get Trakt OAuth authorization URL."""
    cid = client_id or DEFAULT_TRAKT_CLIENT_ID
    return f"{TRAKT_AUTH_URL}/authorize?response_type=code&client_id={cid}&redirect_uri=urn:ietf:wg:oauth:2.0:oob"


async def exchange_trakt_code(
    code: str,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> TraktConfig | None:
    """Exchange authorization code for access token."""
    # Use provided credentials or fall back to server defaults
    cid = client_id or DEFAULT_TRAKT_CLIENT_ID
    secret = client_secret or DEFAULT_TRAKT_CLIENT_SECRET

    # Track if user provided custom credentials
    is_custom_app = bool(client_id and client_secret)

    if not cid or not secret:
        logger.error("Trakt client ID and secret are required")
        return None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TRAKT_AUTH_URL}/token",
                json={
                    "code": code,
                    "client_id": cid,
                    "client_secret": secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "authorization_code",
                },
            )

            if response.status_code != 200:
                logger.error(f"Trakt token exchange failed: {response.text}")
                return None

            data = response.json()

            # Only store custom credentials if user provided them
            # (don't store server defaults in user profile)
            return TraktConfig(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expires_at=data.get("created_at", 0) + data.get("expires_in", 0),
                client_id=client_id if is_custom_app else None,
                client_secret=client_secret if is_custom_app else None,
            )

    except Exception as e:
        logger.exception(f"Trakt token exchange error: {e}")
        return None
