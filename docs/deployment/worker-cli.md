# Worker CLI Reference

The `mediafusion-worker` binary supports a one-shot CLI mode that lets you trigger any registered job directly from the command line without starting the full scheduler. This is useful for manual scraping runs, debugging, and CI pipelines.

## Usage

```
mediafusion-worker [--list-jobs] [--run-job <queue>] [--args <json>]
```

With no arguments the worker starts normally and runs the full scheduler.

---

## Flags

### `--list-jobs`

Prints every registered queue name and exits immediately. Use this to discover available job names.

=== "Binary"

    ```bash
    ./mediafusion-worker --list-jobs
    ```

=== "Docker"

    ```bash
    docker run --rm --env-file .env mhdzumair/mediafusion:6.0.0 \
      mediafusion-worker --list-jobs
    ```

=== "Docker Compose"

    ```bash
    docker compose exec mediafusion-worker \
      /mediafusion-worker --list-jobs
    ```

=== "Makefile (dev)"

    ```bash
    make worker-list-jobs
    ```

---

### `--run-job <queue>`

Runs a single job inline and exits. The worker connects to the database and Redis, runs the job to completion, then exits with code `0` on success or `1` on failure.

=== "Binary"

    ```bash
    ./mediafusion-worker --run-job spider_sport_video
    ```

=== "Docker"

    ```bash
    docker run --rm --env-file .env mhdzumair/mediafusion:6.0.0 \
      mediafusion-worker --run-job spider_sport_video
    ```

=== "Docker Compose"

    ```bash
    docker compose run --rm mediafusion-worker \
      /mediafusion-worker --run-job spider_sport_video
    ```

=== "Makefile (dev)"

    ```bash
    make worker-run-job JOB=spider_sport_video
    ```

---

### `--args <json>`

Passes a JSON payload to the job. Required for jobs that take parameters (see [Jobs that accept arguments](#jobs-that-accept-arguments) below). Ignored by jobs that don't use it.

```bash
./mediafusion-worker --run-job dmm_hashlist --args '{"full": true}'
```

With the Makefile:

```bash
make worker-run-job JOB=dmm_hashlist JOB_ARGS='{"full": true}'
```

---

## All registered jobs

Run `--list-jobs` to get the live list. The table below describes each queue.

### Background scrapers

| Queue | Description |
|---|---|
| `spider_tamilmv` | Scrape TamilMV for Tamil content |
| `spider_tamil_blasters` | Scrape Tamil Blasters |
| `spider_formula_ext` | Scrape Formula Racing (ext source) |
| `spider_motogp_ext` | Scrape MotoGP (ext source) |
| `spider_wwe_ext` | Scrape WWE (ext source) |
| `spider_ufc_ext` | Scrape UFC (ext source) |
| `spider_movies_tv_ext` | Scrape Movies/TV (ext source) |
| `spider_sport_video` | Scrape sport-video.org.ua |
| `spider_registry_crawl` | Run all public indexer spiders (1337x, TPB, YTS, Nyaa, AnimeTosho, SubsPlease, AnimePahe, BT4G, EZTV, Rutor, LimeTorrents, BT52, UIndex) |
| `spider_eztv_rss` | Scrape EZTV RSS feed |
| `acestream_bg` | Scrape AceStream channels |
| `youtube_bg` | Scrape YouTube channels |
| `telegram_bg` | Scrape configured Telegram channels |

### Feed scrapers

| Queue | Description |
|---|---|
| `prowlarr_feed` | Fetch new releases from Prowlarr indexers |
| `jackett_feed` | Fetch new releases from Jackett indexers |
| `rss_feed` | Process all configured RSS feeds |
| `dmm_hashlist` | Sync DebridMediaManager hashlist from GitHub |

### Imports

| Queue | Description |
|---|---|
| `m3u_import` | Import/sync a single M3U IPTV source |
| `xtream_import` | Import/sync a single Xtream Codes source |
| `imdb_dataset_import` | Import IMDb non-commercial datasets into the DB |

### Search & maintenance

| Queue | Description |
|---|---|
| `background_search` | Process the background re-scrape queue |
| `discover_prewarm` | Pre-warm Discover catalog caches |
| `integration_syncs` | Sync Trakt / Simkl watchlists |
| `update_seeders` | Update seeder counts for tracked torrents |
| `update_tv_posters` | Refresh TV show poster images |
| `validate_tv` | Validate and deactivate dead TV stream URLs |
| `cleanup` | Remove expired scraper task records and cache entries |

---

## Jobs that accept arguments

Most jobs ignore `--args`. The following jobs use it:

### `dmm_hashlist`

| Field | Type | Default | Description |
|---|---|---|---|
| `full` | `bool` | `false` | Walk the full commit history instead of only new commits |
| `reset_checkpoints` | `bool` | `false` | Clear saved Redis checkpoints before running |

```bash
# Incremental sync (normal)
./mediafusion-worker --run-job dmm_hashlist

# Full re-sync from the beginning
./mediafusion-worker --run-job dmm_hashlist --args '{"full": true}'

# Reset checkpoints then sync
./mediafusion-worker --run-job dmm_hashlist --args '{"full": true, "reset_checkpoints": true}'
```

### `m3u_import`

| Field | Type | Required | Description |
|---|---|---|---|
| `iptv_source_id` | `int` | yes | Database ID of the `iptv_source` row to import |

```bash
./mediafusion-worker --run-job m3u_import --args '{"iptv_source_id": 42}'
```

### `xtream_import`

| Field | Type | Required | Description |
|---|---|---|---|
| `iptv_source_id` | `int` | yes | Database ID of the `iptv_source` row to import |

```bash
./mediafusion-worker --run-job xtream_import --args '{"iptv_source_id": 7}'
```

### `imdb_dataset_import`

| Field | Type | Default | Description |
|---|---|---|---|
| `datasets` | `string[]` | all datasets | Subset of dataset keys to process |

```bash
# Import all datasets
./mediafusion-worker --run-job imdb_dataset_import

# Import only specific datasets
./mediafusion-worker --run-job imdb_dataset_import \
  --args '{"datasets": ["title.basics", "title.akas"]}'
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Job completed successfully |
| `1` | Job failed or unknown `--run-job` queue name |

---

## Examples

```bash
# Manually trigger the sport video scraper
./mediafusion-worker --run-job spider_sport_video

# Run all public indexer spiders
./mediafusion-worker --run-job spider_registry_crawl

# Force a full DMM hashlist re-sync
./mediafusion-worker --run-job dmm_hashlist --args '{"full": true, "reset_checkpoints": true}'

# Process the background re-scrape queue once
./mediafusion-worker --run-job background_search

# Sync Trakt/Simkl integrations
./mediafusion-worker --run-job integration_syncs

# List all available jobs
./mediafusion-worker --list-jobs
```
