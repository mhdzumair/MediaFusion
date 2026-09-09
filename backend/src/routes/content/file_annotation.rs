use axum::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::PgPool;

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct FileAnnotationUpdate {
    pub file_id: i32,
    #[serde(default)]
    pub clear: bool,
    pub season_number: Option<i32>,
    pub episode_number: Option<i32>,
    #[serde(default)]
    pub episode_end: Option<i32>,
}

#[derive(Debug, Deserialize)]
pub struct BulkFileAnnotationRequest {
    pub stream_id: i32,
    pub media_id: Option<i32>,
    pub updates: Vec<FileAnnotationUpdate>,
    pub reason: Option<String>,
}

pub struct FileAnnotationApplyResult {
    pub updated: i64,
    pub failed: i64,
    pub errors: Vec<String>,
}

/// Resolve the series media row to annotate against.
pub async fn resolve_annotation_media_id(
    pool: &PgPool,
    stream_id: i32,
    media_id: Option<i32>,
) -> Result<i32, Box<Response>> {
    if let Some(media_id) = media_id {
        let media_type: Option<crate::db::MediaType> =
            sqlx::query_scalar("SELECT type FROM media WHERE id = $1")
                .bind(media_id)
                .fetch_optional(pool)
                .await
                .unwrap_or(None);

        return match media_type {
            None => Err(Box::new((
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Media not found"})),
            )
                .into_response())),
            Some(crate::db::MediaType::Series) => Ok(media_id),
            Some(_) => Err(Box::new((
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "File annotation updates are only supported for series media"})),
            )
                .into_response())),
        };
    }

    let resolved: Option<i32> = sqlx::query_scalar(
        r#"
        SELECT sml.media_id
        FROM stream_media_link sml
        JOIN media m ON m.id = sml.media_id
        WHERE sml.stream_id = $1
          AND m.type = 'series'
        ORDER BY sml.is_primary DESC NULLS LAST, sml.id ASC
        LIMIT 1
        "#,
    )
    .bind(stream_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    resolved.ok_or_else(|| {
        Box::new((
            StatusCode::BAD_REQUEST,
            Json(
                json!({"detail": "media_id is required when the stream has no series media link"}),
            ),
        )
            .into_response())
    })
}

/// Apply season/episode annotation changes for a stream/media pair.
pub async fn apply_file_annotation_updates(
    pool: &PgPool,
    stream_id: i32,
    media_id: i32,
    updates: &[FileAnnotationUpdate],
) -> FileAnnotationApplyResult {
    let mut updated = 0i64;
    let mut failed = 0i64;
    let mut errors = Vec::new();

    for update in updates {
        let file_exists: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM stream_file WHERE id = $1 AND stream_id = $2)",
        )
        .bind(update.file_id)
        .bind(stream_id)
        .fetch_one(pool)
        .await
        .unwrap_or(false);

        if !file_exists {
            errors.push(format!("File {} not found in this stream", update.file_id));
            failed += 1;
            continue;
        }

        let result = if update.clear {
            sqlx::query(
                "UPDATE file_media_link \
                 SET season_number = NULL, episode_number = NULL, episode_end = NULL, updated_at = NOW() \
                 WHERE file_id = $1 AND media_id = $2",
            )
            .bind(update.file_id)
            .bind(media_id)
            .execute(pool)
            .await
        } else {
            let existing_link: Option<i32> = sqlx::query_scalar(
                "SELECT id FROM file_media_link WHERE file_id = $1 AND media_id = $2",
            )
            .bind(update.file_id)
            .bind(media_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

            if let Some(link_id) = existing_link {
                sqlx::query(
                    "UPDATE file_media_link \
                     SET season_number = $1, episode_number = $2, episode_end = $3, updated_at = NOW() \
                     WHERE id = $4",
                )
                .bind(update.season_number)
                .bind(update.episode_number)
                .bind(update.episode_end)
                .bind(link_id)
                .execute(pool)
                .await
            } else {
                sqlx::query(
                    "INSERT INTO file_media_link \
                     (file_id, media_id, season_number, episode_number, episode_end, \
                      created_at, is_primary, confidence, link_source) \
                     VALUES ($1, $2, $3, $4, $5, NOW(), true, 1.0, 'MANUAL')",
                )
                .bind(update.file_id)
                .bind(media_id)
                .bind(update.season_number)
                .bind(update.episode_number)
                .bind(update.episode_end)
                .execute(pool)
                .await
            }
        };

        match result {
            Ok(_) => updated += 1,
            Err(e) => {
                errors.push(format!("Failed to update file {}: {e}", update.file_id));
                failed += 1;
            }
        }
    }

    FileAnnotationApplyResult {
        updated,
        failed,
        errors,
    }
}
