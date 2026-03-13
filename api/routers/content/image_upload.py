"""Image upload endpoints for poster/background/logo files."""

import logging
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.content.upload_guard import enforce_upload_permissions
from api.routers.user.auth import require_auth
from db.config import settings
from db.database import get_async_session
from db.models import User
from utils.image_storage import (
    generate_image_storage_key,
    get_image_storage,
    normalize_image_storage_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])

_ALLOWED_IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}


class ImageUploadResponse(BaseModel):
    """Image upload response payload."""

    url: str
    key: str
    content_type: str
    size: int


@router.post("/images/upload", response_model=ImageUploadResponse)
async def upload_image(
    image: UploadFile = File(...),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Upload an image file for poster/background/logo usage."""
    if not settings.image_upload_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image upload is not enabled on this server.",
        )

    await enforce_upload_permissions(user, session)

    content = await image.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image is empty.",
        )
    if len(content) > settings.max_image_upload_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"Image file too large. Maximum size is {settings.max_image_upload_size // (1024 * 1024)} MB."),
        )

    try:
        with Image.open(BytesIO(content)) as parsed_image:
            image_format = (parsed_image.format or "").upper()
    except UnidentifiedImageError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image file.",
        ) from error

    format_info = _ALLOWED_IMAGE_FORMATS.get(image_format)
    if not format_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image type. Allowed: JPEG, PNG, WEBP, GIF.",
        )

    extension, content_type = format_info
    key = generate_image_storage_key(extension)

    try:
        stored_key = await get_image_storage().store_image(key, content, content_type)
    except ValueError as error:
        logger.error("Image upload attempted without valid S3 configuration: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image upload is not currently available.",
        ) from error
    except Exception as error:
        logger.exception("Failed to upload image to storage: %s", error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store image.",
        ) from error

    image_url = f"{settings.host_url.rstrip('/')}/api/v1/import/images/{stored_key}"
    return ImageUploadResponse(
        url=image_url,
        key=stored_key,
        content_type=content_type,
        size=len(content),
    )


@router.get("/images/{key:path}")
async def get_uploaded_image(key: str):
    """Serve an uploaded image object from storage."""
    if not settings.image_upload_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image uploads are not enabled on this server.",
        )

    try:
        normalized_key = normalize_image_storage_key(key)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found.") from error

    image_data = await get_image_storage().retrieve_image(normalized_key)
    if not image_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found.")

    content, content_type = image_data
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
