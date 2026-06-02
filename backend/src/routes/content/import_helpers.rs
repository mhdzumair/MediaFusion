/// Shared helpers for all content import endpoints.
///
/// Used by: torrent_import, nzb_import, http_import, youtube_import, acestream_import, m3u_import.
use std::hash::{Hash, Hasher};
use std::sync::OnceLock;

use axum::http::StatusCode;
use chrono::Utc;
use fred::prelude::KeysInterface;
use serde_json::{json, Value};
use sqlx::PgPool;
use uuid::Uuid;

use crate::{
    db::{MediaType, TorrentType},
    parser::detect_sports_category,
    state::AppState,
};

// ─── Adult content filter ─────────────────────────────────────────────────────

static ADULT_CONTENT_RE: OnceLock<regex::Regex> = OnceLock::new();

pub fn adult_content_re() -> &'static regex::Regex {
    ADULT_CONTENT_RE.get_or_init(|| {
        regex::Regex::new(
            r"(?i)(^|\b|\s|$|[\[._-])(18\s*\+|adults?|porn|sex|xxx|nude|boobs?|pussy|ass|bigass|bigtits?|blowjob|hardfuck|onlyfans?|naked|hot|milf|slut|doggy|anal|threesome|foursome|erotic|sexy|18\s*plus|trailer|RiffTrax|zipx)(\b|\s|$|[\]._-])"
        ).unwrap()
    })
}

pub fn is_adult_content(title: &str) -> bool {
    adult_content_re().is_match(title)
}

// ─── Anonymous display name validation ───────────────────────────────────────

static ANON_NAME_RE: OnceLock<regex::Regex> = OnceLock::new();

pub fn anon_name_re() -> &'static regex::Regex {
    ANON_NAME_RE.get_or_init(|| regex::Regex::new(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$").unwrap())
}

pub fn normalize_anonymous_display_name(value: Option<&str>) -> Option<String> {
    let v = value?;
    let normalized = v.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.is_empty() || normalized.len() > 32 {
        return None;
    }
    if !anon_name_re().is_match(&normalized) {
        return None;
    }
    Some(normalized)
}

pub fn resolve_uploader_identity(
    is_anonymous: bool,
    anon_display_name: Option<&str>,
    username: &str,
    user_id: i64,
) -> (String, Option<i64>) {
    if is_anonymous {
        let name = normalize_anonymous_display_name(Some(anon_display_name.unwrap_or("")))
            .unwrap_or_else(|| "Anonymous".to_string());
        (name, None)
    } else {
        (username.to_string(), Some(user_id))
    }
}

// ─── User info ────────────────────────────────────────────────────────────────

pub struct UserInfo {
    pub username: String,
    pub uploads_restricted: bool,
    pub role: String,
    pub contribute_anonymously: bool,
    pub is_active: bool,
}

/// Python: `should_auto_approve = is_privileged_reviewer or (user.is_active and not resolved_is_anonymous)`.
pub fn should_auto_approve_import(
    is_privileged: bool,
    is_active: bool,
    is_anonymous: bool,
) -> bool {
    is_privileged || (is_active && !is_anonymous)
}

/// Stream visibility at submit (Python: `contribution_data["is_public"] = should_auto_approve`).
pub fn stream_is_public_on_submit(auto_approve: bool, requested_public: bool) -> bool {
    if auto_approve {
        requested_public
    } else {
        false
    }
}

/// Message when a private stream is saved while awaiting moderator review.
pub fn pending_import_message(import_kind: &str) -> String {
    format!("{import_kind} submitted for review and saved privately for your account.")
}

pub async fn fetch_user_info(pool: &sqlx::PgPool, user_id: i64) -> Option<UserInfo> {
    let row: Option<(String, bool, crate::db::UserRole, bool, bool)> = sqlx::query_as(
        "SELECT COALESCE(username, 'user'), uploads_restricted, role, contribute_anonymously, is_active FROM users WHERE id = $1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    row.map(
        |(username, uploads_restricted, role, contribute_anonymously, is_active)| UserInfo {
            username,
            uploads_restricted,
            role: role.as_api_wire().to_string(),
            contribute_anonymously,
            is_active,
        },
    )
}

// ─── Upload permission guard ──────────────────────────────────────────────────

pub async fn enforce_upload_permissions(
    pool: &sqlx::PgPool,
    redis: &fred::clients::Client,
    user_id: i64,
    uploads_restricted: bool,
    role: &str,
) -> Result<(), (StatusCode, String)> {
    if matches!(role, "moderator" | "admin") {
        return Ok(());
    }
    if uploads_restricted {
        return Err((
            StatusCode::FORBIDDEN,
            "Your account is restricted from uploading content. Please contact support."
                .to_string(),
        ));
    }

    let key = format!("upload-attempts:{user_id}");
    let count = async {
        let val: fred::interfaces::FredResult<i64> = redis.incr(&key).await;
        if let Ok(n) = val {
            let ttl: fred::interfaces::FredResult<i64> = redis.ttl(&key).await;
            if ttl.unwrap_or(-1) == -1 {
                redis.expire::<(), _>(&key, 3600, None).await.ok();
            }
            Some(n)
        } else {
            None
        }
    }
    .await;

    let uploads_last_hour = match count {
        Some(n) => n,
        None => {
            let one_hour_ago = Utc::now().timestamp() - 3600;
            sqlx::query_scalar(
                "SELECT COUNT(*) FROM contributions WHERE user_id=$1 AND contribution_type IN ('torrent','nzb','http','youtube','acestream','telegram') AND created_at >= to_timestamp($2)"
            )
            .bind(user_id)
            .bind(one_hour_ago as f64)
            .fetch_one(pool)
            .await
            .unwrap_or(0i64)
        }
    };

    let limit: i64 = sqlx::query_scalar(
        "SELECT max_upload_contributions_per_hour FROM contribution_settings WHERE id='default' LIMIT 1",
    )
    .fetch_optional(pool)
    .await
    .unwrap_or(None)
    .unwrap_or(20);

    if uploads_last_hour > limit {
        return Err((
            StatusCode::TOO_MANY_REQUESTS,
            format!("Upload rate limit reached. Please wait before submitting more than {limit} uploads/hour."),
        ));
    }
    Ok(())
}

// ─── Contribution record ──────────────────────────────────────────────────────

pub async fn create_contribution_record(
    pool: &sqlx::PgPool,
    user_id: Option<i64>,
    contribution_type: &str,
    target_id: Option<&str>,
    data: &serde_json::Value,
    auto_approve: bool,
    is_privileged: bool,
) -> Result<String, sqlx::Error> {
    let id = Uuid::new_v4().to_string();
    let status = if auto_approve {
        crate::db::ContributionStatus::Approved
    } else {
        crate::db::ContributionStatus::Pending
    };
    let reviewed_by: Option<&str> = if auto_approve { Some("auto") } else { None };
    let review_notes: Option<String> = if is_privileged {
        Some("Auto-approved: Privileged reviewer".to_string())
    } else if auto_approve {
        Some("Auto-approved: Active user content import".to_string())
    } else {
        None
    };

    sqlx::query(
        r#"INSERT INTO contributions(
               id, user_id, contribution_type, target_id, data, status,
               reviewed_by, reviewed_at, review_notes, admin_review_requested,
               created_at, updated_at
           ) VALUES(
               $1, $2, $3, $4, $5, $6,
               $7, CASE WHEN $8 THEN NOW() ELSE NULL END, $9, false,
               NOW(), NOW()
           )"#,
    )
    .bind(&id)
    .bind(user_id)
    .bind(contribution_type)
    .bind(target_id)
    .bind(data)
    .bind(status)
    .bind(reviewed_by)
    .bind(auto_approve)
    .bind(review_notes)
    .execute(pool)
    .await?;

    Ok(id)
}

