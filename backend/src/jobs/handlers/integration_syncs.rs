/// Background integration sync handler.
///
/// Runs on a cron schedule (or on-demand via the API). For each enabled
/// `profile_integration` row, decrypts credentials, refreshes the token if
/// needed, then imports watched items from the platform and/or exports local
/// watch history to the platform, depending on `sync_direction`.
///
/// Supported platforms: trakt, simkl.
/// Payload: `{"integration_id": <i32>}` to sync one, or `{}` to sync all.
use async_trait::async_trait;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::PgPool;
use tracing::{debug, info, warn};

use std::sync::Arc;
use tokio_util::sync::CancellationToken;

use crate::{
    crypto::profile::{decrypt_secrets, encrypt_secrets},
    db::{HistorySource, WatchAction},
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    state::AppState,
};

pub struct IntegrationSyncs;

// ─── Job payload ─────────────────────────────────────────────────────────────

#[derive(Deserialize, Default)]
struct SyncArgs {
    integration_id: Option<i32>,
}

// ─── DB row ───────────────────────────────────────────────────────────────────

struct IntegrationRow {
    id: i32,
    profile_id: i32,
    user_id: i32,
    platform: String,
    encrypted_credentials: Option<String>,
    sync_direction: String,
    last_sync_at: Option<DateTime<Utc>>,
}

// ─── Credentials (decrypted) ──────────────────────────────────────────────────

#[derive(Clone)]
struct Creds {
    access_token: String,
    refresh_token: Option<String>,
    expires_at: Option<i64>,
    client_id: String,
    client_secret: String,
}

impl Creds {
    fn from_json(v: &Value, default_id: &str, default_secret: &str) -> Option<Self> {
        let access_token = v["access_token"].as_str()?.to_string();
        let client_id = v["client_id"]
            .as_str()
            .filter(|s| !s.is_empty())
            .unwrap_or(default_id)
            .to_string();
        let client_secret = v["client_secret"]
            .as_str()
            .filter(|s| !s.is_empty())
            .unwrap_or(default_secret)
            .to_string();
        Some(Creds {
            access_token,
            refresh_token: v["refresh_token"].as_str().map(|s| s.to_string()),
            expires_at: v["expires_at"].as_i64(),
            client_id,
            client_secret,
        })
    }

    fn is_expired(&self) -> bool {
        match self.expires_at {
            Some(exp) => Utc::now().timestamp() >= exp - 300, // 5-min buffer
            None => false,
        }
    }
}

// ─── Sync stats ───────────────────────────────────────────────────────────────

#[derive(Default, Serialize)]
struct SyncStats {
    imported: i32,
    import_skipped: i32,
    import_errors: i32,
    exported: i32,
    export_skipped: i32,
    export_errors: i32,
}

// ─── Job handler ─────────────────────────────────────────────────────────────

#[async_trait]
impl JobHandler for IntegrationSyncs {
    const QUEUE: &'static str = "integration_syncs";
    const CONCURRENCY: usize = 4;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let args: SyncArgs = serde_json::from_value(args).unwrap_or_default();

        let rows = fetch_integrations(&ctx.state.pool_ro, args.integration_id).await?;
        if rows.is_empty() {
            info!("integration_syncs: no enabled integrations found");
            return Ok(());
        }
        info!(
            "integration_syncs: processing {} integration(s)",
            rows.len()
        );

        for row in rows {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            sync_one(&ctx, &row).await;
        }
        Ok(())
    }
}

/// Run sync immediately for a single integration (bypasses the job queue).
///
/// Used by the `Sync Now` UI button — the route spawns this so the HTTP
/// response returns instantly while sync proceeds in the background.
pub async fn sync_integration_inline(state: Arc<AppState>, integration_id: i32) {
    let rows = match fetch_integrations(&state.pool_ro, Some(integration_id)).await {
        Ok(r) => r,
        Err(e) => {
            warn!("sync_integration_inline: fetch failed id={integration_id}: {e}");
            return;
        }
    };
    let Some(row) = rows.into_iter().next() else {
        info!("sync_integration_inline: integration id={integration_id} not found or disabled");
        return;
    };
    let ctx = JobCtx {
        job_id: -1,
        attempt: 1,
        state,
        cancel: CancellationToken::new(),
    };
    sync_one(&ctx, &row).await;
}

// ─── Fetch ────────────────────────────────────────────────────────────────────

