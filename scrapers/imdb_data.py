import logging
import math
import re
from datetime import datetime, date
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple

import httpx
from cinemagoerng import model, web, piculet
from cinemagoerng.model import TVSeries, SearchFilters, RangeFilter
from thefuzz import fuzz
from typedload.exceptions import TypedloadValueError

from db.config import settings
from db.models import (
    SeriesEpisode,
)
from utils.const import UA_HEADER


async def get_imdb_title(imdb_id: str, media_type: str) -> Optional[model.Title]:
    try:
        title = await web.get_title_async(
            imdb_id, page="main", httpx_kwargs={"proxy": settings.requests_proxy_url}
        )
        if media_type == "series":
            await web.update_title_async(
                title,
                page="episodes_with_pagination",
                keys=["episodes"],
                paginate_result=True,
            )
    except Exception:
        title = await get_imdb_data_via_cinemeta(imdb_id, media_type)

    if not title:
        return None

    if media_type == "movie" and title.type_id not in [
        "movie",
        "tvMovie",
        "short",
        "tvShort",
        "tvSpecial",
    ]:
        logging.warning(f"IMDB ID {imdb_id} is not a movie. found {title.type_id}")
        return None
    elif media_type == "series" and title.type_id not in ["tvSeries", "tvMiniSeries"]:
        logging.warning(f"IMDB ID {imdb_id} is not a series. found {title.type_id}")
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
            paginate_result=True,
        )
        # Look for worldwide title
        title.title = next(
            (
                aka.title
                for aka in title.akas
                if aka.country_code == "XWW" and aka.language_code == "en"
            ),
            title.title,
        )
    except Exception:
        pass

    return title


class ImageType(Enum):
    POSTER = "poster"  # High res for Pillow resizing to 300x450
    BACKGROUND = "bg"  # Full HD background
    THUMBNAIL = "thumb"  # Medium resolution thumbnail
    CUSTOM = "custom"  # For custom dimensions


def modify_imdb_image_url(
    image_url: str,
    image_type: ImageType = ImageType.POSTER,
    custom_width: Optional[int] = None,
    custom_height: Optional[int] = None,
) -> str | None:
    """
    Modify IMDb image URL based on desired type and dimensions.
    Optimized for different use cases:
    - POSTER: Returns higher resolution for better quality after Pillow resize
    - BACKGROUND: Returns direct FHD resolution
    - THUMBNAIL: Returns medium resolution around 500px
    """
    if not image_url:
        return
    pattern = r"images/M/([^.]+)\..*"
    match = re.search(pattern, image_url)
    if not match:
        return

    image_id = match.group(1)
    base_url = "https://m.media-amazon.com/images/M/"

    if image_type == ImageType.POSTER:
        # High resolution for better quality after Pillow resize to 300x450
        # Use 900px height which is double the target for better quality
        return f"{base_url}{image_id}._V1_SY900_QL75_.jpg"

    elif image_type == ImageType.BACKGROUND:
        # Direct FHD resolution
        return f"{base_url}{image_id}._V1_UX1920_.jpg"

    elif image_type == ImageType.THUMBNAIL:
        # Medium resolution around 500px
        return f"{base_url}{image_id}._V1_SY500_QL75_.jpg"

    else:  # CUSTOM
        if not (custom_width and custom_height):
            raise ValueError("Custom width and height required for CUSTOM type")

        if custom_width > custom_height:
            return f"{base_url}{image_id}._V1_UX{custom_width}_QL75_.jpg"
        else:
            return f"{base_url}{image_id}._V1_SY{custom_height}_QL75_.jpg"


async def get_imdb_title_data(imdb_id: str, media_type: str) -> Optional[dict]:
    imdb_title = await get_imdb_title(imdb_id, media_type)
    if not imdb_title:
        return None
    episodes = []
    if imdb_title.type_id in ["tvSeries", "tvMiniSeries"]:
        episodes = [
            SeriesEpisode(
                season_number=episode.season,
                episode_number=episode.episode,
                title=episode.title,
                overview=episode.plot.get("en-US"),
                released=episode.release_date,
                imdb_rating=episode.rating,
                thumbnail=modify_imdb_image_url(
                    episode.primary_image, ImageType.THUMBNAIL
                )
                or f"https://episodes.metahub.space/{episode.imdb_id}/{episode.season}/{episode.episode}/w780.jpg",
            ).model_dump()
            for episode in imdb_title.episodes
        ]
    return parse_imdb_title(imdb_title, episodes)