pub const POINT_ELIGIBLE_IMPORT_TYPES: &[&str] = &[
    "stream",
    "torrent",
    "telegram",
    "youtube",
    "nzb",
    "http",
    "acestream",
];

pub async fn award_contribution_points(pool: &sqlx::PgPool, user_id: i64, contribution_type: &str) {
    if !POINT_ELIGIBLE_IMPORT_TYPES.contains(&contribution_type) {
        return;
    }
    let settings: Option<(i64, i64, i64, i64)> = sqlx::query_as(
        "SELECT points_per_stream_edit, contributor_threshold, trusted_threshold, expert_threshold FROM contribution_settings WHERE id='default' LIMIT 1"
    )
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    let (points_per_edit, contributor_t, trusted_t, expert_t) =
        settings.unwrap_or((5, 10, 50, 200));

    sqlx::query(
        r#"UPDATE users SET
               contribution_points = GREATEST(0, contribution_points + $1),
               stream_edits_approved = stream_edits_approved + 1,
               contribution_level = CASE
                   WHEN contribution_points + $1 >= $2 THEN 'expert'
                   WHEN contribution_points + $1 >= $3 THEN 'trusted'
                   WHEN contribution_points + $1 >= $4 THEN 'contributor'
                   ELSE 'new'
               END
           WHERE id = $5"#,
    )
    .bind(points_per_edit)
    .bind(expert_t)
    .bind(trusted_t)
    .bind(contributor_t)
    .bind(user_id)
    .execute(pool)
    .await
    .ok();
}

// ─── Moderator notification ───────────────────────────────────────────────────

