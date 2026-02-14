#!/usr/bin/env python3
"""
Check if forwarded Telegram content has been stored in the database.

Usage:
    python scripts/check_forwarded_content.py [user_id] [chat_id]

Example:
    python scripts/check_forwarded_content.py 123456789
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def check_forwarded_content(user_id: str | None = None, chat_id: str | None = None):
    """Check for forwarded Telegram content in the database."""
    from db import crud
    from db.database import get_async_session_context
    from db.models import TelegramStream
    from sqlmodel import select

    print("=" * 60)
    print("Check Forwarded Telegram Content")
    print("=" * 60)
    print()

    async with get_async_session_context() as session:
        # Build query
        query = select(TelegramStream).join(TelegramStream.stream)

        if user_id:
            query = query.where(TelegramStream.chat_id == str(user_id))
        if chat_id:
            query = query.where(TelegramStream.chat_id == str(chat_id))

        # Filter for bot-forwarded content
        query = query.where(TelegramStream.source == "telegram_bot")

        result = await session.exec(query)
        streams = result.all()

        print(f"Found {len(streams)} forwarded content streams")
        print()

        if streams:
            for i, tg_stream in enumerate(streams, 1):
                stream = tg_stream.stream
                print(f"Stream {i}:")
                print(f"  Name: {stream.name}")
                print(f"  File: {tg_stream.file_name}")
                print(f"  Size: {tg_stream.size} bytes" if tg_stream.size else "  Size: Unknown")
                print(f"  Chat ID: {tg_stream.chat_id}")
                print(f"  Message ID: {tg_stream.message_id}")
                print(f"  Source: {tg_stream.source}")
                print(f"  Uploader: {tg_stream.uploader}")
                print(f"  Created: {stream.created_at}")
                if stream.meta_id:
                    print(f"  Meta ID: {stream.meta_id}")
                print()
        else:
            print("No forwarded content found in database.")
            print()
            print("To forward content:")
            print("  1. Send a video to your Telegram bot")
            print("  2. Optionally include IMDb ID in caption (e.g., tt1234567)")
            print("  3. If no IMDb ID, reply to the bot with the IMDb ID")
            print()

        # Check pending content
        from utils.telegram_bot import telegram_content_bot

        if user_id:
            pending_count = telegram_content_bot.get_pending_count(int(user_id))
            if pending_count > 0:
                print(f"⚠️  Found {pending_count} pending content(s) for user {user_id}")
                print("   Reply to the bot with an IMDb ID to link them.")


if __name__ == "__main__":
    user_id = sys.argv[1] if len(sys.argv) > 1 else None
    chat_id = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(check_forwarded_content(user_id, chat_id))
