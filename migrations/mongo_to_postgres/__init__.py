"""
MongoDB to PostgreSQL migration module for MediaFusion 5.0.

This module handles the full migration from the old MongoDB schema
to the new PostgreSQL architecture with:
- Integer primary keys
- Unified stream architecture (Stream base + type-specific tables)
- Multi-provider images and ratings
- Proper normalization and relationships

CLI Usage:
    python -m migrations.mongo_to_postgres migrate --mongo-uri ... --postgres-uri ...
    python -m migrations.mongo_to_postgres status --mongo-uri ... --postgres-uri ...
    python -m migrations.mongo_to_postgres verify --mongo-uri ... --postgres-uri ...
"""

from migrations.mongo_to_postgres.cli import app
from migrations.mongo_to_postgres.metadata_migrator import (
    DatabaseMigration,
    MetadataMigrator,
)
from migrations.mongo_to_postgres.stats import (
    CatalogStatsComputer,
    CollectionCountChecker,
    CollectionStatus,
    MigrationStats,
    ResourceTracker,
    Stats,
)
from migrations.mongo_to_postgres.stream_migrator import StreamMigrator

__all__ = [
    "app",
    "DatabaseMigration",
    "MetadataMigrator",
    "StreamMigrator",
    "MigrationStats",
    "Stats",
    "CollectionStatus",
    "CollectionCountChecker",
    "ResourceTracker",
    "CatalogStatsComputer",
]
