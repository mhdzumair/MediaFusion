# Stremio Integration

How to install and configure MediaFusion as a Stremio addon.

## Install Stremio

Download Stremio for your platform from [stremio.com/downloads](https://www.stremio.com/downloads).

## Configure the addon

1. Open your MediaFusion instance in a browser (e.g. `https://mediafusion.elfhosted.com` or your self-hosted URL)
2. Choose your options:
   - Enable the catalogs and languages you want
   - Add a streaming provider (Real-Debrid, Torbox, etc.) if you have one
   - Adjust stream filters (resolution, size, seeder limits)
3. Click **Configure**

## Install into Stremio

=== "Desktop / Android app"

    Click **Install in Stremio** — the app opens automatically with the installation prompt.

=== "Web / iOS"

    1. Click **Copy Manifest URL**
    2. Open Stremio → **Addons** → **+ Add Addon**
    3. Paste the manifest URL → **Install**

## Updating your configuration

To change your settings (add a provider, change languages):

1. Go back to the MediaFusion configure page
2. Make your changes
3. Click **Install in Stremio** again — this replaces the old configuration

## Video configuration guide

A detailed video walkthrough is available: [youtube.com/watch?v=ctQY8r1KzPM](https://www.youtube.com/watch?v=ctQY8r1KzPM&t=85s)

---

## Stream filters

From the configure page you can set filters that apply to every stream result:

| Filter | Description |
|---|---|
| **Resolution** | Minimum/maximum resolution (480p → 4K) |
| **File size** | Min/max file size range |
| **Seeders** | Minimum seeder count (P2P only) |
| **Cached only** | Show only debrid-cached streams |
| **Sort order** | Sort by quality, size, seeders, or provider |

## Parental controls

Enable parental controls to filter out content based on:
- Nudity ratings
- Certification ratings (PG, R, etc.)

Configure these in the **Parental Controls** section of the configure page.

## RPDB poster ratings

If you have an [RPDB](https://ratingposterdb.com/) API key, enter it in the configure page to overlay IMDb ratings directly on posters.

## Watchlist sync

MediaFusion can sync your debrid provider's watchlist as a Stremio catalog. Enable **Watchlist Catalog** in the configure page after connecting a debrid provider.

To remove all watchlist entries: go to **Configure** → **Watchlist** → **Delete All**.
