"""
User Profile Management API endpoints.
"""

import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi import (
    Response as FastAPIResponse,
)
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db.config import settings
from db.database import get_async_session
from db.models import User, UserProfile
from db.schemas import UserData
from db.schemas.config import MediaFlowConfig, StreamingProvider
from streaming_providers.validator import validate_provider_credentials
from utils import const
from utils.crypto import UUID_PREFIX, crypto_utils
from utils.profile_context import ProfileDataProvider
from utils.profile_crypto import profile_crypto

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/profiles", tags=["Profiles"])


# Dependency to add no-cache headers to responses
async def add_no_cache_headers(response: FastAPIResponse):
    """Add no-cache headers to prevent browser caching of profile data."""
    response.headers.update(const.NO_CACHE_HEADERS)
    return response


# ============================================
# Pydantic Schemas
# ============================================


class ProfileCreate(BaseModel):
    name: str
    config: dict[str, Any] = {}
    is_default: bool = False


class ProfileUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    is_default: bool | None = None


class StreamingProviderInfo(BaseModel):
    """Information about a single streaming provider"""

    service: str
    name: str | None = None  # Optional display name
    enabled: bool = True
    priority: int = 0
    has_credentials: bool = False  # Whether required credentials are present


class StreamingProvidersSummary(BaseModel):
    """Summary of all configured streaming providers"""

    providers: list[StreamingProviderInfo] = []
    has_debrid: bool = False  # Computed property
    primary_service: str | None = None  # First enabled provider


class ProfileResponse(BaseModel):
    id: int
    user_id: int
    name: str
    config: dict[str, Any]
    is_default: bool
    created_at: str
    streaming_providers: StreamingProvidersSummary = StreamingProvidersSummary()
    # Deprecated: for backward compatibility
    streaming_provider: dict[str, Any] | None = None
    catalogs_enabled: int = 0


class ManifestUrlResponse(BaseModel):
    """Response with Stremio manifest URL"""

    profile_id: int
    profile_uuid: str
    profile_name: str
    manifest_url: str
    stremio_install_url: str
    kodi_addon_url: str


class SetDefaultResponse(BaseModel):
    """Response after setting default profile"""

    success: bool
    profile_id: int


# ============================================
# Helper Functions
# ============================================


def _provider_has_credentials(sp: dict, service: str) -> bool:
    """Check whether a raw provider dict has the required credentials for its service."""
    required_fields = const.STREAMING_SERVICE_REQUIREMENTS.get(service, const.STREAMING_SERVICE_REQUIREMENTS["default"])
    for field in required_fields:
        # Check both full name and short alias
        alias_map = {
            "token": "tk",
            "email": "em",
            "password": "pw",
            "url": "url",
            "qbittorrent_config": "qbc",
            "sabnzbd_config": "sbc",
            "nzbget_config": "ngc",
            "nzbdav_config": "ndc",
            "easynews_config": "enc",
        }
        alias = alias_map.get(field, field)
        if not (sp.get(field) or sp.get(alias)):
            return False
    return True


def get_streaming_providers_summary(config: dict) -> StreamingProvidersSummary:
    """Extract all streaming providers from config"""
    providers = []

    # Check multi-provider format first (sps alias)
    sps = config.get("sps") or config.get("streaming_providers") or []
    for i, sp in enumerate(sps):
        if isinstance(sp, dict):
            service = sp.get("sv") or sp.get("service")
            # Skip providers with empty/missing service
            if not service:
                continue
            enabled = sp.get("en", True) if "en" in sp else sp.get("enabled", True)
            providers.append(
                StreamingProviderInfo(
                    service=service,
                    name=sp.get("n") or sp.get("name"),
                    enabled=enabled,
                    priority=sp.get("pr", i) if "pr" in sp else sp.get("priority", i),
                    has_credentials=_provider_has_credentials(sp, service),
                )
            )

    # Fall back to legacy single provider (sp alias)
    if not providers:
        sp = config.get("sp") or config.get("streaming_provider")
        if sp and isinstance(sp, dict):
            service = sp.get("sv") or sp.get("service")
            if service:
                providers.append(
                    StreamingProviderInfo(
                        service=service,
                        enabled=True,
                        priority=0,
                        has_credentials=_provider_has_credentials(sp, service),
                    )
                )

    # Sort by priority and filter to enabled, not disabled, and with valid credentials
    disabled = set(settings.disabled_providers)
    enabled_providers = [p for p in providers if p.enabled and p.service not in disabled and p.has_credentials]
    enabled_providers.sort(key=lambda p: p.priority)

    # has_debrid is True only if there's at least one valid non-p2p debrid service
    has_debrid = any(p.service != "p2p" for p in enabled_providers)

    return StreamingProvidersSummary(
        providers=providers,
        has_debrid=has_debrid,
        primary_service=enabled_providers[0].service if enabled_providers else None,
    )


