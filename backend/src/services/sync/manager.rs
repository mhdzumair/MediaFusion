/// Integration manager — scrobble playback to external platforms (Trakt).
///
/// Mirrors `python-deprecated/api/services/sync/manager.py`.
use std::sync::Arc;

use serde_json::{json, Value};
use sqlx::PgPool;
use tracing::{debug, warn};

use crate::{crypto::profile::decrypt_secrets, db::IntegrationType, state::AppState};

#[derive(Clone, Default)]
pub struct ScrobbleData {
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<i64>,
    pub title: String,
    pub media_type: String,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub progress: f64,
}

/// Media context resolved from a torrent playback request for scrobbling.
pub struct PlaybackMediaContext {
    pub title: String,
    pub media_type: String,
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<i64>,
}

struct ScrobbleIntegration {
    platform: IntegrationType,
    credentials: Value,
    settings: Value,
}

/// Notify external platforms that playback has started.
pub async fn scrobble_playback_start(
    state: &Arc<AppState>,
    profile_id: i32,
    imdb_id: Option<&str>,
    tmdb_id: Option<i64>,
    title: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) {
    if profile_id <= 0 {
        return;
    }
    let data = ScrobbleData {
        imdb_id: imdb_id.map(str::to_string),
        tmdb_id,
        title: title.to_string(),
        media_type: media_type.to_string(),
        season,
        episode,
        progress: 0.0,
    };
    scrobble_to_platforms(state, profile_id, &data, "start").await;
}

/// Notify external platforms that playback has paused.
pub async fn scrobble_playback_pause(
    state: &Arc<AppState>,
    profile_id: i32,
    imdb_id: Option<&str>,
    tmdb_id: Option<i64>,
    title: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    progress: f64,
) {
    if profile_id <= 0 {
        return;
    }
    let data = ScrobbleData {
        imdb_id: imdb_id.map(str::to_string),
        tmdb_id,
        title: title.to_string(),
        media_type: media_type.to_string(),
        season,
        episode,
        progress,
    };
    scrobble_to_platforms(state, profile_id, &data, "pause").await;
}

/// Notify external platforms that playback has stopped.
///
/// When `progress` is below `min_watch_percent` from integration settings, Trakt
/// will not mark the item as watched (same as the Python Trakt scrobble API usage).
pub async fn scrobble_playback_stop(
    state: &Arc<AppState>,
    profile_id: i32,
    imdb_id: Option<&str>,
    tmdb_id: Option<i64>,
    title: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    progress: f64,
) {
    if profile_id <= 0 {
        return;
    }
    let data = ScrobbleData {
        imdb_id: imdb_id.map(str::to_string),
        tmdb_id,
        title: title.to_string(),
        media_type: media_type.to_string(),
        season,
        episode,
        progress,
    };
    scrobble_to_platforms(state, profile_id, &data, "stop").await;
}

