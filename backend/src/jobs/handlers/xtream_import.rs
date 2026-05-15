/// Xtream Codes API import job handler.
///
/// Job payload: `{"iptv_source_id": 123}`
///
/// Fetches the IPTVSource row, decrypts the stored credentials, then queries
/// the Xtream player API for live streams, VOD streams, and/or series depending
/// on the source's import flags.
///
/// Live streams are imported via `import_tv_channel` (same helper used by the
/// M3U route layer).  VOD and series are stored as HTTP streams linked to
/// newly-created media rows.
use async_trait::async_trait;
use serde::Deserialize;
use tracing::{info, warn};

use crate::{
    crypto::decrypt::decrypt_user_data,
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    routes::content::m3u_import::import_tv_channel,
};

pub struct XtreamImport;

// ─── Payload ──────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct XtreamImportArgs {
    pub iptv_source_id: i32,
}

// ─── DB row ───────────────────────────────────────────────────────────────────

#[allow(dead_code)]
struct IptvXtreamRow {
    id: i32,
    user_id: i32,
    server_url: String,
    encrypted_credentials: String,
    name: String,
    is_public: bool,
    import_live: bool,
    import_vod: bool,
    import_series: bool,
    live_category_ids: Option<Vec<String>>,
    vod_category_ids: Option<Vec<String>>,
    series_category_ids: Option<Vec<String>>,
}

async fn fetch_source(
    pool: &sqlx::PgPool,
    source_id: i32,
) -> Result<Option<IptvXtreamRow>, sqlx::Error> {
    let row = sqlx::query(
        r#"SELECT id, user_id, server_url, encrypted_credentials::text,
                  name, is_public, import_live, import_vod, import_series,
                  live_category_ids, vod_category_ids, series_category_ids
           FROM iptv_source
           WHERE id = $1 AND source_type = 'xtream' AND is_active = true"#,
    )
    .bind(source_id)
    .fetch_optional(pool)
    .await?;

    let row = match row {
        Some(r) => r,
        None => return Ok(None),
    };

    use sqlx::Row;

    let server_url: Option<String> = row.try_get("server_url")?;
    let server_url = match server_url {
        Some(u) if !u.is_empty() => u.trim_end_matches('/').to_string(),
        _ => return Ok(None),
    };

    let encrypted_credentials: Option<String> = row.try_get("encrypted_credentials")?;
    let encrypted_credentials = match encrypted_credentials {
        Some(c) if !c.is_empty() => c,
        _ => return Ok(None),
    };

    // Deserialize jsonb arrays of category IDs (stored as text[] or jsonb)
    let live_category_ids: Option<Vec<String>> = row.try_get("live_category_ids").ok();
    let vod_category_ids: Option<Vec<String>> = row.try_get("vod_category_ids").ok();
    let series_category_ids: Option<Vec<String>> = row.try_get("series_category_ids").ok();

    Ok(Some(IptvXtreamRow {
        id: row.try_get("id")?,
        user_id: row.try_get("user_id")?,
        server_url,
        encrypted_credentials,
        name: row.try_get("name")?,
        is_public: row.try_get("is_public")?,
        import_live: row.try_get("import_live")?,
        import_vod: row.try_get("import_vod")?,
        import_series: row.try_get("import_series")?,
        live_category_ids,
        vod_category_ids,
        series_category_ids,
    }))
}

// ─── Credential decryption ────────────────────────────────────────────────────

struct XtreamCreds {
    username: String,
    password: String,
}

fn decrypt_credentials(encrypted: &str, key: &[u8; 32]) -> Result<XtreamCreds, JobError> {
    let data = decrypt_user_data(encrypted, key)
        .map_err(|e| JobError::other(format!("credential decrypt: {e}")))?;

    let username = data
        .get("username")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let password = data
        .get("password")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    if username.is_empty() || password.is_empty() {
        return Err(JobError::other(
            "xtream_import: decrypted credentials missing username or password",
        ));
    }

    Ok(XtreamCreds { username, password })
}

// ─── Xtream API helpers ───────────────────────────────────────────────────────

fn build_api_url(server: &str, user: &str, pass: &str, action: &str) -> String {
    format!(
        "{}/player_api.php?username={}&password={}&action={}",
        server,
        urlencoding::encode(user),
        urlencoding::encode(pass),
        action
    )
}

fn build_api_url_with_param(
    server: &str,
    user: &str,
    pass: &str,
    action: &str,
    param_key: &str,
    param_val: &str,
) -> String {
    format!(
        "{}/player_api.php?username={}&password={}&action={}&{}={}",
        server,
        urlencoding::encode(user),
        urlencoding::encode(pass),
        action,
        param_key,
        param_val
    )
}