# Deprecated: for backward compatibility
def get_streaming_provider_summary(config: dict) -> dict:
    """Extract streaming provider summary from config (deprecated)"""
    summary = get_streaming_providers_summary(config)
    return {
        "service": summary.primary_service,
        "is_configured": summary.has_debrid,
    }


def count_enabled_catalogs(config: dict) -> int:
    """Count enabled catalogs in config.

    Checks new catalog_configs format first, then falls back to legacy
    selected_catalogs for raw config dicts that haven't been processed
    through UserData model yet.
    """
    # Check new catalog_configs format first
    catalog_configs = config.get("cc") or config.get("catalog_configs") or []
    if catalog_configs:
        return sum(1 for c in catalog_configs if c.get("en", True))
    # Fall back to legacy selected_catalogs (for raw config dicts)
    catalogs = config.get("sc") or config.get("selected_catalogs") or []
    return len(catalogs)


def mask_sensitive_config(config: dict) -> dict:
    """Mask sensitive fields in config for display using profile_crypto."""
    if not config:
        return {}
    return profile_crypto.mask_secrets_for_display(config)


def profile_to_response(profile: UserProfile, include_full_config: bool = False) -> ProfileResponse:
    """
    Convert profile model to response.

    Args:
        profile: UserProfile model
        include_full_config: If True, decrypt and include full config (for manifest generation).
                            If False (default), mask sensitive fields for display.
    """
    config = profile.config or {}

    # Decrypt and merge secrets if they exist
    if profile.encrypted_secrets:
        try:
            secrets = profile_crypto.decrypt_secrets(profile.encrypted_secrets)
            config = profile_crypto.merge_secrets(config, secrets)
        except Exception as e:
            logger.warning(f"Failed to decrypt profile secrets: {e}")

    # Get provider summary and catalog count from full config
    providers_summary = get_streaming_providers_summary(config)
    legacy_provider = get_streaming_provider_summary(config)
    catalogs_count = count_enabled_catalogs(config)

    # Mask for display unless full config requested
    display_config = config if include_full_config else mask_sensitive_config(config)

    return ProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        name=profile.name,
        config=display_config,
        is_default=profile.is_default,
        created_at=profile.created_at.isoformat(),
        streaming_providers=providers_summary,
        streaming_provider=legacy_provider,  # Deprecated field for backward compatibility
        catalogs_enabled=catalogs_count,
    )


async def generate_manifest_secret(profile: UserProfile, user: User, api_password: str | None = None) -> str:
    """
    Generate a UUID-based secret string for manifest URL.

    Returns a stable U-{profile_uuid} string that dynamically resolves to the
    current profile config on every request. The profile data is pre-cached in
    Redis so the first Stremio request is fast.

    This eliminates the need to re-install the addon when config changes,
    since the UUID remains stable and always resolves to the latest config.

    Args:
        profile: The user profile
        user: The user
        api_password: Optional API password from X-API-Key header (for private instances)
    """
    try:
        # Build cache data for pre-caching in Redis
        cache_data = {
            "config": profile.config or {},
            "encrypted_secrets": profile.encrypted_secrets,
            "user_id": user.id,
            "profile_id": profile.id,
            "user_uuid": user.uuid,
            "profile_uuid": profile.uuid,
        }

        # Include API password if provided (for private instances)
        if api_password:
            cache_data["api_password"] = api_password

        # Pre-cache the profile data in Redis so the first Stremio request is fast
        await crypto_utils._cache_uuid_profile(profile.uuid, cache_data)

        return f"{UUID_PREFIX}{profile.uuid}"
    except Exception as e:
        logger.error(f"Failed to generate manifest secret: {e}")
        return ""


