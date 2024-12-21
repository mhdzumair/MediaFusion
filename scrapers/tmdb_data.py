import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import dramatiq
import httpx
from urllib.parse import urljoin

from db.config import settings
from db.models import MediaFusionMetaData
from db.schemas import MetaIdProjection
from utils.const import UA_HEADER
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker

# TMDB API Configuration
TMDB_BASE_URL = "https://api.themoviedb.org/3/"
TMDB_API_KEY = settings.tmdb_api_key
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"


async def get_tmdb_data(tmdb_id: str, media_type: str) -> Optional[Dict[str, Any]]:
    """
    Fetch detailed information about a movie or TV show from TMDB using its TMDB ID.
    """
    if TMDB_API_KEY is None:
        logging.error("TMDB API key is not set")
        return {}
    endpoint = f'{"movie" if media_type == "movie" else "tv"}/{tmdb_id}'
    params = {
        "api_key": TMDB_API_KEY,
        "append_to_response": "credits,content_ratings,alternative_titles,external_ids",
    }

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

            if media_type == "tv":
                aka_titles = data.get("alternative_titles", {}).get("results", [])
            else:
                aka_titles = data.get("alternative_titles", {}).get("titles", [])

            stars = data.get("credits", {}).get("cast", [])

            # Process and format the data
            formatted_data = {
                "tmdb_id": str(data["id"]),
                "imdb_id": data["external_ids"].get("imdb_id"),
                "title": data["title"] if media_type == "movie" else data["name"],
                "original_title": (
                    data["original_title"]
                    if media_type == "movie"
                    else data["original_name"]
                ),
                "year": (
                    int(data["release_date"][:4])
                    if media_type == "movie"
                    else int(data["first_air_date"][:4])
                ),
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
                "description": data["overview"],
                "genres": [genre["name"] for genre in data["genres"]],
                "tmdb_rating": data["vote_average"],
                "runtime": data.get("runtime"),
                "aka_titles": [title["title"] for title in aka_titles],
                "stars": [star["name"] for star in stars],
            }

            # Add TV series specific data
            if media_type in ["series", "tv"]:
                formatted_data.update(
                    {
                        "end_year": (
                            int(data["last_air_date"][:4])
                            if data.get("last_air_date")
                            and data.get("next_episode_to_air") is None
                            else None
                        ),
                        "number_of_seasons": data.get("number_of_seasons"),
                        "number_of_episodes": data.get("number_of_episodes"),
                        "status": data.get("status"),
                    }
                )

            return formatted_data

    except Exception as e:
        logging.error(f"Error fetching TMDB data for ID {tmdb_id}: {e}")
        return None


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


async def search_tmdb(title: str, year: int, media_type: str = None) -> Dict[str, Any]:
    """
    Search for a movie or TV show on TMDB.
    """
    if TMDB_API_KEY is None:
        logging.error("TMDB API key is not set")
        return {}
    if media_type:
        media_type = "movie" if media_type == "movie" else "tv"
        endpoint = f"search/{media_type}"
    else:
        endpoint = "search/multi"
    params = {
        "api_key": TMDB_API_KEY,
        "query": title,
        "year": year if media_type == "movie" else None,
        "first_air_date_year": year if media_type == "series" else None,
    }

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

            # Filter and process results
            for result in results:
                result_type = result.get("media_type", media_type)
                if result_type not in ["movie", "tv"]:
                    continue

                result_year = (
                    int(result.get("release_date", "")[:4])
                    if result_type == "movie"
                    else int(result.get("first_air_date", "")[:4])
                )

                if year and result_year != year:
                    continue

                return await get_tmdb_data(str(result["id"]), result_type)

            return {}

    except Exception as e:
        logging.error(f"Error searching TMDB: {e}")
        return {}


async def process_tmdb_data(tmdb_ids: List[str], metadata_type: str):
    """
    Process a batch of TMDB IDs and update the database.
    """
    now = datetime.now()

    # Initialize circuit breaker
    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=10, half_open_attempts=2
    )

    async for result in batch_process_with_circuit_breaker(
        get_tmdb_data,
        tmdb_ids,
        5,
        rate_limit_delay=3,
        cb=circuit_breaker,
        media_type=metadata_type,
    ):
        if not result:
            continue

        tmdb_id = result["tmdb_id"]

        # Update database entries
        await MediaFusionMetaData.find_one({"tmdb_id": tmdb_id}).update(
            {
                "$set": {
                    "genres": result["genres"],
                    "tmdb_rating": result["tmdb_rating"],
                    "aka_titles": result["aka_titles"],
                    "last_updated_at": now,
                    **(
                        {"end_year": result["end_year"]}
                        if metadata_type == "series" and "end_year" in result
                        else {}
                    ),
                }
            }
        )
        logging.info(f"Updated TMDB metadata for {tmdb_id}")


@dramatiq.actor(time_limit=3 * 60 * 60 * 1000, priority=8, max_retries=3)
async def process_tmdb_data_background(
    tmdb_ids: List[str], metadata_type: str = "movie"
):
    """
    Background task to process TMDB data updates.
    """
    now = datetime.now()
    seven_days_ago = now - timedelta(days=7)

    # Validate tmdb_ids from the database
    valid_ids = await MediaFusionMetaData.find(
        {
            "tmdb_id": {"$in": tmdb_ids},
            "$or": [
                {"last_updated_at": {"$lt": seven_days_ago}},
                {"last_updated_at": None},
            ],
        },
        projection_model=MetaIdProjection,
        with_children=True,
    ).to_list()

    valid_ids = [doc.tmdb_id for doc in valid_ids]

    if valid_ids:
        await process_tmdb_data(valid_ids, metadata_type)


@dramatiq.actor(time_limit=60 * 1000, priority=10, max_retries=0, queue_name="scrapy")
async def fetch_tmdb_ids_to_update(*args, **kwargs):
    """
    Fetch TMDB IDs that need updating and queue them for processing.
    """
    now = datetime.now()
    seven_days_ago = now - timedelta(days=7)

    # Fetch IDs needing updates
    documents = await MediaFusionMetaData.find(
        {
            "tmdb_id": {"$exists": True},
            "type": {"$in": ["movie", "series"]},
            "$or": [
                {"last_updated_at": {"$lt": seven_days_ago}},
                {"last_updated_at": None},
            ],
        },
        projection_model=MetaIdProjection,
        with_children=True,
    ).to_list()

    movie_ids = []
    series_ids = []
    for document in documents:
        if document.type == "movie":
            movie_ids.append(document.tmdb_id)
        else:
            series_ids.append(document.tmdb_id)

    logging.info(
        f"Fetched {len(movie_ids)} movie and {len(series_ids)} series TMDB IDs for updating"
    )

    # Process in chunks
    chunk_size = 25
    for i in range(0, len(movie_ids), chunk_size):
        chunk_ids = movie_ids[i : i + chunk_size]
        process_tmdb_data_background.send(chunk_ids, metadata_type="movie")

    for i in range(0, len(series_ids), chunk_size):
        chunk_ids = series_ids[i : i + chunk_size]
        process_tmdb_data_background.send(chunk_ids, metadata_type="series")