async fn fetch_integrations(
    pool: &PgPool,
    id_filter: Option<i32>,
) -> Result<Vec<IntegrationRow>, sqlx::Error> {
    let base = r#"
        SELECT pi.id, pi.profile_id, up.user_id,
               pi.platform::text AS platform,
               pi.encrypted_credentials,
               pi.sync_direction,
               pi.last_sync_at
        FROM profile_integration pi
        JOIN user_profiles up ON up.id = pi.profile_id
        WHERE pi.is_enabled = true
    "#;

    let rows = if let Some(id) = id_filter {
        sqlx::query(&format!("{base} AND pi.id = $1"))
            .bind(id)
            .fetch_all(pool)
            .await?
    } else {
        sqlx::query(base).fetch_all(pool).await?
    };

    rows.iter()
        .map(|r| {
            use sqlx::Row;
            Ok(IntegrationRow {
                id: r.try_get("id")?,
                profile_id: r.try_get("profile_id")?,
                user_id: r.try_get("user_id")?,
                platform: r.try_get::<String, _>("platform")?.to_lowercase(),
                encrypted_credentials: r.try_get("encrypted_credentials")?,
                sync_direction: r.try_get("sync_direction")?,
                last_sync_at: r.try_get("last_sync_at")?,
            })
        })
        .collect()
}

// ─── Per-integration orchestration ───────────────────────────────────────────

async fn sync_one(ctx: &JobCtx, row: &IntegrationRow) {
    mark_status(&ctx.state.pool, row.id, "in_progress", None, None).await;

    let enc = match &row.encrypted_credentials {
        Some(s) if !s.is_empty() => s.clone(),
        _ => {
            warn!("integration_syncs: id={} has no credentials", row.id);
            mark_status(
                &ctx.state.pool,
                row.id,
                "failed",
                Some("no credentials"),
                None,
            )
            .await;
            return;
        }
    };

    let raw = decrypt_secrets(&enc, &ctx.state.config.secret_key);

    let result = match row.platform.as_str() {
        "trakt" => {
            let default_cid = ctx.state.config.trakt_client_id.as_deref().unwrap_or("");
            let default_csec = ctx
                .state
                .config
                .trakt_client_secret
                .as_deref()
                .unwrap_or("");
            let Some(mut creds) = Creds::from_json(&raw, default_cid, default_csec) else {
                mark_status(
                    &ctx.state.pool,
                    row.id,
                    "failed",
                    Some("invalid credentials"),
                    None,
                )
                .await;
                return;
            };
            if creds.client_id.is_empty() || creds.client_secret.is_empty() {
                mark_status(
                    &ctx.state.pool,
                    row.id,
                    "failed",
                    Some("Trakt client_id/secret missing from credentials"),
                    None,
                )
                .await;
                return;
            }
            if creds.is_expired() {
                match trakt_refresh(&ctx.state.http, &creds).await {
                    Ok(refreshed) => {
                        save_credentials(
                            &ctx.state.pool,
                            row.id,
                            &refreshed,
                            &ctx.state.config.secret_key,
                        )
                        .await;
                        creds = refreshed;
                    }
                    Err(e) => {
                        warn!("trakt_refresh: id={} error={}", row.id, e);
                        mark_status(
                            &ctx.state.pool,
                            row.id,
                            "failed",
                            Some(&format!("token refresh failed: {e}")),
                            None,
                        )
                        .await;
                        return;
                    }
                }
            }
            sync_trakt(ctx, row, &creds).await
        }
        "simkl" => {
            let default_cid = ctx.state.config.simkl_client_id.as_deref().unwrap_or("");
            let default_csec = ctx
                .state
                .config
                .simkl_client_secret
                .as_deref()
                .unwrap_or("");
            let Some(mut creds) = Creds::from_json(&raw, default_cid, default_csec) else {
                mark_status(
                    &ctx.state.pool,
                    row.id,
                    "failed",
                    Some("invalid credentials"),
                    None,
                )
                .await;
                return;
            };
            if creds.client_id.is_empty() || creds.client_secret.is_empty() {
                mark_status(
                    &ctx.state.pool,
                    row.id,
                    "failed",
                    Some("Simkl client_id/secret missing from credentials"),
                    None,
                )
                .await;
                return;
            }
            if creds.is_expired() {
                match simkl_refresh(&ctx.state.http, &creds).await {
                    Ok(refreshed) => {
                        save_credentials(
                            &ctx.state.pool,
                            row.id,
                            &refreshed,
                            &ctx.state.config.secret_key,
                        )
                        .await;
                        creds = refreshed;
                    }
                    Err(e) => {
                        warn!("simkl_refresh: id={} error={}", row.id, e);
                        mark_status(
                            &ctx.state.pool,
                            row.id,
                            "failed",
                            Some(&format!("token refresh failed: {e}")),
                            None,
                        )
                        .await;
                        return;
                    }
                }
            }
            sync_simkl(ctx, row, &creds).await
        }
        other => {
            warn!(
                "integration_syncs: unsupported platform '{other}' id={}",
                row.id
            );
            mark_status(
                &ctx.state.pool,
                row.id,
                "failed",
                Some("unsupported platform"),
                None,
            )
            .await;
            return;
        }
    };

    let status = if result.import_errors + result.export_errors > 0 {
        "partial"
    } else {
        "success"
    };
    info!(
        "integration_syncs: id={} platform={} imported={} exported={} status={}",
        row.id, row.platform, result.imported, result.exported, status
    );
    mark_status(&ctx.state.pool, row.id, status, None, Some(&result)).await;
}

