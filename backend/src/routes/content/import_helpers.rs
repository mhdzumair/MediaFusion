/// Shared helpers for all content import endpoints.
///
/// Used by: torrent_import, nzb_import, http_import, youtube_import, acestream_import.
use std::sync::OnceLock;

use axum::http::StatusCode;
use chrono::Utc;
use fred::prelude::KeysInterface;
use serde_json::json;
use uuid::Uuid;

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
}

pub async fn fetch_user_info(pool: &sqlx::PgPool, user_id: i64) -> Option<UserInfo> {
    let row: Option<(String, bool, String, bool)> = sqlx::query_as(
        "SELECT COALESCE(username, 'user'), uploads_restricted, LOWER(role::text), contribute_anonymously FROM users WHERE id = $1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    row.map(
        |(username, uploads_restricted, role, contribute_anonymously)| UserInfo {
            username,
            uploads_restricted,
            role,
            contribute_anonymously,
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
    let status = if auto_approve { "approved" } else { "pending" };
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

pub async fn award_contribution_points(pool: &sqlx::PgPool, user_id: i64) {
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
