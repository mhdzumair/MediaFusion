# Quick Start

Get MediaFusion running in under 2 minutes using a free community instance — no server or configuration required.

## Pick your client

=== "Stremio"

    ## Step 1: Install Stremio

    Download and install Stremio for your platform from [stremio.com/downloads](https://www.stremio.com/downloads).

    ## Step 2: Pick a community instance

    | Instance | URL |
    |---|---|
    | ElfHosted | [mediafusion.elfhosted.com](https://mediafusion.elfhosted.com) |
    | ElfHosted (dev/beta) | [mediafusion-dev.elfhosted.com](https://mediafusion-dev.elfhosted.com) |
    | Midnight | [mediafusionfortheweebs.midnightignite.me](https://mediafusionfortheweebs.midnightignite.me) |

    Open your chosen instance in a browser.

    ## Step 3: Configure

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

    === "Desktop / Android app"

        Click **Install in Stremio** — the app opens automatically with the installation prompt.

    === "Web / iOS"

        1. Click **Copy Manifest URL**
        2. Open Stremio → **Addons** → **+ Add Addon**
        3. Paste the URL → **Install**

    That's it. MediaFusion appears under your Stremio addons and catalogs start populating.

=== "Kodi"

    ## Step 1: Install MediaFusion in Kodi

    Install via the MediaFusion repository for automatic updates:

    1. Launch Kodi → **Settings** (⚙️) → **File manager** → **Add source**
    2. Enter the repository URL:
       ```
       https://mhdzumair.github.io/MediaFusion
       ```
       Name it `MediaFusion` → **OK**
    3. Go to **Add-ons** → **Add-on browser** → **Install from zip file** → **MediaFusion**
    4. Select the `repository.mediafusion-x.x.x.zip` file
    5. Once installed: **Install from repository** → **MediaFusion Repository** → **Video add-ons** → **MediaFusion** → **Install**

    For the full installation guide including manual install, see [Kodi Integration](../integrations/kodi.md).

    ## Step 2: Pair with a MediaFusion instance

    1. In Kodi: **Add-ons** → **My add-ons** → **Video add-ons** → **MediaFusion** → **Configure**
    2. Select **Configure/Reconfigure Secret String** — a QR code and 6-digit key appear
    3. Open a community instance in your browser:

        | Instance | URL |
        |---|---|
        | ElfHosted | [mediafusion.elfhosted.com/configure](https://mediafusion.elfhosted.com/configure) |
        | Midnight | [mediafusionfortheweebs.midnightignite.me/configure](https://mediafusionfortheweebs.midnightignite.me/configure) |

    4. Configure your preferences, then click **Setup Kodi Addon**
    5. Enter the 6-digit key from Kodi → **Submit**
    6. Verify the secret string is populated in Kodi → return to main menu

    Your MediaFusion catalogs will appear in Kodi.

---

## What's next?

- Explore the [feature set](../features.md) to see what else MediaFusion can do
- Want a private instance? → [ElfHosted managed setup](../deployment/elfhosted.md) (7-day trial)
- Want to self-host for free? → [Docker Compose guide](../deployment/docker-compose.md)
- Add a debrid service later? → [Streaming Providers](../configuration/streaming-providers.md)
- Full Kodi guide → [Kodi Integration](../integrations/kodi.md)

!!! tip "First-time scraping"
    On a fresh instance, catalog results may be sparse for the first hour while background scrapers populate the database. Visit `https://your-instance/scraper` to trigger scrapers manually.
