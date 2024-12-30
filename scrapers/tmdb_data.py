import asyncio
import logging
import math
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin

import httpx
from thefuzz import fuzz

from db.config import settings
from utils.const import UA_HEADER

# TMDB API Configuration
TMDB_BASE_URL = "https://api.themoviedb.org/3/"
TMDB_API_KEY = settings.tmdb_api_key
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"


async def get_tmdb_data(
    tmdb_id: str, media_type: str, max_retries: int = 3
) -> Optional[Dict[str, Any]]:
    """
    Enhanced TMDB data fetcher with retry logic and better error handling
    """
    if not settings.tmdb_api_key:
        logging.error("TMDB API key is not configured")
        return None

    endpoint = f'{"movie" if media_type == "movie" else "tv"}/{tmdb_id}'
    params = {
        "api_key": settings.tmdb_api_key,
        "append_to_response": "credits,content_ratings,alternative_titles,external_ids,videos",
    }

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(
                proxy=settings.requests_proxy_url, timeout=10
            ) as client:
                response = await client.get(
                    urljoin(TMDB_BASE_URL, endpoint), params=params, headers=UA_HEADER
                )
                response.raise_for_status()
                data = response.json()

                return await format_tmdb_response(data, media_type)

        except httpx.TimeoutException:
            logging.warning(
                f"TMDB request timeout (attempt {attempt + 1}/{max_retries})"
            )
            if attempt == max_retries - 1:
                raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logging.warning(f"TMDB ID {tmdb_id} not found")
                return None
            if e.response.status_code == 429:  # Rate limit
                await asyncio.sleep(2**attempt)  # Exponential backoff
                continue
            raise
        except Exception as e:
            logging.error(f"Error fetching TMDB data: {e}")
            if attempt == max_retries - 1:
                raise

    return None


