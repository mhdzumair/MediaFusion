"""
Kitsu metadata scraper for fetching anime data.
Kitsu is a modern anime tracking platform with a free API.
"""

import asyncio
import logging
from typing import Any

import httpx

from db.config import settings
from utils.const import UA_HEADER

logger = logging.getLogger(__name__)

# Kitsu API Configuration
KITSU_API_BASE = "https://kitsu.io/api/edge"
KITSU_HEADERS = {
    "Accept": "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
    **UA_HEADER,
}


async def _kitsu_request(
    endpoint: str,
    params: dict | None = None,
    max_retries: int = 3,
) -> dict[str, Any] | None:
    """Make request to Kitsu API with retry logic."""
    url = f"{KITSU_API_BASE}/{endpoint}"

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=30) as client:
                response = await client.get(url, params=params, headers=KITSU_HEADERS)

                if response.status_code == 429:
                    delay = min(2 * (2**attempt), 30)
                    logger.warning(f"Kitsu rate limited, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Kitsu resource not found: {endpoint}")
                return None
            logger.error(f"Kitsu HTTP error: {e}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(1 * (attempt + 1))
        except Exception as e:
            logger.error(f"Kitsu request error: {e}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(1 * (attempt + 1))

    return None


async def search_kitsu(
    query: str,
    subtype: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Search Kitsu for anime.

    Args:
        query: Search query
        subtype: Filter by subtype (TV, movie, OVA, ONA, special, music)
        limit: Maximum results

    Returns:
        List of search results
    """
    params = {
        "filter[text]": query,
        "page[limit]": min(limit, 20),
        "include": "genres",
    }

    if subtype:
        params["filter[subtype]"] = subtype

    response = await _kitsu_request("anime", params=params)

    if not response:
        return []

    return response.get("data", [])


async def get_kitsu_anime_data(
    kitsu_id: str,
    include_episodes: bool = True,
) -> dict[str, Any] | None:
    """
    Get detailed anime data from Kitsu.

    Args:
        kitsu_id: Kitsu anime ID
        include_episodes: Whether to fetch episode data

    Returns:
        Formatted anime metadata dict
    """
    params = {
        "include": "genres,characters,mediaRelationships.destination",
    }

    response = await _kitsu_request(f"anime/{kitsu_id}", params=params)

    if not response:
        return None

    data = response.get("data", {})
    included = response.get("included", [])

    episodes = []
    if include_episodes:
        episodes = await get_kitsu_anime_episodes(kitsu_id)

    return _format_kitsu_response(data, included, episodes)


async def get_kitsu_anime_episodes(kitsu_id: str) -> list[dict[str, Any]]:
    """
    Get all episodes for an anime.

    Args:
        kitsu_id: Kitsu anime ID

    Returns:
        List of episode dicts
    """
    all_episodes = []
    offset = 0
    limit = 20

    while True:
        params = {
            "page[limit]": limit,
            "page[offset]": offset,
        }

        response = await _kitsu_request(f"anime/{kitsu_id}/episodes", params=params)

        if not response:
            break

        data = response.get("data", [])
        if not data:
            break

        all_episodes.extend(data)

        # Check for next page
        links = response.get("links", {})
        if not links.get("next"):
            break

        offset += limit

    return all_episodes


def _normalize_kitsu_data(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize Kitsu API response (flatten attributes)."""
    if "attributes" not in item:
        return item

    return {**item, **item.get("attributes", {})}


def _format_kitsu_response(
    data: dict[str, Any],
    included: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Format Kitsu anime data into standardized format."""

    # Normalize the main data
    anime = _normalize_kitsu_data(data)

    # Extract genres from included
    genres = []
    for item in included:
        if item.get("type") == "genres":
            genre_data = _normalize_kitsu_data(item)
            genre_name = genre_data.get("name")
            if genre_name:
                genres.append(genre_name)

    # Extract characters from included
    characters = []
    for item in included:
        if item.get("type") == "characters":
            char_data = _normalize_kitsu_data(item)
            char_name = char_data.get("name") or char_data.get("canonicalName")
            if char_name:
                characters.append(char_name)

    # Get titles
    titles = anime.get("titles", {})
    title = anime.get("canonicalTitle") or titles.get("en") or titles.get("en_us") or titles.get("en_jp")
    original_title = titles.get("ja_jp") or titles.get("en_jp")

    aka_titles = []
    for lang, t in titles.items():
        if t and t != title:
            aka_titles.append(t)

    # Get images
    poster_image = anime.get("posterImage", {})
    poster = poster_image.get("large") or poster_image.get("medium") or poster_image.get("original")

    cover_image = anime.get("coverImage", {})
    background = cover_image.get("large") or cover_image.get("original")

    # Get dates
    start_date = anime.get("startDate")
    end_date = anime.get("endDate")

    year = None
    if start_date:
        try:
            year = int(start_date[:4])
        except (ValueError, TypeError):
            pass

    end_year = None
    if end_date and anime.get("status") == "finished":
        try:
            end_year = int(end_date[:4])
        except (ValueError, TypeError):
            pass

    # Map subtype to media type
    subtype = anime.get("subtype", "").lower()
    media_type = "movie" if subtype == "movie" else "series"

    # Format episodes
    formatted_episodes = []
    for ep_data in episodes:
        ep = _normalize_kitsu_data(ep_data)
        thumbnail = ep.get("thumbnail", {})

        formatted_episodes.append(
            {
                "season_number": ep.get("seasonNumber", 1),
                "episode_number": ep.get("number", 0),
                "title": ep.get("canonicalTitle") or ep.get("title"),
                "overview": ep.get("synopsis"),
                "released": ep.get("airdate"),
                "thumbnail": thumbnail.get("original") or thumbnail.get("large") if thumbnail else None,
                "length": ep.get("length"),
            }
        )

    # Get trailer
    videos = []
    youtube_id = anime.get("youtubeVideoId")
    if youtube_id:
        videos.append(
            {
                "name": "Trailer",
                "url": f"https://www.youtube.com/watch?v={youtube_id}",
                "youtube_id": youtube_id,
                "type": "Trailer",
            }
        )

    # Age rating mapping
    age_rating = anime.get("ageRating", "")
    age_rating_guide = anime.get("ageRatingGuide", "")
    parental_certificates = []
    if age_rating:
        parental_certificates.append(age_rating)
    if age_rating_guide and age_rating_guide not in parental_certificates:
        parental_certificates.append(age_rating_guide)

    # Runtime
    episode_length = anime.get("episodeLength")
    total_length = anime.get("totalLength")
    runtime = None
    if media_type == "movie" and total_length:
        runtime = f"{total_length} min"
    elif episode_length:
        runtime = f"{episode_length} min"

    return {
        "kitsu_id": str(anime.get("id")),
        "title": title,
        "original_title": original_title,
        "year": year,
        "end_year": end_year,
        "release_date": start_date,
        "poster": poster,
        "background": background,
        "description": anime.get("synopsis") or anime.get("description"),
        "genres": genres,
        "runtime": runtime,
        "runtime_minutes": episode_length,
        "status": anime.get("status"),
        "type": media_type,
        "episodes": formatted_episodes,
        "videos": videos,
        "aka_titles": aka_titles,
        "kitsu_rating": anime.get("averageRating"),
        "kitsu_rating_rank": anime.get("ratingRank"),
        "kitsu_popularity_rank": anime.get("popularityRank"),
        "kitsu_user_count": anime.get("userCount"),
        "kitsu_favorites_count": anime.get("favoritesCount"),
        "episode_count": anime.get("episodeCount"),
        "subtype": anime.get("subtype"),
        "characters": characters[:10],  # Keep for backward compatibility
        "nsfw": anime.get("nsfw", False),
        "parent_guide_certificates": parental_certificates,
        "slug": anime.get("slug"),
        "external_ids": {
            "kitsu_id": str(anime.get("id")),
        },
    }


async def search_multiple_kitsu(
    title: str,
    limit: int = 5,
    media_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search for multiple matching anime on Kitsu.

    Args:
        title: Title to search for
        limit: Maximum results to return
        media_type: "movie" or "series" (None searches all)

    Returns:
        List of metadata dicts
    """
    # Map media type to Kitsu subtype
    subtype = None
    if media_type == "movie":
        subtype = "movie"
    elif media_type == "series":
        subtype = "TV"  # Most common series type

    results = await search_kitsu(title, subtype=subtype, limit=limit * 2)

    full_results = []
    for result in results[:limit]:
        try:
            data = _format_kitsu_search_result(result)
            if data:
                full_results.append(data)
        except Exception as e:
            logger.error(f"Error formatting Kitsu data: {e}")

    return full_results


def _format_kitsu_search_result(data: dict[str, Any]) -> dict[str, Any]:
    """Format Kitsu search result (lighter format)."""
    anime = _normalize_kitsu_data(data)

    titles = anime.get("titles", {})
    title = anime.get("canonicalTitle") or titles.get("en") or titles.get("en_us")

    poster_image = anime.get("posterImage", {})
    poster = poster_image.get("large") or poster_image.get("medium")

    start_date = anime.get("startDate")
    year = None
    if start_date:
        try:
            year = int(start_date[:4])
        except (ValueError, TypeError):
            pass

    subtype = anime.get("subtype", "").lower()
    media_type = "movie" if subtype == "movie" else "series"

    return {
        "kitsu_id": str(anime.get("id")),
        "title": title,
        "year": year,
        "release_date": start_date,
        "poster": poster,
        "description": anime.get("synopsis"),
        "type": media_type,
        "kitsu_rating": anime.get("averageRating"),
        "status": anime.get("status"),
        "episode_count": anime.get("episodeCount"),
        "subtype": anime.get("subtype"),
        "external_ids": {
            "kitsu_id": str(anime.get("id")),
        },
    }


async def get_kitsu_by_title(
    title: str,
    year: int | None = None,
    media_type: str = "series",
) -> dict[str, Any] | None:
    """
    Search Kitsu and return best matching result.

    Args:
        title: Title to search for
        year: Optional year filter
        media_type: "movie" or "series"

    Returns:
        Best matching metadata dict
    """
    from thefuzz import fuzz

    subtype = "movie" if media_type == "movie" else "TV"
    results = await search_kitsu(title, subtype=subtype, limit=10)

    if not results:
        return None

    best_match = None
    best_score = 0

    for result in results:
        anime = _normalize_kitsu_data(result)
        titles = anime.get("titles", {})

        # Check all available titles
        result_titles = [
            anime.get("canonicalTitle"),
            titles.get("en"),
            titles.get("en_us"),
            titles.get("en_jp"),
            titles.get("ja_jp"),
        ]

        max_similarity = 0
        for rt in result_titles:
            if rt:
                similarity = fuzz.ratio(rt.lower(), title.lower())
                max_similarity = max(max_similarity, similarity)

        # Bonus for year match
        start_date = anime.get("startDate")
        if year and start_date:
            try:
                result_year = int(start_date[:4])
                if result_year == year:
                    max_similarity += 10
            except (ValueError, TypeError):
                pass

        if max_similarity > best_score:
            best_score = max_similarity
            best_match = result

    if best_match and best_score >= 70:
        kitsu_id = best_match.get("id")
        return await get_kitsu_anime_data(str(kitsu_id))

    return None


async def get_multiple_kitsu_by_ids(
    kitsu_ids: list[str],
    include: str = "genres",
) -> dict[str, Any] | None:
    """
    Fetch details for multiple Kitsu anime by IDs.

    Args:
        kitsu_ids: List of Kitsu anime IDs
        include: Relationships to include

    Returns:
        Response with data and included arrays
    """
    if not kitsu_ids:
        return None

    params = {
        "filter[id]": ",".join(str(id) for id in kitsu_ids),
        "include": include,
        "page[limit]": 20,
    }

    all_data = []
    all_included = []

    # Handle pagination
    response = await _kitsu_request("anime", params=params)

    while response:
        data = response.get("data", [])
        included = response.get("included", [])

        all_data.extend(data)
        all_included.extend(included)

        # Check for next page
        links = response.get("links", {})
        next_url = links.get("next")
        if not next_url:
            break

        # Parse next page offset
        try:
            # Extract offset from URL
            response = await _kitsu_request(
                "anime",
                params={
                    **params,
                    "page[offset]": len(all_data),
                },
            )
        except Exception:
            break

    return {
        "data": all_data,
        "included": all_included,
        "meta": {"count": len(all_data)},
    }
