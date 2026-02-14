"""Core routes: home, health, favicon."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from db.config import settings
from utils import wrappers

router = APIRouter()


@router.get("/", tags=["home"])
async def get_home(request: Request):
    """Redirect to the React SPA home page."""
    return RedirectResponse(url="/app", status_code=302)


@router.get("/health", tags=["health"])
@wrappers.exclude_rate_limit
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@router.get("/favicon.ico")
async def get_favicon():
    """Redirect to logo URL for favicon."""
    return RedirectResponse(url=settings.logo_url)
