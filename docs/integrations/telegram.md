# Telegram Integration

MediaFusion has two distinct Telegram integrations:

1. **Telegram Bot** — a bot users can interact with to contribute streams, manage their account, and receive moderation notifications
2. **Telegram Channel Scraper** — a background scraper that watches configured channels for media files and imports them as streams

Both are optional. Neither is required for basic MediaFusion operation.

---

## Telegram Bot

### What it does

- Lets users contribute video files from Telegram directly into MediaFusion
- Sends moderation notifications to a configured admin chat
- Supports batch import, metadata detection, and IMDb matching
- Handles user authentication linked to MediaFusion accounts

### Setup

**Step 1: Create a bot**

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the **Bot API token**

**Step 2: Configure MediaFusion**

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_BOT_USERNAME=your_bot_username   # without @
```

**Step 3: Set up a webhook (for production)**

The bot uses webhooks rather than polling. Set a webhook secret to validate incoming updates:

```bash
TELEGRAM_WEBHOOK_SECRET_TOKEN=$(openssl rand -hex 16)
```

MediaFusion automatically registers the webhook at `HOST_URL/bot/webhook` on startup when `TELEGRAM_BOT_TOKEN` is set.

**Step 4: (Optional) Admin notifications**

To receive moderation alerts (new contributions, flagged content) in a Telegram chat:

```bash
TELEGRAM_CHAT_ID=-1001234567890   # get this from @userinfobot or similar
```

**Step 5: (Optional) Backup channel for contributed files**

To store contributed video files in a private Telegram channel:

```bash
TELEGRAM_BACKUP_CHANNEL_ID=-1009876543210
```

---

## Telegram Channel Scraper

The background scraper watches one or more Telegram channels and imports media files it finds as streams in MediaFusion. This requires a **Telegram user account API** (not a bot token).

!!! warning "User API credentials required"
    This scraper uses the Telegram user API (MTProto), not the bot API. You need API credentials from [my.telegram.org](https://my.telegram.org) and an active session string.

### Setup

**Step 1: Get Telegram API credentials**

1. Go to [my.telegram.org](https://my.telegram.org) → **API development tools**
2. Create an application and note the **API ID** and **API Hash**

**Step 2: Generate a session string**

MediaFusion uses [Grammers](https://github.com/Lonami/grammers) for the Telegram client. Generate a session string once and store it:

```bash
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash
```

Run the session generator (see `backend/src/bin/telegram_session.rs`):

```bash
cargo run --manifest-path backend/Cargo.toml --bin telegram_session
```

Copy the output session string into your config:

```bash
TELEGRAM_GRAMMERS_SESSION=your_session_string
```

**Step 3: Configure channels to scrape**

```bash
TELEGRAM_SCRAPING_CHANNELS=channelname1,channelname2,-1001234567890
```

Accepts channel usernames (without `@`) or numeric channel IDs.

**Step 4: Tune scraping limits**

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_SCRAPE_MESSAGE_LIMIT` | `100` | Max messages to fetch per channel per run |
| `TELEGRAM_BACKGROUND_SCRAPER_CRONTAB` | *(built-in)* | Crontab for the background scraper |
| `DISABLE_TELEGRAM_BACKGROUND_SCRAPER` | `false` | Disable the Telegram background scraper |

---

## Configuration reference

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot API token from @BotFather |
| `TELEGRAM_BOT_USERNAME` | Bot username (without @) |
| `TELEGRAM_WEBHOOK_SECRET_TOKEN` | Webhook validation secret |
| `TELEGRAM_CHAT_ID` | Admin chat ID for moderation notifications |
| `TELEGRAM_BACKUP_CHANNEL_ID` | Channel to store contributed files |
| `TELEGRAM_API_ID` | Telegram user API ID (for channel scraper) |
| `TELEGRAM_API_HASH` | Telegram user API hash |
| `TELEGRAM_GRAMMERS_SESSION` | Grammers session string |
| `TELEGRAM_SCRAPING_CHANNELS` | Comma-separated channels to scrape |
| `TELEGRAM_SCRAPE_MESSAGE_LIMIT` | Max messages per channel per run |
| `TELEGRAM_BACKGROUND_SCRAPER_CRONTAB` | Crontab for the scraper |
| `DISABLE_TELEGRAM_BACKGROUND_SCRAPER` | Set `true` to disable |
