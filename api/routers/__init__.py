"""API routers package.

This package contains all API routers organized into logical subpackages:
- stremio: Stremio addon routes (home, manifest, catalog, meta, stream, etc.)
- user: User-related routes (auth, profiles, watch_history, downloads, library)
- admin: Admin routes (admin, scheduler, cache, database_admin, contribution_settings)
- content: Content routes (catalog API, contributions, content_import, voting, suggestions)
- rss: RSS routes (rss_feeds, user_rss)
- kodi: Kodi device pairing routes (setup code, manifest association)
- frontend: Frontend API routes
"""

# Note: Routers are imported directly in api/app.py to avoid circular imports
# This package serves as documentation and provides a clean namespace

__all__ = [
    "stremio",
    "user",
    "admin",
    "content",
    "rss",
    "kodi",
    "frontend",
]
