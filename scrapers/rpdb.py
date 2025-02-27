from db import schemas


async def update_rpdb_posters(
    metas: schemas.Metas, user_data: schemas.UserData, catalog_type: str
) -> schemas.Metas:
    """Update multiple meta items with RPDB posters in an optimized way."""
    if not user_data.rpdb_config or catalog_type not in ["movie", "series"]:
        return metas

    rpdb_poster_base = f"https://api.ratingposterdb.com/{user_data.rpdb_config.api_key}/imdb/poster-default/{{}}.jpg?fallback=true"

    # Update meta items with new poster URLs
    for meta in metas.metas:
        if meta.id.startswith("tt"):
            meta.poster = rpdb_poster_base.format(meta.id)
    return metas


async def update_rpdb_poster(
    meta_item: schemas.MetaItem, user_data: schemas.UserData, catalog_type: str
) -> schemas.MetaItem:
    """Update single meta item with RPDB poster."""
    if not user_data.rpdb_config or catalog_type not in ["movie", "series"]:
        return meta_item

    if meta_item.meta.id.startswith("tt"):
        meta_item.meta.poster = f"https://api.ratingposterdb.com/{user_data.rpdb_config.api_key}/imdb/poster-default/{meta_item.meta.id}.jpg?fallback=true"
    return meta_item
