import logging
import math
import re
from datetime import date, datetime
from enum import Enum
from typing import Any

import httpx
from cinemagoerng import model, piculet, web
from cinemagoerng.model import RangeFilter, SearchFilters, TVSeries
from thefuzz import fuzz
from typedload.exceptions import TypedloadValueError

from db.config import settings
from db.schemas import SeriesEpisodeData
from utils.const import UA_HEADER


async def get_imdb_title(imdb_id: str, media_type: str) -> model.Title | None:
    try:
        title = await web.get_title_async(imdb_id, httpx_kwargs={"proxy": settings.requests_proxy_url})
        if media_type == "series":
            await web.set_all_episodes_async(
                title,
                httpx_kwargs={"proxy": settings.requests_proxy_url},
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
        "video",  # Music videos, direct-to-video releases, etc.
    ]:
        logging.warning(f"IMDB ID {imdb_id} is not a movie. found {title.type_id}")
        return None
    elif media_type == "series" and title.type_id not in ["tvSeries", "tvMiniSeries"]:
        logging.warning(f"IMDB ID {imdb_id} is not a series. found {title.type_id}")
        return None

    try:
        await web.set_parental_guide_async(
            title,
            httpx_kwargs={"proxy": settings.requests_proxy_url},
        )
        await web.set_akas_async(
            title,
            httpx_kwargs={"proxy": settings.requests_proxy_url},
        )
        # Look for worldwide title
        title.title = next(
            (aka.title for aka in title.akas if aka.country_code == "XWW" and aka.language_code == "en"),
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
    custom_width: int | None = None,
    custom_height: int | None = None,
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


async def get_imdb_title_data(imdb_id: str, media_type: str) -> dict | None:
    imdb_title = await get_imdb_title(imdb_id, media_type)
    if not imdb_title:
        return None
    episodes = []
    # imdb_title.episodes structure is now: dict[season_num][episode_num] = TVEpisode
    if imdb_title.type_id in ["tvSeries", "tvMiniSeries"]:
        episodes = []
        for episodes_in_season in imdb_title.episodes.values():
            for episode in episodes_in_season.values():
                episodes.append(
                    SeriesEpisodeData(
                        season_number=episode.season,
                        episode_number=episode.episode,
                        title=episode.title,
                        overview=episode.plot.get("en-US") if hasattr(episode, "plot") and episode.plot else None,
                        released=episode.release_date,
                        imdb_rating=episode.rating if hasattr(episode, "rating") else None,
                        thumbnail=modify_imdb_image_url(getattr(episode, "primary_image", None), ImageType.THUMBNAIL)
                        or (
                            f"https://episodes.metahub.space/{episode.imdb_id}/{episode.season}/{episode.episode}/w780.jpg"
                            if hasattr(episode, "imdb_id") and episode.imdb_id and episode.season and episode.episode
                            else None
                        ),
                    ).model_dump()
                )
    return parse_imdb_title(imdb_title, episodes)


def parse_imdb_title(imdb_title: model.Title, episodes: list[dict]) -> dict:
    """Parse IMDB title data into a standardized dictionary format.

    Extracts comprehensive metadata including cast, crew, ratings, and images.
    """
    end_year = None
    if imdb_title.type_id in ["tvSeries", "tvMiniSeries"]:
        end_year = imdb_title.end_year

    # Parse cast with full details (character name, imdb_id)
    cast_list = []
    for idx, credit in enumerate(imdb_title.cast[:20]):  # Top 20 cast
        cast_list.append(
            {
                "name": credit.name,
                "imdb_id": credit.imdb_id,
                "characters": credit.characters,
                "order": idx,
            }
        )

    # Parse crew by department
    crew_list = []
    crew_departments = [
        ("directors", "Directing", "Director"),
        ("writers", "Writing", "Writer"),
        ("producers", "Production", "Producer"),
        ("composers", "Sound", "Composer"),
        ("cinematographers", "Camera", "Cinematographer"),
        ("editors", "Editing", "Editor"),
    ]
    for attr_name, department, job in crew_departments:
        for credit in getattr(imdb_title, attr_name, [])[:5]:  # Top 5 per department
            crew_list.append(
                {
                    "name": credit.name,
                    "imdb_id": credit.imdb_id,
                    "department": department,
                    "job": job,
                }
            )

    # Get tagline (first one if available)
    tagline = imdb_title.taglines[0] if imdb_title.taglines else None

    # Build images list
    images = []
    if imdb_title.primary_image:
        images.append(
            {
                "type": "poster",
                "url": modify_imdb_image_url(imdb_title.primary_image, ImageType.POSTER),
                "provider": "imdb",
                "is_primary": True,
            }
        )
        images.append(
            {
                "type": "background",
                "url": imdb_title.primary_image,  # Original high-res
                "provider": "imdb",
                "is_primary": True,
            }
        )
    # Add metahub fallback images
    images.append(
        {
            "type": "poster",
            "url": f"https://live.metahub.space/poster/small/{imdb_title.imdb_id}/img",
            "provider": "metahub",
            "is_primary": False,
        }
    )
    images.append(
        {
            "type": "logo",
            "url": f"https://live.metahub.space/logo/medium/{imdb_title.imdb_id}/img",
            "provider": "metahub",
            "is_primary": True,
        }
    )

    certificates = []
    if imdb_title.certification and imdb_title.certification.certificates:
        certificates = list(set(rating for cert in imdb_title.certification.certificates for rating in cert.ratings))

    return {
        "imdb_id": imdb_title.imdb_id,
        "poster": modify_imdb_image_url(imdb_title.primary_image, ImageType.POSTER)
        or f"https://live.metahub.space/poster/small/{imdb_title.imdb_id}/img",
        "background": imdb_title.primary_image,
        "logo": f"https://live.metahub.space/logo/medium/{imdb_title.imdb_id}/img",
        "title": imdb_title.title,
        "year": imdb_title.year,
        "end_year": end_year,
        "release_date": imdb_title.release_date,
        "description": imdb_title.plot.get("en-US") if imdb_title.plot else None,
        "tagline": tagline,
        "countries": imdb_title.countries,
        "country_codes": imdb_title.country_codes,
        "languages": imdb_title.languages,
        "language_codes": imdb_title.language_codes,
        "genres": imdb_title.genres,
        "imdb_rating": float(imdb_title.rating) if imdb_title.rating else None,
        "imdb_vote_count": imdb_title.vote_count,
        "top_ranking": imdb_title.top_ranking,  # IMDB Top 250 rank
        "aka_titles": list(set(aka.title for aka in imdb_title.akas)),
        "type": ("movie" if imdb_title.type_id in ["movie", "tvMovie", "short", "tvShort", "tvSpecial"] else "series"),
        "parent_guide_nudity_status": imdb_title.advisories.nudity.status
        if imdb_title.advisories and imdb_title.advisories.nudity
        else None,
        "parent_guide_certificates": certificates,
        "runtime": f"{imdb_title.runtime} min" if imdb_title.runtime else None,
        "runtime_minutes": imdb_title.runtime,
        "episodes": episodes,
        "stars": [cast.name for cast in imdb_title.cast[:10]],  # Keep for backward compatibility
        "cast": cast_list,
        "crew": crew_list,
        "images": images,
    }


async def get_imdb_rating(movie_id: str) -> float | None:
    try:
        title = await web.get_title_async(movie_id, httpx_kwargs={"proxy": settings.requests_proxy_url})
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
                    return min(abs(title_year - created_year), abs(end_year - created_year))

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
                    search_filters.release_date = RangeFilter(min_value=year, max_value=year)
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
                    return await get_imdb_title_data(best_match.imdb_id, best_match.type_id)
                except Exception as err:
                    logging.error(f"IMDB search: Error fetching best match data: {err}")

            return {}

        except Exception as e:
            logging.debug(f"Error in attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                logging.warning("IMDB Search Max retries reached. Returning empty dictionary.")
                return {}

    return {}


async def get_episode_by_date(series_id: str, series_title: str, expected_date: date) -> model.TVEpisode | None:
    imdb_title = TVSeries(imdb_id=series_id, title=series_title)
    await web.set_all_episodes_async(
        imdb_title,
        year_from=expected_date.year,
        year_to=expected_date.year,
        httpx_kwargs={"proxy": settings.requests_proxy_url},
    )

    filtered_episode = [ep for ep in imdb_title.episodes if ep.release_date == expected_date]
    if not filtered_episode:
        return
    return filtered_episode[0]


async def get_all_episodes(series_id: str, series_title: str) -> list[SeriesEpisodeData]:
    imdb_title = TVSeries(imdb_id=series_id, title=series_title)
    await web.set_all_episodes_async(
        imdb_title,
        httpx_kwargs={"proxy": settings.requests_proxy_url},
    )
    episodes = []
    for episodes_in_season in imdb_title.episodes.values():
        for episode in episodes_in_season.values():
            episodes.append(
                SeriesEpisodeData(
                    season_number=episode.season,
                    episode_number=episode.episode,
                    title=episode.title,
                    overview=episode.plot.get("en-US"),
                    released=episode.release_date,
                    imdb_rating=episode.rating,
                    thumbnail=episode.primary_image,
                )
            )
    return episodes


async def get_season_episodes(series_id: str, series_title: str, season: int) -> list[model.TVEpisode]:
    imdb_title = TVSeries(imdb_id=series_id, title=series_title)
    await web.set_all_episodes_async(
        imdb_title,
        seasons=[str(season)],
        httpx_kwargs={"proxy": settings.requests_proxy_url},
    )
    return list(imdb_title.episodes.get(str(season), {}).values()) or []


async def get_imdb_data_via_cinemeta(title_id: str, media_type: str) -> model.Title | None:
    url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{title_id}.json"
    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
            response = await client.get(url, timeout=10, headers=UA_HEADER, follow_redirects=True)
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
            release_date = datetime.strptime(video["released"], "%Y-%m-%dT%H:%M:%S.%fZ").date()
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
    except TypedloadValueError:
        return None


async def search_multiple_imdb(
    title: str,
    limit: int = 5,
    year: int | None = None,
    media_type: str | None = None,
    created_year: int | None = None,
    min_similarity: int = 60,
) -> list[dict[str, Any]]:
    """
    Search for multiple matching titles on IMDB.
    Similar to existing search_imdb but returns multiple results.
    """

    def calculate_year_score(
        imdb_title: model.Movie | model.TVSeries | model.TVMiniSeries,
    ) -> tuple[bool, float]:
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
                    return True, min(abs(title_year - created_year), abs(end_year - created_year))

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
            # media_type is "movie" or "series", not IMDb type IDs
            if media_type == "movie":
                search_filters.release_date = RangeFilter(min_value=year, max_value=year)
            else:
                # For series, search for titles that started on or before this year
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
                result = await get_imdb_title_data(candidate.imdb_id, candidate.type_id)
                if result:
                    # Add source provider marker
                    result["_source_provider"] = "imdb"
                    full_results.append(result)
            except Exception as err:
                logging.exception(f"Error fetching IMDB data for candidate: {err}")

        return full_results

    except Exception as e:
        logging.error(f"Error in IMDB search: {e}")
        return []
