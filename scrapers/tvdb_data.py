"""
TVDB metadata scraper for fetching TV series and movie data.
Uses TVDB API v4 with token-based authentication.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from db.config import settings
from utils.const import UA_HEADER

logger = logging.getLogger(__name__)

# TVDB API Configuration
TVDB_API_URL = "https://api4.thetvdb.com/v4"
TVDB_IMAGE_BASE = "https://artworks.thetvdb.com/banners/"

# Token cache
_token_cache: dict[str, Any] = {
    "token": None,
    "expires_at": None,
}


async def _get_auth_token() -> str | None:
    """Get TVDB authentication token with caching."""
    global _token_cache

    if not settings.tvdb_api_key:
        logger.warning("TVDB API key is not configured")
        return None

    # Check if we have a valid cached token
    if _token_cache["token"] and _token_cache["expires_at"]:
        if datetime.now() < _token_cache["expires_at"]:
            return _token_cache["token"]

    # Get new token
    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=30) as client:
            response = await client.post(
                f"{TVDB_API_URL}/login",
                json={"apikey": settings.tvdb_api_key},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

            token = data.get("data", {}).get("token")
            if token:
                # Token is valid for 28 days, but we'll refresh after 27
                _token_cache["token"] = token
                _token_cache["expires_at"] = datetime.now() + timedelta(days=27)
                return token

            logger.error("No token in TVDB login response")
            return None

    except Exception as e:
        logger.error(f"Failed to get TVDB auth token: {e}")
        return None


async def _tvdb_request(
    endpoint: str,
    method: str = "GET",
    params: dict | None = None,
    max_retries: int = 3,
) -> dict[str, Any] | None:
    """Make authenticated request to TVDB API with retry logic."""
    token = await _get_auth_token()
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        **UA_HEADER,
    }

    url = f"{TVDB_API_URL}/{endpoint}"

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=30) as client:
                response = await client.request(method, url, headers=headers, params=params)

                if response.status_code == 429:
                    # Rate limited - exponential backoff
                    delay = min(1000 * (2**attempt), 30000) / 1000
                    logger.warning(f"TVDB rate limited, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"TVDB resource not found: {endpoint}")
                return None
            logger.error(f"TVDB HTTP error: {e}")
            if attempt == max_retries - 1:
                return None
        except Exception as e:
            logger.error(f"TVDB request error: {e}")
            if attempt == max_retries - 1:
                return None

    return None


async def search_tvdb(
    query: str,
    media_type: str = "series",
) -> list[dict[str, Any]]:
    """
    Search TVDB for series or movies.

    Args:
        query: Search query
        media_type: "series" or "movie"

    Returns:
        List of search results
    """
    tvdb_type = "series" if media_type == "series" else "movie"

    response = await _tvdb_request(
        "search",
        params={"query": query, "type": tvdb_type},
    )

    if not response:
        return []

    results = response.get("data", [])

    # Filter out YouTube content and items without network/poster
    filtered_results = []
    for item in results:
        if item.get("network") == "YouTube":
            continue
        if not item.get("network") and (
            not item.get("image_url") or "/images/missing/" in (item.get("image_url") or "")
        ):
            continue
        filtered_results.append(item)

    return filtered_results


async def get_tvdb_series_data(
    tvdb_id: str,
    include_episodes: bool = True,
) -> dict[str, Any] | None:
    """
    Get detailed series data from TVDB.

    Args:
        tvdb_id: TVDB series ID
        include_episodes: Whether to fetch episode data

    Returns:
        Formatted series metadata dict
    """
    response = await _tvdb_request(
        f"series/{tvdb_id}/extended",
        params={"meta": "translations"},
    )

    if not response:
        return None

    data = response.get("data", {})

    episodes = []
    if include_episodes:
        episodes = await get_tvdb_series_episodes(tvdb_id)

    return _format_tvdb_series_response(data, episodes)


async def get_tvdb_movie_data(tvdb_id: str) -> dict[str, Any] | None:
    """
    Get detailed movie data from TVDB.

    Args:
        tvdb_id: TVDB movie ID

    Returns:
        Formatted movie metadata dict
    """
    response = await _tvdb_request(
        f"movies/{tvdb_id}/extended",
        params={"meta": "translations"},
    )

    if not response:
        return None

    data = response.get("data", {})
    return _format_tvdb_movie_response(data)


async def get_tvdb_series_episodes(
    tvdb_id: str,
    season_type: str = "default",
    language: str = "eng",
) -> list[dict[str, Any]]:
    """
    Get all episodes for a series.

    Args:
        tvdb_id: TVDB series ID
        season_type: Episode ordering (default, official, absolute, etc.)
        language: Language code (3-letter)

    Returns:
        List of episode dicts
    """
    all_episodes = []
    page = 0

    while True:
        response = await _tvdb_request(
            f"series/{tvdb_id}/episodes/{season_type}/{language}",
            params={"page": page},
        )

        if not response:
            break

        data = response.get("data", {})
        episodes = data.get("episodes", [])

        if not episodes:
            break

        all_episodes.extend(episodes)

        # Check for next page
        links = response.get("links", {})
        if not links.get("next"):
            break

        page += 1

    # Fallback to official if default returns no results
    if not all_episodes and season_type != "official":
        logger.debug(f"No episodes found for {season_type}, trying official")
        return await get_tvdb_series_episodes(tvdb_id, "official", language)

    return all_episodes


async def find_tvdb_by_imdb_id(imdb_id: str) -> dict[str, Any] | None:
    """
    Find TVDB entry by IMDb ID.

    Args:
        imdb_id: IMDb ID (e.g., tt1234567)

    Returns:
        Search result with TVDB ID if found
    """
    response = await _tvdb_request(f"search/remoteid/{imdb_id}")

    if not response:
        return None

    results = response.get("data", [])
    if results:
        result = results[0]
        # Extract tvdb_id from various possible locations
        tvdb_id = (
            result.get("tvdb_id") or result.get("id") or result.get("series", {}).get("id")
            if isinstance(result.get("series"), dict)
            else None or result.get("movie", {}).get("id")
            if isinstance(result.get("movie"), dict)
            else None
        )
        # Normalize the result
        result["tvdb_id"] = tvdb_id
        return result

    return None


async def find_tvdb_by_tmdb_id(tmdb_id: str) -> dict[str, Any] | None:
    """
    Find TVDB entry by TMDB ID.

    Args:
        tmdb_id: TMDB ID

    Returns:
        Search result with TVDB ID if found
    """
    response = await _tvdb_request(f"search/remoteid/{tmdb_id}")

    if not response:
        return None

    results = response.get("data", [])
    if results:
        result = results[0]
        # Extract tvdb_id from various possible locations
        tvdb_id = (
            result.get("tvdb_id") or result.get("id") or result.get("series", {}).get("id")
            if isinstance(result.get("series"), dict)
            else None or result.get("movie", {}).get("id")
            if isinstance(result.get("movie"), dict)
            else None
        )
        # Normalize the result
        result["tvdb_id"] = tvdb_id
        return result

    return None


def _find_artwork(
    artworks: list[dict],
    artwork_type: int,
    language: str = "eng",
) -> str | None:
    """
    Find artwork by type and language with fallback.

    Artwork types:
    - 2: Poster (series)
    - 3: Background (series)
    - 14: Poster (movies)
    - 15: Background (movies)
    - 23: Logo (series)
    - 25: Logo (movies)
    """
    if not artworks:
        return None

    # Try preferred language first
    for artwork in artworks:
        if artwork and artwork.get("type") == artwork_type and artwork.get("language") == language:
            return artwork.get("image")

    # Fallback to English
    if language != "eng":
        for artwork in artworks:
            if artwork and artwork.get("type") == artwork_type and artwork.get("language") == "eng":
                return artwork.get("image")

    # Fallback to any language
    for artwork in artworks:
        if artwork and artwork.get("type") == artwork_type:
            return artwork.get("image")

    return None


def _format_tvdb_series_response(
    data: dict[str, Any],
    episodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Format TVDB series data into standardized format."""
    artworks = data.get("artworks") or []

    # Extract genres (handle None values)
    genres_data = data.get("genres") or []
    genres = [g.get("name") for g in genres_data if g and g.get("name")]

    # Extract cast (handle None values)
    cast_list = []
    characters = data.get("characters") or []
    for char in characters[:20]:
        if char and char.get("personName"):
            cast_list.append(
                {
                    "name": char.get("personName"),
                    "tvdb_id": char.get("peopleId"),
                    "character": char.get("name"),
                    "image": char.get("personImgURL") or char.get("image"),
                }
            )

    # Extract remote IDs (IMDB, TMDB, etc.) - handle None values
    remote_ids = {}
    remote_ids_data = data.get("remoteIds") or []
    for remote in remote_ids_data:
        if not remote:
            continue
        source = remote.get("sourceName", "").lower()
        if "imdb" in source:
            remote_ids["imdb_id"] = remote.get("id")
        elif "tmdb" in source or "themoviedb" in source:
            remote_ids["tmdb_id"] = remote.get("id")

    # Format episodes (handle None values)
    formatted_episodes = []
    episodes = episodes or []
    for ep in episodes:
        if not ep:
            continue
        formatted_episodes.append(
            {
                "season_number": ep.get("seasonNumber", 0),
                "episode_number": ep.get("number", 0),
                "title": ep.get("name"),
                "overview": ep.get("overview"),
                "released": ep.get("aired"),
                "thumbnail": ep.get("image"),
                "runtime": ep.get("runtime"),
            }
        )

    # Extract trailers (handle None values)
    videos = []
    trailers = data.get("trailers") or []
    for trailer in trailers:
        if trailer and trailer.get("url"):
            videos.append(
                {
                    "name": trailer.get("name"),
                    "url": trailer.get("url"),
                    "type": "Trailer",
                }
            )

    # Get networks
    networks = []
    companies_data = data.get("companies", [])
    if isinstance(companies_data, list):
        # Companies as list of objects
        for company in companies_data:
            if company.get("name"):
                networks.append({"name": company.get("name")})
    elif isinstance(companies_data, dict):
        # Companies as nested dict by type
        for company_type in ["network", "production", "studio"]:
            for company in companies_data.get(company_type, []):
                if company.get("name"):
                    networks.append({"name": company.get("name")})

    first_aired = data.get("firstAired")
    year = None
    if first_aired:
        try:
            year = int(first_aired[:4])
        except (ValueError, TypeError):
            pass

    last_aired = data.get("lastAired")
    end_year = None
    if last_aired and data.get("status", {}).get("name") == "Ended":
        try:
            end_year = int(last_aired[:4])
        except (ValueError, TypeError):
            pass

    return {
        "tvdb_id": str(data.get("id")),
        "imdb_id": remote_ids.get("imdb_id"),
        "tmdb_id": remote_ids.get("tmdb_id"),
        "title": data.get("name"),
        "original_title": data.get("name"),  # TVDB doesn't always have original title
        "year": year,
        "end_year": end_year,
        "release_date": first_aired,
        "poster": _find_artwork(artworks, 2),  # Type 2 = poster
        "background": _find_artwork(artworks, 3),  # Type 3 = background
        "logo": _find_artwork(artworks, 23),  # Type 23 = logo
        "description": data.get("overview"),
        "genres": genres,
        "runtime": f"{data.get('averageRuntime')} min" if data.get("averageRuntime") else None,
        "runtime_minutes": data.get("averageRuntime"),
        "status": data.get("status", {}).get("name"),
        "networks": networks,
        "network": networks[0]["name"] if networks else None,
        "cast": cast_list,
        "stars": [c["name"] for c in cast_list[:10]],
        "type": "series",
        "episodes": formatted_episodes,
        "videos": videos,
        "original_country": data.get("originalCountry"),
        "original_language": data.get("originalLanguage"),
        "number_of_seasons": len(set(ep.get("seasonNumber", 0) for ep in episodes)) if episodes else None,
        "number_of_episodes": len(episodes) if episodes else None,
        "external_ids": {
            "tvdb_id": str(data.get("id")),
            "imdb_id": remote_ids.get("imdb_id"),
            "tmdb_id": remote_ids.get("tmdb_id"),
        },
    }


