#!/usr/bin/env python3
"""
Delete Telegram bot webhook.

This is useful when:
- Switching tunnel services
- Testing locally
- Temporarily disabling webhook

Usage:
    python scripts/delete_telegram_webhook.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def main():
    """Delete the Telegram webhook."""
    from db.config import settings
    import aiohttp

    print("=" * 60)
    print("Delete Telegram Bot Webhook")
    print("=" * 60)
    print()

    if not settings.telegram_bot_token:
        print("ERROR: telegram_bot_token not configured in .env")
        return

    # Get current webhook info
    async with aiohttp.ClientSession() as session:
        info_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getWebhookInfo"
        async with session.get(info_url) as info_response:
            info_data = await info_response.json()
            if info_data.get("ok"):
                webhook_info = info_data.get("result", {})
                current_url = webhook_info.get("url")
                if current_url:
                    print(f"Current webhook URL: {current_url}")
                    print(f"Pending updates: {webhook_info.get('pending_update_count', 0)}")
                    print()
                else:
                    print("No webhook currently set.")
                    return

    # Confirm deletion
    confirm = input("Delete webhook? (y/n) [n]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled.")
        return

    # Delete webhook
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/deleteWebhook"
            # Option to drop pending updates
            drop_updates = input("Drop pending updates? (y/n) [n]: ").strip().lower()
            payload = {"drop_pending_updates": drop_updates in ("y", "yes")}

            async with session.post(url, json=payload) as response:
                data = await response.json()

                if data.get("ok"):
                    print("✅ Webhook deleted successfully!")
                    if payload["drop_pending_updates"]:
                        print("   Pending updates have been dropped.")
                else:
                    print(f"❌ Failed to delete webhook: {data.get('description', 'Unknown error')}")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
