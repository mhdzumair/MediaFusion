import logging
from datetime import datetime, timedelta, date
from typing import Optional

import dramatiq
import math
from cinemagoerng import model, web
from cinemagoerng.model import TVSeries
from curl_cffi import requests
from imdb import Cinemagoer, IMDbDataAccessError
from thefuzz import fuzz
from typedload.exceptions import TypedloadValueError

from db.models import (
    MediaFusionMetaData,
)
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker

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

    async for result in batch_process_with_circuit_breaker(
        get_imdb_movie_data,
        movie_ids,
        5,
        rate_limit_delay=3,
        cb=circuit_breaker,
        retry_exceptions=[IMDbDataAccessError],
    ):
        if not result:
            continue

        movie_id = result.imdb_id
        imdb_rating = get_imdb_rating(movie_id)
        end_year_dict = (
            {"end_year": result.end_year} if result.type_id == "tvSeries" else {}
        )

        # Update database entries with the new data
        await MediaFusionMetaData.find({"_id": movie_id}).update(
            {
                "genres": result.genres,
                "imdb_rating": imdb_rating,
                "parent_guide_nudity_status": result.advisories.nudity.status,
                "parent_guide_certificates": list(
                    set(cert.certificate for cert in result.certification.certificates)
                ),
                "aka_titles": list(set(aka.title for aka in result.akas)),
                "last_updated_at": now,
                **end_year_dict,
            }
        )
        logging.info(f"Updating metadata for movie {movie_id}")


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


async def get_episode_by_date(
    series_id: str, series_title: str, expected_date: date
) -> Optional[model.TVEpisode]:
    imdb_title = TVSeries(imdb_id=series_id, title=series_title)
    web.update_title(
        imdb_title,
        page="episodes_with_pagination",
        keys=["episodes"],
        filter_type="year",
        start_year=expected_date.year,
        end_year=expected_date.year,
        paginate_result=True,
    )
    filtered_episode = [
        ep
        for season in imdb_title.episodes.values()
        for ep in season.values()
        if ep.release_date == expected_date
    ]
    if not filtered_episode:
        return
    return filtered_episode[0]


async def get_season_episodes(
    series_id: str, series_title: str, season: str
) -> list[model.TVEpisode]:
    imdb_title = TVSeries(imdb_id=series_id, title=series_title)
    web.update_title(
        imdb_title,
        page="episodes_with_pagination",
        keys=["episodes"],
        filter_type="season",
        season=season,
        paginate_result=True,
    )
    return imdb_title.get_episodes_by_season(season)