pub async fn notify_pending_contribution(
    http: &reqwest::Client,
    bot_token: &str,
    chat_id: &str,
    host_url: &str,
    contribution_type: &str,
    uploader_name: &str,
    data: &serde_json::Value,
) {
    let title = data
        .get("name")
        .or_else(|| data.get("title"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let info_hash = data.get("info_hash").and_then(|v| v.as_str()).unwrap_or("");

    let mut msg = format!(
        "🆕 Pending User Upload\n\n*Type*: `{contribution_type}`\n*Uploader*: `{uploader_name}`\n"
    );
    if !title.is_empty() {
        let t: String = title.chars().take(180).collect();
        msg.push_str(&format!("*Title*: `{t}`\n"));
    }
    if let Some(mt) = data.get("meta_type").and_then(|v| v.as_str()) {
        msg.push_str(&format!("*Media Type*: `{mt}`\n"));
    }
    if !info_hash.is_empty() {
        msg.push_str(&format!("*Info Hash*: `{info_hash}`\n"));
    }
    let review_url = format!("{host_url}/app/dashboard/moderator");
    msg.push_str(&format!("\n*Review Queue*: [View]({review_url})"));
    if !info_hash.is_empty() {
        let block_url = format!("{host_url}/scraper?action=block_torrent&info_hash={info_hash}");
        msg.push_str(&format!("\n[🚫 Block/Delete Torrent]({block_url})"));
    }

    let url = format!("https://api.telegram.org/bot{bot_token}/sendMessage");
    let payload = json!({
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": true,
    });
    http.post(&url).json(&payload).send().await.ok();
}

// ─── Metadata search / resolve (Python meta_fetcher + get_or_create_metadata parity) ─

/// Stable synthetic meta id when the user did not pick an external id (Python `http_{hash}` style).
pub fn synthetic_import_meta_id(prefix: &str, seed: &str) -> String {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    seed.hash(&mut hasher);
    format!("{prefix}_{}", hasher.finish() % 100_000)
}

/// Look up existing media for analyze `meta_match` (supports `tmdb:123`, `tt…`, etc.).
pub async fn lookup_import_media_id(
    pool: &PgPool,
    meta_id: &str,
    meta_type: &str,
) -> Option<crate::db::MediaId> {
    crate::db::get_media_id_by_external_id(pool, meta_id, Some(meta_type))
        .await
        .ok()
        .flatten()
}

/// DB lookup by external id, then title/year fallbacks (torrent analyze `meta_match`).
pub async fn lookup_import_media_id_with_fallback(
    pool: &PgPool,
    meta_id: &str,
    meta_type: &str,
    parsed_title: &str,
    parsed_year: Option<i32>,
) -> Option<crate::db::MediaId> {
    if let Some(id) = lookup_import_media_id(pool, meta_id, meta_type).await {
        return Some(id);
    }

    let media_type = MediaType::from_wire(meta_type)?;
    if let Some(year) = parsed_year {
        let row: Option<(i32,)> = sqlx::query_as(
            "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND year = $2 AND type = $3 LIMIT 1",
        )
        .bind(parsed_title)
        .bind(year)
        .bind(media_type)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);
        if let Some((id,)) = row {
            return Some(crate::db::MediaId(id));
        }
    }

    let pattern = format!("%{parsed_title}%");
    let row: Option<(i32,)> = sqlx::query_as(
        "SELECT id FROM media WHERE LOWER(title) LIKE LOWER($1) AND type = $2 LIMIT 1",
    )
    .bind(&pattern)
    .bind(media_type)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    row.map(|(id,)| crate::db::MediaId(id))
}

/// External metadata search for import analyze UIs (Python `search_multiple_results`).
pub async fn search_analyze_matches(
    state: &AppState,
    title: &str,
    year: Option<i32>,
    meta_type: &str,
) -> Vec<serde_json::Value> {
    crate::scrapers::metadata::search_import_matches(
        &state.http,
        &state.pool,
        title,
        year,
        meta_type,
        state.config.tmdb_api_key.as_deref(),
        state.config.tvdb_api_key.as_deref(),
        state.config.imdb_cinemeta_fallback_enabled,
    )
    .await
}

/// Fetch/create media for import submission (Python `fetch_and_create_media_from_external`).
pub async fn resolve_media_for_import(
    pool: &PgPool,
    http: &reqwest::Client,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    meta_id: &str,
    meta_type: &str,
    overrides: crate::scrapers::media_resolve::ImportMediaOverrides<'_>,
    prefetch: Option<&crate::scrapers::media_resolve::ImportMetadataCache>,
) -> Option<i32> {
    crate::scrapers::media_resolve::ensure_media_for_import(
        pool,
        http,
        meta_id,
        meta_type,
        tmdb_api_key,
        tvdb_api_key,
        overrides,
        prefetch,
    )
    .await
}

/// Compact media linkage info for an existing torrent stream (Python `_get_stream_media_attachment_details`).
pub async fn stream_media_attachment_details(
    pool: &PgPool,
    stream_id: i32,
    max_items: usize,
) -> serde_json::Value {
    let rows: Vec<(i32, String, Option<i32>, crate::db::MediaType)> = sqlx::query_as(
        r#"SELECT m.id, m.title, m.year, m.type
           FROM stream_media_link sml
           JOIN media m ON m.id = sml.media_id
           WHERE sml.stream_id = $1
           ORDER BY sml.is_primary DESC, sml.id ASC
           LIMIT $2"#,
    )
    .bind(stream_id)
    .bind(max_items as i64)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    let total: i64 =
        sqlx::query_scalar("SELECT COUNT(*)::bigint FROM stream_media_link WHERE stream_id = $1")
            .bind(stream_id)
            .fetch_one(pool)
            .await
            .unwrap_or(0);

    let mut items = Vec::new();
    for (media_id, title, year, media_type) in rows {
        let external_id: Option<String> = sqlx::query_scalar(
            r#"SELECT external_id FROM media_external_id
               WHERE media_id = $1
               ORDER BY CASE provider WHEN 'imdb' THEN 0 WHEN 'tmdb' THEN 1 ELSE 2 END
               LIMIT 1"#,
        )
        .bind(media_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);

        items.push(json!({
            "media_id": media_id,
            "external_id": external_id.unwrap_or_else(|| format!("mf:{media_id}")),
            "title": title,
            "year": year,
            "type": media_type.as_wire(),
        }));
    }

    json!({
        "count": total,
        "items": items,
    })
}

/// User-facing duplicate torrent message (Python `_build_existing_torrent_warning_message`).
pub fn build_existing_torrent_warning_message(
    info_hash: &str,
    attachment_details: &serde_json::Value,
) -> String {
    let items = attachment_details
        .get("items")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let linked_count = attachment_details
        .get("count")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);

    let mut message = "⚠️ Upload skipped: this torrent already exists in MediaFusion.".to_string();
    if let Some(first) = items.first() {
        let title = first
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown title");
        let year = first.get("year").and_then(|v| v.as_i64());
        let media_type = first
            .get("type")
            .and_then(|v| v.as_str())
            .unwrap_or("media");
        let external_id = first
            .get("external_id")
            .and_then(|v| v.as_str().map(str::to_string))
            .or_else(|| {
                first
                    .get("media_id")
                    .and_then(|v| v.as_i64())
                    .map(|id| format!("mf:{id}"))
            })
            .unwrap_or_else(|| "unknown".to_string());
        let year_suffix = year.map(|y| format!(" ({y})")).unwrap_or_default();
        let extra_suffix = if linked_count > 1 {
            format!(" and {} more linked media item(s)", linked_count - 1)
        } else {
            String::new()
        };
        message.push_str(&format!(
            " Already attached to {title}{year_suffix} [{media_type}, {external_id}]{extra_suffix}."
        ));
    } else {
        message.push_str(" Existing stream linkage metadata could not be resolved.");
    }
    message.push_str(&format!(
        " Thank you for trying to contribute ✨. If you cannot find it, contact support with this info hash ({info_hash})."
    ));
    message
}

