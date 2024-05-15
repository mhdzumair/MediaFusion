from uuid import uuid4

from fastapi import HTTPException, UploadFile, File, Form, APIRouter
from fastapi.requests import Request
from fastapi.responses import RedirectResponse, Response
from db import schemas
from db.config import settings
from mediafusion_scrapy.task import run_spider
from scrapers import imdb_data
from scrapers.tv import add_tv_metadata, parse_m3u_playlist
from utils import const
from utils.runtime_const import TEMPLATES

router = APIRouter()


def validate_api_password(api_password: str):
    if settings.api_password and api_password != settings.api_password:
        raise HTTPException(status_code=401, detail="Invalid API password.")
    return True


@router.get("/", tags=["scraper"])
async def get_scraper(request: Request):
    return TEMPLATES.TemplateResponse(
        "html/scraper.html",
        {
            "request": request,
            "authentication_required": settings.api_password is not None,
            "logo_url": settings.logo_url,
            "addon_name": settings.addon_name,
            "scrapy_spiders": const.SCRAPY_SPIDERS.items(),
        },
    )


@router.post("/run", tags=["scraper"])
async def run_scraper_task(task: schemas.ScraperTask):
    validate_api_password(task.api_password)
    run_spider.send(
        task.spider_name,
        pages=task.pages,
        start_page=task.start_page,
        search_keyword=task.search_keyword,
        scrape_all=str(task.scrape_all),
        scrap_catalog_id=task.scrap_catalog_id,
    )

    return {"status": f"Scraping {task.spider_name} task has been scheduled."}


@router.post("/add_tv_metadata", tags=["scraper"])
async def add_tv_meta_data(data: schemas.TVMetaDataUpload):
    validate_api_password(data.api_password)
    add_tv_metadata.send([data.tv_metadata.model_dump()])
    return {"status": "TV metadata task has been scheduled."}


@router.post("/m3u_upload", tags=["scraper"])
async def upload_m3u_playlist(
    request: Request,
    scraper_type: str = Form(...),
    m3u_playlist_source: str = Form(...),
    m3u_playlist_url: str = Form(None),
    m3u_playlist_file: UploadFile = File(None),
    api_password: str = Form(...),
):
    validate_api_password(api_password)
    if scraper_type != "add_m3u_playlist":
        raise HTTPException(
            status_code=400, detail="Invalid scraper type for this endpoint."
        )

    if m3u_playlist_file:
        content = await m3u_playlist_file.read()
        redis_key = f"m3u_playlist_{uuid4().hex[:10]}"
        await request.app.state.redis.set(redis_key, content)
        parse_m3u_playlist.send(m3u_playlist_source, playlist_redis_key=redis_key)
    elif m3u_playlist_url:
        # Process URL submission...
        parse_m3u_playlist.send(m3u_playlist_source, playlist_url=m3u_playlist_url)
    else:
        raise HTTPException(
            status_code=400, detail="Either M3U playlist URL or file must be provided."
        )

    return {"status": "M3U playlist upload task has been scheduled."}


@router.get("/imdb_data", tags=["scraper"])
async def update_imdb_data(
    response: Response, meta_id: str, redirect_video: bool = False
):
    response.headers.update(const.NO_CACHE_HEADERS)
    if not meta_id.startswith("tt"):
        raise HTTPException(
            status_code=400, detail="Invalid IMDb ID. Must start with 'tt'."
        )

    await imdb_data.process_imdb_data([meta_id])

    if redirect_video:
        return RedirectResponse(
            url=f"{settings.host_url}/static/exceptions/update_imdb_data.mp4"
        )

    return {"status": f"Successfully updated IMDb data for {meta_id}."}
