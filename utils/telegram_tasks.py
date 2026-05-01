import logging

import PTT
from sqlalchemy.exc import IntegrityError

from api.task_queue import actor
from db import crud
from db.database import get_background_session
from db.redis_database import REDIS_ASYNC_CLIENT
from scrapers.non_torrent_background_scraper import (
    _build_telegram_candidate,
    _is_adult_title,
    _iter_unique,
    _resolve_metadata,
    _tmp_external_id,
)
from scrapers.telegram import telegram_scraper

logger = logging.getLogger(__name__)

_AUTO_MATCH_MAX_CANDIDATES = 1


def _infer_media_type(file_name: str | None) -> str:
    """Derive movie vs series from PTT-parsed filename."""
    if not file_name:
        return "movie"
    try:
        parsed = PTT.parse_title(file_name, True)
        if parsed.get("seasons") or parsed.get("episodes"):
            return "series"
    except Exception:
        pass
    return "movie"


@actor(queue_name="telegram_contrib")
async def analyze_telegram_item(user_id: int, item_id: str) -> None:
    """Background analysis of a single batch item: PTT parse + IMDb search."""
    from utils.telegram_bot import BatchItemStatus, telegram_content_bot  # noqa: PLC0415

    batch = await telegram_content_bot.aget_batch(user_id)
    if not batch:
        return

    item = batch.get_item(item_id)
    if not item or item.status != BatchItemStatus.PENDING_ANALYSIS:
        return

    inferred_type = _infer_media_type(item.file_name)
    item.inferred_media_type = inferred_type

    video_info = {
        "file_id": item.file_id,
        "file_unique_id": item.file_unique_id,
        "file_name": item.file_name,
        "file_size": item.file_size,
        "mime_type": item.mime_type,
    }

    try:
        analysis = await telegram_content_bot._analyze_video(video_info, inferred_type)
    except Exception as e:
        logger.warning(f"Analysis error for item {item_id}: {e}")
        item.status = BatchItemStatus.NO_MATCH
        item.error = "Analysis failed"
        batch.touch()
        await telegram_content_bot._aset_batch(batch)
        await telegram_content_bot.render_batch_summary(batch)
        return

    if not analysis.get("success"):
        item.status = BatchItemStatus.NO_MATCH
        item.error = analysis.get("error", "Analysis failed")
    else:
        item.analysis_result = analysis
        matches = analysis.get("matches") or []
        item.imdb_candidates = matches

        parsed_title = analysis.get("parsed_title")
        if parsed_title and len(matches) == _AUTO_MATCH_MAX_CANDIDATES:
            item.selected_match = matches[0]
            item.status = BatchItemStatus.AUTO_MATCHED
        elif matches:
            item.status = BatchItemStatus.NEEDS_REVIEW
        else:
            item.status = BatchItemStatus.NO_MATCH

    batch.touch()
    await telegram_content_bot._aset_batch(batch)
    await telegram_content_bot.render_batch_summary(batch)


@actor(queue_name="telegram_contrib")
async def import_telegram_item(user_id: int, item_id: str) -> None:
    """Background import of a single AUTO_MATCHED batch item."""
    from utils.telegram_bot import (  # noqa: PLC0415
        BatchItemStatus,
        ContentType,
        ConversationState,
        ConversationStep,
        telegram_content_bot,
    )

    batch = await telegram_content_bot.aget_batch(user_id)
    if not batch:
        return

    item = batch.get_item(item_id)
    if not item or item.status != BatchItemStatus.IMPORTING:
        return

    mf_user_id = await telegram_content_bot._get_mediafusion_user_id(user_id)
    if not mf_user_id:
        item.status = BatchItemStatus.FAILED
        item.error = "Account not linked"
        batch.touch()
        await telegram_content_bot._aset_batch(batch)
        await telegram_content_bot.render_batch_summary(batch)
        return

    # Build a temporary ConversationState so _import_video can run unchanged
    raw_input = {
        "file_id": item.file_id,
        "file_unique_id": item.file_unique_id,
        "file_name": item.file_name,
        "file_size": item.file_size,
        "mime_type": item.mime_type,
    }
    temp_state = ConversationState(
        user_id=user_id,
        chat_id=batch.chat_id,
        content_type=ContentType.VIDEO,
        raw_input=raw_input,
        media_type=item.inferred_media_type or "movie",
        analysis_result=item.analysis_result,
        matches=item.imdb_candidates,
        selected_match=item.selected_match,
        metadata_overrides=item.metadata_overrides or {},
        original_message_id=item.original_message_id,
        step=ConversationStep.IMPORTING,
    )

    try:
        result = await telegram_content_bot._import_video(temp_state, mf_user_id)
        if result.get("success"):
            item.status = BatchItemStatus.IMPORTED
            item.error = None
        else:
            item.status = BatchItemStatus.FAILED
            item.error = result.get("error", "Import failed")
    except Exception as e:
        logger.exception(f"Import error for batch item {item_id}: {e}")
        item.status = BatchItemStatus.FAILED
        item.error = str(e)

    batch.touch()
    await telegram_content_bot._aset_batch(batch)
    await telegram_content_bot.render_batch_summary(batch)