/// Prefetch provider metadata for torrent import (primary + per-file ids).
pub async fn prefetch_torrent_import_metadata(
    http: &reqwest::Client,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    meta_type: &str,
    meta_id: &str,
    primary_title: &str,
    default_sports_category: Option<&str>,
    file_rows: &[serde_json::Value],
) -> crate::scrapers::media_resolve::ImportMetadataCache {
    crate::scrapers::media_resolve::prefetch_import_metadata(
        http,
        tmdb_api_key,
        tvdb_api_key,
        meta_id,
        meta_type,
        primary_title,
        default_sports_category,
        file_rows,
    )
    .await
}

/// Link stream ↔ media and bump `total_streams` when the link is new.
pub async fn link_stream_to_media(
    pool: &PgPool,
    stream_id: i32,
    media_id: crate::db::MediaId,
) -> Result<(), sqlx::Error> {
    crate::scrapers::media_resolve::link_stream_to_media(
        pool,
        crate::db::StreamId(stream_id),
        media_id,
    )
    .await
}

fn import_meta_id_candidates<'a>(
    request_meta_id: Option<&'a str>,
    stream_source: Option<&'a str>,
) -> Vec<&'a str> {
    let mut out = Vec::new();
    for candidate in [request_meta_id, stream_source] {
        let Some(id) = candidate.map(str::trim).filter(|s| !s.is_empty()) else {
            continue;
        };
        if id == "manual" || out.contains(&id) {
            continue;
        }
        out.push(id);
    }
    out
}

/// Attach a torrent stream that has no `stream_media_link` rows (duplicate import / prior failed link).
pub async fn try_link_orphan_torrent_stream(
    pool: &PgPool,
    http: &reqwest::Client,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    stream_id: i32,
    request_meta_id: Option<&str>,
    meta_type: &str,
    overrides: crate::scrapers::media_resolve::ImportMediaOverrides<'_>,
    prefetch: Option<&crate::scrapers::media_resolve::ImportMetadataCache>,
) -> Option<i32> {
    let stream_source: Option<String> =
        sqlx::query_scalar("SELECT NULLIF(TRIM(source), '') FROM stream WHERE id = $1")
            .bind(stream_id)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten();

    for meta_id in import_meta_id_candidates(request_meta_id, stream_source.as_deref()) {
        if let Some(media_id) = lookup_import_media_id(pool, meta_id, meta_type).await {
            match link_stream_to_media(pool, stream_id, media_id).await {
                Ok(()) => return Some(media_id.0),
                Err(e) => tracing::warn!(
                    "try_link_orphan_torrent_stream: link failed stream={stream_id} media={media_id} meta_id={meta_id}: {e}"
                ),
            }
        }

        if let Ok(Some(media_id)) =
            crate::db::get_media_id_by_external_id(pool, meta_id, None).await
        {
            match link_stream_to_media(pool, stream_id, media_id).await {
                Ok(()) => return Some(media_id.0),
                Err(e) => tracing::warn!(
                    "try_link_orphan_torrent_stream: link failed stream={stream_id} media={media_id} (no type filter) meta_id={meta_id}: {e}"
                ),
            }
        }

        if let Some(media_id) = resolve_media_for_import(
            pool,
            http,
            tmdb_api_key,
            tvdb_api_key,
            meta_id,
            meta_type,
            crate::scrapers::media_resolve::ImportMediaOverrides {
                title: overrides.title,
                poster: overrides.poster,
                background: overrides.background,
                release_date: overrides.release_date,
                year: overrides.year,
            },
            prefetch,
        )
        .await
        {
            match link_stream_to_media(pool, stream_id, crate::db::MediaId(media_id)).await {
                Ok(()) => return Some(media_id),
                Err(e) => tracing::warn!(
                    "try_link_orphan_torrent_stream: link failed stream={stream_id} media={media_id} (resolved) meta_id={meta_id}: {e}"
                ),
            }
        }
    }

    None
}

/// Extract a deduplicated list of non-empty strings from a contribution JSON field.
pub fn contribution_string_list(data: &serde_json::Value, key: &str) -> Vec<String> {
    let mut out = Vec::new();
    let Some(arr) = data.get(key).and_then(|v| v.as_array()) else {
        return out;
    };
    for item in arr {
        if let Some(s) = item.as_str() {
            let t = s.trim();
            if !t.is_empty() && !out.iter().any(|x| x == t) {
                out.push(t.to_string());
            }
        }
    }
    out
}

/// Link audio format names to a stream.
pub async fn link_stream_audio_formats(
    pool: &PgPool,
    stream_id: i32,
    formats: &[String],
) -> Result<(), sqlx::Error> {
    crate::db::link_stream_audio_formats(pool, stream_id, formats).await
}

/// Link HDR format names to a stream.
pub async fn link_stream_hdr_formats(
    pool: &PgPool,
    stream_id: i32,
    formats: &[String],
) -> Result<(), sqlx::Error> {
    crate::db::link_stream_hdr_formats(pool, stream_id, formats).await
}

/// Link audio channel names to a stream.
pub async fn link_stream_audio_channels(
    pool: &PgPool,
    stream_id: i32,
    channels: &[String],
) -> Result<(), sqlx::Error> {
    crate::db::link_stream_audio_channels(pool, stream_id, channels).await
}

/// Link audio languages to a stream (Python `StreamLanguageLink`).
pub async fn link_stream_languages(
    pool: &PgPool,
    stream_id: i32,
    languages: &[String],
) -> Result<(), sqlx::Error> {
    crate::db::link_stream_languages(pool, stream_id, languages).await
}

