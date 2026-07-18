# Telegram Integration

MediaFusion has two related Telegram features:

| Feature | What it uses | Purpose |
|---|---|---|
| **Telegram Bot** | Bot API (`TELEGRAM_BOT_TOKEN`) | User contributions, account linking, scrape triggers, admin alerts |
| **Channel Scraper** | User API / MTProto (`TELEGRAM_API_ID` + per-user session) | Import video files from Telegram channels/groups as streams |

Both are optional. Neither is required for basic MediaFusion operation.

---

## Quick start checklist

### Bot only (contributions + notifications)

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET_TOKEN`
3. Set `HOST_URL` so the webhook registers at `{HOST_URL}/bot/webhook`
4. Optional: `TELEGRAM_CHAT_ID` for admin/moderation alerts
5. Optional: `TELEGRAM_BACKUP_CHANNEL_ID` for storing contributed/scraped files

### Channel scraping (requires bot + user API)

1. Complete the bot steps above
2. Set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from [my.telegram.org](https://my.telegram.org)
3. Each user links their MediaFusion account to the bot (`/login`)
4. Each user connects a Telegram scraping session in **Configure → Integrations → Telegram**
5. Add channels to scrape (web UI, `/browsechannels`, or `/addchannel`)
6. Run a scrape from the web UI or `/scrape` in the bot

---

## Telegram Bot

### What it does

- Lets users contribute magnets, torrents, NZBs, HTTP links, and video files
- Guides users through metadata matching and import confirmation
- Links Telegram accounts to MediaFusion profiles (`/login`)
- Triggers channel scrapes (`/scrape`)
- Sends moderation and scrape-completion notifications to `TELEGRAM_CHAT_ID`

### Server setup

**Step 1: Create a bot**

1. Message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the **Bot API token** and **username**

**Step 2: Configure MediaFusion**

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_BOT_USERNAME=your_bot_username   # without @
```

**Step 3: Webhook (production)**

The bot uses webhooks, not polling:

```bash
TELEGRAM_WEBHOOK_SECRET_TOKEN=$(openssl rand -hex 16)
```

On startup, MediaFusion registers the webhook at `{HOST_URL}/bot/webhook` when `TELEGRAM_BOT_TOKEN` is set. `HOST_URL` must be publicly reachable by Telegram.

**Step 4: Admin notifications (optional)**

Receive moderation alerts and scrape summaries:

```bash
TELEGRAM_CHAT_ID=-1001234567890
```

