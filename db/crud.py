import logging
from typing import Optional
from uuid import uuid4

from imdb import Cinemagoer, IMDbDataAccessError

from db import schemas
from db.models import TamilBlasterMovie
from db.schemas import Stream
from utils.parser import extract_stream_details

ia = Cinemagoer()


async def get_movies_and_series_meta(catalog: str, skip: int = 0, limit: int = 100) -> list[schemas.Meta]:
    movies_meta = []
    movies = (
        await TamilBlasterMovie.find(TamilBlasterMovie.catalog == catalog)
        .sort("-created_at")
        .skip(skip)
        .limit(limit)
        .to_list()
    )

    unique_names = []

    for movie in movies:
        meta_data = schemas.Meta.model_validate(movie.model_dump())
        meta_data.id = movie.imdb_id if movie.imdb_id else movie.tamilblaster_id
        if movie.name not in unique_names:
            movies_meta.append(meta_data)
            unique_names.append(movie.name)
    return movies_meta


async def get_movies_data(video_id: str, video_type: str = "movie") -> list[Optional[TamilBlasterMovie]]:
    if video_id.startswith("tt"):
        movie_data = await TamilBlasterMovie.find(
            TamilBlasterMovie.imdb_id == video_id, TamilBlasterMovie.type == video_type
        ).to_list()
    else:
        movie_data = await TamilBlasterMovie.find(
            TamilBlasterMovie.tamilblaster_id == video_id, TamilBlasterMovie.type == video_type
        ).to_list()

    return movie_data


async def get_movie_streams(user_data, video_id: str) -> list[Stream]:
    movies_data = await get_movies_data(video_id)
    if not movies_data:
        return []

    stream_data = []
    for movie_data in movies_data:
        stream_data.extend(extract_stream_details(movie_data.name, movie_data.video_qualities, user_data))

    return stream_data


async def get_series_streams(user_data, video_id: str, season: int, episode: int) -> list[Stream]:
    series_data = await get_movies_data(video_id, video_type="series")
    if not series_data:
        return []

    stream_data = []
    for series in series_data:
        if "-" in series.episode:
            # Episode range detected in the database
            start_ep, end_ep = map(int, series.episode.split("-"))
            if start_ep <= episode <= end_ep and series.season == season:
                stream_data.extend(
                    extract_stream_details(
                        f"{series.name} {series.season}:{series.episode}", series.video_qualities, user_data
                    )
                )
        elif int(series.episode) == episode and series.season == season:
            stream_data.extend(
                extract_stream_details(f"{series.name} {series.season}:{episode}", series.video_qualities, user_data)
            )

    return stream_data


async def get_movie_meta(meta_id: str):
    movies_data = await get_movies_data(meta_id)
    if not movies_data:
        return {
            "meta": {
                "id": meta_id,
                "type": "movie",
            }
        }

    return {
        "meta": {
            "id": meta_id,
            "type": "movie",
            "name": movies_data[0].name,
            "poster": movies_data[0].poster,
            "background": movies_data[0].poster,
        }
    }


async def get_series_meta(meta_id: str):
    series_data = await get_movies_data(meta_id, video_type="series")
    if not series_data:
        return {
            "meta": {
                "id": meta_id,
                "type": "series",
            }
        }

    metadata = {
        "meta": {
            "id": meta_id,
            "type": "series",
            "name": series_data[0].name,
            "poster": series_data[0].poster,
            "background": series_data[0].poster,
            "videos": [],
        }
    }
    for series in series_data:
        if "-" in series.episode:
            # Episode range detected
            start_ep, end_ep = map(int, series.episode.split("-"))
            for ep_num in range(start_ep, end_ep + 1):
                metadata["meta"]["videos"].append(
                    {
                        "id": f"{meta_id}:{series.season}:{ep_num}",
                        "name": f"S{series.season} EP{ep_num}",
                        "season": series.season,
                        "episode": ep_num,
                        "released": series.created_at,
                    }
                )
        else:
            episode = int(series.episode)
            metadata["meta"]["videos"].append(
                {
                    "id": f"{meta_id}:{series.season}:{episode}",
                    "name": f"S{series.season} EP{episode}",
                    "season": series.season,
                    "episode": episode,
                    "released": series.created_at,
                }
            )

    return metadata


def search_imdb(title: str):
    try:
        result = ia.search_movie(title)
    except IMDbDataAccessError:
        return search_imdb(title)
    for movie in result:
        if movie.get("title").lower() in title.lower():
            return f"tt{movie.movieID}"


async def save_movie_metadata(metadata: dict):
    movie_data = await TamilBlasterMovie.find_one(
        TamilBlasterMovie.name == metadata["name"],
        TamilBlasterMovie.catalog == metadata["catalog"],
        TamilBlasterMovie.season == metadata["season"],
        TamilBlasterMovie.episode == metadata["episode"],
    )

    if movie_data:
        movie_data.video_qualities.update(metadata["video_qualities"])
        movie_data.created_at = metadata["created_at"]
        logging.debug("updated video qualities for: %s", metadata["name"])
    else:
        movie_data = TamilBlasterMovie.model_validate(metadata)
        movie_data.video_qualities = metadata["video_qualities"]

        series_data = await TamilBlasterMovie.find_one(
            TamilBlasterMovie.name == metadata["name"],
            TamilBlasterMovie.catalog == metadata["catalog"],
            TamilBlasterMovie.type == "series",
        )
        if series_data:
            movie_data.tamilblaster_id = series_data.tamilblaster_id
            movie_data.imdb_id = series_data.imdb_id
        else:
            imdb_id = search_imdb(movie_data.name)
            if any(
                [
                    metadata["type"] == "series" and metadata["episode"].isdigit() and imdb_id,
                    all([metadata["type"] == "movie", imdb_id]),
                ]
            ):
                movie_data.imdb_id = imdb_id
            else:
                movie_data.tamilblaster_id = f"tb{uuid4().fields[-1]}"

        logging.info(f"new movie '{metadata['name']}' added.")

    await movie_data.save()


async def process_search_query(search_query: str, catalog_type: str) -> dict:
    query = {"$text": {"$search": search_query}, "type": catalog_type}
    search_results = await TamilBlasterMovie.find(query).to_list()
    logging.debug("Found %s results for %s in %s", len(search_results), search_query, catalog_type)

    movies_meta = []
    unique_names = []

    for movie in search_results:
        meta_data = schemas.Meta.model_validate(movie.model_dump())
        meta_data.id = movie.imdb_id or movie.tamilblaster_id
        if movie.name not in unique_names:
            movies_meta.append(meta_data)
            unique_names.append(movie.name)

    return {"metas": movies_meta}