def parse_imdb_title(imdb_title: model.Title, episodes: list[dict]) -> dict:
    end_year = None
    if imdb_title.type_id in ["tvSeries", "tvMiniSeries"]:
        end_year = imdb_title.end_year

    return {
        "imdb_id": imdb_title.imdb_id,
        "poster": modify_imdb_image_url(imdb_title.primary_image, ImageType.POSTER)
        or f"https://live.metahub.space/poster/small/{imdb_title.imdb_id}/img",
        "background": imdb_title.primary_image,
        "logo": f"https://live.metahub.space/logo/medium/{imdb_title.imdb_id}/img",
        "title": imdb_title.title,
        "year": imdb_title.year,
        "end_year": end_year,
        "description": imdb_title.plot.get("en-US"),
        "genres": imdb_title.genres,
        "imdb_rating": float(imdb_title.rating) if imdb_title.rating else None,
        "aka_titles": list(set(aka.title for aka in imdb_title.akas)),
        "type": (
            "movie"
            if imdb_title.type_id
            in ["movie", "tvMovie", "short", "tvShort", "tvSpecial"]
            else "series"
        ),
        "parent_guide_nudity_status": imdb_title.advisories.nudity.status,
        "parent_guide_certificates": list(
            set(
                rating
                for cert in imdb_title.certification.certificates
                for rating in cert.ratings
            )
        ),
        "runtime": f"{imdb_title.runtime} min" if imdb_title.runtime else None,
        "episodes": episodes,
        "stars": [cast.name for cast in imdb_title.cast],
    }


async def get_imdb_rating(movie_id: str) -> Optional[float]:
    try:
        title = await web.get_title_async(
            movie_id, page="main", httpx_kwargs={"proxy": settings.requests_proxy_url}
        )
        return float(title.rating) if title and title.rating else None
    except Exception:
        return None


