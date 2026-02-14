"""
MediaFusion API - Main application entry point.

This module serves as the entry point for the FastAPI application.
All routes are registered via the api/app.py factory pattern.

Stremio addon routes are in api/routers/stremio/
Other API routes are in their respective modules under api/
"""

from api.app import create_app

# Create the FastAPI application using the factory
app = create_app()
