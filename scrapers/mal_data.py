"""
MyAnimeList (MAL) metadata scraper using Jikan API v4.
Jikan is an unofficial MAL API that provides anime/manga data.
"""

import asyncio
import logging
from typing import Any

import httpx

from db.config import settings
from utils.const import UA_HEADER

logger = logging.getLogger(__name__)

# Jikan API Configuration
JIKAN_API_BASE = "https://api.jikan.moe/v4"

# Rate limiting
_last_request_time = 0
BASE_REQUEST_DELAY = 0.4  # 400ms between requests (2.5 req/sec)


async def _rate_limited_request(
    endpoint: str,
    params: dict | None = None,
    max_retries: int = 3,
) -> dict[str, Any] | None:
    """
    Make rate-limited request to Jikan API.
    Jikan has a rate limit of 3 requests/second.
    """
    global _last_request_time

    url = f"{JIKAN_API_BASE}/{endpoint}"

    for attempt in range(max_retries):
        # Ensure minimum delay between requests
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - _last_request_time
        if time_since_last < BASE_REQUEST_DELAY:
            await asyncio.sleep(BASE_REQUEST_DELAY - time_since_last)

        try:
            async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=30) as client:
                _last_request_time = asyncio.get_event_loop().time()
                response = await client.get(url, params=params, headers=UA_HEADER)

                if response.status_code == 429:
                    # Rate limited - exponential backoff
                    delay = min(1.5 * (2**attempt), 30)
                    logger.warning(f"Jikan rate limited, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"MAL resource not found: {endpoint}")
                return None
            logger.error(f"Jikan HTTP error: {e}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(1 * (attempt + 1))
        except Exception as e:
            logger.error(f"Jikan request error: {e}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(1 * (attempt + 1))

    return None


async def search_mal(
    query: str,
    media_type: str = "series",
    limit: int = 10,
    sfw: bool = True,
) -> list[dict[str, Any]]:
    """
    Search MAL for anime.

    Args:
        query: Search query
        media_type: "movie" or "series" (mapped to anime types)
        limit: Maximum results
        sfw: Filter out NSFW content

    Returns:
        List of search results
    """
    params = {
        "q": query,
        "limit": limit,
    }

    if sfw:
        params["sfw"] = "true"

    # Map media type to MAL anime type
    if media_type == "movie":
        params["type"] = "movie"
    elif media_type == "series":
        params["type"] = "tv"
    # If media_type is None, search all anime types

    response = await _rate_limited_request("anime", params=params)

    if not response:
        return []

    return response.get("data", [])


async def get_mal_anime_data(
    mal_id: str,
    include_episodes: bool = True,
) -> dict[str, Any] | None:
    """
    Get detailed anime data from MAL.

    Args:
        mal_id: MAL anime ID
        include_episodes: Whether to fetch episode data

    Returns:
        Formatted anime metadata dict
    """
    # Get full anime details
    response = await _rate_limited_request(f"anime/{mal_id}/full")

    if not response:
        return None

    data = response.get("data", {})

    episodes = []
    if include_episodes and data.get("type") in ["TV", "ONA", "OVA"]:
        episodes = await get_mal_anime_episodes(mal_id)

    return _format_mal_response(data, episodes)


async def get_mal_anime_episodes(mal_id: str) -> list[dict[str, Any]]:
    """
    Get all episodes for an anime.

    Args:
        mal_id: MAL anime ID

    Returns:
        List of episode dicts
    """
    all_episodes = []
    page = 1

    while True:
        response = await _rate_limited_request(
            f"anime/{mal_id}/episodes",
            params={"page": page},
        )

        if not response:
            break

        data = response.get("data", [])
        if not data:
            break

        all_episodes.extend(data)

        # Check pagination
        pagination = response.get("pagination", {})
        if not pagination.get("has_next_page"):
            break

        page += 1

    return all_episodes


async def get_mal_anime_characters(mal_id: str) -> list[dict[str, Any]]:
    """
    Get character list for an anime.

    Args:
        mal_id: MAL anime ID

    Returns:
        List of character dicts
    """
    response = await _rate_limited_request(f"anime/{mal_id}/characters")

    if not response:
        return []

    return response.get("data", [])


def _format_mal_response(
    data: dict[str, Any],
    episodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Format MAL anime data into standardized format."""

    # Extract titles
    titles = data.get("titles", [])
    title = data.get("title")
    original_title = None
    aka_titles = []

    for t in titles:
        t_type = t.get("type", "").lower()
        if t_type == "japanese":
            original_title = t.get("title")
        elif t_type in ["english", "synonym"]:
            aka_titles.append(t.get("title"))

    # Extract images
    images = data.get("images", {})
    jpg_images = images.get("jpg", {})
    poster = jpg_images.get("large_image_url") or jpg_images.get("image_url")

    # Extract genres
    genres = [g.get("name") for g in data.get("genres", []) if g.get("name")]
    # Add demographics as genres too (e.g., "Shounen", "Seinen")
    demographics = [d.get("name") for d in data.get("demographics", []) if d.get("name")]
    genres.extend(demographics)

    # Extract studios (similar to networks)
    studios = [{"name": s.get("name")} for s in data.get("studios", []) if s.get("name")]

    # Get dates
    aired = data.get("aired", {})
    release_date = aired.get("from")
    if release_date:
        release_date = release_date.split("T")[0]  # Remove time portion

    end_date = aired.get("to")
    if end_date:
        end_date = end_date.split("T")[0]

    year = data.get("year")
    if not year and release_date:
        try:
            year = int(release_date[:4])
        except (ValueError, TypeError):
            pass

    end_year = None
    if end_date and data.get("status") == "Finished Airing":
        try:
            end_year = int(end_date[:4])
        except (ValueError, TypeError):
            pass

    # Map MAL type to our type
    mal_type = data.get("type", "")
    media_type = "movie" if mal_type == "Movie" else "series"

    # Format episodes
    formatted_episodes = []
    for ep in episodes:
        formatted_episodes.append(
            {
                "season_number": 1,  # MAL doesn't have seasons concept
                "episode_number": ep.get("mal_id", 0),
                "title": ep.get("title"),
                "overview": ep.get("synopsis"),
                "released": ep.get("aired")[:10] if ep.get("aired") else None,
                "filler": ep.get("filler", False),
                "recap": ep.get("recap", False),
            }
        )

    # Get trailer
    videos = []
    trailer = data.get("trailer", {})
    if trailer.get("url"):
        videos.append(
            {
                "name": "Trailer",
                "url": trailer.get("url"),
                "youtube_id": trailer.get("youtube_id"),
                "type": "Trailer",
                "thumbnail": trailer.get("images", {}).get("maximum_image_url"),
            }
        )

    # Rating mapping (MAL uses string ratings)
    rating_map = {
        "G": "G",
        "PG": "PG",
        "PG-13": "PG-13",
        "R - 17+": "R",
        "R+": "R",
        "Rx": "NC-17",
    }
    rating = data.get("rating", "")
    parental_rating = rating_map.get(rating.split(" ")[0], "Unknown") if rating else "Unknown"

    return {
        "mal_id": str(data.get("mal_id")),
        "title": title,
        "original_title": original_title,
        "year": year,
        "end_year": end_year,
        "release_date": release_date,
        "poster": poster,
        "background": None,  # MAL doesn't provide background images
        "description": data.get("synopsis"),
        "genres": genres,
        "runtime": f"{data.get('duration', '')}" if data.get("duration") else None,
        "status": data.get("status"),
        "studios": studios,
        "network": studios[0]["name"] if studios else None,
        "type": media_type,
        "episodes": formatted_episodes,
        "videos": videos,
        "aka_titles": aka_titles,
        "mal_rating": data.get("score"),
        "mal_vote_count": data.get("scored_by"),
        "mal_rank": data.get("rank"),
        "mal_popularity": data.get("popularity"),
        "mal_members": data.get("members"),
        "episode_count": data.get("episodes"),
        "source": data.get("source"),  # Manga, Light Novel, Original, etc.
        "season": data.get("season"),  # winter, spring, summer, fall
        "broadcast": data.get("broadcast", {}).get("string"),
        "parent_guide_certificates": [parental_rating] if parental_rating != "Unknown" else [],
        "external_ids": {
            "mal_id": str(data.get("mal_id")),
        },
    }


async def search_multiple_mal(
    title: str,
    limit: int = 5,
    media_type: str | None = None,
    sfw: bool = True,
) -> list[dict[str, Any]]:
    """
    Search for multiple matching anime on MAL.

    Args:
        title: Title to search for
        limit: Maximum results to return
        media_type: "movie" or "series" (None searches both)
        sfw: Filter out NSFW content

    Returns:
        List of metadata dicts
    """
    results = await search_mal(title, media_type, limit=limit * 2, sfw=sfw)

    full_results = []
    for result in results[:limit]:
        mal_id = result.get("mal_id")
        if not mal_id:
            continue

        try:
            # Use basic data from search to avoid too many API calls
            data = _format_mal_search_result(result)
            if data:
                full_results.append(data)
        except Exception as e:
            logger.error(f"Error formatting MAL data for {mal_id}: {e}")

    return full_results


def _format_mal_search_result(data: dict[str, Any]) -> dict[str, Any]:
    """Format MAL search result (lighter format without full details)."""

    images = data.get("images", {})
    jpg_images = images.get("jpg", {})
    poster = jpg_images.get("large_image_url") or jpg_images.get("image_url")

    genres = [g.get("name") for g in data.get("genres", []) if g.get("name")]

    aired = data.get("aired", {})
    release_date = aired.get("from")
    if release_date:
        release_date = release_date.split("T")[0]

    year = data.get("year")
    if not year and release_date:
        try:
            year = int(release_date[:4])
        except (ValueError, TypeError):
            pass

    mal_type = data.get("type", "")
    media_type = "movie" if mal_type == "Movie" else "series"

    return {
        "mal_id": str(data.get("mal_id")),
        "title": data.get("title"),
        "year": year,
        "release_date": release_date,
        "poster": poster,
        "description": data.get("synopsis"),
        "genres": genres,
        "type": media_type,
        "mal_rating": data.get("score"),
        "status": data.get("status"),
        "episode_count": data.get("episodes"),
        "external_ids": {
            "mal_id": str(data.get("mal_id")),
        },
    }


async def get_mal_by_title(
    title: str,
    year: int | None = None,
    media_type: str = "series",
) -> dict[str, Any] | None:
    """
    Search MAL and return best matching result.

    Args:
        title: Title to search for
        year: Optional year filter
        media_type: "movie" or "series"

    Returns:
        Best matching metadata dict
    """
    from thefuzz import fuzz

    results = await search_mal(title, media_type, limit=10)

    if not results:
        return None

    # Find best match by title similarity and year
    best_match = None
    best_score = 0

    for result in results:
        result_title = result.get("title", "")
        similarity = fuzz.ratio(result_title.lower(), title.lower())

        # Bonus for year match
        result_year = result.get("year")
        if year and result_year and result_year == year:
            similarity += 10

        if similarity > best_score:
            best_score = similarity
            best_match = result

    if best_match and best_score >= 70:
        mal_id = best_match.get("mal_id")
        return await get_mal_anime_data(str(mal_id))

    return None
