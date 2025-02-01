from datetime import datetime

import pymongo
from beanie import Document, free_fall_migration
from pydantic import BaseModel, Field
from pymongo import IndexModel, ASCENDING, DESCENDING


# Old Models
class OldMediaFusionMetaData(Document):
    id: str
    title: str
    is_custom: bool = False
    aka_titles: list[str] | None = None
    year: int | None = None
    poster: str | None = None
    is_poster_working: bool | None = True
    is_add_title_to_poster: bool | None = False
    background: str | None = None
    type: str
    description: str | None = None
    runtime: str | None = None
    website: str | None = None
    genres: list[str] | None = None
    last_updated_at: datetime = Field(default_factory=datetime.now)
    catalogs: list[str] = Field(default_factory=list)
    total_streams: int = 0
    last_stream_added: datetime | None = None

    class Settings:
        name = "MediaFusionMetaData"
        is_root = True
        indexes = [
            # Partial index for custom IDs (mf prefix) to enforce uniqueness
            IndexModel(
                [("title", ASCENDING), ("year", ASCENDING), ("type", ASCENDING)],
                unique=True,
                partialFilterExpression={"is_custom": True},
                name="unique_title_year_type_for_mf_id",
            ),
            # Regular index for all documents
            IndexModel(
                [("title", ASCENDING), ("year", ASCENDING), ("type", ASCENDING)],
                unique=False,
            ),
            IndexModel(
                [("title", pymongo.TEXT), ("aka_titles", pymongo.TEXT)],
                weights={"title": 10, "aka_titles": 5},  # Prioritize main title matches
            ),
            IndexModel([("year", ASCENDING), ("end_year", ASCENDING)]),
            IndexModel(
                [
                    ("type", ASCENDING),
                    ("catalogs", ASCENDING),
                    ("last_stream_added", DESCENDING),
                ]
            ),
            IndexModel(
                [
                    ("type", ASCENDING),
                    ("genres", ASCENDING),
                    ("last_stream_added", DESCENDING),
                ]
            ),
            IndexModel([("_class_id", ASCENDING)]),
        ]


# New Models
class CatalogStats(BaseModel):
    total_streams: int = 0
    last_stream_added: datetime | None = None


class NewMediaFusionMetaData(Document):
    id: str
    title: str
    is_custom: bool = False
    aka_titles: list[str] | None = None
    year: int | None = None
    poster: str | None = None
    is_poster_working: bool | None = True
    is_add_title_to_poster: bool | None = False
    background: str | None = None
    type: str
    description: str | None = None
    runtime: str | None = None
    website: str | None = None
    genres: list[str] | None = None
    last_updated_at: datetime = Field(default_factory=datetime.now)
    total_streams: int = 0
    last_stream_added: datetime | None = None

    class Settings:
        name = "MediaFusionMetaData"
        is_root = True
        indexes = [
            # Partial index for custom IDs (mf prefix) to enforce uniqueness
            IndexModel(
                [("title", ASCENDING), ("year", ASCENDING), ("type", ASCENDING)],
                unique=True,
                partialFilterExpression={"is_custom": True},
                name="unique_title_year_type_for_mf_id",
            ),
            # Regular index for all documents
            IndexModel(
                [("title", ASCENDING), ("year", ASCENDING), ("type", ASCENDING)],
                unique=False,
            ),
            IndexModel(
                [("title", pymongo.TEXT), ("aka_titles", pymongo.TEXT)],
                weights={"title": 10, "aka_titles": 5},  # Prioritize main title matches
            ),
            IndexModel([("year", ASCENDING), ("end_year", ASCENDING)]),
            IndexModel([("_class_id", ASCENDING)]),
            IndexModel([("type", ASCENDING), ("genres", ASCENDING)]),
            IndexModel(
                [
                    ("type", ASCENDING),
                    ("catalog_stats.catalog", ASCENDING),
                    ("catalog_stats.total_streams", ASCENDING),
                ]
            ),
        ]


class Forward:
    @free_fall_migration(
        document_models=[OldMediaFusionMetaData, NewMediaFusionMetaData]
    )
    async def update_catalog_stats(self, session):
        """Update metadata with per-catalog statistics using direct aggregation"""
        metadata_collection = NewMediaFusionMetaData.get_motor_collection()
        # Create new indexes first
        await metadata_collection.create_indexes(
            NewMediaFusionMetaData.Settings.indexes
        )
        print("New indexes created successfully")

        print("Converting to array-based catalog stats...")
        pipeline = [
            {
                "$lookup": {
                    "from": "TorrentStreams",
                    "let": {"meta_id": "$_id"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {"$eq": ["$meta_id", "$$meta_id"]},
                                "is_blocked": {"$ne": True},
                            }
                        },
                        {"$unwind": "$catalog"},
                        {
                            "$group": {
                                "_id": "$catalog",
                                "total_streams": {"$sum": 1},
                                "last_stream_added": {"$max": "$created_at"},
                            }
                        },
                    ],
                    "as": "catalog_data",
                }
            },
            {
                "$set": {
                    "catalog_stats": {
                        "$map": {
                            "input": "$catalog_data",
                            "as": "cd",
                            "in": {
                                "catalog": "$$cd._id",
                                "total_streams": "$$cd.total_streams",
                                "last_stream_added": "$$cd.last_stream_added",
                            },
                        }
                    }
                }
            },
            {"$unset": ["catalogs", "catalog_data"]},
            {
                "$merge": {
                    "into": "MediaFusionMetaData",
                    "on": "_id",
                    "whenMatched": "merge",
                }
            },
        ]

        print("Executing aggregation pipeline...")
        await metadata_collection.aggregate(pipeline).to_list(None)
        print("Forward migration completed successfully")


class Backward:
    @free_fall_migration(document_models=[OldMediaFusionMetaData])
    async def revert_catalog_stats(self, session):
        """Remove catalog_stats field and revert indexes"""
        metadata_collection = OldMediaFusionMetaData.get_motor_collection()

        # Drop the new indexes first
        await metadata_collection.drop_indexes()

        # Recreate old indexes
        await metadata_collection.create_indexes(
            OldMediaFusionMetaData.Settings.indexes
        )

        print("Converting back to catalogs array...")
        pipeline = [
            {"$set": {"catalogs": "$catalog_stats.catalog"}},
            {"$unset": "catalog_stats"},
            {
                "$merge": {
                    "into": "MediaFusionMetaData",
                    "on": "_id",
                    "whenMatched": "merge",
                }
            },
        ]

        await metadata_collection.aggregate(pipeline).to_list(None)
        print("Backward migration completed successfully")
