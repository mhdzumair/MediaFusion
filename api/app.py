"""FastAPI application factory."""

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import middleware
from api.exception_handlers import (
    api_http_exception_handler,
    api_validation_exception_handler,
)
from api.lifespan import lifespan
from db.config import settings
from utils import const

# Path to React frontend build
FRONTEND_DIST_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title=settings.addon_name,
        description=settings.description,
        version=settings.version,
        lifespan=lifespan,
    )

    # Exception handlers: wrap 4xx/5xx as HTTP 200 for /api/v1/* paths so that
    # reverse proxies (e.g. Traefik) don't replace the response body.
    app.add_exception_handler(HTTPException, api_http_exception_handler)
    app.add_exception_handler(RequestValidationError, api_validation_exception_handler)

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add custom middleware
    @app.middleware("http")
    async def add_cors_header(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(const.CORS_HEADERS)
        # if "cache-control" not in response.headers:
        #     response.headers.update(const.CACHE_HEADERS)
        return response

    app.add_middleware(middleware.RateLimitMiddleware)
    app.add_middleware(middleware.APIKeyMiddleware)
    app.add_middleware(middleware.UserDataMiddleware)
    app.add_middleware(middleware.TimingMiddleware)
    app.add_middleware(middleware.SecureLoggingMiddleware)

    # Mount static files
    app.mount("/static", StaticFiles(directory="resources"), name="static")

    # Setup React SPA serving
    _setup_spa(app)

    # Register routers
    _register_routers(app)

    return app


def _register_routers(app: FastAPI) -> None:
    """Register all API routers.

    Args:
        app: FastAPI application instance.
    """
    # Import routers here to avoid circular imports
    # Organized router packages - use get_router() to avoid circular imports
    from api.routers.admin import get_router as get_admin_router
    from api.routers.content import get_router as get_content_router
    from api.routers.instance import get_router as get_instance_router
    from api.routers.rss import get_router as get_rss_router
    from api.routers.streaming import (
        get_provider_router as get_streaming_provider_router,
    )
    from api.routers.streaming import get_router as get_streaming_router
    from api.routers.stremio import get_router as get_stremio_router
    from api.routers.torznab import get_router as get_torznab_router
    from api.routers.user import get_router as get_user_router

    from api.routers.kodi import get_router as get_kodi_router

    # Register Stremio addon routes (home, manifest, catalog, meta, stream, etc.)
    app.include_router(get_stremio_router())

    # Register organized router packages
    app.include_router(get_instance_router())  # instance info, app-config, constants
    app.include_router(get_user_router())  # auth, user, profiles, watch_history, downloads, user_library, indexers
    app.include_router(
        get_admin_router()
    )  # admin, scheduler, cache, database_admin, contribution_settings, metrics, scrapers
    app.include_router(get_content_router())  # catalog, contributions, content_import, voting, suggestions, scraping
    app.include_router(get_rss_router())  # rss_feeds, user_rss
    app.include_router(get_streaming_router(), prefix="/streaming_provider")  # playback, cache
    app.include_router(get_streaming_provider_router(), prefix="/streaming_provider")  # debrid provider auth

    # Kodi device pairing routes
    app.include_router(get_kodi_router())

    # Torznab API (optional, controlled by enable_torznab_api setting)
    app.include_router(get_torznab_router())


def _setup_spa(app: FastAPI) -> None:
    """Setup React SPA serving.

    Args:
        app: FastAPI application instance.
    """
    if not os.path.exists(FRONTEND_DIST_PATH):
        return

    # Mount static assets from the React build
    assets_path = os.path.join(FRONTEND_DIST_PATH, "assets")
    if os.path.exists(assets_path):
        app.mount("/app/assets", StaticFiles(directory=assets_path), name="app_assets")

    @app.get("/app/{path:path}", tags=["spa"])
    @app.get("/app", tags=["spa"])
    async def serve_spa(path: str = ""):
        """Serve the React SPA for all /app routes."""
        index_path = os.path.join(FRONTEND_DIST_PATH, "index.html")
        if os.path.exists(index_path):
            # Add no-cache headers to ensure the latest version is always served
            # The JS/CSS assets have content hashes and can be cached indefinitely
            return FileResponse(
                index_path,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        raise HTTPException(
            status_code=404,
            detail="Frontend not built. Run 'npm run build' in the frontend directory.",
        )