/// Link announce trackers to a torrent (`stream.id`).
pub async fn link_torrent_trackers(
    pool: &PgPool,
    stream_id: i32,
    tracker_urls: &[String],
) -> Result<(), sqlx::Error> {
    crate::db::link_torrent_trackers_for_stream(pool, crate::db::StreamId(stream_id), tracker_urls)
        .await
}

/// Attach catalog names to media (Python import catalog linking).
pub async fn link_media_catalogs(
    pool: &PgPool,
    media_id: i32,
    catalogs: &[String],
) -> Result<(), sqlx::Error> {
    for cat_name in catalogs {
        if cat_name.is_empty() {
            continue;
        }
        // `is_system` and `display_order` are NOT NULL with no default, so they
        // must be supplied when creating a new catalog.
        let cat_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO catalog(name, is_system, display_order) VALUES($1, false, 0) \
             ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        )
        .bind(cat_name)
        .fetch_optional(pool)
        .await?;
        if let Some(cid) = cat_id {
            sqlx::query(
                "INSERT INTO media_catalog_link(media_id, catalog_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
            )
            .bind(media_id)
            .bind(cid)
            .execute(pool)
            .await?;
        }
    }
    Ok(())
}

/// Apply fetched provider metadata to an existing media row via the storage funnel.
pub async fn apply_fetched_metadata_to_media(
    pool: &PgPool,
    media_id: i32,
    meta: &crate::db::NormalizedMetadata,
) {
    let _ = crate::db::store_media(
        pool,
        meta,
        crate::db::StoreMediaOpts::refresh(crate::db::MediaId(media_id)),
    )
    .await;
}

/// Insert per-file rows and link each file to resolved media (Python `process_torrent_import` file loop).
pub async fn insert_torrent_import_files(
    pool: &PgPool,
    http: &reqwest::Client,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    stream_id: i32,
    default_meta_type: &str,
    primary_media_id: Option<i32>,
    files: &[Value],
    default_sports_category: Option<&str>,
    prefetch: &crate::scrapers::media_resolve::ImportMetadataCache,
) -> Result<(), String> {
    for (idx, file_info) in files.iter().enumerate() {
        let file_index = file_info
            .get("index")
            .and_then(|v| v.as_i64())
            .unwrap_or(idx as i64) as i32;
        let filename = file_info
            .get("filename")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if is_adult_content(filename) {
            return Err("Adult content is not allowed.".to_string());
        }
        let size = file_info.get("size").and_then(|v| v.as_i64()).unwrap_or(0);

        let file_row = crate::db::StreamFileStoreInput {
            file_index,
            filename: filename.to_string(),
            size: Some(size),
            season_number: 0,
            episode_number: 0,
        };
        let file_id =
            crate::db::upsert_stream_file_row(pool, crate::db::StreamId(stream_id), &file_row)
                .await
                .map_err(|e| e.to_string())?;

        let Some(file_id) = file_id else {
            continue;
        };

        let file_meta_id = file_info.get("meta_id").and_then(|v| v.as_str());
        let file_meta_type_owned = {
            let raw = file_info
                .get("meta_type")
                .and_then(|v| v.as_str())
                .unwrap_or(default_meta_type);
            if raw == "sports" || default_meta_type == "sports" {
                let cat = file_info
                    .get("sports_category")
                    .and_then(|v| v.as_str())
                    .or_else(|| detect_sports_category(filename))
                    .or(default_sports_category)
                    .unwrap_or("other_sports");
                crate::scrapers::media_resolve::resolve_file_fetch_meta_type("sports", Some(cat))
                    .to_string()
            } else {
                raw.to_string()
            }
        };
        let file_meta_type = file_meta_type_owned.as_str();
        let file_title = file_info
            .get("meta_title")
            .or_else(|| file_info.get("title"))
            .or_else(|| file_info.get("episode_title"))
            .and_then(|v| v.as_str())
            .unwrap_or(filename);

        let target_media = if let Some(mid) = file_meta_id.filter(|s| !s.is_empty()) {
            let effective = crate::scrapers::media_resolve::normalize_contributor_meta_id(mid);
            resolve_media_for_import(
                pool,
                http,
                tmdb_api_key,
                tvdb_api_key,
                &effective,
                file_meta_type,
                crate::scrapers::media_resolve::ImportMediaOverrides {
                    title: Some(file_title),
                    poster: None,
                    background: None,
                    release_date: None,
                    year: file_info
                        .get("year")
                        .and_then(|v| v.as_i64())
                        .map(|y| y as i32),
                },
                Some(prefetch),
            )
            .await
        } else {
            primary_media_id
        };

        let Some(target_media) = target_media else {
            continue;
        };

        let season = file_info
            .get("season_number")
            .and_then(|v| v.as_i64())
            .map(|n| n as i32);
        let episode = file_info
            .get("episode_number")
            .and_then(|v| v.as_i64())
            .map(|n| n as i32);

        let (s, e) = if default_meta_type == "series" {
            crate::db::resolve_series_episode_numbers(file_index, season, episode)
        } else if let (Some(s), Some(e)) = (season, episode) {
            (s, e)
        } else {
            continue;
        };

        crate::db::link_file_to_media_episode(
            pool,
            file_id,
            crate::db::MediaId(target_media),
            s,
            e,
            crate::db::LinkSource::User,
            true,
        )
        .await
        .map_err(|e| e.to_string())?;

        if target_media != primary_media_id.unwrap_or(-1) {
            let _ = link_stream_to_media(pool, stream_id, crate::db::MediaId(target_media)).await;
        }
    }
    Ok(())
}

