import logging
from uuid import uuid4

from imdb import Cinemagoer
from sqlalchemy.orm import Session

import schemas
from models import TamilBlasterMovie


ia = Cinemagoer()


def get_movies_meta(db: Session, catalog: str, skip: int = 0, limit: int = 25):
    movies_meta = []
    language, video_type = catalog.split("_")
    movies = db.query(TamilBlasterMovie).filter(
        TamilBlasterMovie.language == language,
        TamilBlasterMovie.video_type == video_type,
    ).order_by(TamilBlasterMovie.id.desc()).offset(skip).limit(limit).all()

    for movie in movies:
        meta_data = schemas.Meta.from_orm(movie)
        meta_data.id = movie.imdb_id if movie.imdb_id else movie.tamilblaster_id
        movies_meta.append(meta_data)
    return movies_meta


def get_movie_data(db: Session, video_id: str) -> TamilBlasterMovie | None:
    if video_id.startswith("tt"):
        movie_data = db.query(TamilBlasterMovie).filter(TamilBlasterMovie.imdb_id == video_id).order_by(
            TamilBlasterMovie.created_at.desc()
        ).first()
    else:
        movie_data = db.query(TamilBlasterMovie).filter(TamilBlasterMovie.tamilblaster_id == video_id).order_by(
            TamilBlasterMovie.created_at.desc()
        ).first()

    return movie_data


def get_movie_streams(db: Session, video_id: str):
    movie_data = get_movie_data(db, video_id)
    if not movie_data:
        return []

    stream_data = []
    for name, info_hash in movie_data.video_qualities.items():
        stream_data.append({
            "name": name,
            "infoHash": info_hash,
        })

    return stream_data


def get_movie_meta(db: Session, meta_id: str):
    movie_data = get_movie_data(db, meta_id)
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


def save_movie_metadata(db: Session, metadata: dict):
    movie_data: TamilBlasterMovie = db.query(TamilBlasterMovie).filter(TamilBlasterMovie.name == metadata["name"]).one_or_none()
    if movie_data:
        movie_data.video_qualities.update(metadata["video_qualities"])
        movie_data.created_at = metadata["created_at"]
        logging.info(f"update video qualities for {metadata['name']}")
    else:
        movie_data = TamilBlasterMovie(**metadata)
        movie_data.video_qualities = metadata["video_qualities"]
        imdb_id = search_imdb(movie_data.name)
        if imdb_id:
            movie_data.imdb_id = imdb_id
        else:
            movie_data.tamilblaster_id = f"tb{uuid4().fields[-1]}"

        db.add(movie_data)
        logging.info(f"new movie '{metadata['name']}' added.")

    db.commit()
