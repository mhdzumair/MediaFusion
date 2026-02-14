#!/usr/bin/env python3
"""
Set up Telegram bot webhook to receive forwarded messages.

Usage:
    python scripts/setup_telegram_webhook.py [webhook_url]

Examples:
    # Using a public domain
    python scripts/setup_telegram_webhook.py https://your-domain.com/api/v1/telegram/webhook

    # Using Cloudflare Tunnel (cloudflared)
    # First run: cloudflared tunnel --url http://localhost:8001
    # Then use the provided URL:
    python scripts/setup_telegram_webhook.py https://abc123.trycloudflare.com/api/v1/telegram/webhook

    # Using localtunnel
    # First run: npx localtunnel --port 8001
    # Then use the provided URL:
    python scripts/setup_telegram_webhook.py https://abc123.loca.lt/api/v1/telegram/webhook
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def verify_url_accessible(url: str, timeout: int = 10) -> bool:
    """Verify that a URL is accessible."""
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(url) as response:
                return response.status < 500  # Any status < 500 means server is reachable
    except Exception:
        return False


async def delete_webhook():
    """Delete the current Telegram webhook."""
    from db.config import settings

    if not settings.telegram_bot_token:
        print("ERROR: telegram_bot_token not configured in .env")
        return False

    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/deleteWebhook"
            async with session.post(url, json={"drop_pending_updates": False}) as response:
                data = await response.json()
                if data.get("ok"):
                    print("✅ Webhook deleted successfully")
                    return True
                else:
                    print(f"❌ Failed to delete webhook: {data.get('description', 'Unknown error')}")
                    return False
    except Exception as e:
        print(f"ERROR deleting webhook: {e}")
        return False


async def setup_webhook(
    webhook_url: str | None = None, secret_token: str | None = None, retries: int = 5, delay: int = 5
):
    """Set up Telegram bot webhook with retry logic."""
    from db.config import settings
    import secrets

    print("=" * 60)
    print("Telegram Bot Webhook Setup")
    print("=" * 60)
    print()

    if not settings.telegram_bot_token:
        print("ERROR: telegram_bot_token not configured in .env")
        return

    if not webhook_url:
        webhook_url = input("Enter webhook URL (e.g., https://your-domain.com/api/v1/telegram/webhook): ").strip()
        if not webhook_url:
            print("No webhook URL provided. Exiting.")
            return

    # Generate or use secret token
    if not secret_token:
        if settings.telegram_webhook_secret_token:
            use_existing = input("Use existing secret token from .env? (y/n) [y]: ").strip().lower()
            if use_existing in ("", "y", "yes"):
                secret_token = settings.telegram_webhook_secret_token
                print("Using existing secret token from .env")
            else:
                generate_new = input("Generate new secret token? (y/n) [y]: ").strip().lower()
                if generate_new in ("", "y", "yes"):
                    secret_token = secrets.token_urlsafe(32)
                    print(f"Generated new secret token: {secret_token}")
                    print()
                    print("⚠️  IMPORTANT: Add this to your .env file:")
                    print(f"   telegram_webhook_secret_token={secret_token}")
                    print()
                    confirm = input("Continue with webhook setup? (y/n) [y]: ").strip().lower()
                    if confirm not in ("", "y", "yes"):
                        print("Cancelled.")
                        return
        else:
            generate = input("Generate secret token for webhook security? (y/n) [y]: ").strip().lower()
            if generate in ("", "y", "yes"):
                secret_token = secrets.token_urlsafe(32)
                print(f"Generated secret token: {secret_token}")
                print()
                print("⚠️  IMPORTANT: Add this to your .env file:")
                print(f"   telegram_webhook_secret_token={secret_token}")
                print()
                confirm = input("Continue with webhook setup? (y/n) [y]: ").strip().lower()
                if confirm not in ("", "y", "yes"):
                    print("Cancelled.")
                    return

    # Verify URL is accessible first
    print("Verifying webhook URL is accessible...")
    if await verify_url_accessible(webhook_url):
        print("✅ URL is accessible")
    else:
        print("⚠️  URL is not yet accessible (this is normal for new tunnels)")
        print("   Will retry setting webhook...")
    print()

    import aiohttp

    # Check current webhook and inform user if it's different
    async with aiohttp.ClientSession() as session:
        info_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getWebhookInfo"
        async with session.get(info_url) as info_response:
            info_data = await info_response.json()
            if info_data.get("ok"):
                current_webhook = info_data.get("result", {}).get("url")
                if current_webhook and current_webhook != webhook_url:
                    print(f"⚠️  Current webhook: {current_webhook}")
                    print(f"   Updating to: {webhook_url}")
                    print("   (Old webhook will be automatically replaced)")

    # Try setting webhook with retries
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                # Set webhook with optional secret token
                url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"
                payload = {"url": webhook_url}
                if secret_token:
                    payload["secret_token"] = secret_token
                async with session.post(url, json=payload) as response:
                    data = await response.json()

                    if data.get("ok"):
                        print("✅ Webhook set successfully!")
                        print(f"   URL: {webhook_url}")
                        print()

                        # Get webhook info
                        info_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getWebhookInfo"
                        async with session.get(info_url) as info_response:
                            info_data = await info_response.json()
                            if info_data.get("ok"):
                                webhook_info = info_data.get("result", {})
                                print("Webhook Info:")
                                print(f"   URL: {webhook_info.get('url', 'Not set')}")
                                print(f"   Pending updates: {webhook_info.get('pending_update_count', 0)}")
                                if webhook_info.get("last_error_date"):
                                    print(f"   Last error: {webhook_info.get('last_error_message')}")
                        return
                    else:
                        error_msg = data.get("description", "Unknown error")
                        print(f"❌ Attempt {attempt + 1}/{retries} failed: {error_msg}")

                        # If it's a DNS resolution error, wait and retry
                        if (
                            "resolve host" in error_msg.lower()
                            or "name or service not known" in error_msg.lower()
                            or "bad webhook" in error_msg.lower()
                        ):
                            if attempt < retries - 1:
                                print(f"   Waiting {delay} seconds for DNS propagation...")
                                await asyncio.sleep(delay)
                                delay = min(delay * 1.5, 30)  # Increase delay, max 30 seconds
                                continue
                        else:
                            # Other errors, don't retry
                            print("   Error is not retryable. Exiting.")
                            return

        except Exception as e:
            print(f"ERROR (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
            else:
                import traceback

                traceback.print_exc()

    print()
    print("⚠️  Failed to set webhook after all retries.")
    print("   This is common with Cloudflare Tunnels - they need time for DNS propagation.")
    print("   Try again in a minute or two, or use a different tunnel service.")


if __name__ == "__main__":
    webhook_url = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(setup_webhook(webhook_url))
