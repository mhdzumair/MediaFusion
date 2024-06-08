import logging
from datetime import datetime, timedelta
from typing import Optional

import dramatiq
from beanie import BulkWriter
from curl_cffi import requests
from imdb import Cinemagoer
from imdb import IMDbDataAccessError, Movie

from db.models import (
    MediaFusionMetaData,
)
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker

ia = Cinemagoer()


async def get_imdb_movie_data(movie_id: str) -> Optional[Movie]:
    try:
        movie = ia.get_movie(
            movie_id.removeprefix("tt"), info=["main", "parents guide"]
        )
        movie.set_item(
            "parent_guide_nudity_status",
            movie.get("advisory votes", {}).get("nudity", {}).get("status"),
        )
        movie.set_item(
            "parent_guide_certificates",
            list(
                set(
                    [
                        certificate.get("certificate")
                        for certificate in movie.get("certificates", [])
                    ]
                )
            ),
        )
        movie.set_item(
            "aka_titles",
            list({title.split("(")[0].strip() for title in movie.get("aka")}),
        )
    except Exception:
        return None
    return movie


def get_imdb_rating(movie_id: str) -> Optional[float]:
    try:
        movie = ia.get_movie(movie_id.removeprefix("tt"), info=["main"])
    except Exception:
        return None
    return movie.get("rating")


def search_imdb(title: str, year: int, retry: int = 5) -> dict:
    query = title
    if year:
        query += f" {year}"
    try:
        result = ia.search_movie(query)
    except Exception:
        return search_imdb(title, year, retry - 1) if retry > 0 else {}
    if not result:
        return {}

    for movie in result:
        if movie.get("year") == year and movie.get("title").lower() in title.lower():
            imdb_id = f"tt{movie.movieID}"
            movie.set_item(
                "aka_titles",
                list({title.split("(")[0].strip() for title in movie.get("aka")}),
            )
            poster = f"https://live.metahub.space/poster/small/{imdb_id}/img"
            if requests.get(poster).status_code == 200:
                return {
                    "imdb_id": imdb_id,
                    "poster": poster.replace("small", "medium"),
                    "background": f"https://live.metahub.space/background/medium/{imdb_id}/img",
                    "title": movie.get("title"),
                }
            poster = movie.get("full-size cover url")
            return {
                "imdb_id": imdb_id,
                "poster": poster,
                "background": poster,
                "title": movie.get("title"),
            }
    return {}


async def process_imdb_data(movie_ids):
    now = datetime.now()

    # Initialize circuit breaker
    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=10, half_open_attempts=2
    )

    results = await batch_process_with_circuit_breaker(
        get_imdb_movie_data,
        movie_ids,
        5,
        rate_limit_delay=3,
        cb=circuit_breaker,
        retry_exceptions=[IMDbDataAccessError],
    )

    # Update database entries with the new data
    bulk_writer = BulkWriter()
    for movie_id, imdb_movie in zip(movie_ids, results):
        if not imdb_movie:
            logging.warning(f"Failed to fetch data for movie {movie_id}")
            continue

        if imdb_movie:
            update_data = {
                "genres": imdb_movie.get("genres"),
                "imdb_rating": imdb_movie.get("rating"),
                "parent_guide_nudity_status": imdb_movie.get(
                    "parent_guide_nudity_status"
                ),
                "parent_guide_certificates": imdb_movie.get(
                    "parent_guide_certificates"
                ),
                "aka_titles": imdb_movie.get("aka_titles"),
                "last_updated_at": now,
            }
            await MediaFusionMetaData.find({"_id": movie_id}).update(
                {"$set": update_data}, bulk_writer=bulk_writer
            )
            logging.info(f"Updating metadata for movie {movie_id}")

    logging.info(f"Committing {len(bulk_writer.operations)} updates to the database")
    await bulk_writer.commit()


@dramatiq.actor(time_limit=3 * 60 * 60 * 1000, priority=8, max_retries=3)
async def process_imdb_data_background(movie_ids):
    await process_imdb_data(movie_ids)


@dramatiq.actor(
    time_limit=60 * 1000, priority=10, max_retries=0, queue_name="scrapy"
)  # Short time limit as this should be a fast operation
async def fetch_movie_ids_to_update(*args, **kwargs):
    now = datetime.now()
    seven_days_ago = now - timedelta(days=7)

    # Fetch only the IDs of movies that need updating
    movie_documents = (
        await MediaFusionMetaData.get_motor_collection()
        .find(
            {
                "_id": {"$regex": r"tt\d+"},
                "$or": [
                    {"last_updated_at": {"$lt": seven_days_ago}},
                    {"last_updated_at": None},
                ],
            },
            {"_id": 1},
        )
        .to_list(None)
    )
    movie_ids = [doc["_id"] for doc in movie_documents]
    logging.info(f"Fetched {len(movie_ids)} movie IDs for updating")

    # Divide the IDs into chunks and send them to another actor for processing
    chunk_size = 25
    for i in range(0, len(movie_ids), chunk_size):
        chunk_ids = movie_ids[i : i + chunk_size]
        process_imdb_data_background.send(chunk_ids)