def unmask_config_update(new_config: dict, existing_full_config: dict) -> dict:
    """
    Merge new config with existing config, preserving credentials.

    This function:
    1. Starts with the existing config as base (preserving all existing values)
    2. Deep merges the new config on top (updating changed values)
    3. Restores masked values ('****' or similar) from existing config

    Args:
        new_config: New config from client (may have masked values or missing fields)
        existing_full_config: Existing full config with secrets decrypted

    Returns:
        Merged config with credentials preserved
    """
    import copy

    if not new_config:
        return existing_full_config or {}
    if not existing_full_config:
        return new_config

    def is_masked(value):
        """Check if a value is the standard mask pattern."""
        return value == "••••••••"

    def deep_merge(base: dict, overlay: dict) -> dict:
        """
        Deep merge overlay into base, preserving nested structures.
        Lists are replaced entirely (not merged) to allow removing items.
        None values in overlay remove the key from the result.
        """
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            if value is None:
                result.pop(key, None)
            elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    # Start with existing config as base, merge new config on top
    result = deep_merge(existing_full_config, new_config)

    # Secret keys that should be restored if masked
    SECRET_KEYS = [
        "token",
        "tk",
        "password",
        "pw",
        "email",
        "em",
        "api_key",
        "ak",
        "api_password",
        "ap",
        "qb_username",
        "qus",
        "qb_password",
        "qpw",
    ]

    def restore_masked_in_dict(merged: dict, existing: dict):
        """Recursively restore masked values from existing config"""
        if not merged or not existing:
            return
        for key, value in merged.items():
            if isinstance(value, dict) and key in existing and isinstance(existing[key], dict):
                restore_masked_in_dict(value, existing[key])
            elif isinstance(value, list) and key in existing and isinstance(existing[key], list):
                # Handle lists of dicts (like streaming_providers)
                for i, item in enumerate(value):
                    if isinstance(item, dict) and i < len(existing[key]) and isinstance(existing[key][i], dict):
                        restore_masked_in_dict(item, existing[key][i])
            elif key in SECRET_KEYS and is_masked(value):
                # Restore masked secret from existing config
                merged[key] = existing.get(key)

    # Restore any masked values from the existing config
    restore_masked_in_dict(result, existing_full_config)

    return result


def save_profile_config(profile: UserProfile, config: dict) -> None:
    """
    Save config to profile, extracting and encrypting secrets.

    This separates sensitive data (tokens, passwords, API keys) and stores them
    encrypted in the encrypted_secrets column, while non-sensitive config
    goes in the config JSON column.
    """
    # Extract secrets from config
    clean_config, secrets = profile_crypto.extract_secrets(config)

    # Store clean config (without secrets)
    profile.config = clean_config

    # Encrypt and store secrets
    profile.encrypted_secrets = profile_crypto.encrypt_secrets(secrets)


def get_full_config(profile: UserProfile) -> dict:
    """
    Get the full config with secrets decrypted and merged.

    Returns:
        Complete config dict with secrets restored
    """
    config = profile.config or {}

    if profile.encrypted_secrets:
        try:
            secrets = profile_crypto.decrypt_secrets(profile.encrypted_secrets)
            config = profile_crypto.merge_secrets(config, secrets)
        except Exception as e:
            logger.warning(f"Failed to decrypt profile secrets: {e}")

    return config


def _validate_provider_configs(config: dict) -> None:
    """
    Check that every enabled streaming provider in the config has its required
    credentials/configuration filled in. Raises HTTPException(400) on the first
    provider that is missing required fields.
    """
    disabled = set(settings.disabled_providers)

    # Human-readable names for error messages
    field_labels = {
        "token": "API token",
        "email": "email",
        "password": "password",
        "url": "URL",
        "qbittorrent_config": "qBittorrent configuration",
        "sabnzbd_config": "SABnzbd configuration",
        "nzbget_config": "NZBGet configuration",
        "nzbdav_config": "NzbDAV configuration",
        "easynews_config": "Easynews configuration",
    }

    providers_raw = config.get("streaming_providers") or config.get("sps") or []
    for sp in providers_raw:
        if not isinstance(sp, dict):
            continue
        service = sp.get("sv") or sp.get("service")
        if not service or service in disabled:
            continue
        enabled = sp.get("en", True) if "en" in sp else sp.get("enabled", True)
        if not enabled:
            continue

        required_fields = const.STREAMING_SERVICE_REQUIREMENTS.get(
            service, const.STREAMING_SERVICE_REQUIREMENTS["default"]
        )
        for field in required_fields:
            if not _provider_has_credentials(sp, service):
                label = field_labels.get(field, field)
                provider_name = sp.get("n") or sp.get("name") or service
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Provider '{provider_name}' is missing required {label}. "
                    f"Please complete the configuration or remove the provider.",
                )


