"""Stremio manifest route."""

import logging

from fastapi import APIRouter, Depends, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from db import crud, schemas
from db.database import get_read_session
from utils import const, wrappers
from utils.network import get_user_data
from utils.parser import generate_manifest

router = APIRouter()


@router.get("/manifest.json", tags=["manifest"])
@router.get("/{secret_str}/manifest.json", tags=["manifest"])
@wrappers.auth_required
async def get_manifest(
    response: Response,
    user_data: schemas.UserData = Depends(get_user_data),
    session: AsyncSession = Depends(get_read_session),
):
    """Get the Stremio addon manifest."""
    response.headers.update(const.NO_CACHE_HEADERS)

    # Fetch all genres in a single efficient query
    try:
        genres = await crud.get_all_genres_by_type(session)
    except Exception as e:
        logging.exception("Error fetching genres: %s", e)
        genres = {"movie": [], "series": [], "tv": []}

    return await generate_manifest(user_data, genres)
