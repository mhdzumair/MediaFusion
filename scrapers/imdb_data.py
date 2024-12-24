import logging
import math
from datetime import datetime, date
from typing import Optional

import httpx
from cinemagoerng import model, web, piculet
from cinemagoerng.model import TVSeries, SearchFilters, RangeFilter
from thefuzz import fuzz
from typedload.exceptions import TypedloadValueError

from db.config import settings
from db.models import (
    MediaFusionMetaData,
)
from utils.const import UA_HEADER
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker


async def get_imdb_title(imdb_id: str, media_type: str) -> Optional[model.Title]:
    try:
        title = await web.get_title_async(
            imdb_id, page="main", httpx_kwargs={"proxy": settings.requests_proxy_url}
        )
    except Exception:
        title = await get_imdb_data_via_cinemeta(imdb_id, media_type)

    if not title:
        return None

    try:
        web.update_title(
            title,
            page="parental_guide",
            keys=["certification", "advisories"],
            httpx_kwargs={"proxy": settings.requests_proxy_url},
        )
        web.update_title(
            title,
            page="akas",
            keys=["akas"],
            httpx_kwargs={"proxy": settings.requests_proxy_url},
        )
    except Exception:
        pass

    return title


async def get_imdb_title_data(imdb_id: str, media_type: str) -> Optional[dict]:
    imdb_title = await get_imdb_title(imdb_id, media_type)
    if not imdb_title:
        return None

    poster_image, background_image = await get_poster_urls(imdb_title.imdb_id)
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
                rating
                for cert in imdb_title.certification.certificates
                for rating in cert.ratings
            )
        ),
        "runtime": imdb_title.runtime,
    }


async def get_imdb_rating(movie_id: str) -> Optional[float]:
    try:
        title = await web.get_title_async(
            movie_id, page="main", httpx_kwargs={"proxy": settings.requests_proxy_url}
        )
        return float(title.rating) if title and title.rating else None
    except Exception:
        return None


async def get_poster_urls(imdb_id: str) -> tuple:
    poster = f"https://live.metahub.space/poster/medium/{imdb_id}/img"
    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
            response = await client.head(poster, timeout=10)
            if response.status_code == 200:
                return poster, poster
    except httpx.RequestError:
        pass
    return None, None


async def search_imdb(
    title: str, year: int | None, media_type: str = None, max_retries: int = 3
) -> dict:
    async def process_movie(imdb_title: model.Movie | model.TVSeries) -> dict:
        try:
            if (imdb_title.type_id != "tvSeries" and imdb_title.year != year) or (
                imdb_title.type_id == "tvSeries"
                and year is not None
                and not (imdb_title.year <= year <= (imdb_title.end_year or math.inf))
            ):
                return {}

            await web.update_title_async(
                imdb_title, page="parental_guide", keys=["certification", "advisories"]
            )
            await web.update_title_async(imdb_title, page="akas", keys=["akas"])

            poster_image, background_image = await get_poster_urls(imdb_title.imdb_id)
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
                        rating
                        for cert in imdb_title.certification.certificates
                        for rating in cert.ratings
                    )
                ),
                "runtime": imdb_title.runtime,
            }
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            logging.debug(f"IMDB search: Error processing movie: {e}")
            return await process_movie(imdb_title)
        except (AttributeError, Exception) as err:
            logging.error(f"IMDB search: Error processing movie: {err}")
            return {}

    title_types = ["movie", "tvSeries"]
    if media_type:
        title_types = ["movie"] if media_type == "movie" else ["tvSeries"]
    for attempt in range(max_retries):
        try:
            results = await web.search_titles_async(
                title,
                filters=SearchFilters(
                    title_types=title_types,
                    release_date=(
                        RangeFilter(min_value=year, max_value=year)
                        if media_type == "movie"
                        else RangeFilter(max_value=year)
                    ),
                ),
                count=5,
            )

            for imdb_data in results:
                if fuzz.ratio(imdb_data.title.lower(), title.lower()) < 85:
                    continue

                imdb_title_data = await process_movie(imdb_data)
                if imdb_title_data:
                    return imdb_title_data

            return {}  # No matching movie found

        except Exception as e:
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
        get_imdb_title,
        imdb_ids,
        5,
        rate_limit_delay=3,
        cb=circuit_breaker,
        media_type=metadata_type,
    ):
        if not result:
            continue

        movie_id = result.imdb_id
        imdb_rating = float(result.rating) if result.rating is not None else None
        end_year_dict = (
            {"end_year": result.end_year}
            if metadata_type == "series" and result.end_year
            else {}
        )

        # Update database entries with the new data
        await MediaFusionMetaData.find_one({"_id": movie_id}).update(
            {
                "$set": {
                    "genres": result.genres,
                    "poster": result.primary_image,
                    "description": result.plot.get("en-US"),
                    "imdb_rating": imdb_rating,
                    "parent_guide_nudity_status": result.advisories.nudity.status,
                    "parent_guide_certificates": list(
                        set(
                            rating
                            for cert in result.certification.certificates
                            for rating in cert.ratings
                        )
                    ),
                    "aka_titles": list(set(aka.title for aka in result.akas)),
                    "last_updated_at": now,
                    **end_year_dict,
                }
            },
        )
        logging.info(f"Updating metadata for movie {movie_id}")


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
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
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
