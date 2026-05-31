use sqlx::PgPool;
use tracing::warn;

use super::types::{HistorySource, MediaId, MediaType, ProfileId, StreamType, UserId, WatchAction};

/// Fire-and-forget: insert a watch_history row if we have a user.
/// Silently ignores errors (best-effort tracking).
#[allow(clippy::too_many_arguments)]
pub async fn record_playback(
    pool: &PgPool,
    user_id: UserId,
    profile_id: ProfileId,
    media_id: MediaId,
    title: &str,
    media_type: MediaType,
    season: Option<i32>,
    episode: Option<i32>,
    stream_type: StreamType,
    provider_name: Option<&str>,
) {
    let stream_info = serde_json::json!({
        "stream_type": stream_type.as_wire().to_ascii_lowercase(),
        "provider": provider_name,
    });

    let result = sqlx::query(
        r#"
        INSERT INTO watch_history
            (user_id, profile_id, media_id, title, media_type, season, episode,
             action, source, stream_info, watched_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
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
    .bind(WatchAction::Watched)
    .bind(HistorySource::Mediafusion)
    .bind(stream_info)
    .execute(pool)
    .await;

    if let Err(e) = result {
        warn!("watch_history insert failed: {e}");
    }
}