@actor(queue_name="telegram_contrib", time_limit=30 * 60 * 1000)
async def scrape_telegram_channel_for_user(
    user_id: int,
    chat_id: int,
    progress_message_id: int,
    channel: str,
    notification_chat_id: str | None,
) -> None:
    """Scrape a user-submitted public Telegram channel and report progress to the submitter."""
    from utils.telegram_bot import telegram_content_bot  # noqa: PLC0415

    scrape_job_key = f"telegram:scrape_job:{user_id}"

    async def _update(text: str) -> None:
        await telegram_content_bot.edit_message(chat_id, progress_message_id, text)

    try:
        await _update(f"🔍 *Scraping {channel}*\n\n⏳ Connecting to Telegram...")

        client = await telegram_scraper.get_client()
        if not client:
            await _update(
                "❌ *Scraping Failed*\n\n"
                "Telegram scraper is not configured on this server.\n"
                "Contact your admin to enable Telegram scraping."
            )
            return

        try:
            entity = await client.get_entity(channel)
            chat_title = getattr(entity, "title", None) or channel
            chat_username = getattr(entity, "username", None)
        except Exception as e:
            await _update(
                f"❌ *Channel Not Found*\n\nCould not resolve `{channel}`.\n\nMake sure it's a public channel or group."
            )
            logger.warning("Failed to resolve channel %s for user %s: %s", channel, user_id, e)
            return

        display_name = f"@{chat_username}" if chat_username else chat_title
        await _update(f"📥 *Scraping {display_name}*\n\n⏳ Fetching messages...")

        raw_candidates = await telegram_scraper.scrape_feed_candidates(extra_channels=[channel])
        candidates = [c for c in (_build_telegram_candidate(item) for item in raw_candidates) if c is not None]

        if not candidates:
            await _update(f"ℹ️ *Scraping Complete*\n\nChannel: {display_name}\nNo new video content found.")
            return

        await _update(f"⚙️ *Processing {display_name}*\n\nFound {len(candidates)} candidates, importing...")

        metrics = {"processed": 0, "created": 0, "skipped": 0, "errors": 0}

        async with get_background_session() as session:
            for candidate in _iter_unique(candidates, lambda item: item.dedupe_key):
                metrics["processed"] += 1
                try:
                    if await crud.telegram_stream_exists(
                        session,
                        chat_id=candidate.chat_id,
                        message_id=candidate.message_id,
                    ):
                        metrics["skipped"] += 1
                        continue

                    if _is_adult_title(candidate.inferred_title):
                        metrics["skipped"] += 1
                        continue

                    metadata_external_id = candidate.imdb_id or _tmp_external_id("telegram", candidate.dedupe_key)
                    metadata = await _resolve_metadata(
                        session=session,
                        title=candidate.inferred_title,
                        media_type=candidate.inferred_media_type,
                        year=candidate.inferred_year,
                        external_id=metadata_external_id,
                    )
                    if not metadata:
                        metrics["skipped"] += 1
                        continue

                    await crud.create_telegram_stream(
                        session,
                        chat_id=candidate.chat_id,
                        message_id=candidate.message_id,
                        name=candidate.name,
                        media_id=metadata.id,
                        chat_username=candidate.chat_username,
                        file_id=candidate.file_id,
                        file_unique_id=candidate.file_unique_id,
                        file_name=candidate.file_name,
                        mime_type=candidate.mime_type,
                        size=candidate.size,
                        posted_at=candidate.posted_at,
                        source="telegram_user_scrape",
                        resolution=candidate.resolution,
                        codec=candidate.codec,
                        quality=candidate.quality,
                        bit_depth=candidate.bit_depth,
                        uploader=candidate.uploader,
                        release_group=candidate.release_group,
                        is_remastered=candidate.is_remastered,
                        is_proper=candidate.is_proper,
                        is_repack=candidate.is_repack,
                        is_extended=candidate.is_extended,
                        is_dubbed=candidate.is_dubbed,
                        is_subbed=candidate.is_subbed,
                        season_number=candidate.season_number,
                        episode_number=candidate.episode_number,
                        episode_end=candidate.episode_end,
                    )
                    await session.commit()
                    metrics["created"] += 1
                except IntegrityError:
                    await session.rollback()
                    metrics["skipped"] += 1
                except Exception as exc:
                    await session.rollback()
                    metrics["errors"] += 1
                    logger.exception("Error processing candidate from %s: %s", channel, exc)

        summary = (
            f"✅ *Scrape Complete*\n\n"
            f"Channel: {display_name}\n"
            f"📊 Results:\n"
            f"• Imported: {metrics['created']}\n"
            f"• Skipped: {metrics['skipped']}\n"
            f"• Errors: {metrics['errors']}"
        )
        await _update(summary)

        if notification_chat_id:
            try:
                await telegram_content_bot.send_reply(
                    int(notification_chat_id),
                    f"📡 *Channel Scrape Completed*\n\n"
                    f"Channel: {display_name}\n"
                    f"Submitted by: user `{user_id}`\n"
                    f"• Imported: {metrics['created']}\n"
                    f"• Skipped: {metrics['skipped']}\n"
                    f"• Errors: {metrics['errors']}",
                )
            except Exception as notify_err:
                logger.warning("Failed to send scrape notification: %s", notify_err)

    except Exception as exc:
        logger.exception("Unexpected error in scrape_telegram_channel_for_user: %s", exc)
        try:
            await _update("❌ *Scraping Failed*\n\nAn unexpected error occurred.")
        except Exception:
            pass
    finally:
        await REDIS_ASYNC_CLIENT.delete(scrape_job_key)