async fn fetch_json_list(http: &reqwest::Client, url: &str) -> Vec<serde_json::Value> {
    match http
        .get(url)
        .timeout(std::time::Duration::from_secs(60))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
        Ok(r) => {
            warn!("xtream_import: HTTP {} from {}", r.status(), url);
            vec![]
        }
        Err(e) => {
            warn!("xtream_import: fetch error {}: {e}", url);
            vec![]
        }
    }
}

/// Returns true if the category_id should be imported given the configured
/// allowlist (None = import all).
fn category_allowed(cat_id: &str, allowlist: &Option<Vec<String>>) -> bool {
    match allowlist {
        None => true,
        Some(ids) if ids.is_empty() => true,
        Some(ids) => ids.iter().any(|id| id == cat_id),
    }
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

/// Find or create a media row by title + type (case-insensitive).  Returns the
/// media id.
async fn find_or_create_media(
    pool: &sqlx::PgPool,
    title: &str,
    media_type_str: &str, // "TV", "MOVIE", "SERIES"
    is_public: bool,
) -> Result<i32, sqlx::Error> {
    // Try exact case-insensitive match first
    let existing: Option<(i32,)> = sqlx::query_as(
        "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type = $2::mediatype LIMIT 1",
    )
    .bind(title)
    .bind(media_type_str)
    .fetch_optional(pool)
    .await?;

    if let Some((id,)) = existing {
        return Ok(id);
    }

    // Insert
    let res: Option<(i32,)> = sqlx::query_as(
        r#"INSERT INTO media (title, type, is_public, is_user_created, adult, is_blocked,
                              total_streams, playback_count, created_at, updated_at)
           VALUES ($1, $2::mediatype, $3, true, false, false, 0, 0, NOW(), NOW())
           ON CONFLICT DO NOTHING
           RETURNING id"#,
    )
    .bind(title)
    .bind(media_type_str)
    .bind(is_public)
    .fetch_optional(pool)
    .await?;

    match res {
        Some((id,)) => Ok(id),
        None => {
            // Race: fetch after conflict
            let row: (i32,) = sqlx::query_as(
                "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type = $2::mediatype LIMIT 1",
            )
            .bind(title)
            .bind(media_type_str)
            .fetch_one(pool)
            .await?;
            Ok(row.0)
        }
    }
}

/// Insert an HTTP stream + link to media.  Returns true if a new stream was
/// created.
async fn insert_http_stream_for_media(
    pool: &sqlx::PgPool,
    media_id: i32,
    stream_name: &str,
    url: &str,
    source_label: &str,
    is_public: bool,
) -> Result<bool, sqlx::Error> {
    // Check for existing URL already linked to this media
    let existing: Option<i32> = sqlx::query_scalar(
        "SELECT hs.stream_id FROM http_stream hs
         JOIN stream_media_link sml ON sml.stream_id = hs.stream_id
         WHERE hs.url = $1 AND sml.media_id = $2
         LIMIT 1",
    )
    .bind(url)
    .bind(media_id)
    .fetch_optional(pool)
    .await?;

    if existing.is_some() {
        return Ok(false);
    }

    // Insert stream
    let stream_row: Option<(i32,)> = sqlx::query_as(
        r#"INSERT INTO stream (
            stream_type, name, source, is_active, is_blocked, is_public, playback_count,
            is_remastered, is_upscaled, is_proper, is_repack, is_extended, is_complete,
            is_dubbed, is_subbed, created_at, updated_at
        ) VALUES (
            'HTTP'::streamtype, $1, $2, true, false, $3, 0,
            false, false, false, false, false, false,
            false, false, NOW(), NOW()
        ) RETURNING id"#,
    )
    .bind(stream_name)
    .bind(source_label)
    .bind(is_public)
    .fetch_optional(pool)
    .await?;

    let stream_id = match stream_row {
        Some((id,)) => id,
        None => return Ok(false),
    };

    // Insert http_stream
    let hs = sqlx::query(
        "INSERT INTO http_stream (stream_id, url, stream_behavior)
         VALUES ($1, $2, 'DIRECT'::streambehavior)
         ON CONFLICT (stream_id) DO NOTHING",
    )
    .bind(stream_id)
    .bind(url)
    .execute(pool)
    .await;

    if hs.is_err() {
        // Roll back the stream row on failure
        let _ = sqlx::query("DELETE FROM stream WHERE id = $1")
            .bind(stream_id)
            .execute(pool)
            .await;
        return Ok(false);
    }

    // Link to media
    let _ = sqlx::query(
        "INSERT INTO stream_media_link (stream_id, media_id, is_primary)
         SELECT $1, $2, true
         WHERE NOT EXISTS (
             SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2
         )",
    )
    .bind(stream_id)
    .bind(media_id)
    .execute(pool)
    .await;

    Ok(true)
}

// ─── Handler ──────────────────────────────────────────────────────────────────

