import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import dramatiq
from beanie import BulkWriter
from curl_cffi import requests
from typedload.exceptions import TypedloadValueError

from db.models import (
    MediaFusionMetaData,
)
from imdb import Cinemagoer, IMDbDataAccessError
from thefuzz import fuzz
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker

from cinemagoerng import model, web


ia = Cinemagoer()


async def get_imdb_movie_data(movie_id: str) -> Optional[model.Title]:
    try:
        movie = web.get_title(movie_id, page="main")
        web.update_title(
            movie, page="parental_guide", keys=["certification", "advisories"]
        )
        web.update_title(movie, page="akas", keys=["akas"])
    except Exception:
        return None
    return movie


def get_imdb_rating(movie_id: str) -> Optional[float]:
    try:
        movie = web.get_title(movie_id, page="main")
    except Exception:
        return None
    return movie.rating


def search_imdb(
    title: str, year: int, media_type: str = None, max_retries: int = 3
) -> dict:
    def get_poster_urls(imdb_id: str) -> tuple:
        poster = f"https://live.metahub.space/poster/medium/{imdb_id}/img"
        try:
            response = requests.head(poster, timeout=10)
            if response.status_code == 200:
                return (
                    poster,
                    poster,
                )
        except requests.RequestsError:
            pass
        return None, None

    def process_movie(movie, year: int) -> dict:
        try:
            try:
                imdb_title = web.get_title(f"tt{movie.movieID}", page="main")
            except TypedloadValueError:
                return {}
            if not imdb_title:
                return {}

            if (imdb_title.type_id != "tvSeries" and imdb_title.year != year) or (
                imdb_title.type_id == "tvSeries"
                and year is not None
                and not (imdb_title.year <= year <= (imdb_title.end_year or math.inf))
            ):
                return {}

            web.update_title(
                imdb_title, page="parental_guide", keys=["certification", "advisories"]
            )
            web.update_title(imdb_title, page="akas", keys=["akas"])

            poster_image, background_image = get_poster_urls(imdb_title.imdb_id)
            if not poster_image:
                poster_image = imdb_title.primary_image
                background_image = poster_image

            end_year = imdb_title.end_year if imdb_title.type_id == "tvSeries" else None

            return {
                "imdb_id": imdb_title.imdb_id,
                "poster": poster_image,
                "background": background_image,
                "title": imdb_title.title,
                "year": imdb_title.year,
                "end_year": end_year,
                "description": imdb_title.plot.get("en-US"),
                "genres": imdb_title.genres,
                "imdb_rating": imdb_title.rating,
                "aka_titles": list(set(aka.title for aka in imdb_title.akas)),
                "type_id": imdb_title.type_id,
                "parent_guide_nudity_status": imdb_title.advisories.nudity.status,
                "parent_guide_certificates": list(
                    set(
                        cert.certificate
                        for cert in imdb_title.certification.certificates
                    )
                ),
                "runtime": imdb_title.runtime,
            }
        except (IMDbDataAccessError, AttributeError, Exception) as e:
            logging.error(f"IMDB search: Error processing movie: {e}")
            return {}

    if media_type:
        media_type = "movie" if media_type == "movie" else "tv series"
    for attempt in range(max_retries):
        try:
            results = ia.search_movie(title)

            for movie in results:
                if media_type and movie.get("kind", "") != media_type:
                    continue

                if fuzz.ratio(movie.get("title", "").lower(), title.lower()) < 85:
                    continue

                movie_data = process_movie(movie, year)
                if movie_data:
                    return movie_data

            return {}  # No matching movie found

        except (IMDbDataAccessError, Exception) as e:
            logging.debug(f"Error in attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                logging.warning(
                    "IMDB Search Max retries reached. Returning empty dictionary."
                )
                return {}

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
                "genres": imdb_movie.genres,
                "imdb_rating": imdb_movie.rating,
                "parent_guide_nudity_status": imdb_movie.advisories.nudity.status,
                "parent_guide_certificates": list(
                    set(
                        cert.certificate
                        for cert in imdb_movie.certification.certificates
                    )
                ),
                "aka_titles": list(set(aka.title for aka in imdb_movie.akas)),
                "last_updated_at": now,
            }
            if imdb_movie.type_id == "tvSeries":
                update_data["end_year"] = imdb_movie.end_year

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
