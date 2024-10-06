import logging
from datetime import datetime, timedelta, date
from typing import Optional

import dramatiq
import httpx
import math
from cinemagoerng import model, web, piculet
from cinemagoerng.model import TVSeries
from curl_cffi import requests
from imdb import Cinemagoer, IMDbDataAccessError
from thefuzz import fuzz
from typedload.exceptions import TypedloadValueError

from db.models import (
    MediaFusionMetaData,
)
from utils.const import UA_HEADER
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker

ia = Cinemagoer()


async def get_imdb_movie_data(imdb_id: str, media_type: str) -> Optional[model.Title]:
    try:
        title = web.get_title(imdb_id, page="main")
    except Exception:
        title = await get_imdb_data_via_cinemeta(imdb_id, media_type)

    if not title:
        return None

    try:
        web.update_title(
            title, page="parental_guide", keys=["certification", "advisories"]
        )
        web.update_title(title, page="akas", keys=["akas"])
    except Exception:
        pass

    return title


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


async def process_imdb_data(imdb_ids: list[str], metadata_type: str):
    now = datetime.now()

    # Initialize circuit breaker
    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=10, half_open_attempts=2
    )

    async for result in batch_process_with_circuit_breaker(
        get_imdb_movie_data,
        imdb_ids,
        5,
        rate_limit_delay=3,
        cb=circuit_breaker,
        retry_exceptions=[IMDbDataAccessError],
        media_type=metadata_type,
    ):
        if not result:
            continue

        movie_id = result.imdb_id
        imdb_rating = float(result.rating)
        end_year_dict = (
            {"end_year": result.end_year} if metadata_type == "series" else {}
        )

        # Update database entries with the new data
        await MediaFusionMetaData.get_motor_collection().update_one(
            {"_id": movie_id},
            {
                "$set": {
                    "genres": result.genres,
                    "imdb_rating": imdb_rating,
                    "parent_guide_nudity_status": result.advisories.nudity.status,
                    "parent_guide_certificates": list(
                        set(
                            cert.certificate
                            for cert in result.certification.certificates
                        )
                    ),
                    "aka_titles": list(set(aka.title for aka in result.akas)),
                    "last_updated_at": now,
                    **end_year_dict,
                }
            },
        )
        logging.info(f"Updating metadata for movie {movie_id}")


@dramatiq.actor(time_limit=3 * 60 * 60 * 1000, priority=8, max_retries=3)
async def process_imdb_data_background(imdb_ids, metadata_type="movie"):
    await process_imdb_data(imdb_ids, metadata_type)


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
            {"_id": 1, "type": 1},
        )
        .to_list(None)
    )
    movie_ids = []
    series_ids = []
    for movie in movie_documents:
        if movie["type"] == "movie":
            movie_ids.append(movie["_id"])
        elif movie["type"] == "series":
            series_ids.append(movie["_id"])
    logging.info(
        f"Fetched {len(movie_ids)} movie and {len(series_ids)} series IDs for updating"
    )

    # Divide the IDs into chunks and send them to another actor for processing
    chunk_size = 25
    for i in range(0, len(movie_ids), chunk_size):
        chunk_ids = movie_ids[i : i + chunk_size]
        process_imdb_data_background.send(chunk_ids, metadata_type="movie")

    for i in range(0, len(series_ids), chunk_size):
        chunk_ids = series_ids[i : i + chunk_size]
        process_imdb_data_background.send(chunk_ids, metadata_type="series")


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


async def get_imdb_data_via_cinemeta(
    title_id: str, media_type: str
) -> Optional[model.Title]:
    url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{title_id}.json"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url, timeout=10, headers=UA_HEADER, follow_redirects=True
            )
            response.raise_for_status()
    except httpx.RequestError as e:
        logging.error(f"Error fetching Cinemeta data: {e}")
        return None

    data = response.json()["meta"]
    data.update(
        {
            "title": data["name"],
            "rating": data["imdbRating"] if data.get("imdbRating") else None,
            "primary_image": data["poster"],
            "plot": {"en-US": data["description"]},
            "type_id": "tvSeries" if media_type == "series" else "movie",
            "cast": [{"name": cast, "imdb_id": ""} for cast in data.get("cast", [])],
            "runtime": (
                int(data["runtime"].split(" ")[0]) if data.get("runtime") else None
            ),
        }
    )
    year = data.get("year", "").split("–")
    if len(year) == 2:
        year, end_year = int(year[0]), int(year[1])
        data["year"] = year
        data["end_year"] = end_year
    elif len(year) == 1:
        data["year"] = int(year[0])
    if media_type == "series":
        episode_data = {}
        for video in data.get("videos", []):
            season = str(video["season"])
            episode = str(video["episode"])
            release_date = datetime.strptime(
                video["released"], "%Y-%m-%dT%H:%M:%S.%fZ"
            ).date()
            if season not in episode_data:
                episode_data[season] = {}
            episode_data[season][episode] = {
                "title": video["name"],
                "type_id": "tvEpisode",
                "imdb_id": "",
                "release_date": release_date.isoformat(),
                "year": release_date.year,
                "season": season,
                "episode": episode,
            }
        data["episodes"] = episode_data
    try:
        return piculet.deserialize(data, model.Title)
    except TypedloadValueError:
        return None