#[async_trait]
impl JobHandler for XtreamImport {
    const QUEUE: &'static str = "xtream_import";
    const CONCURRENCY: usize = 2;
    type Args = XtreamImportArgs;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let source = match fetch_source(&ctx.state.pool_ro, args.iptv_source_id).await? {
            Some(s) => s,
            None => {
                info!(
                    "xtream_import: source_id={} not found or inactive",
                    args.iptv_source_id
                );
                return Ok(());
            }
        };

        let creds =
            decrypt_credentials(&source.encrypted_credentials, &ctx.state.config.secret_key)?;

        let server = &source.server_url;
        let user = &creds.username;
        let pass = &creds.password;
        let source_label = format!("xtream:{}", source.id);
        let is_public = source.is_public;

        info!(
            "xtream_import: source_id={} name={:?} server={}",
            source.id, source.name, server
        );

        let http = &ctx.state.http;
        let pool = &ctx.state.pool;

        let mut total_imported = 0usize;
        let mut total_skipped = 0usize;

        // ── Live streams ─────────────────────────────────────────────────────

        if source.import_live {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let categories = fetch_json_list(
                http,
                &build_api_url(server, user, pass, "get_live_categories"),
            )
            .await;

            info!(
                "xtream_import: source_id={} got {} live categories",
                source.id,
                categories.len()
            );

            for cat in &categories {
                let cat_id = cat
                    .get("category_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                if !category_allowed(cat_id, &source.live_category_ids) {
                    continue;
                }

                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                let streams = fetch_json_list(
                    http,
                    &build_api_url_with_param(
                        server,
                        user,
                        pass,
                        "get_live_streams",
                        "category_id",
                        cat_id,
                    ),
                )
                .await;

                for stream in &streams {
                    let name = stream
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("Unknown");
                    let stream_id = stream
                        .get("stream_id")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(0);
                    let logo = stream
                        .get("stream_icon")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty());
                    let stream_url =
                        format!("{}/live/{}/{}/{}.m3u8", server, user, pass, stream_id);

                    if import_tv_channel(pool, name, &stream_url, logo, &source_label).await {
                        total_imported += 1;
                    } else {
                        total_skipped += 1;
                    }
                }
            }

