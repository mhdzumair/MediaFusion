"""
User Indexer Test API endpoints.

Provides test endpoints for Prowlarr, Jackett, and Torznab connections.
The actual indexer configuration is stored as part of the user profile.
"""

import logging
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from db.config import settings as app_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/profile/indexers", tags=["Indexers"])


# ============================================
# Pydantic Schemas
# ============================================


class IndexerInstanceInput(BaseModel):
    """Input schema for testing Prowlarr/Jackett instance."""

    enabled: bool = Field(default=False, description="Enable this indexer")
    url: str | None = Field(default=None, description="Indexer URL")
    api_key: str | None = Field(default=None, description="API key")
    use_global: bool = Field(default=True, description="Use global instance instead of custom")


class TorznabEndpointInput(BaseModel):
    """Input schema for testing a Torznab endpoint."""

    name: str = Field(..., description="Display name for the endpoint")
    url: str = Field(
        ...,
        description="Torznab API URL (include apikey in URL if required, e.g., ?apikey=xxx)",
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Custom headers for requests",
    )
    enabled: bool = Field(default=True, description="Enable this endpoint")
    categories: list[int] = Field(default_factory=list, description="Category IDs to search")
    priority: int = Field(default=1, description="Priority (lower = higher priority)")


class NewznabIndexerInput(BaseModel):
    """Input schema for testing a Newznab indexer."""

    name: str = Field(..., description="Display name for the indexer")
    url: str = Field(..., description="Newznab API URL (base URL without query params)")
    api_key: str = Field(..., description="API key for the indexer")
    enabled: bool = Field(default=True, description="Enable this indexer")
    categories: list[int] = Field(default_factory=list, description="Category IDs to search")


class IndexerHealth(BaseModel):
    """Health status of an individual indexer."""

    name: str
    id: str | int | None = None
    enabled: bool = True
    status: str = "unknown"  # healthy, unhealthy, unknown, warning, disabled
    error_message: str | None = None
    priority: int | None = None


class ConnectionTestResult(BaseModel):
    """Result of a connection test."""

    success: bool
    message: str
    indexer_count: int | None = None
    indexer_names: list[str] | None = None
    indexers: list[IndexerHealth] | None = None


class GlobalIndexerStatus(BaseModel):
    """Status of global indexer availability."""

    prowlarr_available: bool = False
    jackett_available: bool = False


# ============================================
# API Endpoints
# ============================================


@router.get("/global-status", response_model=GlobalIndexerStatus)
async def get_global_indexer_status():
    """Get the availability status of global Prowlarr/Jackett instances."""
    return GlobalIndexerStatus(
        prowlarr_available=bool(app_settings.prowlarr_url and app_settings.prowlarr_api_key),
        jackett_available=bool(app_settings.jackett_url and app_settings.jackett_api_key),
    )


