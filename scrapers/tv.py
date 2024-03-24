import logging

import dramatiq

from db import schemas, crud
from utils import validation_helper


@dramatiq.actor(priority=5, time_limit=15 * 60 * 1000)
async def add_tv_metadata(metadata):
    metadata = schemas.TVMetaData.model_validate(metadata)
    logging.info(f"Adding TV metadata: {metadata.title}")
    try:
        metadata.streams = await validation_helper.validate_tv_metadata(metadata)
    except validation_helper.ValidationError as e:
        logging.error(f"Error validating TV metadata: {metadata.title}, {e}")
        return

    channel_id = await crud.save_tv_channel_metadata(metadata)
    logging.info(f"Added TV metadata: {metadata.title}, Channel ID: {channel_id}")