// ─── Trakt ────────────────────────────────────────────────────────────────────

async fn sync_trakt(ctx: &JobCtx, row: &IntegrationRow, creds: &Creds) -> SyncStats {
    let mut stats = SyncStats::default();
    let dir = row.sync_direction.as_str();
    let bidirectional = dir == "two_way" || dir == "bidirectional";
    if dir == "platform_to_mf" || bidirectional {
        trakt_import(ctx, row, creds, &mut stats).await;
    }
    if dir == "mf_to_platform" || bidirectional {
        trakt_export(ctx, row, creds, &mut stats).await;
    }
    stats
}

async fn trakt_import(ctx: &JobCtx, row: &IntegrationRow, creds: &Creds, stats: &mut SyncStats) {
    // Movies
    if let Some(items) = trakt_get(
        &ctx.state.http,
        "https://api.trakt.tv/sync/watched/movies",
        creds,
    )
    .await
    .and_then(|v| v.as_array().cloned())
    {
        for item in &items {
            let movie = &item["movie"];
            let imdb = movie["ids"]["imdb"].as_str();
            let tmdb = movie["ids"]["tmdb"].as_i64().map(|n| n.to_string());
            let title = movie["title"].as_str().unwrap_or("").to_string();
            let watched_at = parse_dt(item["last_watched_at"].as_str());

            let Some(mid) = resolve_media_id(&ctx.state.pool_ro, imdb, tmdb.as_deref()).await
            else {
                debug!("trakt_import: no local media for movie '{title}'");
                stats.import_skipped += 1;
                continue;
            };
            match upsert_watch(
                &ctx.state.pool,
                row.user_id,
                row.profile_id,
                mid,
                &title,
                "movie",
                None,
                None,
                watched_at,
                "TRAKT",
            )
            .await
            {
                true => stats.imported += 1,
                false => stats.import_skipped += 1,
            }
        }
    }

    // Shows
    if let Some(shows) = trakt_get(
        &ctx.state.http,
        "https://api.trakt.tv/sync/watched/shows",
        creds,
    )
    .await
    .and_then(|v| v.as_array().cloned())
    {
        for show in &shows {
            let s = &show["show"];
            let imdb = s["ids"]["imdb"].as_str();
            let tmdb = s["ids"]["tmdb"].as_i64().map(|n| n.to_string());
            let title = s["title"].as_str().unwrap_or("").to_string();
            let Some(mid) = resolve_media_id(&ctx.state.pool_ro, imdb, tmdb.as_deref()).await
            else {
                debug!("trakt_import: no local media for show '{title}'");
                stats.import_skipped += 1;
                continue;
            };
            for season in show["seasons"].as_array().unwrap_or(&vec![]) {
                let s_num = season["number"].as_i64().unwrap_or(1) as i32;
                for ep in season["episodes"].as_array().unwrap_or(&vec![]) {
                    let e_num = ep["number"].as_i64().unwrap_or(1) as i32;
                    let watched_at = parse_dt(ep["last_watched_at"].as_str());
                    match upsert_watch(
                        &ctx.state.pool,
                        row.user_id,
                        row.profile_id,
                        mid,
                        &title,
                        "series",
                        Some(s_num),
                        Some(e_num),
                        watched_at,
                        "TRAKT",
                    )
                    .await
                    {
                        true => stats.imported += 1,
                        false => stats.import_skipped += 1,
                    }
                }
            }
        }
    }
}

