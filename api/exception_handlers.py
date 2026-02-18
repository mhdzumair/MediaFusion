"""Custom exception handlers that return HTTP 200 for /api/v1/* paths.

Hosters running MediaFusion behind Traefik (or similar proxies) may intercept
4xx/5xx responses and replace the body with their own error pages.  By returning
HTTP 200 with an ``error: true`` JSON envelope, the real error details pass
through the proxy untouched and the frontend can handle them properly.
"""

import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1/"


def _is_api_path(request: Request) -> bool:
    return request.url.path.startswith(API_PREFIX)


async def api_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Wrap HTTPException as 200 + ``{error: true}`` for API paths."""
    if not _is_api_path(request):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    return JSONResponse(
        status_code=200,
        content={
            "error": True,
            "detail": exc.detail,
            "status_code": exc.status_code,
        },
    )


async def api_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Wrap RequestValidationError (422) as 200 + ``{error: true}`` for API paths."""
    if not _is_api_path(request):
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
        )

    return JSONResponse(
        status_code=200,
        content={
            "error": True,
            "detail": "Validation error",
            "status_code": 422,
            "errors": exc.errors(),
        },
    )
