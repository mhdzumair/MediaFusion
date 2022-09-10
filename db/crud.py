import logging
from typing import Optional
from uuid import uuid4

from imdb import Cinemagoer

from api import schemas
from db.models import TamilBlasterMovie

ia = Cinemagoer()


async def get_movies_meta(catalog: str, skip: int = 0, limit: int = 25):
    movies_meta = []
    movies = await TamilBlasterMovie.find(TamilBlasterMovie.catalog == catalog).sort("-created_at").skip(skip).limit(
        limit).to_list()

    for movie in movies:
        meta_data = schemas.Meta.parse_obj(movie)
        meta_data.id = movie.imdb_id if movie.imdb_id else movie.tamilblaster_id
        movies_meta.append(meta_data)
    return movies_meta


async def get_movie_data(video_id: str) -> Optional[TamilBlasterMovie]:
    if video_id.startswith("tt"):
        movie_data = await TamilBlasterMovie.find_one(TamilBlasterMovie.imdb_id == video_id)
    else:
        movie_data = await TamilBlasterMovie.find_one(TamilBlasterMovie.tamilblaster_id == video_id)

    return movie_data


async def get_movie_streams(video_id: str):
    movie_data = await get_movie_data(video_id)
    if not movie_data:
        return []

    stream_data = []
    for name, info_hash in movie_data.video_qualities.items():
        stream_data.append({
            "name": name,
            "infoHash": info_hash,
        })

    return stream_data


async def get_movie_meta(meta_id: str):
    movie_data = await get_movie_data(meta_id)
    if not movie_data:
        return

    return {
        "meta": {
            "id": meta_id,
            "type": "movie",
            "name": movie_data.name,
            "poster": movie_data.poster,
            "background": movie_data.poster
        }
    }


def search_imdb(title: str):
    result = ia.search_movie(title)
    for movie in result:
        if movie.get("title").lower() in title.lower():
            return f"tt{movie.movieID}"


async def save_movie_metadata(metadata: dict):
    movie_data = await TamilBlasterMovie.find_one(
        TamilBlasterMovie.name == metadata["name"], TamilBlasterMovie.catalog == metadata["catalog"]
    )

    if movie_data:
        movie_data.video_qualities.update(metadata["video_qualities"])
        movie_data.created_at = metadata["created_at"]
        logging.info(f"update video qualities for {metadata['name']}")
    else:
        movie_data = TamilBlasterMovie.parse_obj(metadata)
        movie_data.video_qualities = metadata["video_qualities"]
        imdb_id = search_imdb(movie_data.name)
        if imdb_id:
            movie_data.imdb_id = imdb_id
        else:
            movie_data.tamilblaster_id = f"tb{uuid4().fields[-1]}"

        logging.info(f"new movie '{metadata['name']}' added.")

    await movie_data.save()
