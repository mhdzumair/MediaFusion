from fastapi import HTTPException, UploadFile, File, Form, APIRouter, Request

from db import schemas
from db.config import settings
from mediafusion_scrapy.task import run_spider
from scrapers.tamil_blasters import run_tamil_blasters_scraper
from scrapers.tamilmv import run_tamilmv_scraper
from scrapers.tv import add_tv_metadata, parse_m3u_playlist

router = APIRouter()


def validate_api_password(api_password: str):
    if settings.api_password and api_password != settings.api_password:
        raise HTTPException(status_code=401, detail="Invalid API password.")
    return True


@router.post("/run", tags=["scraper"])
async def run_scraper_task(task: schemas.ScraperTask):
    validate_api_password(task.api_password)
    if task.scraper_type == "tamilmv":
        run_tamilmv_scraper.send(task.pages, task.start_page)
    elif task.scraper_type == "tamilblasters":
        run_tamil_blasters_scraper.send(task.pages, task.start_page)
    elif task.scraper_type == "scrapy":
        if not task.spider_name:
            raise HTTPException(
                status_code=400, detail="Spider name is required for scrapy tasks."
            )
        run_spider.send(task.spider_name)
    else:
        raise HTTPException(status_code=400, detail="Invalid scraper type.")

    return {"status": f"Scraping {task.scraper_type} task has been scheduled."}


@router.post("/add_tv_metadata", tags=["scraper"])
async def add_tv_meta_data(data: schemas.TVMetaDataUpload):
    validate_api_password(data.api_password)
    add_tv_metadata.send(data.tv_metadata.model_dump())
    return {"status": "TV metadata task has been scheduled."}


@router.post("/m3u_upload", tags=["scraper"])
async def upload_m3u_playlist(
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
        # Send to Dramatiq actor with file content
        parse_m3u_playlist.send(
            m3u_playlist_source, playlist_content=content.decode("utf-8")
        )
    elif m3u_playlist_url:
        # Process URL submission...
        parse_m3u_playlist.send(m3u_playlist_source, playlist_url=m3u_playlist_url)
    else:
        raise HTTPException(
            status_code=400, detail="Either M3U playlist URL or file must be provided."
        )

    return {"status": "M3U playlist upload task has been scheduled."}
