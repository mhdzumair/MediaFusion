import asyncio
import logging

from pymongo import UpdateOne

from db import database
from db.models import (
    TorrentStreams,
    MediaFusionMovieMetaData,
    MediaFusionSeriesMetaData,
    TVStreams,
    MediaFusionTVMetaData,
    MediaFusionMetaData,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def cleanup_invalid_torrents():
    """Remove torrent streams with invalid meta_ids"""
    torrent_collection = TorrentStreams.get_motor_collection()

    # Get all unique meta_ids from metadata collections
    valid_meta_ids = set()
    for meta_collection in [
        MediaFusionMovieMetaData,
        MediaFusionSeriesMetaData,
    ]:
        meta_ids = await meta_collection.distinct("_id", {"_id": {"$regex": "^mf"}})
        valid_meta_ids.update(meta_ids)

    # Delete torrents with non-existent meta_ids
    result = await torrent_collection.delete_many(
        {"meta_id": {"$regex": "^mf", "$nin": list(valid_meta_ids)}}
    )
    logger.info(f"Deleted {result.deleted_count} torrents with invalid meta_ids")


async def cleanup_torrent_streams():
    """Migrate TorrentStreams using direct MongoDB operations"""
    collection = TorrentStreams.get_motor_collection()

    # Update string audio fields to arrays
    result = await collection.update_many(
        {"audio": {"$type": "string"}}, [{"$set": {"audio": ["$audio"]}}]
    )
    logger.info(
        f"Updated {result.modified_count} documents with string audio to arrays"
    )

    # Remove unused fields
    result = await collection.update_many(
        {
            "$or": [
                {"indexer_flags": {"$exists": True}},
                {"cached": {"$exists": True}},
                {"encoder": {"$exists": True}},
            ]
        },
        {"$unset": {"indexer_flags": "", "cached": "", "encoder": ""}},
    )
    logger.info(f"Removed unused fields from {result.modified_count} documents")


async def migrate_torrent_streams():
    """Migrate TorrentStreams to new episode structure and clean up fields"""
    collection = TorrentStreams.get_motor_collection()

    # Define video file extensions
    VIDEO_EXTENSIONS = (
        ".mp4",
        ".mkv",
        ".webm",
        ".m4v",
        ".mov",
        ".m3u8",
        ".m3u",
        ".mpd",
        ".ts",
        ".mts",
        ".m2ts",
        ".m2t",
        ".mpeg",
        ".mpg",
        ".mp2",
        ".m2v",
        ".m4p",
        ".avi",
        ".wmv",
        ".flv",
        ".f4v",
        ".ogv",
        ".ogm",
        ".rm",
        ".rmvb",
        ".asf",
        ".divx",
        ".3gp",
        ".3g2",
        ".vob",
        ".ifo",
        ".bdmv",
        ".hevc",
        ".av1",
        ".vp8",
        ".vp9",
        ".mxf",
        ".dav",
        ".swf",
        ".nsv",
        ".strm",
        ".mvi",
        ".vid",
        ".amv",
        ".m4s",
        ".mqv",
        ".nuv",
        ".wtv",
        ".dvr-ms",
        ".pls",
        ".cue",
        ".dash",
        ".hls",
        ".ismv",
        ".m4f",
        ".mp4v",
        ".gif",
        ".gifv",
        ".apng",
    )

    # Create regex pattern for video extensions
    regex_pattern = (
        "(" + "|".join(ext.replace(".", "\\.") for ext in VIDEO_EXTENSIONS) + ")$"
    )

    # Convert season/episodes structure to episode_files
    pipeline = [
        {"$match": {"season": {"$exists": True}}},
        {
            "$addFields": {
                "episode_files": {
                    "$filter": {
                        "input": {
                            "$map": {
                                "input": "$season.episodes",
                                "in": {
                                    "season_number": "$season.season_number",
                                    "episode_number": "$$this.episode_number",
                                    "size": "$$this.size",
                                    "filename": "$$this.filename",
                                    "file_index": "$$this.file_index",
                                    "title": "$$this.title",
                                    "released": "$$this.released",
                                },
                            }
                        },
                        "as": "episode",
                        "cond": {
                            "$regexMatch": {
                                "input": {"$toLower": "$$episode.filename"},
                                "regex": regex_pattern,
                                "options": "i",
                            }
                        },
                    }
                },
                "audio": {
                    "$cond": {
                        "if": {"$eq": [{"$type": "$audio"}, "string"]},
                        "then": ["$audio"],
                        "else": {
                            "$cond": {
                                "if": {"$isArray": "$audio"},
                                "then": {
                                    "$reduce": {
                                        "input": "$audio",
                                        "initialValue": [],
                                        "in": {
                                            "$cond": {
                                                "if": {"$isArray": "$$this"},
                                                "then": {
                                                    "$concatArrays": [
                                                        "$$value",
                                                        "$$this",
                                                    ]
                                                },
                                                "else": {
                                                    "$concatArrays": [
                                                        "$$value",
                                                        ["$$this"],
                                                    ]
                                                },
                                            }
                                        },
                                    }
                                },
                                "else": [],
                            }
                        },
                    }
                },
            }
        },
        {"$unset": ["season"]},
        {
            "$merge": {
                "into": collection.name,
                "whenMatched": "replace",
                "whenNotMatched": "fail",
            }
        },
    ]

    logger.info("Starting TorrentStreams migration")
    try:
        await collection.aggregate(pipeline).to_list(None)
        logger.info("Completed TorrentStreams migration")
    except Exception as e:
        logger.error(f"Failed to migrate TorrentStreams: {str(e)}")
        raise


async def migrate_series_metadata():
    """Update series metadata with episode information"""
    series_collection = MediaFusionSeriesMetaData.get_motor_collection()
    temp_collection_name = "temp_series_metadata_migration"

    # Create aggregation pipeline to gather episode data and update metadata
    pipeline = [
        {
            "$lookup": {
                "from": TorrentStreams.get_motor_collection().name,
                "localField": "_id",
                "foreignField": "meta_id",
                "pipeline": [
                    {"$match": {"is_blocked": {"$ne": True}}},
                    {"$unwind": "$episode_files"},
                    {
                        "$group": {
                            "_id": {
                                "season": "$episode_files.season_number",
                                "episode": "$episode_files.episode_number",
                            },
                            "title": {"$first": "$episode_files.title"},
                            "released": {
                                "$first": {
                                    "$ifNull": [
                                        "$episode_files.released",
                                        "$created_at",
                                    ]
                                }
                            },
                            "catalogs": {"$addToSet": "$catalog"},
                            "total_streams": {"$sum": 1},
                            "last_stream_added": {"$max": "$created_at"},
                        }
                    },
                    {
                        "$project": {
                            "season_number": "$_id.season",
                            "episode_number": "$_id.episode",
                            "title": 1,
                            "released": 1,
                        }
                    },
                    {"$sort": {"season_number": 1, "episode_number": 1}},
                ],
                "as": "episodes",
            }
        },
        {
            "$addFields": {
                "episodes": {
                    "$map": {
                        "input": "$episodes",
                        "in": {
                            "season_number": "$$this.season_number",
                            "episode_number": "$$this.episode_number",
                            "title": "$$this.title",
                            "released": "$$this.released",
                        },
                    }
                }
            }
        },
        {
            "$merge": {
                "into": temp_collection_name,
                "whenMatched": "replace",
                "whenNotMatched": "insert",
            }
        },
    ]

    # Run the aggregation
    logger.info("Starting series metadata migration")
    await series_collection.aggregate(pipeline).to_list(None)

    # Rename the temporary collection
    try:
        await series_collection.database.drop_collection(series_collection.name)
        await series_collection.database[temp_collection_name].rename(
            series_collection.name
        )
        logger.info("Successfully migrated series metadata")
    except Exception as e:
        logger.error(f"Error during collection rename: {e}")
        await series_collection.database.drop_collection(temp_collection_name)
        raise


async def migrate_movie_series_metadata(meta_class):
    """Migrate movie and series metadata using aggregation pipeline"""
    meta_collection = meta_class.get_motor_collection()
    streams_collection = TorrentStreams.get_motor_collection()

    # Build aggregation pipeline to compute new fields
    pipeline = [
        {
            "$match": {
                "type": "movie" if meta_class == MediaFusionMovieMetaData else "series"
            }
        },
        {
            "$lookup": {
                "from": streams_collection.name,
                "localField": "_id",
                "foreignField": "meta_id",
                "pipeline": [
                    {"$match": {"is_blocked": {"$ne": True}}},
                    {
                        "$group": {
                            "_id": "$meta_id",
                            "catalogs": {"$addToSet": "$catalog"},
                            "total_streams": {"$sum": 1},
                            "last_stream_added": {"$max": "$created_at"},
                        }
                    },
                    {
                        "$project": {
                            "catalogs": {
                                "$reduce": {
                                    "input": "$catalogs",
                                    "initialValue": [],
                                    "in": {"$setUnion": ["$$value", "$$this"]},
                                }
                            },
                            "total_streams": 1,
                            "last_stream_added": 1,
                        }
                    },
                ],
                "as": "stream_data",
            }
        },
        {
            "$addFields": {
                "catalogs": {"$ifNull": [{"$first": "$stream_data.catalogs"}, []]},
                "total_streams": {
                    "$ifNull": [{"$first": "$stream_data.total_streams"}, 0]
                },
                "last_stream_added": {
                    "$ifNull": [
                        {"$first": "$stream_data.last_stream_added"},
                        "$last_updated_at",  # Fallback to last_updated_at if no streams
                    ]
                },
            }
        },
        {
            "$project": {
                "_id": 1,
                "catalogs": 1,
                "total_streams": 1,
                "last_stream_added": 1,
            }
        },
    ]

    try:
        bulk_operations = []
        total_processed = 0

        async for doc in meta_collection.aggregate(pipeline):
            doc_id = doc["_id"]
            update_data = {
                "catalogs": doc.get("catalogs", []),
                "total_streams": doc.get("total_streams", 0),
                "last_stream_added": doc.get("last_stream_added"),
            }

            bulk_operations.append(UpdateOne({"_id": doc_id}, {"$set": update_data}))

            total_processed += 1

            # Process in batches of 1000
            if len(bulk_operations) >= 1000:
                result = await meta_collection.bulk_write(bulk_operations)
                logger.info(
                    f"Processed batch of {len(bulk_operations)} documents. "
                    f"Total processed: {total_processed}"
                )
                bulk_operations = []

        # Process remaining operations
        if bulk_operations:
            result = await meta_collection.bulk_write(bulk_operations)
            logger.info(
                f"Processed final batch of {len(bulk_operations)} documents. "
                f"Total processed: {total_processed}"
            )

        logger.info(
            f"Successfully migrated {meta_class.__name__}. "
            f"Total documents processed: {total_processed}"
        )

    except Exception as e:
        logger.error(f"Error during migration of {meta_class.__name__}: {str(e)}")
        raise


async def migrate_tv_metadata():
    """Migrate TV metadata using aggregation pipeline"""
    meta_collection = MediaFusionTVMetaData.get_motor_collection()
    streams_collection = TVStreams.get_motor_collection()

    pipeline = [
        {"$match": {"type": "tv"}},
        {
            "$lookup": {
                "from": streams_collection.name,
                "localField": "_id",
                "foreignField": "meta_id",
                "pipeline": [
                    {
                        "$match": {
                            "is_working": True,
                        }
                    },
                    {
                        "$group": {
                            "_id": "$meta_id",
                            "total_streams": {"$sum": 1},
                            "last_stream_added": {"$max": "$created_at"},
                        }
                    },
                ],
                "as": "stream_data",
            }
        },
        {
            "$addFields": {
                "total_streams": {
                    "$ifNull": [{"$first": "$stream_data.total_streams"}, 0]
                },
                "last_stream_added": {
                    "$ifNull": [
                        {"$first": "$stream_data.last_stream_added"},
                        "$last_updated_at",
                    ]
                },
            }
        },
        {"$project": {"_id": 1, "total_streams": 1, "last_stream_added": 1}},
    ]

    try:
        bulk_operations = []
        total_processed = 0

        async for doc in meta_collection.aggregate(pipeline):
            doc_id = doc["_id"]
            update_data = {
                "total_streams": doc.get("total_streams", 0),
                "last_stream_added": doc.get("last_stream_added"),
            }

            bulk_operations.append(UpdateOne({"_id": doc_id}, {"$set": update_data}))

            total_processed += 1

            # Process in batches of 1000
            if len(bulk_operations) >= 1000:
                result = await meta_collection.bulk_write(bulk_operations)
                logger.info(
                    f"Processed batch of {len(bulk_operations)} TV documents. "
                    f"Total processed: {total_processed}"
                )
                bulk_operations = []

        # Process remaining operations
        if bulk_operations:
            result = await meta_collection.bulk_write(bulk_operations)
            logger.info(
                f"Processed final batch of {len(bulk_operations)} TV documents. "
                f"Total processed: {total_processed}"
            )

        logger.info(
            f"Successfully migrated TV metadata. "
            f"Total documents processed: {total_processed}"
        )

    except Exception as e:
        logger.error(f"Error during TV metadata migration: {str(e)}")
        raise


async def cleanup_metadata():
    """Unset unused fields in metadata collections and remove documents with 0 total_streams"""
    collection = MediaFusionMetaData.get_motor_collection()

    # Unset unused fields "streams"
    result = await collection.update_many(
        {"streams": {"$exists": True}}, {"$unset": {"streams": ""}}
    )
    logger.info(f"Removed 'streams' field from {result.modified_count} documents")

    # Remove documents with 0 total_streams and _id starting with "mf"
    result = await collection.delete_many(
        {"total_streams": 0, "_id": {"$regex": "^mf"}}
    )
    logger.info(f"Deleted {result.deleted_count} documents with 0 total_streams")


async def main():
    logger.info("Starting migration")
    await database.init(allow_index_dropping=True)

    # Migrate TorrentStreams first
    logger.info("Migrating TorrentStreams")
    await cleanup_invalid_torrents()
    await cleanup_torrent_streams()
    await migrate_torrent_streams()

    # Migrate metadata collections
    await migrate_movie_series_metadata(MediaFusionMovieMetaData)
    await migrate_movie_series_metadata(MediaFusionSeriesMetaData)
    await migrate_tv_metadata()

    await migrate_series_metadata()
    await cleanup_metadata()

    logger.info("Migration completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