async def get_imdb_id_from_tmdb(tmdb_id: str, media_type: str) -> Optional[str]:
    """
    Fetch IMDB ID from TMDB data
    """
    if TMDB_API_KEY is None:
        logging.error("TMDB API key is not set")
        return None
    endpoint = f'{"movie" if media_type == "movie" else "tv"}/{tmdb_id}'
    params = {"api_key": TMDB_API_KEY, "append_to_response": "external_ids"}

    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
            response = await client.get(
                urljoin(TMDB_BASE_URL, endpoint),
                params=params,
                headers=UA_HEADER,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            return data["external_ids"].get("imdb_id")

    except Exception as e:
        logging.error(f"Error fetching IMDB ID from TMDB: {e}")
        return None


async def get_tmdb_season_episodes(
    tmdb_id: str, season_number: int
) -> List[Dict[str, Any]]:
    """Fetch episode data for a specific season"""
    if not settings.tmdb_api_key:
        return []

    endpoint = f"tv/{tmdb_id}/season/{season_number}"
    params = {"api_key": settings.tmdb_api_key}

    try:
        async with httpx.AsyncClient(
            proxy=settings.requests_proxy_url, timeout=10
        ) as client:
            response = await client.get(
                urljoin(TMDB_BASE_URL, endpoint), params=params, headers=UA_HEADER
            )
            response.raise_for_status()
            season_data = response.json()

            episodes = []
            for episode in season_data.get("episodes", []):
                air_date = episode.get("air_date")
                episodes.append(
                    {
                        "season_number": episode["season_number"],
                        "episode_number": episode["episode_number"],
                        "title": episode["name"],
                        "overview": episode["overview"],
                        "released": air_date,
                        "release_date": air_date,
                        "tmdb_rating": episode.get("vote_average"),
                        "imdb_rating": None,
                        "thumbnail": (
                            f"{TMDB_IMAGE_BASE_URL}w500{episode['still_path']}"
                            if episode.get("still_path")
                            else None
                        ),
                    }
                )
            return episodes
    except Exception as e:
        logging.error(f"Error fetching TMDB season {season_number} episodes: {e}")
        return []


async def format_tmdb_response(data: Dict[str, Any], media_type: str) -> Dict[str, Any]:
    """
    Format TMDB API response into standardized metadata format
    """
    is_movie = media_type == "movie"
    release_date = data.get("release_date" if is_movie else "first_air_date")

    formatted = {
        "tmdb_id": str(data["id"]),
        "imdb_id": data["external_ids"].get("imdb_id"),
        "title": data.get("title" if is_movie else "name"),
        "original_title": data.get("original_title" if is_movie else "original_name"),
        "year": int(release_date[:4]) if release_date else None,
        "poster": (
            f"{TMDB_IMAGE_BASE_URL}w500{data['poster_path']}"
            if data.get("poster_path")
            else None
        ),
        "background": (
            f"{TMDB_IMAGE_BASE_URL}original{data['backdrop_path']}"
            if data.get("backdrop_path")
            else None
        ),
        "description": data.get("overview"),
        "genres": [genre["name"] for genre in data.get("genres", [])],
        "tmdb_rating": data.get("vote_average"),
        "runtime": f"{data.get('runtime')} min" if data.get("runtime") else None,
        "aka_titles": [
            title["title"]
            for title in data.get("alternative_titles", {}).get(
                "titles" if is_movie else "results", []
            )
        ],
        "stars": [
            star["name"] for star in data.get("credits", {}).get("cast", [])[:10]
        ],
        "imdb_rating": None,
        "type": "movie" if is_movie else "series",
        "parent_guide_nudity_status": "Unknown",
        "parent_guide_certificates": list(
            {
                result["rating"]
                for result in data.get("content_ratings", {}).get("results", [])
            }
        ),
    }

    # Add TV series specific data
    if not is_movie:
        last_air_date = data.get("last_air_date")
        formatted.update(
            {
                "end_year": (
                    int(last_air_date[:4])
                    if last_air_date and data.get("status") == "Ended"
                    else None
                ),
                "number_of_seasons": data.get("number_of_seasons"),
                "number_of_episodes": data.get("number_of_episodes"),
                "status": data.get("status"),
            }
        )

        # Fetch episode data for each season
        episodes = []
        for season_number in range(1, data["number_of_seasons"] + 1):
            episodes.extend(
                await get_tmdb_season_episodes(str(data["id"]), season_number)
            )
        formatted["episodes"] = episodes

    return formatted


async def get_tmdb_data_by_imdb(
    imdb_id: str, media_type: str
) -> Optional[Dict[str, Any]]:
    """
    Fetch TMDB data using an IMDB ID.
    """
    if TMDB_API_KEY is None:
        logging.error("TMDB API key is not set")
        return {}
    endpoint = "find/" + imdb_id
    params = {"api_key": TMDB_API_KEY, "external_source": "imdb_id"}

    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
            response = await client.get(
                urljoin(TMDB_BASE_URL, endpoint),
                params=params,
                headers=UA_HEADER,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            if media_type == "movie":
                results = data.get("movie_results", [])
            elif media_type == "series":
                results = data.get("tv_results", [])
            else:
                return None
            if not results:
                return None
            result = results[0]
            # Fetch full details using TMDB ID
            return await get_tmdb_data(str(result["id"]), media_type)

    except Exception as e:
        logging.error(f"Error fetching TMDB data for IMDB ID {imdb_id}: {e}")
        return None


async def search_tmdb(
    title: str,
    year: int | None,
    media_type: str = None,
    max_retries: int = 3,
    created_year: int | None = None,
) -> Dict[str, Any]:
    """
    Search for a movie or TV show on TMDB with strict year validation.
    When year is provided, only exact matches are considered.
    When year is None, results are sorted by proximity to created_year.
    """
    if TMDB_API_KEY is None:
        logging.error("TMDB API key is not set")
        return {}

    def calculate_year_difference(result: Dict[str, Any]) -> float | None:
        """Calculate year difference score for sorting"""
        try:
            result_type = result.get("media_type")
            if media_type and result_type != media_type:
                return None

            # Extract year based on media type
            date_field = "release_date" if result_type == "movie" else "first_air_date"
            result_date = result.get(date_field, "")

            if not result_date:
                return None

            try:
                result_year = int(result_date[:4])
            except (ValueError, TypeError, IndexError):
                return None

            # If target year is provided, only allow exact matches
            if year is not None:
                if result_type == "movie":
                    return 0 if result_year == year else None
                else:
                    last_air_date = result.get("last_air_date", "")
                    try:
                        end_year = int(last_air_date[:4]) if last_air_date else None
                    except (ValueError, TypeError, IndexError):
                        end_year = None

                    # If it's an ongoing series, use math.inf for end_year
                    if end_year is None:
                        end_year = math.inf if result_year <= year else None

                    # Only proceed if we have valid end_year
                    if end_year is not None:
                        return 0 if (result_year <= year <= end_year) else None
                    return None

            # Only use created_year for sorting when target year is None
            if created_year:
                if result_type == "movie":
                    return abs(result_year - created_year)
                else:
                    # For series, use the minimum difference between created_year
                    # and either start or end year
                    last_air_date = result.get("last_air_date", "")
                    try:
                        end_year = int(last_air_date[:4]) if last_air_date else None
                    except (ValueError, TypeError, IndexError):
                        end_year = None

                    if end_year is None:
                        # If series is ongoing/unknown end, just use start year difference
                        return abs(result_year - created_year)
                    return min(
                        abs(result_year - created_year), abs(end_year - created_year)
                    )

            return float("inf")  # No year information available

        except (AttributeError, ValueError) as err:
            logging.error(f"TMDB search: Error calculating year difference: {err}")
            return float("inf")

    if media_type:
        media_type = "movie" if media_type == "movie" else "tv"
        endpoint = f"search/{media_type}"
    else:
        endpoint = "search/multi"

    params = {
        "api_key": TMDB_API_KEY,
        "query": title,
        "year": year if media_type == "movie" and year is not None else None,
        "first_air_date_year": (
            year if media_type == "tv" and year is not None else None
        ),
    }

    params = {k: v for k, v in params.items() if v is not None}

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
                response = await client.get(
                    urljoin(TMDB_BASE_URL, endpoint),
                    params=params,
                    headers=UA_HEADER,
                    timeout=10,
                )
                response.raise_for_status()
                results = response.json()["results"]

                if not results:
                    return {}

                # Filter by title similarity and calculate year differences
                candidates = []
                for result in results:
                    result_title = (
                        result.get("title")
                        if result.get("media_type") == "movie"
                        else result.get("name")
                    )
                    if (
                        not result_title
                        or fuzz.ratio(result_title.lower(), title.lower()) < 85
                    ):
                        continue

                    year_diff = calculate_year_difference(result)
                    if year_diff is not None:  # Only include valid matches
                        candidates.append((result, year_diff))

                # Sort candidates by year difference
                candidates.sort(key=lambda x: x[1])

                # Only fetch full data for the best match
                if candidates:
                    best_match = candidates[0][0]
                    try:
                        return await get_tmdb_data(
                            str(best_match["id"]), best_match.get("media_type")
                        )
                    except Exception as err:
                        logging.error(
                            f"TMDB search: Error fetching best match data: {err}"
                        )

                return {}

        except Exception as e:
            logging.debug(f"Error in attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                logging.warning(
                    "TMDB Search: Max retries reached. Returning empty dictionary."
                )
                return {}

    return {}