def _format_tvdb_movie_response(data: dict[str, Any]) -> dict[str, Any]:
    """Format TVDB movie data into standardized format."""
    artworks = data.get("artworks") or []

    # Extract genres (handle None values)
    genres_data = data.get("genres") or []
    genres = [g.get("name") for g in genres_data if g and g.get("name")]

    # Extract cast (handle None values)
    cast_list = []
    characters = data.get("characters") or []
    for char in characters[:20]:
        if char and char.get("personName"):
            cast_list.append(
                {
                    "name": char.get("personName"),
                    "tvdb_id": char.get("peopleId"),
                    "character": char.get("name"),
                    "image": char.get("personImgURL") or char.get("image"),
                }
            )

    # Extract remote IDs (handle None values)
    remote_ids = {}
    remote_ids_data = data.get("remoteIds") or []
    for remote in remote_ids_data:
        if not remote:
            continue
        source = remote.get("sourceName", "").lower()
        if "imdb" in source:
            remote_ids["imdb_id"] = remote.get("id")
        elif "tmdb" in source or "themoviedb" in source:
            remote_ids["tmdb_id"] = remote.get("id")

    # Extract trailers (handle None values)
    videos = []
    trailers = data.get("trailers") or []
    for trailer in trailers:
        if trailer and trailer.get("url"):
            videos.append(
                {
                    "name": trailer.get("name"),
                    "url": trailer.get("url"),
                    "type": "Trailer",
                }
            )

    release_date = None
    year = None
    first_release = data.get("first_release", {})
    if first_release and first_release.get("Date"):
        release_date = first_release.get("Date")
    elif data.get("year"):
        release_date = f"{data.get('year')}-01-01"

    if release_date:
        try:
            year = int(release_date[:4])
        except (ValueError, TypeError):
            pass
    elif data.get("year"):
        try:
            year = int(data.get("year"))
        except (ValueError, TypeError):
            pass

    return {
        "tvdb_id": str(data.get("id")),
        "imdb_id": remote_ids.get("imdb_id"),
        "tmdb_id": remote_ids.get("tmdb_id"),
        "title": data.get("name"),
        "original_title": data.get("name"),
        "year": year,
        "release_date": release_date,
        "poster": _find_artwork(artworks, 14),  # Type 14 = movie poster
        "background": _find_artwork(artworks, 15) or _find_artwork(artworks, 3),  # Type 15/3 = background
        "logo": _find_artwork(artworks, 25),  # Type 25 = movie logo
        "description": data.get("overview"),
        "genres": genres,
        "runtime": f"{data.get('runtime')} min" if data.get("runtime") else None,
        "runtime_minutes": data.get("runtime"),
        "cast": cast_list,
        "stars": [c["name"] for c in cast_list[:10]],
        "type": "movie",
        "videos": videos,
        "original_country": data.get("originalCountry"),
        "original_language": data.get("originalLanguage"),
        "external_ids": {
            "tvdb_id": str(data.get("id")),
            "imdb_id": remote_ids.get("imdb_id"),
            "tmdb_id": remote_ids.get("tmdb_id"),
        },
    }