def _validate_external_services(config: dict) -> None:
    """
    Validate external service configs (MediaFlow, RPDB, MDBList).
    If a service is present in the config, its required fields must be filled
    with valid values.  Raises HTTPException(400) on the first problem found.
    """
    mfc = config.get("mediaflow_config") or config.get("mfc")
    if isinstance(mfc, dict):
        proxy_url = mfc.get("proxy_url") or mfc.get("pu") or ""
        api_password = mfc.get("api_password") or mfc.get("ap") or ""

        if not proxy_url.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MediaFlow Proxy URL is required when MediaFlow is enabled.",
            )
        parsed = urlparse(proxy_url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MediaFlow Proxy URL must be a valid HTTP/HTTPS URL.",
            )
        if not api_password.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MediaFlow API password is required when MediaFlow is enabled.",
            )

    rpc = config.get("rpdb_config") or config.get("rpc")
    if isinstance(rpc, dict):
        api_key = rpc.get("api_key") or rpc.get("ak") or ""
        if not api_key.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="RPDB API key is required when RPDB is enabled.",
            )

    mdb = config.get("mdblist_config") or config.get("mdb")
    if isinstance(mdb, dict):
        api_key = mdb.get("api_key") or mdb.get("ak") or ""
        if not api_key.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MDBList API key is required when MDBList is enabled.",
            )


def _build_user_data_for_validation(config: dict) -> UserData:
    """
    Build a minimal UserData with only the fields needed for provider credential
    validation. Providers that are disabled or that fail to parse (e.g. missing
    nested config like qbittorrent_config) are silently skipped — the credential
    validator will simply not check them.
    """
    disabled = set(settings.disabled_providers)

    providers_raw = config.get("streaming_providers") or config.get("sps") or []
    providers: list[StreamingProvider] = []
    for p in providers_raw:
        if isinstance(p, dict):
            service = p.get("sv") or p.get("service")
            if not service or service in disabled:
                continue
            try:
                providers.append(StreamingProvider.model_validate(p))
            except Exception:
                logger.debug("Skipping provider '%s' during validation (parse error)", service)
        elif isinstance(p, StreamingProvider) and p.service not in disabled:
            providers.append(p)

    # Also grab legacy single-provider field
    sp: StreamingProvider | None = None
    sp_raw = config.get("streaming_provider") or config.get("sp")
    if sp_raw:
        service = (
            sp_raw.get("sv") or sp_raw.get("service") if isinstance(sp_raw, dict) else getattr(sp_raw, "service", None)
        )
        if service and service not in disabled:
            try:
                sp = StreamingProvider.model_validate(sp_raw) if isinstance(sp_raw, dict) else sp_raw
            except Exception:
                logger.debug("Skipping legacy provider '%s' during validation (parse error)", service)

    # MediaFlow config is needed for IP resolution during validation
    mfc_raw = config.get("mediaflow_config") or config.get("mfc")
    mfc = None
    if isinstance(mfc_raw, dict):
        try:
            mfc = MediaFlowConfig.model_validate(mfc_raw)
        except Exception:
            pass
    elif isinstance(mfc_raw, MediaFlowConfig):
        mfc = mfc_raw

    return UserData(
        streaming_providers=providers,
        streaming_provider=sp,
        mediaflow_config=mfc,
    )


# ============================================
# API Endpoints
# ============================================


class UserConfigResponse(BaseModel):
    """User-specific configuration data"""

    user_data: dict[str, Any]
    configured_fields: list[str]


