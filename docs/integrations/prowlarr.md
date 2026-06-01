# Prowlarr Integration

[Prowlarr](https://prowlarr.com/) aggregates torrent and Usenet indexers and feeds search results to MediaFusion. It is the recommended way to get broad torrent coverage.

## How it works

MediaFusion queries Prowlarr when:
- A stream is requested and live search is enabled (`PROWLARR_LIVE_TITLE_SEARCH=true`)
- The background scheduler runs a periodic re-search for cached titles

Prowlarr in turn queries all configured indexers and returns torrent results.

## Setup with Docker Compose

The Docker Compose stack includes Prowlarr out of the box. Run the setup script to configure it:

=== "Linux / macOS"

    ```bash
    cd deployment/docker-compose
    ./setup-prowlarr.sh
    ```

=== "Windows (PowerShell)"

    ```powershell
    cd deployment\docker-compose
    .\setup-prowlarr.ps1
    ```

This script:
- Sets a random `PROWLARR_API_KEY` in your `.env`
- Adds a set of tested public trackers to Prowlarr

## Manual Prowlarr setup

If Prowlarr is running separately (not in the Docker Compose stack):

1. Open Prowlarr at `http://your-prowlarr-host:9696`
2. Go to **Settings** → **General** and copy your API key
3. Set in your MediaFusion `.env`:
   ```bash
   PROWLARR_URL=http://your-prowlarr-host:9696
   PROWLARR_API_KEY=your_api_key
   ```

## Adding indexers

Visit `http://localhost:9696` (or your Prowlarr URL) → **Indexers** → **Add Indexer** to add more trackers.

Popular public trackers are free to add. Private trackers require an account on each site.

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `PROWLARR_URL` | `http://prowlarr:9696` | Prowlarr base URL |
| `PROWLARR_API_KEY` | `None` | API key from Prowlarr → Settings → General |
| `PROWLARR_LIVE_TITLE_SEARCH` | `true` | Query Prowlarr in real time on stream requests |
| `PROWLARR_IMMEDIATE_MAX_PROCESS` | `30` | Max parallel Prowlarr searches on live requests |
| `PROWLARR_IMMEDIATE_MAX_PROCESS_TIME` | `30` | Timeout (seconds) for live Prowlarr searches |
| `PROWLARR_SEARCH_INTERVAL_HOUR` | `24` | Re-search interval for cached titles (hours) |

## Using MediaFusion as a Torznab indexer in Radarr/Sonarr

MediaFusion exposes a native [Torznab API](https://github.com/mhdzumair/MediaFusion/blob/main/resources/yaml/mediafusion.yaml) that Radarr, Sonarr, and Prowlarr can use as an indexer.

Add `https://your-mediafusion-instance/torznab` as a custom Torznab indexer in Prowlarr (or directly in Radarr/Sonarr).