async def get_tvdb_data_by_imdb(
    imdb_id: str,
    media_type: str,
) -> dict[str, Any] | None:
    """
    Get TVDB data using an IMDb ID.

    Args:
        imdb_id: IMDb ID (e.g., tt1234567)
        media_type: "movie" or "series"

    Returns:
        Formatted metadata dict
    """
    result = await find_tvdb_by_imdb_id(imdb_id)
    if not result:
        return None

    tvdb_id = result.get("tvdb_id") or result.get("id")
    if not tvdb_id:
        return None

    result_type = result.get("type", "").lower()

    if media_type == "movie" or result_type == "movie":
        return await get_tvdb_movie_data(str(tvdb_id))
    else:
        return await get_tvdb_series_data(str(tvdb_id))


async def search_multiple_tvdb(
    title: str,
    limit: int = 5,
    media_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search for multiple matching titles on TVDB.

    Args:
        title: Title to search for
        limit: Maximum results to return
        media_type: "movie" or "series" (None searches both)

    Returns:
        List of metadata dicts
    """
    results = await search_tvdb(title, media_type or "series")

    full_results = []
    for result in results[:limit]:
        tvdb_id = result.get("tvdb_id") or result.get("id")
        if not tvdb_id:
            continue

        result_type = result.get("type", "").lower()

        try:
            if result_type == "movie":
                data = await get_tvdb_movie_data(str(tvdb_id))
            else:
                data = await get_tvdb_series_data(str(tvdb_id), include_episodes=False)

            if data:
                # Add source provider marker
                data["_source_provider"] = "tvdb"
                full_results.append(data)
        except Exception as e:
            logger.error(f"Error fetching TVDB data for {tvdb_id}: {e}")

    return full_results
