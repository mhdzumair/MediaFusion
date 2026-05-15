"""
Torznab API endpoints.

Implements the Torznab protocol to expose MediaFusion's torrent database
to external applications like Sonarr, Radarr, and Prowlarr.

Authentication:
- Public instances: apikey is optional
- Private instances: apikey must be API_PASSWORD
"""

import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Literal
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from db.config import settings
from db.crud.torznab import (
    get_category_for_stream,
    search_torrents_by_imdb,
    search_torrents_by_title,
    search_torrents_by_tmdb,
)
from db.database import get_async_session_context, get_read_session_context
from db.enums import MediaType
from db.retry_utils import run_db_read_with_primary_fallback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/torznab", tags=["Torznab"])

# Torznab XML namespace
TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
ATOM_NS = "http://www.w3.org/2005/Atom"

# Standard Torznab categories
MOVIE_CATEGORIES = [
    (2000, "Movies", []),
    (2010, "Movies/Foreign", []),
    (2020, "Movies/Other", []),
    (2030, "Movies/SD", []),
    (2040, "Movies/HD", []),
    (2045, "Movies/UHD", []),
    (2050, "Movies/BluRay", []),
    (2060, "Movies/3D", []),
]

TV_CATEGORIES = [
    (5000, "TV", []),
    (5010, "TV/Foreign", []),
    (5020, "TV/SD", []),
    (5030, "TV/HD", []),
    (5040, "TV/Other", []),
    (5045, "TV/UHD", []),
    (5060, "TV/Sport", []),
    (5070, "TV/Anime", []),
]


def create_xml_response(root: ET.Element) -> Response:
    """Create an XML response with proper content type."""
    xml_str = ET.tostring(root, encoding="unicode", method="xml")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    return Response(content=xml_str, media_type="application/xml; charset=utf-8")


def create_error_response(code: int, description: str) -> Response:
    """Create a Torznab error response."""
    root = ET.Element("error", code=str(code), description=description)
    return create_xml_response(root)


def validate_apikey(apikey: str | None) -> bool:
    """
    Validate the API key.

    API key format:
    - Public instances: optional
    - Private instances: "API_PASSWORD" (or legacy "API_PASSWORD:any-suffix")
    """
    # Require password only for private instances to match frontend app-config.
    require_password = bool(settings.api_password) and not settings.is_public_instance
    if not require_password:
        return True

    if not apikey:
        return False

    # Support both "password" and legacy "password:uuid" formats.
    provided_password = apikey.split(":", 1)[0]
    return provided_password == settings.api_password


def check_feature_enabled() -> Response | None:
    """Check if Torznab API is enabled. Returns error response if disabled."""
    if not settings.enable_torznab_api:
        return create_error_response(503, "Torznab API is disabled on this server")
    return None


def get_validation_sample_results(limit: int) -> list[dict]:
    """Return deterministic sample entries for indexer validation flows."""
    samples = [
        {
            "info_hash": "1111111111111111111111111111111111111111",
            "name": "MediaFusion Validation Sample Movie 1080p",
            "size": 2_147_483_648,
            "seeders": 250,
            "leechers": 10,
            "uploaded_at": datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            "resolution": "1080p",
            "media_type": MediaType.MOVIE,
            "imdb_id": "tt0000001",
            "tmdb_id": "550",
            "trackers": [],
            "source": "validation",
        },
        {
            "info_hash": "2222222222222222222222222222222222222222",
            "name": "MediaFusion Validation Sample Series S01E01 1080p",
            "size": 1_073_741_824,
            "seeders": 180,
            "leechers": 8,
            "uploaded_at": datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc),
            "resolution": "1080p",
            "media_type": MediaType.SERIES,
            "imdb_id": "tt0000002",
            "tmdb_id": "1399",
            "trackers": [],
            "source": "validation",
        },
    ]
    return samples[: min(limit, len(samples))]


def build_magnet_link(info_hash: str, name: str, trackers: list[str]) -> str:
    """Build a magnet link from torrent info."""
    encoded_name = urllib.parse.quote(name)
    magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={encoded_name}"

    for tracker in trackers[:10]:  # Limit trackers to avoid overly long URLs
        magnet += f"&tr={urllib.parse.quote(tracker)}"

    return magnet


