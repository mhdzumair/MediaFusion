"""
Secret path segment for /streaming_provider/{secret}/... when the caller is logged in via the web UI.
"""

from sqlmodel.ext.asyncio.session import AsyncSession

from db.models import User, UserProfile
from db.schemas import UserData
from utils.crypto import crypto_utils
from utils.profile_context import ProfileContext


async def resolve_playback_secret_str_for_ui(
    session: AsyncSession,
    profile_ctx: ProfileContext,
    current_user: User,
    user_data_embed_fallback: UserData,
) -> str:
    """
    Prefer U-{{uuid}} for a stored profile; fall back to D- only when no DB profile exists (no R-).
    """
    if profile_ctx.profile_id is not None:
        profile = await session.get(UserProfile, profile_ctx.profile_id)
        if profile is not None and profile.user_id == current_user.id:
            return crypto_utils.format_profile_uuid_secret(profile.uuid)

    return await crypto_utils.process_user_data(user_data_embed_fallback)
