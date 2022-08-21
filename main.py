import json

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from starlette.staticfiles import StaticFiles

import schemas
import utils
from database import SessionLocal

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="resources"), name="static")

with open("manifest.json") as file:
    manifest = json.load(file)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/manifest.json")
async def get_manifest():
    return manifest


@app.get("/catalog/movie/{catalog_id}.json", response_model=schemas.Movie)
@app.get("/catalog/movie/{catalog_id}/skip={skip}.json", response_model=schemas.Movie)
async def get_catalog(catalog_id: str, skip: int = 0, db: Session = Depends(get_db)):
    movies = schemas.Movie()
    movies.metas.extend(utils.get_movies_meta(db, catalog_id, skip))
    return movies


@app.get("/meta/movie/{meta_id}.json")
async def get_meta(meta_id: str, db: Session = Depends(get_db)):
    return utils.get_movie_meta(db, meta_id)


@app.get("/stream/movie/{video_id}.json", response_model=schemas.Streams)
async def get_stream(video_id: str, db: Session = Depends(get_db)):
    streams = schemas.Streams()
    streams.streams.extend(utils.get_movie_streams(db, video_id))
    return streams