async fn trakt_export(ctx: &JobCtx, row: &IntegrationRow, creds: &Creds, stats: &mut SyncStats) {
    let items = local_history(&ctx.state.pool_ro, row.profile_id, row.last_sync_at).await;
    if items.is_empty() {
        return;
    }

    let mut movies: Vec<Value> = Vec::new();
    let mut shows_map: std::collections::HashMap<
        i32,
        (Value, std::collections::HashMap<i32, Vec<i32>>),
    > = std::collections::HashMap::new();

    for item in &items {
        let imdb = resolve_ext_id(&ctx.state.pool_ro, item.media_id, "imdb").await;
        let tmdb = resolve_ext_id(&ctx.state.pool_ro, item.media_id, "tmdb").await;
        if imdb.is_none() && tmdb.is_none() {
            stats.export_skipped += 1;
            continue;
        }
        let mut ids = serde_json::json!({});
        if let Some(ref v) = imdb {
            ids["imdb"] = Value::String(v.clone());
        }
        if let Some(ref v) = tmdb {
            if let Ok(n) = v.parse::<i64>() {
                ids["tmdb"] = n.into();
            }
        }

        if item.media_type == "movie" {
            movies.push(
                serde_json::json!({ "ids": ids, "watched_at": item.watched_at.to_rfc3339() }),
            );
        } else if let (Some(s), Some(e)) = (item.season, item.episode) {
            let entry = shows_map.entry(item.media_id).or_insert_with(|| {
                (
                    serde_json::json!({ "ids": ids }),
                    std::collections::HashMap::new(),
                )
            });
            entry.1.entry(s).or_default().push(e);
        }
    }

    let shows: Vec<Value> = shows_map
        .into_values()
        .map(|(show_ids, seasons)| {
            let seasons: Vec<Value> = seasons
                .into_iter()
                .map(|(s, eps)| {
                    serde_json::json!({
                        "number": s,
                        "episodes": eps.iter().map(|e| serde_json::json!({"number": e})).collect::<Vec<_>>(),
                    })
                })
                .collect();
            serde_json::json!({ "ids": show_ids["ids"], "seasons": seasons })
        })
        .collect();

    let total = movies.len()
        + shows
            .iter()
            .flat_map(|s| s["seasons"].as_array())
            .flatten()
            .flat_map(|s| s["episodes"].as_array())
            .map(|e| e.len())
            .sum::<usize>();

    let res = ctx
        .state
        .http
        .post("https://api.trakt.tv/sync/history")
        .bearer_auth(&creds.access_token)
        .header("trakt-api-version", "2")
        .header("trakt-api-key", &creds.client_id)
        .json(&serde_json::json!({"movies": movies, "shows": shows}))
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await;

    match res {
        Ok(r) if r.status().is_success() => stats.exported += total as i32,
        Ok(r) => {
            warn!(
                "trakt_export: HTTP {} profile={}",
                r.status(),
                row.profile_id
            );
            stats.export_errors += 1;
        }
        Err(e) => {
            warn!("trakt_export: {e}");
            stats.export_errors += 1;
        }
    }
}

async fn trakt_get(http: &reqwest::Client, url: &str, creds: &Creds) -> Option<Value> {
    http.get(url)
        .bearer_auth(&creds.access_token)
        .header("trakt-api-version", "2")
        .header("trakt-api-key", &creds.client_id)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()
}

async fn trakt_refresh(http: &reqwest::Client, creds: &Creds) -> Result<Creds, String> {
    let res = http
        .post("https://api.trakt.tv/oauth/token")
        .json(&serde_json::json!({
            "refresh_token": creds.refresh_token,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        }))
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("network error: {e}"))?;

    let status = res.status();
    let body = res.text().await.unwrap_or_default();

    if !status.is_success() {
        let snippet: String = body.chars().take(200).collect();
        return Err(format!("Trakt HTTP {status}: {snippet}"));
    }

    let resp: Value =
        serde_json::from_str(&body).map_err(|e| format!("invalid JSON from Trakt: {e}"))?;

    let access_token = resp["access_token"]
        .as_str()
        .ok_or("Trakt response missing access_token")?
        .to_string();

    Ok(Creds {
        access_token,
        refresh_token: resp["refresh_token"]
            .as_str()
            .map(|s| s.to_string())
            .or_else(|| creds.refresh_token.clone()),
        expires_at: resp["expires_in"]
            .as_i64()
            .map(|e| Utc::now().timestamp() + e),
        client_id: creds.client_id.clone(),
        client_secret: creds.client_secret.clone(),
    })
}