def build_caps_xml() -> ET.Element:
    """Build the capabilities XML response."""
    root = ET.Element("caps")

    # Server info
    server = ET.SubElement(root, "server")
    server.set("version", settings.version)
    server.set("title", settings.addon_name)
    server.set("strapline", "Torznab API for MediaFusion")
    if settings.contact_email and settings.contact_email != "admin@example.com":
        server.set("email", settings.contact_email)
    server.set("url", settings.host_url)

    # Limits
    limits = ET.SubElement(root, "limits")
    limits.set("max", "100")
    limits.set("default", "50")

    # Registration (open = no registration required for API key)
    registration = ET.SubElement(root, "registration")
    registration.set("available", "yes")
    registration.set("open", "yes")

    # Searching capabilities
    searching = ET.SubElement(root, "searching")

    # General search
    search = ET.SubElement(searching, "search")
    search.set("available", "yes")
    search.set("supportedParams", "q")

    # TV search
    tv_search = ET.SubElement(searching, "tv-search")
    tv_search.set("available", "yes")
    tv_search.set("supportedParams", "q,season,ep,imdbid,tmdbid")

    # Movie search
    movie_search = ET.SubElement(searching, "movie-search")
    movie_search.set("available", "yes")
    movie_search.set("supportedParams", "q,imdbid,tmdbid")

    # Categories
    categories = ET.SubElement(root, "categories")

    for cat_id, cat_name, subcats in MOVIE_CATEGORIES:
        cat = ET.SubElement(categories, "category")
        cat.set("id", str(cat_id))
        cat.set("name", cat_name)
        for sub_id, sub_name in subcats:
            subcat = ET.SubElement(cat, "subcat")
            subcat.set("id", str(sub_id))
            subcat.set("name", sub_name)

    for cat_id, cat_name, subcats in TV_CATEGORIES:
        cat = ET.SubElement(categories, "category")
        cat.set("id", str(cat_id))
        cat.set("name", cat_name)
        for sub_id, sub_name in subcats:
            subcat = ET.SubElement(cat, "subcat")
            subcat.set("id", str(sub_id))
            subcat.set("name", sub_name)

    return root


def build_rss_xml(results: list[dict], request: Request) -> ET.Element:
    """Build an RSS feed XML from search results."""
    # Register namespaces
    ET.register_namespace("atom", ATOM_NS)
    ET.register_namespace("torznab", TORZNAB_NS)

    root = ET.Element("rss")
    root.set("version", "2.0")
    root.set(f"{{{TORZNAB_NS}}}attr", "")  # Declare namespace

    channel = ET.SubElement(root, "channel")

    # Channel metadata
    title = ET.SubElement(channel, "title")
    title.text = settings.addon_name

    description = ET.SubElement(channel, "description")
    description.text = "Torznab feed from MediaFusion"

    link = ET.SubElement(channel, "link")
    link.text = settings.host_url

    # Self link (Atom)
    atom_link = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_link.set("href", str(request.url))
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    # Add items
    for result in results:
        item = ET.SubElement(channel, "item")

        item_title = ET.SubElement(item, "title")
        item_title.text = result["name"]

        # GUID (use info_hash)
        guid = ET.SubElement(item, "guid")
        guid.text = result["info_hash"]

        # Size
        size = ET.SubElement(item, "size")
        size.text = str(result["size"])

        # Publication date
        if result.get("uploaded_at"):
            pub_date = ET.SubElement(item, "pubDate")
            dt = result["uploaded_at"]
            if isinstance(dt, datetime):
                pub_date.text = dt.strftime("%a, %d %b %Y %H:%M:%S %z")

        # Build magnet link
        magnet = build_magnet_link(
            result["info_hash"],
            result["name"],
            result.get("trackers", []),
        )

        # Link (magnet)
        link_elem = ET.SubElement(item, "link")
        link_elem.text = magnet

        # Enclosure
        enclosure = ET.SubElement(item, "enclosure")
        enclosure.set("url", magnet)
        enclosure.set("length", str(result["size"]))
        enclosure.set("type", "application/x-bittorrent;x-scheme-handler/magnet")

        # Category
        category = get_category_for_stream(result["media_type"], result.get("resolution"))
        cat_elem = ET.SubElement(item, "category")
        cat_elem.text = str(category)

        # Torznab attributes
        def add_attr(name: str, value: str | int | None):
            if value is not None:
                attr = ET.SubElement(item, f"{{{TORZNAB_NS}}}attr")
                attr.set("name", name)
                attr.set("value", str(value))

        add_attr("category", category)
        add_attr("size", result["size"])
        add_attr("infohash", result["info_hash"])
        add_attr("magneturl", magnet)

        if result.get("seeders") is not None:
            add_attr("seeders", result["seeders"])
        if result.get("leechers") is not None:
            add_attr("peers", result["leechers"])

        # External IDs
        if result.get("imdb_id"):
            add_attr("imdb", result["imdb_id"].replace("tt", ""))
        if result.get("tmdb_id"):
            add_attr("tmdbid", result["tmdb_id"])

    return root


