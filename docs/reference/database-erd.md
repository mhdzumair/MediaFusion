# Database Schema

MediaFusion 6.0 uses PostgreSQL. The schema is managed with **sqlx** migrations (`backend/migrations/`). Migrations apply automatically at startup.

!!! note "Current baseline"
    The schema described here reflects the 6.0 baseline (`0001_baseline.up.sql`) plus incremental migrations through `0013`. Check `backend/migrations/` for the latest changes.

---

## Core Domain: Media

The `media` table is the central entity. All movies, series, and other content hang off it.

```mermaid
erDiagram
    media {
        int id PK
        mediatype type
        varchar title
        varchar original_title
        int year
        date release_date
        date end_date
        varchar status
        int runtime_minutes
        text description
        bool adult
        varchar original_language
        float popularity
        int primary_provider_id FK
        bool is_user_created
    }

    movie_metadata {
        int id PK
        int media_id FK
        varchar budget
        varchar revenue
    }

    series_metadata {
        int id PK
        int media_id FK
        int total_episodes
        int total_seasons
    }

    season {
        int id PK
        int media_id FK
        int season_number
        varchar title
        date air_date
    }

    episode {
        int id PK
        int season_id FK
        int episode_number
        varchar title
        date air_date
        int runtime_minutes
    }

    media ||--o| movie_metadata : "movie"
    media ||--o| series_metadata : "series"
    media ||--o{ season : "seasons"
    season ||--o{ episode : "episodes"
```

---

## Media Associations

```mermaid
erDiagram
    media {
        int id PK
        varchar title
    }
    genre {
        int id PK
        varchar name
    }
    keyword {
        int id PK
        varchar name
    }
    person {
        int id PK
        varchar name
    }
    production_company {
        int id PK
        varchar name
    }
    media_external_id {
        int id PK
        int media_id FK
        varchar provider
        varchar external_id
    }
    media_rating {
        int id PK
        int media_id FK
        int rating_provider_id FK
        float value
    }
    rating_provider {
        int id PK
        varchar name
    }
    aka_title {
        int id PK
        int media_id FK
        varchar title
        varchar region
    }

    media ||--o{ media_genre_link : ""
    genre ||--o{ media_genre_link : ""
    media ||--o{ media_keyword_link : ""
    keyword ||--o{ media_keyword_link : ""
    media ||--o{ media_external_id : "IMDb/TMDB/TVDB IDs"
    media ||--o{ media_rating : ""
    rating_provider ||--o{ media_rating : ""
    media ||--o{ aka_title : "alternate titles"
    media ||--o{ media_cast : ""
    person ||--o{ media_cast : ""
    media ||--o{ media_crew : ""
    person ||--o{ media_crew : ""
    media ||--o{ media_production_company_link : ""
    production_company ||--o{ media_production_company_link : ""
```

---

## Stream Architecture

MediaFusion uses a **base stream + type-specific extension** pattern. Every stream has a row in the base `stream` table plus exactly one row in a type table.

```mermaid
erDiagram
    stream {
        int id PK
        streamtype stream_type
        varchar name
        varchar source
        varchar uploader
        bool is_active
        bool is_blocked
        bool is_public
        int playback_count
        varchar resolution
        varchar codec
        varchar quality
        bool is_dubbed
        bool is_subbed
    }

    torrent_stream {
        int id PK
        int stream_id FK
        varchar info_hash
        bigint total_size
        int seeders
        int leechers
        torrenttype torrent_type
        timestamp uploaded_at
        int file_count
    }

    http_stream {
        int id PK
        int stream_id FK
        varchar url
        varchar drm_key_id
        varchar drm_key
    }

    usenet_stream {
        int id PK
        int stream_id FK
        varchar nzb_url
        bigint size
    }

    youtube_stream {
        int id PK
        int stream_id FK
        varchar youtube_id
    }

    acestream_stream {
        int id PK
        int stream_id FK
        varchar ace_id
    }

    telegram_stream {
        int id PK
        int stream_id FK
        varchar channel_id
        varchar message_id
    }

    external_link_stream {
        int id PK
        int stream_id FK
        varchar url
        linksource source
    }

    stream ||--o| torrent_stream : "torrent"
    stream ||--o| http_stream : "http/mpd"
    stream ||--o| usenet_stream : "usenet/nzb"
    stream ||--o| youtube_stream : "youtube"
    stream ||--o| acestream_stream : "acestream"
    stream ||--o| telegram_stream : "telegram"
    stream ||--o| external_link_stream : "external link"
```