async def search_imdb(
    title: str,
    year: int | None,
    media_type: str = None,
    max_retries: int = 3,
    created_year: int | None = None,
) -> dict:
    """
    Search for a movie or TV show on IMDB with strict year validation.
    When year is provided, only exact matches are considered.
    When year is None, results are sorted by proximity to created_year.
    """

    def calculate_year_difference(
        imdb_title: model.Movie | model.TVSeries | model.TVMiniSeries,
    ) -> float | None:
        """Calculate year difference score for sorting"""
        try:
            title_year = getattr(imdb_title, "year", None)
            if title_year is None:
                return None

            # If target year is provided, only allow exact matches
            if year is not None:
                if imdb_title.type_id in [
                    "movie",
                    "tvMovie",
                    "short",
                    "tvShort",
                    "tvSpecial",
                ]:
                    return 0 if title_year == year else None
                else:
                    end_year = getattr(imdb_title, "end_year", None)
                    # If it's an ongoing series, we can use math.inf for end_year
                    if end_year is None:
                        end_year = math.inf if title_year <= year else None

                    # Only proceed if we have valid end_year
                    if end_year is not None:
                        return 0 if (title_year <= year <= end_year) else float("inf")
                    return None

            # Only use created_year for sorting when target year is None
            if created_year:
                if imdb_title.type_id in [
                    "movie",
                    "tvMovie",
                    "short",
                    "tvShort",
                    "tvSpecial",
                ]:
                    return abs(title_year - created_year)
                else:
                    # For series, use the minimum difference between created_year
                    # and either start or end year
                    end_year = getattr(imdb_title, "end_year", None)
                    if end_year is None:
                        # If series is ongoing/unknown end, just use start year difference
                        return abs(title_year - created_year)
                    return min(
                        abs(title_year - created_year), abs(end_year - created_year)
                    )

            return float("inf")  # No year information available

        except (AttributeError, Exception) as err:
            logging.error(f"IMDB search: Error calculating year difference: {err}")
            return float("inf")

    title_types = [
        "movie",
        "tvMovie",
        "short",
        "tvShort",
        "tvSpecial",
        "tvSeries",
        "tvMiniSeries",
    ]
    if media_type:
        title_types = (
            ["movie", "tvMovie", "short", "tvShort", "tvSpecial"]
            if media_type == "movie"
            else ["tvSeries", "tvMiniSeries"]
        )

    for attempt in range(max_retries):
        try:
            # Only add year filter if year is specifically provided
            search_filters = SearchFilters(title_types=title_types)
            if year is not None:
                if media_type in ["movie", "tvMovie", "short", "tvShort", "tvSpecial"]:
                    search_filters.release_date = RangeFilter(
                        min_value=year, max_value=year
                    )
                else:
                    search_filters.release_date = RangeFilter(max_value=year)

            results = await web.search_titles_async(
                title,
                filters=search_filters,
                count=10,
            )

            # Filter by title similarity and calculate year differences
            candidates = []
            for imdb_data in results:
                if fuzz.ratio(imdb_data.title.lower(), title.lower()) < 85:
                    continue

                year_diff = calculate_year_difference(imdb_data)
                if year_diff is not None:  # Only include valid matches
                    candidates.append((imdb_data, year_diff))

            # Sort candidates by year difference
            candidates.sort(key=lambda x: x[1])

            # Only fetch full data for the best match
            if candidates:
                best_match = candidates[0][0]
                try:
                    return await get_imdb_title_data(
                        best_match.imdb_id, best_match.type_id
                    )
                except Exception as err:
                    logging.error(f"IMDB search: Error fetching best match data: {err}")

            return {}

        except Exception as e:
            logging.debug(f"Error in attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                logging.warning(
                    "IMDB Search Max retries reached. Returning empty dictionary."
                )
                return {}

    return {}


async def get_episode_by_date(
    series_id: str, series_title: str, expected_date: date
) -> Optional[model.TVEpisode]:
    imdb_title = TVSeries(imdb_id=series_id, title=series_title)
    await web.update_title_async(
        imdb_title,
        page="episodes_with_pagination",
        keys=["episodes"],
        filter_type="year",
        start_year=expected_date.year,
        end_year=expected_date.year,
        paginate_result=True,
    )
    filtered_episode = [
        ep for ep in imdb_title.episodes if ep.release_date == expected_date
    ]
    if not filtered_episode:
        return
    return filtered_episode[0]


async def get_all_episodes(series_id: str, series_title: str) -> list[SeriesEpisode]:
    imdb_title = TVSeries(imdb_id=series_id, title=series_title)
    await web.update_title_async(
        imdb_title,
        page="episodes_with_pagination",
        keys=["episodes"],
        paginate_result=True,
    )
    return [
        SeriesEpisode(
            season_number=episode.season,
            episode_number=episode.episode,
            title=episode.title,
            overview=episode.plot.get("en-US"),
            released=episode.release_date,
            imdb_rating=episode.rating,
            thumbnail=episode.primary_image,
        )
        for episode in imdb_title.episodes
    ]


async def get_season_episodes(
    series_id: str, series_title: str, season: int
) -> list[model.TVEpisode]:
    imdb_title = TVSeries(imdb_id=series_id, title=series_title)
    await web.update_title_async(
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
            "type_id": "movie" if media_type == "movie" else "tvSeries",
            "cast": [{"name": cast, "imdb_id": ""} for cast in data.get("cast", [])],
            "runtime": data["runtime"] if data.get("runtime") else None,
        }
    )
    year = data.get("releaseInfo", "").split("–")
    if len(year) == 2:
        data["year"] = int(year[0])
        data["end_year"] = int(year[1]) if year[1] else None
    elif len(year) == 1:
        data["year"] = int(year[0])
    if media_type == "series":
        episode_data = []
        for video in data.get("videos", []):
            release_date = datetime.strptime(
                video["released"], "%Y-%m-%dT%H:%M:%S.%fZ"
            ).date()
            episode_data.append(
                {
                    "title": video.get("title") or video.get("name"),
                    "type_id": "tvEpisode",
                    "imdb_id": video["id"].split(":")[0],
                    "release_date": release_date.isoformat(),
                    "year": release_date.year,
                    "season": video["season"],
                    "episode": video["episode"],
                    "plot": {"en-US": video.get("overview")},
                    "rating": video.get("imdbRating"),
                    "primary_image": video.get("thumbnail"),
                }
            )
        data["episodes"] = episode_data
    try:
        return piculet.deserialize(data, model.Title)
    except TypedloadValueError as err:
        return None


