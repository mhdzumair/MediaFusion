"""
Telegram bot authentication endpoints.

Handles linking Telegram user accounts to MediaFusion user accounts.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.routers.user.auth import require_auth
from db.models import User
from utils.telegram_bot import telegram_content_bot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram Auth"])


class TelegramLinkResponse(BaseModel):
    """Response for Telegram account linking."""

    success: bool
    message: str


@router.get("/login", response_model=TelegramLinkResponse)
async def telegram_login(
    token: str = Query(..., description="Login token from Telegram bot /login command"),
    current_user: User = Depends(require_auth),
):
    """Link Telegram account to MediaFusion account using login token.

    Usage:
    1. User sends /login to Telegram bot
    2. Bot provides a login token and URL
    3. User visits this endpoint with the token while logged into MediaFusion web UI
    4. Telegram account is linked to MediaFusion account

    Note: You must be logged into MediaFusion web UI to use this endpoint.
    """
    result = await telegram_content_bot.link_telegram_user(token, current_user.id)

    if result["success"]:
        return TelegramLinkResponse(
            success=True,
            message="✅ Telegram account linked successfully!\n\nYour uploaded content will now be stored with your MediaFusion account.",
        )
    else:
        raise HTTPException(status_code=400, detail=result["message"])
