"""Instance information endpoints.

Provides endpoints to get current instance configuration,
allowing the frontend to determine if API key is required.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from db.config import settings
from utils import const

router = APIRouter(prefix="/api/v1/instance", tags=["Instance"])


class InstanceInfo(BaseModel):
    """Instance configuration information."""

    is_public: bool
    requires_api_key: bool
    addon_name: str
    version: str
    logo_url: str
    branding_svg: str | None = None  # Optional partner/host SVG logo URL


class TelegramFeatureConfig(BaseModel):
    """Telegram feature configuration for the frontend."""

    enabled: bool  # Whether Telegram streaming is enabled on this instance
    bot_configured: bool  # Whether the Telegram bot is configured
    bot_username: str | None = None  # Bot @username for deep links (without @)
    scraping_enabled: bool  # Whether Telegram scraping is enabled


class AppConfig(BaseModel):
    """Full application configuration for the frontend."""

    addon_name: str
    logo_url: str
    branding_svg: str | None = None  # Optional partner/host SVG logo URL
    host_url: str
    poster_host_url: str | None
    version: str
    description: str
    branding_description: str
    is_public_instance: bool
    disabled_providers: list[str]
    disabled_content_imports: list[str]
    authentication_required: bool
    torznab_enabled: bool
    telegram: TelegramFeatureConfig


@router.get("/info", response_model=InstanceInfo)
async def get_instance_info():
    """Get current instance configuration (public vs private).

    This endpoint is always accessible (no auth required) so the frontend
    can determine whether to show the API key input field.

    Returns:
        InstanceInfo: Instance configuration including whether API key is required.
    """
    return InstanceInfo(
        is_public=settings.is_public_instance,
        requires_api_key=not settings.is_public_instance,
        addon_name=settings.addon_name,
        version=settings.version,
        logo_url=settings.logo_url,
        branding_svg=settings.branding_svg,
    )


@router.get("/app-config", response_model=AppConfig)
async def get_app_config():
    """Get full application configuration for the frontend.

    This endpoint provides all configuration needed by the frontend UI,
    including branding, disabled providers, and authentication requirements.
    """
    return AppConfig(
        addon_name=settings.addon_name,
        logo_url=settings.logo_url,
        branding_svg=settings.branding_svg,
        host_url=settings.host_url,
        poster_host_url=settings.poster_host_url,
        version=settings.version,
        description=settings.description,
        branding_description=settings.branding_description,
        is_public_instance=settings.is_public_instance,
        disabled_providers=settings.disabled_providers,
        disabled_content_imports=settings.disabled_content_imports,
        authentication_required=settings.api_password is not None and not settings.is_public_instance,
        torznab_enabled=settings.enable_torznab_api,
        telegram=TelegramFeatureConfig(
            enabled=settings.is_scrap_from_telegram,
            bot_configured=bool(settings.telegram_bot_token),
            bot_username=settings.telegram_bot_username,
            scraping_enabled=settings.is_scrap_from_telegram,
        ),
    )


@router.get("/constants")
async def get_system_constants():
    """Get all system constants needed by the frontend.

    Returns catalog data, resolutions, sorting options, languages, and quality groups.
    """
    return {
        "CATALOG_DATA": const.CATALOG_DATA,
        "RESOLUTIONS": const.RESOLUTIONS,
        "TORRENT_SORTING_PRIORITY_OPTIONS": const.TORRENT_SORTING_PRIORITY_OPTIONS,
        "SUPPORTED_LANGUAGES": const.SUPPORTED_LANGUAGES,
        "QUALITY_GROUPS": const.QUALITY_GROUPS,
    }