@router.post("/prowlarr/test", response_model=ConnectionTestResult)
async def test_prowlarr_connection(
    config: IndexerInstanceInput,
):
    """Test connection to a Prowlarr instance and return indexer health status."""
    # Determine URL and API key to use
    if config.use_global:
        url = app_settings.prowlarr_url
        api_key = app_settings.prowlarr_api_key
    else:
        url = config.url
        api_key = config.api_key

    if not url or not api_key:
        return ConnectionTestResult(
            success=False,
            message="URL and API key are required",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get indexers list
            response = await client.get(
                f"{url}/api/v1/indexer",
                headers={"X-Api-Key": api_key},
            )
            response.raise_for_status()
            indexers = response.json()

            # Also get indexer status for health info
            status_response = await client.get(
                f"{url}/api/v1/indexerstatus",
                headers={"X-Api-Key": api_key},
            )
            indexer_statuses = {}
            if status_response.status_code == 200:
                for status_item in status_response.json():
                    indexer_statuses[status_item.get("indexerId")] = status_item

            # Build health list
            indexer_health_list = []
            healthy_count = 0

            for indexer in indexers:
                indexer_id = indexer.get("id")
                indexer_name = indexer.get("name", "Unknown")
                is_enabled = indexer.get("enable", True)
                priority = indexer.get("priority", 25)

                # Check status
                status_info = indexer_statuses.get(indexer_id, {})
                disabled_till = status_info.get("disabledTill")
                most_recent_failure = status_info.get("mostRecentFailure")

                if not is_enabled:
                    health_status = "disabled"
                    error_msg = "Disabled by user"
                elif disabled_till:
                    health_status = "unhealthy"
                    error_msg = f"Disabled until {disabled_till}"
                elif most_recent_failure:
                    health_status = "warning"
                    error_msg = most_recent_failure
                else:
                    health_status = "healthy"
                    error_msg = None
                    healthy_count += 1

                indexer_health_list.append(
                    IndexerHealth(
                        name=indexer_name,
                        id=indexer_id,
                        enabled=is_enabled,
                        status=health_status,
                        error_message=error_msg,
                        priority=priority,
                    )
                )

            # Sort by priority (lower = higher priority)
            indexer_health_list.sort(key=lambda x: (x.priority or 999, x.name))

            return ConnectionTestResult(
                success=True,
                message=f"Connected successfully. {healthy_count}/{len(indexers)} indexers healthy.",
                indexer_count=healthy_count,
                indexer_names=[i.name for i in indexer_health_list if i.status == "healthy"],
                indexers=indexer_health_list,
            )

    except httpx.HTTPStatusError as e:
        return ConnectionTestResult(
            success=False,
            message=f"HTTP error: {e.response.status_code}",
        )
    except Exception as e:
        return ConnectionTestResult(
            success=False,
            message=f"Connection failed: {str(e)}",
        )


@router.post("/jackett/test", response_model=ConnectionTestResult)
async def test_jackett_connection(
    config: IndexerInstanceInput,
):
    """Test connection to a Jackett instance and return indexer health status."""
    # Determine URL and API key to use
    if config.use_global:
        url = app_settings.jackett_url
        api_key = app_settings.jackett_api_key
    else:
        url = config.url
        api_key = config.api_key

    if not url or not api_key:
        return ConnectionTestResult(
            success=False,
            message="URL and API key are required",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get indexers list from Jackett
            response = await client.get(
                f"{url}/api/v2.0/indexers",
                params={
                    "apikey": api_key,
                    "configured": "true",
                },
            )
            response.raise_for_status()
            indexers = response.json()

            # Also try to get server config to verify connection
            config_response = await client.get(
                f"{url}/api/v2.0/server/config",
                params={"apikey": api_key},
            )
            config_response.raise_for_status()

            # Build health list
            indexer_health_list = []
            healthy_count = 0

            for indexer in indexers:
                indexer_id = indexer.get("id", "unknown")
                indexer_name = indexer.get("name", "Unknown")
                is_configured = indexer.get("configured", False)
                last_error = indexer.get("last_error")

                if not is_configured:
                    health_status = "disabled"
                    error_msg = "Not configured"
                elif last_error:
                    health_status = "unhealthy"
                    error_msg = last_error
                else:
                    health_status = "healthy"
                    error_msg = None
                    healthy_count += 1

                indexer_health_list.append(
                    IndexerHealth(
                        name=indexer_name,
                        id=indexer_id,
                        enabled=is_configured,
                        status=health_status,
                        error_message=error_msg,
                    )
                )

            # Sort alphabetically
            indexer_health_list.sort(key=lambda x: x.name)

            return ConnectionTestResult(
                success=True,
                message=f"Connected successfully. {healthy_count}/{len(indexers)} indexers healthy.",
                indexer_count=healthy_count,
                indexer_names=[i.name for i in indexer_health_list if i.status == "healthy"],
                indexers=indexer_health_list,
            )

    except httpx.HTTPStatusError as e:
        return ConnectionTestResult(
            success=False,
            message=f"HTTP error: {e.response.status_code}",
        )
    except Exception as e:
        return ConnectionTestResult(
            success=False,
            message=f"Connection failed: {str(e)}",
        )


@router.post("/torznab/test", response_model=ConnectionTestResult)
async def test_torznab_endpoint(
    endpoint: TorznabEndpointInput,
):
    """Test connection to a Torznab endpoint configuration."""
    if not endpoint.url:
        return ConnectionTestResult(
            success=False,
            message="URL is required",
        )

    try:
        # Build URL with t=caps parameter
        test_url = endpoint.url
        separator = "&" if "?" in test_url else "?"
        test_url = f"{test_url}{separator}t=caps"

        async with httpx.AsyncClient(timeout=15) as client:
            # Test with caps request (standard Torznab)
            response = await client.get(
                test_url,
                headers=endpoint.headers or {},
            )
            response.raise_for_status()

            # Parse XML response
            root = ElementTree.fromstring(response.text)

            # Try to get indexer info from caps
            server_elem = root.find("server")
            title = "Unknown"
            if server_elem is not None:
                title = server_elem.get("title", "Unknown")

            # Get categories
            cats_elem = root.find(".//categories")
            cat_count = 0
            if cats_elem is not None:
                cat_count = len(cats_elem.findall(".//category"))

            return ConnectionTestResult(
                success=True,
                message=f"Connected to {title}. {cat_count} categories available.",
                indexer_count=1,
                indexer_names=[title],
            )

    except httpx.HTTPStatusError as e:
        return ConnectionTestResult(
            success=False,
            message=f"HTTP error: {e.response.status_code}",
        )
    except ElementTree.ParseError:
        return ConnectionTestResult(
            success=False,
            message="Invalid response (not valid Torznab XML)",
        )
    except Exception as e:
        return ConnectionTestResult(
            success=False,
            message=f"Connection failed: {str(e)}",
        )


@router.post("/newznab/test", response_model=ConnectionTestResult)
async def test_newznab_indexer(
    indexer: NewznabIndexerInput,
):
    """Test connection to a Newznab indexer configuration."""
    if not indexer.url or not indexer.api_key:
        return ConnectionTestResult(
            success=False,
            message="URL and API key are required",
        )

    try:
        # Build URL with t=caps parameter (standard Newznab capability check)
        base_url = indexer.url.rstrip("/")
        # Check if it's a direct API endpoint or needs /api appended
        if not base_url.endswith("/api"):
            test_url = f"{base_url}/api"
        else:
            test_url = base_url

        params = {
            "t": "caps",
            "apikey": indexer.api_key,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(test_url, params=params)
            response.raise_for_status()

            # Parse XML response
            root = ElementTree.fromstring(response.text)

            # Check for error response
            error_elem = root.find(".//error")
            if error_elem is not None:
                error_code = error_elem.get("code", "unknown")
                error_desc = error_elem.get("description", "Unknown error")
                return ConnectionTestResult(
                    success=False,
                    message=f"API error {error_code}: {error_desc}",
                )

            # Try to get indexer info from caps
            server_elem = root.find("server")
            title = indexer.name
            if server_elem is not None:
                title = server_elem.get("title", indexer.name)

            # Get categories
            cats_elem = root.find(".//categories")
            cat_count = 0
            cat_names = []
            if cats_elem is not None:
                categories = cats_elem.findall(".//category")
                cat_count = len(categories)
                # Get top-level category names
                for cat in categories:
                    cat_name = cat.get("name")
                    if cat_name and cat.get("id"):
                        cat_names.append(cat_name)

            # Check for searching capabilities
            searching_elem = root.find(".//searching")
            search_available = False
            if searching_elem is not None:
                # Check if movie-search or tv-search is available
                movie_search = searching_elem.find("movie-search")
                tv_search = searching_elem.find("tv-search")
                search = searching_elem.find("search")
                search_available = (
                    (movie_search is not None and movie_search.get("available") == "yes")
                    or (tv_search is not None and tv_search.get("available") == "yes")
                    or (search is not None and search.get("available") == "yes")
                )

            message_parts = [f"Connected to {title}"]
            if cat_count > 0:
                message_parts.append(f"{cat_count} categories available")
            if search_available:
                message_parts.append("Search supported")

            return ConnectionTestResult(
                success=True,
                message=". ".join(message_parts) + ".",
                indexer_count=1,
                indexer_names=[title],
            )

    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP error: {e.response.status_code}"
        # Try to parse error from response body
        try:
            root = ElementTree.fromstring(e.response.text)
            error_elem = root.find(".//error")
            if error_elem is not None:
                error_desc = error_elem.get("description", "")
                if error_desc:
                    error_msg = f"API error: {error_desc}"
        except Exception:
            pass
        return ConnectionTestResult(
            success=False,
            message=error_msg,
        )
    except ElementTree.ParseError:
        return ConnectionTestResult(
            success=False,
            message="Invalid response (not valid Newznab XML)",
        )
    except Exception as e:
        return ConnectionTestResult(
            success=False,
            message=f"Connection failed: {str(e)}",
        )
