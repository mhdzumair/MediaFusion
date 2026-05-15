#!/usr/bin/env python3
"""
Generate a Telethon session string for Telegram API access.

This script helps you create a session string that can be used in the
telegram_session_string environment variable for channel scraping.

Usage:
    python scripts/generate_telethon_session.py

You'll need:
- API ID and API Hash from https://my.telegram.org
- Your phone number registered with Telegram
- Access to the verification code sent to your Telegram

The generated session string should be added to your .env file:
    telegram_api_id=12345678
    telegram_api_hash=your_api_hash_here
    telegram_session_string=your_generated_session_string
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def generate_session():
    """Generate a Telethon session string interactively."""
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("ERROR: Telethon not installed.")
        print("Install with: pip install telethon")
        return

    print("=" * 60)
    print("Telethon Session String Generator")
    print("=" * 60)
    print()
    print("This script will help you generate a session string for")
    print("Telegram channel scraping. You'll need:")
    print("  1. API ID and API Hash from https://my.telegram.org")
    print("  2. Your phone number registered with Telegram")
    print("  3. Access to verification code sent to your Telegram")
    print()

    # Check if credentials are in .env
    from db.config import settings

    api_id = settings.telegram_api_id
    api_hash = settings.telegram_api_hash

    if api_id and api_hash:
        use_env = input(f"Use API credentials from .env? (API ID: {api_id}) [y/n]: ").strip().lower()
        if use_env not in ("y", "yes", ""):
            api_id = None
            api_hash = None

    if not api_id:
        api_id_str = input("Enter your API ID: ").strip()
        try:
            api_id = int(api_id_str)
        except ValueError:
            print("ERROR: API ID must be a number")
            return

    if not api_hash:
        api_hash = input("Enter your API Hash: ").strip()
        if not api_hash:
            print("ERROR: API Hash is required")
            return

    print()
    print("Connecting to Telegram...")
    print()

    # Create client with StringSession
    client = TelegramClient(StringSession(), api_id, api_hash)

    try:
        await client.start()
        session_string = client.session.save()

        print()
        print("=" * 60)
        print("SUCCESS! Session string generated.")
        print("=" * 60)
        print()
        print("Add the following to your .env file:")
        print()
        print(f"telegram_api_id={api_id}")
        print(f"telegram_api_hash={api_hash}")
        print(f"telegram_session_string={session_string}")
        print()
        print("=" * 60)
        print()
        print("IMPORTANT:")
        print("- Keep your session string secret!")
        print("- Don't share it or commit it to version control.")
        print("- If compromised, revoke it at https://my.telegram.org")
        print()

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(generate_session())