            info!(
                "xtream_import: source_id={} live done — imported={} skipped={}",
                source.id, total_imported, total_skipped
            );
        }

        // ── VOD streams ──────────────────────────────────────────────────────

        if source.import_vod {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let categories = fetch_json_list(
                http,
                &build_api_url(server, user, pass, "get_vod_categories"),
            )
            .await;

            info!(
                "xtream_import: source_id={} got {} VOD categories",
                source.id,
                categories.len()
            );

            let mut vod_imported = 0usize;
            let mut vod_skipped = 0usize;

            for cat in &categories {
                let cat_id = cat
                    .get("category_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                if !category_allowed(cat_id, &source.vod_category_ids) {
                    continue;
                }

                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                let streams = fetch_json_list(
                    http,
                    &build_api_url_with_param(
                        server,
                        user,
                        pass,
                        "get_vod_streams",
                        "category_id",
                        cat_id,
                    ),
                )
                .await;

                for stream in &streams {
                    let name = stream
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("Unknown");
                    let stream_id = stream
                        .get("stream_id")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(0);
                    let ext = stream
                        .get("container_extension")
                        .and_then(|v| v.as_str())
                        .unwrap_or("mp4");
                    let logo = stream
                        .get("stream_icon")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty());
                    let stream_url =
                        format!("{}/movie/{}/{}/{}.{}", server, user, pass, stream_id, ext);

                    match find_or_create_media(pool, name, "MOVIE", is_public).await {
                        Ok(media_id) => {
                            // Update poster if needed
                            if let Some(poster) = logo {
                                let _ = sqlx::query(
                                    "UPDATE media SET poster = $1 WHERE id = $2 AND (poster IS NULL OR poster = '')",
                                )
                                .bind(poster)
                                .bind(media_id)
                                .execute(pool)
                                .await;
                            }

                            match insert_http_stream_for_media(
                                pool,
                                media_id,
                                name,
                                &stream_url,
                                &source_label,
                                is_public,
                            )
                            .await
                            {
                                Ok(true) => vod_imported += 1,
                                Ok(false) => vod_skipped += 1,
                                Err(e) => {
                                    warn!("xtream_import: VOD stream insert error: {e}");
                                    vod_skipped += 1;
                                }
                            }
                        }
                        Err(e) => {
                            warn!("xtream_import: media lookup/create error for {}: {e}", name);
                            vod_skipped += 1;
                        }
                    }
                }
            }

            total_imported += vod_imported;
            total_skipped += vod_skipped;

            info!(
                "xtream_import: source_id={} VOD done — imported={} skipped={}",
                source.id, vod_imported, vod_skipped
            );
        }

        // ── Series ───────────────────────────────────────────────────────────

        if source.import_series {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            let categories = fetch_json_list(
                http,
                &build_api_url(server, user, pass, "get_series_categories"),
            )
            .await;

            info!(
                "xtream_import: source_id={} got {} series categories",
                source.id,
                categories.len()
            );

            let mut series_imported = 0usize;
            let mut series_skipped = 0usize;

            for cat in &categories {
                let cat_id = cat
                    .get("category_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                if !category_allowed(cat_id, &source.series_category_ids) {
                    continue;
                }

                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                let series_list = fetch_json_list(
                    http,
                    &build_api_url_with_param(
                        server,
                        user,
                        pass,
                        "get_series",
                        "category_id",
                        cat_id,
                    ),
                )
                .await;

                for series_item in &series_list {
                    let series_id = match series_item.get("series_id").and_then(|v| v.as_i64()) {
                        Some(id) => id,
                        None => continue,
                    };

                    let series_name = series_item
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("Unknown");
                    let cover = series_item
                        .get("cover")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty());

                    if ctx.is_cancelled() {
                        return Err(JobError::Cancelled);
                    }

                    // get_series_info returns an object (not an array) — fetch directly
                    let episodes_json = {
                        let url = build_api_url_with_param(
                            server,
                            user,
                            pass,
                            "get_series_info",
                            "series_id",
                            &series_id.to_string(),
                        );
                        match http
                            .get(&url)
                            .timeout(std::time::Duration::from_secs(60))
                            .send()
                            .await
                        {
                            Ok(r) if r.status().is_success() => {
                                r.json::<serde_json::Value>().await.unwrap_or_default()
                            }
                            _ => serde_json::Value::Null,
                        }
                    };

                    let media_id =
                        match find_or_create_media(pool, series_name, "SERIES", is_public).await {
                            Ok(id) => id,
                            Err(e) => {
                                warn!("xtream_import: series media error for {}: {e}", series_name);
                                series_skipped += 1;
                                continue;
                            }
                        };

                    if let Some(poster) = cover {
                        let _ = sqlx::query(
                            "UPDATE media SET poster = $1 WHERE id = $2 AND (poster IS NULL OR poster = '')",
                        )
                        .bind(poster)
                        .bind(media_id)
                        .execute(pool)
                        .await;
                    }

                    // Iterate seasons → episodes
                    let episodes_obj = episodes_json
                        .get("episodes")
                        .cloned()
                        .unwrap_or(serde_json::Value::Null);

                    if let Some(seasons_obj) = episodes_obj.as_object() {
                        for (_season_num, episodes_arr) in seasons_obj {
                            if let Some(episodes) = episodes_arr.as_array() {
                                for episode in episodes {
                                    // Episode id may be a string or integer in the API
                                    let ep_id_str = {
                                        let v = episode.get("id");
                                        if let Some(s) = v.and_then(|v| v.as_str()) {
                                            s.to_string()
                                        } else if let Some(n) = v.and_then(|v| v.as_i64()) {
                                            n.to_string()
                                        } else {
                                            continue;
                                        }
                                    };
                                    if ep_id_str.is_empty() {
                                        continue;
                                    }

                                    let ep_ext = episode
                                        .get("container_extension")
                                        .and_then(|v| v.as_str())
                                        .unwrap_or("mp4");
                                    let ep_title = episode
                                        .get("title")
                                        .and_then(|v| v.as_str())
                                        .unwrap_or(series_name);
                                    let ep_url = format!(
                                        "{}/series/{}/{}/{}.{}",
                                        server, user, pass, ep_id_str, ep_ext
                                    );

                                    match insert_http_stream_for_media(
                                        pool,
                                        media_id,
                                        ep_title,
                                        &ep_url,
                                        &source_label,
                                        is_public,
                                    )
                                    .await
                                    {
                                        Ok(true) => series_imported += 1,
                                        Ok(false) => series_skipped += 1,
                                        Err(e) => {
                                            warn!("xtream_import: episode insert error: {e}");
                                            series_skipped += 1;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            total_imported += series_imported;
            total_skipped += series_skipped;

            info!(
                "xtream_import: source_id={} series done — imported={} skipped={}",
                source.id, series_imported, series_skipped
            );
        }

        // ── Update last_synced_at ─────────────────────────────────────────────

        sqlx::query(
            "UPDATE iptv_source SET last_synced_at = NOW(), last_sync_stats = $1::jsonb WHERE id = $2",
        )
        .bind(serde_json::json!({"imported": total_imported, "skipped": total_skipped}))
        .bind(source.id)
        .execute(pool)
        .await?;

        info!(
            "xtream_import: source_id={} complete — total_imported={} total_skipped={}",
            source.id, total_imported, total_skipped
        );

        Ok(())
    }
}