/// Extract an ISO `YYYY-MM-DD` date from a torrent/file name. Supports
/// `DD.MM.YYYY`, `YYYY.MM.DD` and `.`/`-`/`_`/space separators.
pub fn extract_iso_date(text: &str) -> Option<String> {
    static DMY: OnceLock<regex::Regex> = OnceLock::new();
    static YMD: OnceLock<regex::Regex> = OnceLock::new();
    let dmy = DMY.get_or_init(|| {
        regex::Regex::new(r"\b(\d{1,2})[._\- ](\d{1,2})[._\- ]((?:19|20)\d{2})\b").unwrap()
    });
    let ymd = YMD.get_or_init(|| {
        regex::Regex::new(r"\b((?:19|20)\d{2})[._\- ](\d{1,2})[._\- ](\d{1,2})\b").unwrap()
    });

    let valid = |y: i32, m: u32, d: u32| -> Option<String> {
        if (1..=12).contains(&m) && (1..=31).contains(&d) {
            Some(format!("{y:04}-{m:02}-{d:02}"))
        } else {
            None
        }
    };

    if let Some(c) = ymd.captures(text) {
        let y: i32 = c[1].parse().ok()?;
        let m: u32 = c[2].parse().ok()?;
        let d: u32 = c[3].parse().ok()?;
        if let Some(s) = valid(y, m, d) {
            return Some(s);
        }
    }
    if let Some(c) = dmy.captures(text) {
        let d: u32 = c[1].parse().ok()?;
        let m: u32 = c[2].parse().ok()?;
        let y: i32 = c[3].parse().ok()?;
        if let Some(s) = valid(y, m, d) {
            return Some(s);
        }
    }
    None
}

/// Organize episode metadata for a *user-created* (non-IMDb/TMDb/TVDb) series whose
/// files lack explicit episode numbers. No-op for non-series media, for media that
/// already has external IDs (provider supplies episodes), and for files that
/// already carry an `episode_number`.
///
/// Strategy (per the import requirements):
///   1. Racing sessions (F1/MotoGP) → canonical fixed slots (FP1, FP2/SprintQ, …).
///   2. Files with a detectable date → ordered chronologically; dates already
///      present in the series reuse their episode number (stable re-imports).
///   3. Remaining files → ordered by filename, numbered after the current max.
///
/// Mutates `file_rows` in place, filling `season_number` / `episode_number` /
/// `episode_title` / `release_date`.
pub async fn organize_user_series_episodes(
    pool: &PgPool,
    media_id: i64,
    file_rows: &mut [Value],
    sports_category: Option<&str>,
) {
    // Only for series-type media without external IDs (i.e. user-created).
    let is_user_series: bool = sqlx::query_scalar(
        "SELECT m.type = $2 \
              AND NOT EXISTS (SELECT 1 FROM media_external_id e WHERE e.media_id = m.id) \
         FROM media m WHERE m.id = $1",
    )
    .bind(media_id as i32)
    .bind(MediaType::Series)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
    .unwrap_or(false);
    if !is_user_series {
        return;
    }

    let is_racing = matches!(
        sports_category,
        Some("formula_racing") | Some("motogp_racing")
    );

    // Existing episodes in season 1 → align re-imports (date → number, and current max).
    let existing: Vec<(i32, Option<chrono::NaiveDate>)> = sqlx::query_as(
        "SELECT e.episode_number, e.air_date FROM episode e \
         JOIN season s ON e.season_id = s.id \
         JOIN series_metadata sm ON s.series_id = sm.id \
         WHERE sm.media_id = $1 AND s.season_number = 1",
    )
    .bind(media_id as i32)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    let mut date_to_num: std::collections::HashMap<String, i32> = std::collections::HashMap::new();
    let mut max_num = 0i32;
    for (num, air) in &existing {
        max_num = max_num.max(*num);
        if let Some(d) = air {
            date_to_num.insert(d.to_string(), *num);
        }
    }

    // Pass 1: racing slots + collect undated/dated leftovers (preserving original index).
    let mut dated: Vec<(usize, String)> = Vec::new();
    let mut undated: Vec<usize> = Vec::new();
    for (idx, f) in file_rows.iter_mut().enumerate() {
        if let Some(obj) = f.as_object_mut() {
            obj.entry("season_number").or_insert(json!(1));
            if obj
                .get("season_number")
                .map(|v| v.is_null())
                .unwrap_or(false)
            {
                obj.insert("season_number".to_string(), json!(1));
            }
        }
        // Respect an explicit episode number.
        if f.get("episode_number").and_then(|v| v.as_i64()).is_some() {
            continue;
        }
        let filename = f
            .get("filename")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if is_racing {
            if let Some((ep, title)) = crate::parser::racing_session_episode(&filename) {
                if let Some(obj) = f.as_object_mut() {
                    obj.insert("episode_number".to_string(), json!(ep));
                    obj.entry("episode_title").or_insert(json!(title));
                    if obj
                        .get("episode_title")
                        .map(|v| v.is_null())
                        .unwrap_or(false)
                    {
                        obj.insert("episode_title".to_string(), json!(title));
                    }
                }
                continue;
            }
        }

        // Date-based organization.
        let existing_date = f
            .get("release_date")
            .and_then(|v| v.as_str())
            .map(str::to_string);
        if let Some(date) = existing_date.or_else(|| extract_iso_date(&filename)) {
            dated.push((idx, date));
        } else {
            undated.push(idx);
        }
    }

    // Pass 2: dated files in chronological order.
    dated.sort_by(|a, b| a.1.cmp(&b.1));
    for (idx, date) in dated {
        let number = match date_to_num.get(&date) {
            Some(n) => *n,
            None => {
                max_num += 1;
                date_to_num.insert(date.clone(), max_num);
                max_num
            }
        };
        if let Some(obj) = file_rows[idx].as_object_mut() {
            obj.insert("episode_number".to_string(), json!(number));
            obj.insert("release_date".to_string(), json!(date));
            obj.entry("episode_title").or_insert(json!(null));
        }
    }

    // Pass 3: remaining files by filename order.
    undated.sort_by(|&a, &b| {
        let fa = file_rows[a]
            .get("filename")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let fb = file_rows[b]
            .get("filename")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        fa.cmp(fb)
    });
    for idx in undated {
        max_num += 1;
        if let Some(obj) = file_rows[idx].as_object_mut() {
            obj.insert("episode_number".to_string(), json!(max_num));
        }
    }
}