@router.get("/user-config", response_model=UserConfigResponse)
async def get_user_config(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get user-specific configuration from their default profile.

    Returns only user configuration with sensitive data masked.
    """
    # Get user's default profile
    result = await session.exec(
        select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.is_default == True)
    )
    profile = result.first()

    if not profile:
        # Return empty config if no profile exists
        return UserConfigResponse(user_data={}, configured_fields=[])

    # Get full config with secrets
    config = get_full_config(profile)
    configured_fields = []

    try:
        user_data = UserData(**config)

        # Handle sensitive data masking for streaming_providers
        if user_data.streaming_providers:
            for provider in user_data.streaming_providers:
                provider.password = "••••••••"
                provider.token = "••••••••"
                configured_fields.extend(["provider_token", "password"])

                if provider.qbittorrent_config:
                    provider.qbittorrent_config.qbittorrent_password = "••••••••"
                    provider.qbittorrent_config.webdav_password = "••••••••"
                    configured_fields.extend(["qbittorrent_password", "webdav_password"])

        if user_data.mediaflow_config:
            user_data.mediaflow_config.api_password = "••••••••"
            configured_fields.append("mediaflow_api_password")

        if user_data.rpdb_config:
            user_data.rpdb_config.api_key = "••••••••"
            configured_fields.append("rpdb_api_key")

        user_data.api_password = None

        return UserConfigResponse(
            user_data=user_data.model_dump(),
            configured_fields=configured_fields,
        )
    except Exception as e:
        logger.warning(f"Failed to parse user config: {e}")
        return UserConfigResponse(
            user_data=mask_sensitive_config(config),
            configured_fields=configured_fields,
        )


class RpdbApiKeyResponse(BaseModel):
    """Response containing RPDB API key for poster display"""

    rpdb_api_key: str | None = None


@router.get("/rpdb-key", response_model=RpdbApiKeyResponse)
async def get_rpdb_api_key(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get RPDB API key from user's default profile.

    This endpoint returns the unmasked RPDB API key to allow the frontend
    to construct RPDB poster URLs. RPDB keys only grant access to poster
    images, not personal data.
    """
    # Get user's default profile
    result = await session.exec(
        select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.is_default == True)
    )
    profile = result.first()

    if not profile:
        logger.info(f"No default profile found for user {user.id}")
        return RpdbApiKeyResponse(rpdb_api_key=None)

    # Get full config with secrets
    config = get_full_config(profile)

    # Extract RPDB API key - check both alias and full name formats
    rpdb_api_key = None

    # Check alias format first (rpc.ak)
    rpc = config.get("rpc", {})
    if rpc and isinstance(rpc, dict):
        rpdb_api_key = rpc.get("ak") or rpc.get("api_key")

    # Check full name format (rpdb_config.api_key)
    if not rpdb_api_key:
        rpdb_config = config.get("rpdb_config", {})
        if rpdb_config and isinstance(rpdb_config, dict):
            rpdb_api_key = rpdb_config.get("api_key") or rpdb_config.get("ak")

    return RpdbApiKeyResponse(rpdb_api_key=rpdb_api_key)