async def search_multiple_imdb(
    title: str,
    limit: int = 5,
    year: Optional[int] = None,
    media_type: Optional[str] = None,
    created_year: Optional[int] = None,
    min_similarity: int = 60,
) -> List[Dict[str, Any]]:
    """
    Search for multiple matching titles on IMDB.
    Similar to existing search_imdb but returns multiple results.
    """

    def calculate_year_score(
        imdb_title: model.Movie | model.TVSeries | model.TVMiniSeries,
    ) -> Tuple[bool, float]:
        """Calculate year match score and validity"""
        try:
            title_year = getattr(imdb_title, "year", None)
            if title_year is None:
                return False, float("inf")

            if year is not None:
                if imdb_title.type_id in [
                    "movie",
                    "tvMovie",
                    "short",
                    "tvShort",
                    "tvSpecial",
                ]:
                    return title_year == year, 0 if title_year == year else float("inf")
                else:
                    end_year = getattr(imdb_title, "end_year", None)
                    if end_year is None:
                        end_year = math.inf if title_year <= year else None

                    if end_year is not None:
                        return (title_year <= year <= end_year), (
                            0 if (title_year <= year <= end_year) else float("inf")
                        )
                    return False, float("inf")

            if created_year:
                if imdb_title.type_id in [
                    "movie",
                    "tvMovie",
                    "short",
                    "tvShort",
                    "tvSpecial",
                ]:
                    return True, abs(title_year - created_year)
                else:
                    end_year = getattr(imdb_title, "end_year", None)
                    if end_year is None:
                        return True, abs(title_year - created_year)
                    return True, min(
                        abs(title_year - created_year), abs(end_year - created_year)
                    )

            return True, 0  # No year criteria

        except Exception as err:
            logging.error(f"IMDB search: Error calculating year score: {err}")
            return False, float("inf")

    title_types = [
        "movie",
        "tvMovie",
        "short",
        "tvShort",
        "tvSpecial",
        "tvSeries",
        "tvMiniSeries",
    ]
    if media_type:
        title_types = (
            ["movie", "tvMovie", "short", "tvShort", "tvSpecial"]
            if media_type == "movie"
            else ["tvSeries", "tvMiniSeries"]
        )

    try:
        search_filters = SearchFilters(title_types=title_types)
        if year is not None:
            if media_type in ["movie", "tvMovie", "short", "tvShort", "tvSpecial"]:
                search_filters.release_date = RangeFilter(
                    min_value=year, max_value=year
                )
            else:
                search_filters.release_date = RangeFilter(max_value=year)

        results = await web.search_titles_async(
            title,
            filters=search_filters,
            count=20,  # Request more to account for filtering
        )

        candidates = []
        for imdb_data in results:
            similarity = fuzz.ratio(imdb_data.title.lower(), title.lower())
            if similarity < min_similarity:
                continue

            valid_year, year_score = calculate_year_score(imdb_data)
            if not valid_year:
                continue

            # Combine similarity and year score for ranking
            combined_score = (similarity / 100.0) * (1.0 / (1.0 + year_score))
            candidates.append((imdb_data, combined_score))

        # Sort by combined score and take top results
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_candidates = candidates[:limit]

        full_results = []
        for candidate, _ in top_candidates:
            try:
                result = parse_imdb_title(candidate, [])
                full_results.append(result)
            except Exception as err:
                logging.error(f"Error fetching IMDB data for candidate: {err}")

        return full_results

    except Exception as e:
        logging.error(f"Error in IMDB search: {e}")
        return []