/// Insert a `stream` + `torrent_stream` row and link to `media_id` when provided.
#[allow(clippy::too_many_arguments)]
pub async fn insert_torrent_stream_row(
    pool: &PgPool,
    info_hash: &str,
    name: &str,
    source: &str,
    size: Option<i64>,
    seeders: Option<i32>,
    file_count: i32,
    parsed: &crate::parser::ParsedTitle,
    media_id: Option<i64>,
    is_public: bool,
    torrent_type: TorrentType,
    torrent_file: Option<&[u8]>,
) -> Result<i32, sqlx::Error> {
    let _ = file_count;
    let mut base =
        crate::db::StreamStoreBase::from_parsed(name.to_string(), source.to_string(), parsed);
    base.is_public = is_public;

    let stream = crate::db::TorrentStoreInput {
        base,
        info_hash: info_hash.to_string(),
        total_size: size.unwrap_or(0),
        seeders,
        torrent_type,
        torrent_file: torrent_file.map(|b| b.to_vec()),
        announce_list: vec![],
        files: vec![],
    };

    let opts = if let Some(mid) = media_id {
        crate::db::StoreStreamOpts::user_import(crate::db::MediaId(mid as i32), MediaType::Movie)
    } else {
        crate::db::StoreStreamOpts {
            media_id: crate::db::MediaId(0),
            media_type: MediaType::Movie,
            season: None,
            episode: None,
            link_source: crate::db::LinkSource::User,
            is_primary: true,
            is_verified: false,
        }
    };

    let result = crate::db::store_torrent_stream(pool, &stream, &opts).await?;
    Ok(result.stream_id().0)
}

/// Populate `series_metadata` / `season` / `episode` rows so series detail pages
/// list the imported episodes. No-op for non-series media. Mirrors the Python
/// `_ensure_series_episode_metadata`.
pub async fn ensure_series_episode_metadata(
    pool: &PgPool,
    media_id: i64,
    file_rows: &[Value],
    fallback_title: &str,
) {
    let is_series: bool = sqlx::query_scalar("SELECT type = $2 FROM media WHERE id = $1")
        .bind(media_id as i32)
        .bind(MediaType::Series)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
        .unwrap_or(false);
    if !is_series {
        return;
    }

    // NOTE: series_metadata.id / season.id / episode.id are `integer` (i32) — decoding
    // them as i64 fails at runtime, so these must be i32.
    let series_id: Option<i32> = match sqlx::query_scalar(
        "INSERT INTO series_metadata (media_id, total_seasons, total_episodes, created_at) \
         VALUES ($1, 0, 0, NOW()) ON CONFLICT (media_id) DO UPDATE SET media_id = EXCLUDED.media_id \
         RETURNING id",
    )
    .bind(media_id as i32)
    .fetch_optional(pool)
    .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("ensure_series_episode_metadata series_metadata media_id={media_id}: {e}");
            None
        }
    };
    let Some(series_id) = series_id else {
        return;
    };

    let mut touched_seasons: std::collections::HashSet<i32> = std::collections::HashSet::new();
    for (idx, f) in file_rows.iter().enumerate() {
        let season_number = f
            .get("season_number")
            .and_then(|v| v.as_i64())
            .map(|n| n as i32)
            .unwrap_or(1);
        let episode_number = f
            .get("episode_number")
            .and_then(|v| v.as_i64())
            .map(|n| n as i32)
            .unwrap_or((idx as i32) + 1);
        let episode_title = f
            .get("episode_title")
            .and_then(|v| v.as_str())
            .filter(|s| !s.trim().is_empty())
            .map(str::to_string)
            .or_else(|| {
                f.get("filename")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
            })
            .unwrap_or_else(|| format!("Episode {episode_number}"));
        let air_date = f
            .get("release_date")
            .and_then(|v| v.as_str())
            .and_then(|s| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok());

        let season_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO season (series_id, season_number, name, episode_count) \
             VALUES ($1, $2, $3, 0) \
             ON CONFLICT (series_id, season_number) DO UPDATE SET series_id = EXCLUDED.series_id \
             RETURNING id",
        )
        .bind(series_id)
        .bind(season_number)
        .bind(format!("Season {season_number}"))
        .fetch_optional(pool)
        .await
        .unwrap_or(None);
        let Some(season_id) = season_id else {
            continue;
        };
        touched_seasons.insert(season_number);

        if let Err(e) = sqlx::query(
            "INSERT INTO episode \
               (season_id, episode_number, title, air_date, is_user_created, is_user_addition, created_at, updated_at) \
             VALUES ($1, $2, $3, $4, true, true, NOW(), NOW()) \
             ON CONFLICT (season_id, episode_number) \
             DO UPDATE SET title = EXCLUDED.title, \
                           air_date = COALESCE(EXCLUDED.air_date, episode.air_date), \
                           updated_at = NOW()",
        )
        .bind(season_id)
        .bind(episode_number)
        .bind(&episode_title)
        .bind(air_date)
        .execute(pool)
        .await
        {
            tracing::warn!("ensure_series_episode_metadata episode s{season_number}e{episode_number}: {e}");
        }
    }

    if touched_seasons.is_empty() {
        let season_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO season (series_id, season_number, name, episode_count) \
             VALUES ($1, 1, 'Season 1', 0) \
             ON CONFLICT (series_id, season_number) DO UPDATE SET series_id = EXCLUDED.series_id \
             RETURNING id",
        )
        .bind(series_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);
        if let Some(season_id) = season_id {
            let _ = sqlx::query(
                "INSERT INTO episode \
                   (season_id, episode_number, title, is_user_created, is_user_addition, created_at, updated_at) \
                 VALUES ($1, 1, $2, true, true, NOW(), NOW()) \
                 ON CONFLICT (season_id, episode_number) DO NOTHING",
            )
            .bind(season_id)
            .bind(fallback_title)
            .execute(pool)
            .await;
            touched_seasons.insert(1);
        }
    }

    for season_number in &touched_seasons {
        let _ = sqlx::query(
            "UPDATE season SET episode_count = \
               (SELECT COUNT(*) FROM episode e WHERE e.season_id = season.id) \
             WHERE series_id = $1 AND season_number = $2",
        )
        .bind(series_id)
        .bind(season_number)
        .execute(pool)
        .await;
    }
    let _ = sqlx::query(
        "UPDATE series_metadata SET \
           total_seasons = (SELECT COUNT(*) FROM season WHERE series_id = $1), \
           total_episodes = (SELECT COUNT(*) FROM episode e JOIN season s ON e.season_id = s.id WHERE s.series_id = $1), \
           updated_at = NOW() \
         WHERE id = $1",
    )
    .bind(series_id)
    .execute(pool)
    .await;
}