// ─── Simkl ────────────────────────────────────────────────────────────────────

async fn sync_simkl(ctx: &JobCtx, row: &IntegrationRow, creds: &Creds) -> SyncStats {
    let mut stats = SyncStats::default();
    let dir = row.sync_direction.as_str();
    let bidirectional = dir == "two_way" || dir == "bidirectional";
    if dir == "platform_to_mf" || bidirectional {
        simkl_import(ctx, row, creds, &mut stats).await;
    }
    if dir == "mf_to_platform" || bidirectional {
        simkl_export(ctx, row, creds, &mut stats).await;
    }
    stats
}

async fn simkl_import(ctx: &JobCtx, row: &IntegrationRow, creds: &Creds, stats: &mut SyncStats) {
    let date_from = row
        .last_sync_at
        .map(|dt| format!("&date_from={}", dt.format("%Y-%m-%d")))
        .unwrap_or_default();

    for kind in &["movies", "shows", "anime"] {
        let url = format!("https://api.simkl.com/sync/all-items/{kind}?extended=full{date_from}");
        let resp: Option<Value> = async {
            let r = ctx
                .state
                .http
                .get(&url)
                .bearer_auth(&creds.access_token)
                .header("simkl-api-key", &creds.client_id)
                .timeout(std::time::Duration::from_secs(30))
                .send()
                .await
                .ok()?;
            r.json().await.ok()
        }
        .await;

        let items = match resp.as_ref().and_then(|v| v[*kind].as_array()) {
            Some(arr) => arr.clone(),
            None => continue,
        };

        let mf_type = if *kind == "movies" { "movie" } else { "series" };
        let inner_key = if *kind == "movies" { "movie" } else { "show" };

        for item in &items {
            if item["status"].as_str() != Some("completed") {
                stats.import_skipped += 1;
                continue;
            }
            let inner = &item[inner_key];
            let imdb = inner["ids"]["imdb"].as_str();
            let tmdb = inner["ids"]["tmdb"].as_i64().map(|n| n.to_string());
            let title = inner["title"].as_str().unwrap_or("").to_string();
            let watched_at = parse_dt(item["last_watched_at"].as_str());

            let Some(mid) = resolve_media_id(&ctx.state.pool_ro, imdb, tmdb.as_deref()).await
            else {
                debug!("simkl_import: no local media for '{title}'");
                stats.import_skipped += 1;
                continue;
            };

            if mf_type == "movie" {
                match upsert_watch(
                    &ctx.state.pool,
                    row.user_id,
                    row.profile_id,
                    mid,
                    &title,
                    "movie",
                    None,
                    None,
                    watched_at,
                    "SIMKL",
                )
                .await
                {
                    true => stats.imported += 1,
                    false => stats.import_skipped += 1,
                }
            } else {
                for season in item["seasons"].as_array().unwrap_or(&vec![]) {
                    let s_num = season["number"].as_i64().unwrap_or(1) as i32;
                    for ep in season["episodes"].as_array().unwrap_or(&vec![]) {
                        let e_num = ep["number"].as_i64().unwrap_or(1) as i32;
                        match upsert_watch(
                            &ctx.state.pool,
                            row.user_id,
                            row.profile_id,
                            mid,
                            &title,
                            "series",
                            Some(s_num),
                            Some(e_num),
                            watched_at,
                            "SIMKL",
                        )
                        .await
                        {
                            true => stats.imported += 1,
                            false => stats.import_skipped += 1,
                        }
                    }
                }
            }
        }
    }
}