Get a chat ID by forwarding a message from that chat to [@userinfobot](https://t.me/userinfobot) or [@RawDataBot](https://t.me/RawDataBot).

**Step 5: Backup channel (optional but recommended for playback)**

Store contributed and scraped video files in a private channel or supergroup you control:

```bash
TELEGRAM_BACKUP_CHANNEL_ID=-1009876543210
```

!!! note "Bots are added as administrators, not subscribers"
    In Telegram **channels**, add the bot under **Administrators** (not as a regular member). Your **scraping user account** must also be able to post/forwards into the backup chat. A **private supergroup** is often easier to set up than a broadcast channel.

Required bot permissions in the backup chat:

- Post messages
- Read message history (if shown)

### Bot commands

| Command | Description |
|---|---|
| `/login` | Link Telegram account to MediaFusion (private chat only) |
| `/status` | Show link and scraping session status |
| `/session` | Instructions for connecting the scraping session (web UI) |
| `/dropsession` | Disconnect scraping session |
| `/browsechannels` | Pick channels/groups from your Telegram account |
| `/addchannel` | Add a channel by `@username` or ID |
| `/removechannel` | Remove a configured channel |
| `/channels` | List configured scraping channels |
| `/scrape` | Scrape configured channels (prompts for message count) |
| `/scrape 25` | Scrape last 25 messages per channel |
| `/scrape all` | Scrape full channel history |
| `/scrape 50 @channel` | Scrape one channel (last 50 messages) |
| `/cancel` | Cancel an active import, scrape, or setup prompt |

Forward video files or send content links to start the contribution wizard. The bot parses titles, searches metadata matches, and lets you confirm before import.

---

## Telegram Channel Scraper

The scraper watches configured channels/groups using each user's **own Telegram session** (MTProto). It finds video documents, matches them to media metadata, and stores them as `telegram` streams.

!!! warning "User API credentials required"
    Channel scraping uses the Telegram **user API**, not the bot token. You need `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` from [my.telegram.org](https://my.telegram.org) and a per-user encrypted session stored in the database.

### Server setup

```bash
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash
MIN_SCRAPING_VIDEO_SIZE=26214400   # default 25 MB — ignore smaller files
```

Enable the background scheduler (optional — keeps catalogs fresh on a cron):

```bash
IS_SCRAP_FROM_TELEGRAM_BACKGROUND=true
# TELEGRAM_BACKGROUND_SCRAPER_CRONTAB uses built-in default (every 6 hours)
# DISABLE_TELEGRAM_BACKGROUND_SCRAPER=false
```

See [Content Sources](../configuration/content-sources.md#telegram-channel-scraping) for scheduler variables.

### Per-user setup (web UI)

1. Link the bot to MediaFusion: send `/login` in a private chat with the bot
2. Open **Configure → Integrations → Telegram**
3. Click **Connect Telegram for scraping**
4. Enter phone number, verification code, and 2FA password if enabled
5. Browse or add channels to scrape
6. Click **Scrape** (choose message depth) or use `/scrape` in the bot

Sessions are encrypted and stored in PostgreSQL. The session string is never shown again after login.

### Supported channel types

| Type | Identifier | Notes |
|---|---|---|
| Public channel | `@username` | Visible in browse list when joined |
| Private channel / group | `id:-100…` | Requires scraping session membership; use **Browse** or `/browsechannels` |

Invite links (`t.me/+…`) are not supported directly — add the channel in Telegram first, then pick it from the browse list.

### Running scrapes

Each scrape run asks **how many recent messages** to scan per channel:

| Option | Behavior |
|---|---|
| **Default (25)** | Scan the last 25 messages per channel |
| **Custom number** | e.g. `50`, `200` |
| **All messages** | Full channel history (can be slow on large channels) |

**Web UI:** set **Messages per channel** or check **Scrape all messages** before clicking Scrape.

**Bot:**

```
/scrape                  → prompts for count, scrapes all configured channels
/scrape 25               → last 25 messages, all channels
/scrape all               → full history, all channels
/scrape 25 @mychannel     → one channel, last 25 messages
/scrape all id:-100123…   → one private channel, full history
```

When a scrape finishes, MediaFusion sends a summary to:

1. The user (via the bot — progress message or DM)
2. `TELEGRAM_CHAT_ID` (admin chat), if configured

### Background scraper

The scheduled `telegram_bg` worker re-scans configured channels for all users who have both a session and channels configured. It uses the **25-message default** per channel unless triggered manually with a different depth.

Disable with:

```bash
DISABLE_TELEGRAM_BACKGROUND_SCRAPER=true
```

---

## Playback requirements

Telegram streams appear in Stremio/catalogs once indexed, but **playback** needs a few extra pieces:

| Requirement | Why |
|---|---|
| **MediaFlow Proxy** enabled in profile | Serves the stream URL to the client |
| **`enable_telegram_streams`** in profile | Allows telegram stream type |
| **`file_id` on the stream row** | Bot API identifier for forwarding at play time |
| **Bot linked** (`/login`) | Sends the file to your Telegram DM when playing |
| **Backup channel configured** (recommended) | User session forwards scraped files there; bot captures `file_id` |

Streams scraped before backup was configured may need to be **re-scraped** to get a `file_id`.

See [MediaFlow Proxy](../configuration/mediaflow.md) for proxy setup.

---

## Configuration reference

### Bot

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot API token from @BotFather |
| `TELEGRAM_BOT_USERNAME` | Bot username (without `@`) |
| `TELEGRAM_WEBHOOK_SECRET_TOKEN` | Webhook validation secret |
| `TELEGRAM_CHAT_ID` | Admin chat for moderation + scrape summaries |
| `TELEGRAM_BACKUP_CHANNEL_ID` | Private channel/group for file backup and `file_id` capture |

### Channel scraper

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_API_ID` | — | Telegram user API ID |
| `TELEGRAM_API_HASH` | — | Telegram user API hash |
| `MIN_SCRAPING_VIDEO_SIZE` | `26214400` (25 MB) | Minimum video file size to import |
| `IS_SCRAP_FROM_TELEGRAM_BACKGROUND` | `false` | Enable scheduled background scraper |
| `TELEGRAM_BACKGROUND_SCRAPER_CRONTAB` | built-in | Cron schedule for background scraper |
| `DISABLE_TELEGRAM_BACKGROUND_SCRAPER` | `false` | Disable the background scraper job |

Full defaults and types: [Environment Variable Reference](../reference/env-reference.md#telegram-bot).

---

## Troubleshooting

### Bot cannot be added to backup channel

- Use a **private supergroup** you own instead of a broadcast channel
- Add the bot as **Administrator** with post permission (channels have no “subscribe” for bots)
- Add your **scraping Telegram account** to the same chat

### Scrape finds no streams

- Confirm the scraping session account is a **member** of the channel
- Try increasing message depth (`/scrape all` or higher count)
- Check `MIN_SCRAPING_VIDEO_SIZE` — small files are skipped
- Private channels must use `id:-100…` identifiers or be picked via browse

### Streams indexed but won't play

- Verify MediaFlow + `enable_telegram_streams` in profile
- Link bot with `/login`
- Configure backup channel and re-scrape to populate `file_id`
- Check worker logs for `telegram_bg` enrichment errors

### `/scrape` says session required

Connect the scraping session in **Configure → Integrations → Telegram**, not just the bot link.

---

## Related docs

- [Content Sources — Telegram background scraper](../configuration/content-sources.md#telegram-channel-scraping)
- [MediaFlow Proxy](../configuration/mediaflow.md)
- [Stream formatting — telegram stream fields](../configuration/stream-formatting.md)
- [Worker CLI — telegram_bg job](../deployment/worker-cli.md)
