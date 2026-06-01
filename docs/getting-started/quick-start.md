# Quick Start

Get MediaFusion running in Stremio in under 2 minutes using a free community instance — no server or configuration required.

## Step 1: Install Stremio

Download and install Stremio for your platform from [stremio.com/downloads](https://www.stremio.com/downloads).

## Step 2: Pick a community instance

| Instance | URL |
|---|---|
| ElfHosted | [mediafusion.elfhosted.com](https://mediafusion.elfhosted.com) |
| ElfHosted (dev/beta) | [mediafusion-dev.elfhosted.com](https://mediafusion-dev.elfhosted.com) |
| Midnight | [mediafusionfortheweebs.midnightignite.me](https://mediafusionfortheweebs.midnightignite.me) |

Open your chosen instance in a browser.

## Step 3: Configure MediaFusion

1. Choose the **catalogs and languages** you want (movies, series, live TV, sports)
2. Optionally add a **streaming provider**:

    | I have... | Choose... |
    |---|---|
    | No debrid account | **Direct P2P** (free, slower) |
    | Real-Debrid / AllDebrid / etc. | Your debrid provider |
    | Seedr, PikPak, or OffCloud | That cloud service |
    | A qBittorrent server | qBittorrent WebDAV |

3. Adjust **stream filters** if you want (resolution, file size, cached-only)

## Step 4: Install into Stremio

=== "Desktop / Android Stremio app"

    Click **Install in Stremio** — the app opens automatically with the installation prompt.

=== "Web / iOS"

    1. Click **Copy Manifest URL**
    2. Open Stremio → **Addons** → **+ Add Addon**
    3. Paste the URL → **Install**

That's it. MediaFusion appears under your Stremio addons and catalogs start populating.

---

## What's next?

- Explore the [feature set](../features.md) to see what else MediaFusion can do
- Want a private instance? → [ElfHosted managed setup](../deployment/elfhosted.md) (7-day trial)
- Want to self-host for free? → [Docker Compose guide](../deployment/docker-compose.md)
- Add a debrid service later? → [Streaming Providers](../configuration/streaming-providers.md)

!!! tip "First-time scraping"
    On a fresh instance, catalog results may be sparse for the first hour while background scrapers populate the database. Visit `https://your-instance/scraper` to trigger scrapers manually.