async fn simkl_export(ctx: &JobCtx, row: &IntegrationRow, creds: &Creds, stats: &mut SyncStats) {
    let items = local_history(&ctx.state.pool_ro, row.profile_id, row.last_sync_at).await;
    if items.is_empty() {
        return;
    }

    let mut movies: Vec<Value> = Vec::new();
    let mut shows: Vec<Value> = Vec::new();

    for item in &items {
        let imdb = resolve_ext_id(&ctx.state.pool_ro, item.media_id, "imdb").await;
        let tmdb = resolve_ext_id(&ctx.state.pool_ro, item.media_id, "tmdb").await;
        if imdb.is_none() && tmdb.is_none() {
            stats.export_skipped += 1;
            continue;
        }

        let mut ids = serde_json::json!({});
        if let Some(ref v) = imdb {
            ids["imdb"] = Value::String(v.clone());
        }
        if let Some(ref v) = tmdb {
            if let Ok(n) = v.parse::<i64>() {
                ids["tmdb"] = n.into();
            }
        }

        if item.media_type == "movie" {
            movies.push(serde_json::json!({
                "ids": ids, "to": "completed",
                "watched_at": item.watched_at.to_rfc3339()
            }));
        } else if let (Some(s), Some(e)) = (item.season, item.episode) {
            shows.push(serde_json::json!({
                "ids": ids, "to": "completed",
                "seasons": [{"number": s, "episodes": [{"number": e}]}]
            }));
        }
    }

    let total = movies.len() + shows.len();
    let res = ctx
        .state
        .http
        .post("https://api.simkl.com/sync/history")
        .bearer_auth(&creds.access_token)
        .header("simkl-api-key", &creds.client_id)
        .json(&serde_json::json!({"movies": movies, "shows": shows}))
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await;

    match res {
        Ok(r) if r.status().is_success() => stats.exported += total as i32,
        Ok(r) => {
            warn!(
                "simkl_export: HTTP {} profile={}",
                r.status(),
                row.profile_id
            );
            stats.export_errors += 1;
        }
        Err(e) => {
            warn!("simkl_export: {e}");
            stats.export_errors += 1;
        }
    }
}

async fn simkl_refresh(http: &reqwest::Client, creds: &Creds) -> Result<Creds, String> {
    let res = http
        .post("https://api.simkl.com/oauth/token")
        .header("simkl-api-key", &creds.client_id)
        .json(&serde_json::json!({
            "refresh_token": creds.refresh_token,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "grant_type": "refresh_token",
        }))
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("network error: {e}"))?;

    let status = res.status();
    let body = res.text().await.unwrap_or_default();

    if !status.is_success() {
        let snippet: String = body.chars().take(200).collect();
        return Err(format!("Simkl HTTP {status}: {snippet}"));
    }

    let resp: Value =
        serde_json::from_str(&body).map_err(|e| format!("invalid JSON from Simkl: {e}"))?;

    let access_token = resp["access_token"]
        .as_str()
        .ok_or("Simkl response missing access_token")?
        .to_string();

    Ok(Creds {
        access_token,
        refresh_token: resp["refresh_token"]
            .as_str()
            .map(|s| s.to_string())
            .or_else(|| creds.refresh_token.clone()),
        expires_at: resp["expires_in"]
            .as_i64()
            .map(|e| Utc::now().timestamp() + e),
        client_id: creds.client_id.clone(),
        client_secret: creds.client_secret.clone(),
    })
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

struct HistoryItem {
    media_id: i32,
    media_type: String,
    season: Option<i32>,
    episode: Option<i32>,
    watched_at: DateTime<Utc>,
}

async fn local_history(
    pool: &PgPool,
    profile_id: i32,
    since: Option<DateTime<Utc>>,
) -> Vec<HistoryItem> {
    let rows = if let Some(since) = since {
        sqlx::query(
            "SELECT media_id, media_type, season, episode, watched_at \
             FROM watch_history \
             WHERE profile_id=$1 AND action=$2 \
             AND source=$3 AND watched_at > $4 \
             ORDER BY watched_at ASC",
        )
        .bind(profile_id)
        .bind(WatchAction::Watched)
        .bind(HistorySource::Mediafusion)
        .bind(since)
        .fetch_all(pool)
        .await
    } else {
        sqlx::query(
            "SELECT media_id, media_type, season, episode, watched_at \
             FROM watch_history \
             WHERE profile_id=$1 AND action=$2 \
             AND source=$3 \
             ORDER BY watched_at ASC",
        )
        .bind(profile_id)
        .bind(WatchAction::Watched)
        .bind(HistorySource::Mediafusion)
        .fetch_all(pool)
        .await
    };

    rows.unwrap_or_default()
        .into_iter()
        .filter_map(|r| {
            use sqlx::Row;
            Some(HistoryItem {
                media_id: r.try_get("media_id").ok()?,
                media_type: r.try_get("media_type").ok()?,
                season: r.try_get("season").ok()?,
                episode: r.try_get("episode").ok()?,
                watched_at: r.try_get("watched_at").ok()?,
            })
        })
        .collect()
}