/// Inputs for [`persist_torrent_import`] — the single shared routine that writes a
/// torrent stream and every associated link.
pub struct TorrentImportPersist<'a> {
    pub info_hash: &'a str,
    pub name: &'a str,
    pub source: &'a str,
    pub total_size: Option<i64>,
    pub seeders: Option<i32>,
    pub file_count: i32,
    pub parsed: &'a crate::parser::ParsedTitle,
    pub media_id: Option<i64>,
    pub meta_type: &'a str,
    pub is_public: bool,
    /// JSON file rows (index/filename/size/season_number/episode_number/episode_title/…).
    pub file_rows: &'a [Value],
    pub languages: &'a [String],
    pub catalogs: &'a [String],
    pub trackers: &'a [String],
    pub sports_category: Option<&'a str>,
    /// Title used for placeholder episode names when files carry none.
    pub fallback_title: &'a str,
    pub tmdb_api_key: Option<&'a str>,
    pub tvdb_api_key: Option<&'a str>,
    pub prefetch: &'a crate::scrapers::media_resolve::ImportMetadataCache,
    pub torrent_type: TorrentType,
    pub torrent_file: Option<&'a [u8]>,
}

/// Persist a torrent stream and all of its links in one place: the stream +
/// torrent_stream rows, media link, trackers, languages, audio/HDR/channel
/// extras, per-file metadata (with season/episode links), catalogs, and series
/// season/episode metadata. Returns the `stream.id`.
pub async fn persist_torrent_import(
    pool: &PgPool,
    http: &reqwest::Client,
    input: TorrentImportPersist<'_>,
) -> Result<i32, sqlx::Error> {
    let stream_id = insert_torrent_stream_row(
        pool,
        input.info_hash,
        input.name,
        input.source,
        input.total_size,
        input.seeders,
        input.file_count,
        input.parsed,
        input.media_id,
        input.is_public,
        input.torrent_type,
        input.torrent_file,
    )
    .await?;

    // Trackers (resolve torrent_stream.id from the stream).
    if !input.trackers.is_empty() {
        let _ =
            crate::db::link_torrent_trackers(pool, crate::db::StreamId(stream_id), input.trackers)
                .await;
    }

    // Languages + audio/HDR/channel extras.
    if !input.languages.is_empty() {
        let _ = link_stream_languages(pool, stream_id, input.languages).await;
    }
    if !input.parsed.audio.is_empty() {
        let _ = link_stream_audio_formats(pool, stream_id, &input.parsed.audio).await;
    }
    if !input.parsed.hdr.is_empty() {
        let _ = link_stream_hdr_formats(pool, stream_id, &input.parsed.hdr).await;
    }
    if !input.parsed.channels.is_empty() {
        let _ = link_stream_audio_channels(pool, stream_id, &input.parsed.channels).await;
    }

    // Organize episodes for user-created series (date/session ordering) before
    // any file/episode links are written, so both use the same numbers.
    let mut file_rows: Vec<Value> = input.file_rows.to_vec();
    if let Some(mid) = input.media_id {
        organize_user_series_episodes(pool, mid, &mut file_rows, input.sports_category).await;
    }

    // Per-file metadata (creates stream_file + file_media_link with season/episode).
    if !file_rows.is_empty() {
        let _ = insert_torrent_import_files(
            pool,
            http,
            input.tmdb_api_key,
            input.tvdb_api_key,
            stream_id,
            input.meta_type,
            input.media_id.map(|m| m as i32),
            &file_rows,
            input.sports_category,
            input.prefetch,
        )
        .await;
    }

    // Catalogs + series episode metadata for the primary media.
    if let Some(mid) = input.media_id {
        if !input.catalogs.is_empty() {
            let _ = link_media_catalogs(pool, mid as i32, input.catalogs).await;
        }
        ensure_series_episode_metadata(pool, mid, &file_rows, input.fallback_title).await;
    }

    Ok(stream_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_approve_requires_active_non_anonymous_or_privileged() {
        assert!(should_auto_approve_import(true, false, true));
        assert!(should_auto_approve_import(false, true, false));
        assert!(!should_auto_approve_import(false, false, false));
        assert!(!should_auto_approve_import(false, true, true));
        assert!(!should_auto_approve_import(false, false, true));
    }

    #[test]
    fn point_eligible_types_match_python() {
        assert!(POINT_ELIGIBLE_IMPORT_TYPES.contains(&"torrent"));
        assert!(!POINT_ELIGIBLE_IMPORT_TYPES.contains(&"metadata"));
    }
}