@router.get("")
async def list_profiles(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """List all profiles for the current user."""
    result = await session.exec(
        select(UserProfile)
        .where(UserProfile.user_id == user.id)
        .order_by(UserProfile.is_default.desc(), UserProfile.created_at)
    )
    profiles = result.all()

    response_data = [profile_to_response(p).model_dump() for p in profiles]
    return JSONResponse(content=response_data, headers=const.NO_CACHE_HEADERS)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(
    profile_data: ProfileCreate,
    request: Request,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new profile for the current user."""
    # Check profile count limit (max 5 profiles per user)
    count_query = select(func.count(UserProfile.id)).where(UserProfile.user_id == user.id)
    count_result = await session.exec(count_query)
    count = count_result.one()

    if count >= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum number of profiles (5) reached. Delete an existing profile to create a new one.",
        )

    # If this is the first profile or set as default, unset other defaults
    if profile_data.is_default or count == 0:
        result = await session.exec(
            select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.is_default == True)
        )
        existing_profiles = result.all()
        for p in existing_profiles:
            p.is_default = False
            session.add(p)

    profile = UserProfile(
        user_id=user.id,
        name=profile_data.name,
        is_default=profile_data.is_default or count == 0,
    )

    # Extract API key from header and include it in config (for private instances)
    config = profile_data.config.copy() if profile_data.config else {}
    api_password = request.headers.get("X-API-Key")
    if api_password:
        config["api_password"] = api_password
        config["ap"] = api_password  # Also set alias

    # Reject save if any config is incomplete
    _validate_provider_configs(config)
    _validate_external_services(config)

    # Validate provider credentials against their APIs
    user_data = _build_user_data_for_validation(config)
    if user_data.get_active_providers():
        validation_result = await validate_provider_credentials(request, user_data)
        if validation_result.get("status") == "error":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=validation_result.get("message", "Streaming provider credential validation failed"),
            )

    # Save config with encrypted secrets
    save_profile_config(profile, config)

    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    return JSONResponse(
        content=profile_to_response(profile).model_dump(),
        status_code=status.HTTP_201_CREATED,
        headers=const.NO_CACHE_HEADERS,
    )


@router.get("/{profile_id}")
async def get_profile(
    profile_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a specific profile by ID."""
    profile = await session.get(UserProfile, profile_id)

    if not profile or profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    return JSONResponse(
        content=profile_to_response(profile).model_dump(),
        headers=const.NO_CACHE_HEADERS,
    )


@router.put("/{profile_id}")
async def update_profile(
    profile_id: int,
    update_data: ProfileUpdate,
    request: Request,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Update a profile."""
    profile = await session.get(UserProfile, profile_id)

    if not profile or profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    # Update fields if provided
    if update_data.name is not None:
        profile.name = update_data.name

    if update_data.config is not None:
        # Get existing full config (with secrets decrypted)
        existing_full_config = get_full_config(profile)

        # Unmask any masked values before saving
        new_config = unmask_config_update(update_data.config, existing_full_config)

        # Extract API key from header and include it in config (for private instances)
        api_password = request.headers.get("X-API-Key")
        if api_password:
            new_config["api_password"] = api_password
            new_config["ap"] = api_password  # Also set alias

        # Reject save if any config is incomplete
        _validate_provider_configs(new_config)
        _validate_external_services(new_config)

        # Validate provider credentials against their APIs
        user_data = _build_user_data_for_validation(new_config)
        if user_data.get_active_providers():
            validation_result = await validate_provider_credentials(request, user_data)
            if validation_result.get("status") == "error":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=validation_result.get("message", "Streaming provider credential validation failed"),
                )

        # Save config with encrypted secrets
        save_profile_config(profile, new_config)

    if update_data.is_default is not None:
        if update_data.is_default:
            # Unset other defaults
            result = await session.exec(
                select(UserProfile).where(
                    UserProfile.user_id == user.id,
                    UserProfile.is_default == True,
                    UserProfile.id != profile_id,
                )
            )
            existing_profiles = result.all()
            for p in existing_profiles:
                p.is_default = False
                session.add(p)
        profile.is_default = update_data.is_default

    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    # Invalidate Redis caches for user's profile data
    await ProfileDataProvider.invalidate_cache(user.id)
    # Also invalidate the UUID-keyed cache so Stremio/Kodi pick up new config
    await crypto_utils.invalidate_uuid_cache(profile.uuid)

    return JSONResponse(
        content=profile_to_response(profile).model_dump(),
        headers=const.NO_CACHE_HEADERS,
    )


@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a profile."""
    profile = await session.get(UserProfile, profile_id)

    if not profile or profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    # Check if it's the only profile
    result = await session.exec(select(UserProfile).where(UserProfile.user_id == user.id))
    profile_count = result.all()

    if len(profile_count) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the only profile. Create another profile first.",
        )

    # If deleting the default profile, make another one default
    if profile.is_default:
        result = await session.exec(
            select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.id != profile_id)
        )
        other_profile = result.first()
        if other_profile:
            other_profile.is_default = True
            session.add(other_profile)

    # Capture UUID before deletion
    profile_uuid = profile.uuid

    await session.delete(profile)
    await session.commit()

    # Invalidate Redis caches for user's profile data
    await ProfileDataProvider.invalidate_cache(user.id)
    # Also invalidate the UUID-keyed cache so Stremio/Kodi get a clear error
    await crypto_utils.invalidate_uuid_cache(profile_uuid)

    return {"message": "Profile deleted successfully"}


@router.post("/{profile_id}/set-default", response_model=SetDefaultResponse)
async def set_default_profile(
    profile_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Set a profile as the default."""
    profile = await session.get(UserProfile, profile_id)

    if not profile or profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    # Unset other defaults
    result = await session.exec(
        select(UserProfile).where(
            UserProfile.user_id == user.id,
            UserProfile.is_default == True,
            UserProfile.id != profile_id,
        )
    )
    existing_profiles = result.all()
    for p in existing_profiles:
        p.is_default = False
        session.add(p)

    profile.is_default = True
    session.add(profile)
    await session.commit()

    # Invalidate Redis cache for user's profile data (active profile changed)
    await ProfileDataProvider.invalidate_cache(user.id)

    return SetDefaultResponse(success=True, profile_id=profile_id)


@router.get("/{profile_id}/manifest-url", response_model=ManifestUrlResponse)
async def get_manifest_url(
    profile_id: int,
    request: Request,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get the Stremio manifest URL for a profile.
    Also provides the Kodi addon URL.
    """
    profile = await session.get(UserProfile, profile_id)

    if not profile or profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    # Extract API key from header (for private instances)
    api_password = request.headers.get("X-API-Key")

    # Generate encrypted secret string from config (with secrets merged)
    # Pass user for UUID-based identification in the secret and API password from header
    secret_str = await generate_manifest_secret(profile, user, api_password=api_password)

    if not secret_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to generate manifest URL. Please check your profile configuration.",
        )

    # Construct URLs
    base_url = settings.host_url.rstrip("/")
    manifest_url = f"{base_url}/{secret_str}/manifest.json"

    # For Stremio, we need to strip the protocol for the stremio:// URL
    host_without_protocol = base_url.replace("https://", "").replace("http://", "")
    stremio_url = f"stremio://{host_without_protocol}/{secret_str}/manifest.json"

    kodi_url = f"{base_url}/kodi/{secret_str}/addon.xml"

    return ManifestUrlResponse(
        profile_id=profile_id,
        profile_uuid=profile.uuid,
        profile_name=profile.name,
        manifest_url=manifest_url,
        stremio_install_url=stremio_url,
        kodi_addon_url=kodi_url,
    )


@router.get("/{profile_id}/kodi-addon")
async def get_kodi_addon(
    profile_id: int,
    request: Request,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Generate Kodi addon.xml for a profile.
    Returns the addon.xml file content.
    """
    profile = await session.get(UserProfile, profile_id)

    if not profile or profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    # Extract API key from header (for private instances)
    api_password = request.headers.get("X-API-Key")

    # Generate encrypted secret string from config (with secrets merged)
    # Pass user for UUID-based identification in the secret and API password from header
    secret_str = await generate_manifest_secret(profile, user, api_password=api_password)

    if not secret_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to generate addon. Please check your profile configuration.",
        )

    base_url = settings.host_url.rstrip("/")

    # Generate Kodi addon.xml
    addon_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<addon id="plugin.video.mediafusion.{profile.id[:8]}"
       name="MediaFusion - {profile.name}"
       version="1.0.0"
       provider-name="MediaFusion">
    <requires>
        <import addon="xbmc.python" version="3.0.0"/>
        <import addon="script.module.inputstreamhelper" version="0.5.0"/>
    </requires>
    <extension point="xbmc.python.pluginsource" library="addon.py">
        <provides>video</provides>
    </extension>
    <extension point="xbmc.addon.metadata">
        <summary lang="en">MediaFusion Streaming</summary>
        <description lang="en">Stream movies, TV shows, and live TV with MediaFusion</description>
        <platform>all</platform>
        <news>Initial release</news>
        <website>{base_url}</website>
        <source>{base_url}</source>
        <forum></forum>
        <email></email>
        <assets>
            <icon>icon.png</icon>
            <fanart>fanart.jpg</fanart>
        </assets>
    </extension>
    <!-- Manifest URL: {base_url}/{secret_str}/manifest.json -->
</addon>
"""

    return Response(
        content=addon_xml,
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="addon.xml"'},
    )