/// Returns `true` if a new row was inserted.
#[allow(clippy::too_many_arguments)]
async fn upsert_watch(
    pool: &PgPool,
    user_id: i32,
    profile_id: i32,
    media_id: i32,
    title: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    watched_at: DateTime<Utc>,
    source: &str,
) -> bool {
    let exists: bool = if season.is_some() {
        sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM watch_history \
             WHERE profile_id=$1 AND media_id=$2 AND season=$3 AND episode=$4)",
        )
        .bind(profile_id)
        .bind(media_id)
        .bind(season)
        .bind(episode)
        .fetch_one(pool)
        .await
        .unwrap_or(true)
    } else {
        sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM watch_history \
             WHERE profile_id=$1 AND media_id=$2 AND season IS NULL)",
        )
        .bind(profile_id)
        .bind(media_id)
        .fetch_one(pool)
        .await
        .unwrap_or(true)
    };

    if exists {
        return false;
    }

    sqlx::query(
        r#"
        INSERT INTO watch_history
            (user_id, profile_id, media_id, title, media_type,
             season, episode, progress, watched_at, action, source, stream_info)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 100, $8, $9,
                $10, '{}')
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(user_id)
    .bind(profile_id)
    .bind(media_id)
    .bind(title)
    .bind(media_type)
    .bind(season)
    .bind(episode)
    .bind(watched_at)
    .bind(WatchAction::Watched)
    .bind(HistorySource::from_wire(source).unwrap_or(HistorySource::Mediafusion))
    .execute(pool)
    .await
    .map(|r| r.rows_affected() > 0)
    .unwrap_or(false)
}

async fn resolve_media_id(pool: &PgPool, imdb: Option<&str>, tmdb: Option<&str>) -> Option<i32> {
    if let Some(iid) = imdb {
        let r: Option<(i32,)> = sqlx::query_as(
            "SELECT media_id FROM media_external_id WHERE provider='imdb' AND external_id=$1 LIMIT 1",
        )
        .bind(iid).fetch_optional(pool).await.ok().flatten();
        if let Some((id,)) = r {
            return Some(id);
        }
    }
    if let Some(tid) = tmdb {
        let r: Option<(i32,)> = sqlx::query_as(
            "SELECT media_id FROM media_external_id WHERE provider='tmdb' AND external_id=$1 LIMIT 1",
        )
        .bind(tid).fetch_optional(pool).await.ok().flatten();
        if let Some((id,)) = r {
            return Some(id);
        }
    }
    None
}

async fn resolve_ext_id(pool: &PgPool, media_id: i32, provider: &str) -> Option<String> {
    sqlx::query_scalar::<_, String>(
        "SELECT external_id FROM media_external_id WHERE media_id=$1 AND provider=$2 LIMIT 1",
    )
    .bind(media_id)
    .bind(provider)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
}

async fn save_credentials(pool: &PgPool, id: i32, creds: &Creds, key: &[u8; 32]) {
    let secrets = serde_json::json!({
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "expires_at": creds.expires_at,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    });
    if let Some(enc) = encrypt_secrets(&secrets, key) {
        let _ = sqlx::query(
            "UPDATE profile_integration SET encrypted_credentials=$1, updated_at=NOW() WHERE id=$2",
        )
        .bind(enc)
        .bind(id)
        .execute(pool)
        .await;
    }
}

async fn mark_status(
    pool: &PgPool,
    id: i32,
    status: &str,
    error: Option<&str>,
    stats: Option<&SyncStats>,
) {
    let stats_json = stats
        .and_then(|s| serde_json::to_value(s).ok())
        .unwrap_or(Value::Object(Default::default()));

    let _ = sqlx::query(
        r#"
        UPDATE profile_integration
        SET last_sync_status = $1,
            last_sync_error  = $2,
            last_sync_stats  = $3,
            last_sync_at     = CASE WHEN $1 != 'in_progress' THEN NOW() ELSE last_sync_at END,
            updated_at       = NOW()
        WHERE id = $4
        "#,
    )
    .bind(status)
    .bind(error)
    .bind(stats_json)
    .bind(id)
    .execute(pool)
    .await;
}

fn parse_dt(s: Option<&str>) -> DateTime<Utc> {
    s.and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .map(|dt| dt.with_timezone(&Utc))
        .unwrap_or_else(Utc::now)
}