### Stream ↔ Media links

```mermaid
erDiagram
    stream {
        int id PK
        varchar name
    }
    media {
        int id PK
        varchar title
    }
    episode {
        int id PK
        int episode_number
    }
    stream_media_link {
        int stream_id FK
        int media_id FK
        int episode_id FK
        varchar file_index
    }
    stream_file {
        int id PK
        int stream_id FK
        varchar filename
        int file_index
        bigint size
        filetype file_type
    }
    tracker {
        int id PK
        varchar url
    }

    stream ||--o{ stream_media_link : ""
    media ||--o{ stream_media_link : ""
    episode ||--o{ stream_media_link : ""
    stream ||--o{ stream_file : "files in torrent"
    stream ||--o{ torrent_tracker_link : ""
    tracker ||--o{ torrent_tracker_link : ""
    stream ||--o{ stream_language_link : ""
    stream ||--o{ stream_audio_link : ""
    stream ||--o{ stream_hdr_link : ""
```

---

## Users & Profiles

```mermaid
erDiagram
    users {
        int id PK
        varchar uuid
        varchar email
        varchar username
        varchar password_hash
        userrole role
        bool is_verified
        bool is_active
        timestamp last_login
        int contribution_points
    }

    user_profiles {
        int id PK
        varchar uuid
        int user_id FK
        varchar name
        json config
        varchar encrypted_secrets
        bool is_default
    }

    profile_integration {
        int id PK
        int user_id FK
        integrationtype type
        varchar access_token
        timestamp expires_at
    }

    watch_history {
        int id PK
        int user_id FK
        int media_id FK
        int episode_id FK
        historysource source
        timestamp watched_at
    }

    user_library_item {
        int id PK
        int user_id FK
        int media_id FK
        varchar list_type
    }

    users ||--o{ user_profiles : "has profiles"
    users ||--o{ profile_integration : "integrations"
    users ||--o{ watch_history : ""
    users ||--o{ user_library_item : ""
```

---

## Catalog System

```mermaid
erDiagram
    catalog {
        int id PK
        varchar name
        varchar type
        varchar language
        bool is_kids
        bool is_adult
    }

    media_catalog_link {
        int media_id FK
        int catalog_id FK
    }

    user_catalog {
        int id PK
        int user_id FK
        varchar name
        varchar type
        bool is_public
    }

    user_catalog_item {
        int id PK
        int catalog_id FK
        int media_id FK
        int stream_id FK
        int position
    }

    catalog ||--o{ media_catalog_link : ""
    user_catalog ||--o{ user_catalog_item : ""
    users ||--o{ user_catalog : ""
    users ||--o{ user_catalog_subscription : ""
```

---

## IPTV, RSS, and Contributions

```mermaid
erDiagram
    iptv_source {
        int id PK
        int user_id FK
        varchar name
        iptvsourcetype type
        varchar url
        bool is_public
        timestamp last_synced_at
        int stream_count
    }

    rss_feed {
        int id PK
        varchar name
        varchar url
        bool is_enabled
        timestamp last_fetched_at
    }

    rss_feed_catalog_pattern {
        int id PK
        int rss_feed_id FK
        varchar pattern
        varchar catalog_type
        varchar language
    }

    contributions {
        int id PK
        int stream_id FK
        int submitted_by FK
        int reviewed_by FK
        contributionstatus status
        timestamp submitted_at
    }

    users ||--o{ iptv_source : ""
    rss_feed ||--o{ rss_feed_catalog_pattern : ""
    users ||--o{ contributions : "submits"
```