async fn min_watch_percent_for_profile(pool: &PgPool, profile_id: i32) -> f64 {
    let settings: Option<serde_json::Value> = sqlx::query_scalar(
        r#"
        SELECT settings FROM profile_integration
        WHERE profile_id = $1 AND platform = 'TRAKT' AND is_enabled = true AND scrobble_enabled = true
        LIMIT 1
        "#,
    )
    .bind(profile_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    settings
        .and_then(|s| s.get("min_watch_percent").and_then(|v| v.as_i64()))
        .unwrap_or(80) as f64
}

/// Scrobble pause/stop based on watch progress (Trakt marks watched on stop ≥ min_watch_percent).
pub async fn scrobble_playback_for_progress(
    state: &Arc<AppState>,
    profile_id: i32,
    imdb_id: Option<&str>,
    tmdb_id: Option<i64>,
    title: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    progress_seconds: i32,
    duration_seconds: Option<i32>,
) {
    if profile_id <= 0 || (imdb_id.is_none() && tmdb_id.is_none()) {
        return;
    }
    let Some(duration) = duration_seconds.filter(|&d| d > 0) else {
        return;
    };
    let progress_pct = (progress_seconds as f64 / duration as f64) * 100.0;
    let min_watch_percent = min_watch_percent_for_profile(&state.pool_ro, profile_id).await;
    if progress_pct >= min_watch_percent {
        scrobble_playback_stop(
            state,
            profile_id,
            imdb_id,
            tmdb_id,
            title,
            media_type,
            season,
            episode,
            progress_pct,
        )
        .await;
    } else if progress_pct >= 1.0 {
        scrobble_playback_pause(
            state,
            profile_id,
            imdb_id,
            tmdb_id,
            title,
            media_type,
            season,
            episode,
            progress_pct,
        )
        .await;
    }
}

async fn scrobble_to_platforms(
    state: &Arc<AppState>,
    profile_id: i32,
    data: &ScrobbleData,
    action: &str,
) {
    let integrations =
        match fetch_scrobble_integrations(&state.pool_ro, profile_id, &state.config.secret_key)
            .await
        {
            Ok(v) => v,
            Err(e) => {
                warn!("Failed to lookup integrations for scrobbling: {e}");
                return;
            }
        };

    for integration in integrations {
        if integration.platform != IntegrationType::Trakt {
            // Simkl does not support real-time scrobbling.
            continue;
        }
        if let Err(e) = scrobble_trakt(
            state,
            &integration.credentials,
            &integration.settings,
            data,
            action,
        )
        .await
        {
            warn!("Failed to scrobble to Trakt: {e}");
        }
    }
}

async fn fetch_scrobble_integrations(
    pool: &PgPool,
    profile_id: i32,
    secret_key: &[u8; 32],
) -> Result<Vec<ScrobbleIntegration>, sqlx::Error> {
    let rows = sqlx::query(
        r#"
        SELECT platform, encrypted_credentials, settings
        FROM profile_integration
        WHERE profile_id = $1 AND is_enabled = true AND scrobble_enabled = true
        "#,
    )
    .bind(profile_id)
    .fetch_all(pool)
    .await?;

    let mut out = Vec::new();
    for row in rows {
        use sqlx::Row;
        let platform: IntegrationType = row.try_get("platform")?;
        let encrypted: Option<String> = row.try_get("encrypted_credentials")?;
        let settings: Value = row
            .try_get::<Option<Value>, _>("settings")?
            .unwrap_or_else(|| json!({}));

        let Some(enc) = encrypted.filter(|s| !s.is_empty()) else {
            continue;
        };

        let credentials = decrypt_secrets(&enc, secret_key);
        out.push(ScrobbleIntegration {
            platform,
            credentials,
            settings,
        });
    }
    Ok(out)
}

async fn scrobble_trakt(
    state: &Arc<AppState>,
    credentials: &Value,
    settings: &Value,
    data: &ScrobbleData,
    action: &str,
) -> Result<(), String> {
    let min_watch_percent = settings
        .get("min_watch_percent")
        .and_then(|v| v.as_i64())
        .unwrap_or(80) as f64;

    if action == "stop" && data.progress < min_watch_percent {
        return Ok(());
    }

    let default_cid = state.config.trakt_client_id.as_deref().unwrap_or("");

    let access_token = credentials["access_token"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("missing access_token")?
        .to_string();
    let client_id = credentials["client_id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .unwrap_or(default_cid)
        .to_string();

    if client_id.is_empty() {
        return Err("Trakt client_id missing".to_string());
    }

    let mut ids = json!({});
    if let Some(ref imdb) = data.imdb_id {
        ids["imdb"] = json!(imdb);
    }
    if let Some(tmdb) = data.tmdb_id {
        ids["tmdb"] = json!(tmdb);
    }
    if ids.as_object().is_some_and(|o| o.is_empty()) {
        return Err("no external ids for scrobble".to_string());
    }

    let mut body = json!({ "progress": data.progress });
    if data.media_type == "movie" {
        body["movie"] = json!({ "ids": ids });
    } else {
        body["show"] = json!({ "ids": ids });
        body["episode"] = json!({
            "season": data.season,
            "number": data.episode,
        });
    }

    let url = format!("https://api.trakt.tv/scrobble/{action}");
    let res = state
        .http
        .post(&url)
        .bearer_auth(&access_token)
        .header("trakt-api-version", "2")
        .header("trakt-api-key", &client_id)
        .json(&body)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("network error: {e}"))?;

    let status = res.status();
    if status.is_success() {
        debug!(
            "Trakt scrobble {action} successful for {}",
            data.imdb_id.as_deref().unwrap_or(&data.title)
        );
        Ok(())
    } else if status == reqwest::StatusCode::TOO_MANY_REQUESTS {
        debug!(
            "Trakt scrobble {action} rate limited (429); will retry on next event"
        );
        Ok(())
    } else {
        Err(format!("Trakt scrobble HTTP {status}"))
    }
}

/// Resolve media + external IDs from a torrent info_hash for scrobbling.
pub async fn fetch_playback_media(
    pool: &PgPool,
    info_hash: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<PlaybackMediaContext> {
    let media_id: Option<i32> = match (season, episode) {
        (Some(s), Some(e)) => sqlx::query_scalar(
            r#"
                SELECT COALESCE(
                    (SELECT sml.media_id FROM stream_media_link sml
                     JOIN torrent_stream ts ON ts.stream_id = sml.stream_id
                     WHERE ts.info_hash = $1 AND sml.is_primary = true
                     LIMIT 1),
                    (SELECT fml.media_id FROM file_media_link fml
                     JOIN stream_file sf ON sf.id = fml.file_id
                     JOIN torrent_stream ts ON ts.stream_id = sf.stream_id
                     WHERE ts.info_hash = $1
                       AND fml.season_number = $2 AND fml.episode_number = $3
                     LIMIT 1),
                    (SELECT fml.media_id FROM file_media_link fml
                     JOIN stream_file sf ON sf.id = fml.file_id
                     JOIN torrent_stream ts ON ts.stream_id = sf.stream_id
                     WHERE ts.info_hash = $1
                     LIMIT 1)
                )
                "#,
        )
        .bind(info_hash)
        .bind(s)
        .bind(e)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten(),
        _ => sqlx::query_scalar(
            r#"
                SELECT COALESCE(
                    (SELECT sml.media_id FROM stream_media_link sml
                     JOIN torrent_stream ts ON ts.stream_id = sml.stream_id
                     WHERE ts.info_hash = $1 AND sml.is_primary = true
                     LIMIT 1),
                    (SELECT fml.media_id FROM file_media_link fml
                     JOIN stream_file sf ON sf.id = fml.file_id
                     JOIN torrent_stream ts ON ts.stream_id = sf.stream_id
                     WHERE ts.info_hash = $1
                     LIMIT 1)
                )
                "#,
        )
        .bind(info_hash)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten(),
    };

    let media_id = media_id?;
    let row = sqlx::query("SELECT title, type::text FROM media WHERE id = $1")
        .bind(media_id)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()?;

    use sqlx::Row;
    let title: String = row.try_get("title").ok()?;
    let raw_type: String = row.try_get("type").ok()?;
    let media_type = if raw_type == "SERIES" {
        "series".to_string()
    } else {
        "movie".to_string()
    };

    let ext_rows: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
            .bind(media_id)
            .fetch_all(pool)
            .await
            .unwrap_or_default();

    let mut imdb_id = None;
    let mut tmdb_id = None;
    for (provider, external_id) in ext_rows {
        match provider.as_str() {
            "imdb" => imdb_id = Some(external_id),
            "tmdb" => tmdb_id = external_id.parse().ok(),
            _ => {}
        }
    }

    if imdb_id.is_none() && tmdb_id.is_none() {
        return None;
    }

    Some(PlaybackMediaContext {
        title,
        media_type,
        imdb_id,
        tmdb_id,
    })
}

/// Spawn a background scrobble-start for torrent playback (non-blocking).
pub fn spawn_playback_scrobble(
    state: Arc<AppState>,
    profile_id: i32,
    info_hash: String,
    season: Option<i32>,
    episode: Option<i32>,
) {
    if profile_id <= 0 {
        return;
    }
    tokio::spawn(async move {
        let Some(ctx) = fetch_playback_media(&state.pool_ro, &info_hash, season, episode).await
        else {
            return;
        };
        scrobble_playback_start(
            &state,
            profile_id,
            ctx.imdb_id.as_deref(),
            ctx.tmdb_id,
            &ctx.title,
            &ctx.media_type,
            season,
            episode,
        )
        .await;
    });
}
