import json
import logging
from typing import Literal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api import schemas
from db import database, crud
from utils import scrap

logging.basicConfig(format='%(levelname)s::%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S', level=logging.INFO)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="resources"), name="static")
TEMPLATES = Jinja2Templates(directory="resources")

with open("resources/manifest.json") as file:
    manifest = json.load(file)


@app.on_event("startup")
async def init_db():
    await database.init()


@app.on_event("startup")
async def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scrap.run_schedule_scrape, CronTrigger(hour="*/3")
    )
    scheduler.start()
    app.state.scheduler = scheduler


@app.on_event("shutdown")
async def stop_scheduler():
    app.state.scheduler.shutdown(wait=False)


@app.get("/")
async def get_home(request: Request):
    return TEMPLATES.TemplateResponse(
        "home.html",
        {
            "request": request, "name": manifest.get("name"), "version": manifest.get("version"),
            "description": manifest.get("description"), "gives": [
            "Tamil Movies", "Malayalam Movies", "Telugu Movies", "Hindi Movies", "Kannada Movies", "English Movies",
            "Dubbed Movies", "Series"
        ],
            "logo": "static/tamilblasters.png"
        },
    )


@app.get("/manifest.json")
async def get_manifest(response: Response):
    response.headers.update({
        "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"
    })
    return manifest


@app.get("/catalog/movie/{catalog_id}.json", response_model=schemas.Movie)
@app.get("/catalog/movie/{catalog_id}/skip={skip}.json", response_model=schemas.Movie)
@app.get("/catalog/series/{catalog_id}.json", response_model=schemas.Movie)
@app.get("/catalog/series/{catalog_id}/skip={skip}.json", response_model=schemas.Movie)
async def get_catalog(response: Response, catalog_id: str, skip: int = 0):
    response.headers.update({
        "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"
    })
    movies = schemas.Movie()
    movies.metas.extend(await crud.get_movies_meta(catalog_id, skip))
    return movies


@app.get("/meta/movie/{meta_id}.json")
async def get_meta(meta_id: str, response: Response):
    response.headers.update({
        "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"
    })
    return await crud.get_movie_meta(meta_id)


@app.get("/stream/movie/{video_id}.json", response_model=schemas.Streams)
async def get_stream(video_id: str, response: Response):
    response.headers.update({
        "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"
    })
    streams = schemas.Streams()
    streams.streams.extend(await crud.get_movie_streams(video_id))
    return streams


@app.post("/scraper")
def run_scraper(
        background_tasks: BackgroundTasks,
        language: Literal["tamil", "malayalam", "telugu", "hindi", "kannada", "english"] = "tamil",
        video_type: Literal["hdrip", "tcrip", "dubbed", "series"] = "hdrip", pages: int = 1, start_page: int = 1,
        is_scrape_home: bool = False,
):
    background_tasks.add_task(scrap.run_scraper, language, video_type, pages, start_page, is_scrape_home)
    return {"message": "Scraping in background..."}


@app.get("/meta/series/{meta_id}.json")
async def get_series_meta(meta_id: str, response: Response):
    response.headers.update({
        "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"
    })
    return await crud.get_series_meta(meta_id)


@app.get("/stream/series/{video_id}:{season}:{episode}.json", response_model=schemas.Streams)
async def get_series_streams(video_id: str, season: int, episode: str, response: Response):
    response.headers.update({
        "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"
    })
    streams = schemas.Streams()
    streams.streams.extend(await crud.get_series_streams(video_id, season, episode))
    return streams