@router.get("")
@router.get("/api")
async def torznab_api(
    request: Request,
    t: str = Query(..., description="Request type (caps, search, movie, tvsearch)"),
    apikey: str | None = Query(
        None,
        description="API key (public: optional, private: API_PASSWORD)",
    ),
    q: str | None = Query(None, description="Search query"),
    imdbid: str | None = Query(None, description="IMDb ID"),
    tmdbid: str | None = Query(None, description="TMDB ID"),
    season: int | None = Query(None, description="Season number"),
    ep: int | None = Query(None, description="Episode number"),
    cat: str | None = Query(None, description="Category IDs (comma-separated)"),
    limit: int = Query(50, ge=1, le=100, description="Result limit"),
    offset: int = Query(0, ge=0, description="Result offset"),
):
    """
    Torznab API endpoint.

    Supports the following request types:
    - t=caps: Return indexer capabilities
    - t=search: General search by query
    - t=movie: Movie search by IMDb/TMDB ID or query
    - t=tvsearch: TV search by IMDb/TMDB ID, season, episode, or query
    """
    # Check if feature is enabled
    error = check_feature_enabled()
    if error:
        return error

    # Handle caps request (no auth required)
    if t == "caps":
        return create_xml_response(build_caps_xml())

    # All other requests require authentication
    if not validate_apikey(apikey):
        return create_error_response(100, "Invalid API key")

    # Determine media type from request type
    media_type: Literal["movie", "series"] | None = None
    if t == "movie":
        media_type = "movie"
    elif t == "tvsearch":
        media_type = "series"

    # Perform search based on available parameters
    results = []

    async def _search(session_factory, search_call):
        async with session_factory() as session:
            return await search_call(session)

    if imdbid:
        # Normalize IMDb ID (add tt prefix if missing)
        if not imdbid.startswith("tt"):
            imdbid = f"tt{imdbid}"

        async def _do(session):
            return await search_torrents_by_imdb(session, imdbid, media_type, season, ep, limit)

        results = await run_db_read_with_primary_fallback(
            lambda: _search(get_read_session_context, _do),
            lambda: _search(get_async_session_context, _do),
            operation_name=f"torznab search imdb:{imdbid}",
            on_fallback=lambda exc: logger.warning(
                "Read replica conflict for torznab imdb search %s, retrying on primary: %s",
                imdbid,
                exc,
            ),
        )
    elif tmdbid:

        async def _do(session):
            return await search_torrents_by_tmdb(session, tmdbid, media_type, season, ep, limit)

        results = await run_db_read_with_primary_fallback(
            lambda: _search(get_read_session_context, _do),
            lambda: _search(get_async_session_context, _do),
            operation_name=f"torznab search tmdb:{tmdbid}",
            on_fallback=lambda exc: logger.warning(
                "Read replica conflict for torznab tmdb search %s, retrying on primary: %s",
                tmdbid,
                exc,
            ),
        )
    elif q:

        async def _do(session):
            return await search_torrents_by_title(session, q, media_type, limit=limit)

        results = await run_db_read_with_primary_fallback(
            lambda: _search(get_read_session_context, _do),
            lambda: _search(get_async_session_context, _do),
            operation_name=f"torznab search title:{q}",
            on_fallback=lambda exc: logger.warning(
                "Read replica conflict for torznab title search %r, retrying on primary: %s",
                q,
                exc,
            ),
        )
    else:
        # Some clients (e.g., Prowlarr Generic Torznab validation) issue
        if t == "search":
            results = get_validation_sample_results(limit)
        else:
            return create_error_response(200, "Missing search parameters (q, imdbid, or tmdbid required)")

    # Apply offset
    if offset > 0:
        results = results[offset:]

    logger.info(f"Torznab search: t={t}, q={q}, imdbid={imdbid}, results={len(results)}")

    return create_xml_response(build_rss_xml(results, request))
