/// Run import processors when contributions are approved (Python `_apply_contribution_review`).
use serde_json::{json, Value};
use sqlx::PgPool;

use crate::{
    db::{StreamType, TorrentType},
    parser,
    scrapers::media_resolve::ImportMediaOverrides,
    state::AppState,
};

use super::import_helpers::{
    self, contribution_string_list, is_adult_content, link_media_catalogs,
    link_stream_audio_channels, link_stream_audio_formats, link_stream_hdr_formats,
    link_stream_languages, link_torrent_trackers, resolve_uploader_identity,
};

pub const PROCESSABLE_IMPORT_TYPES: &[&str] =
    &["torrent", "nzb", "youtube", "http", "acestream", "telegram"];

#[derive(Debug)]
pub struct ImportProcessResult {
    pub status: &'static str,
    pub stream_id: Option<i64>,
    pub message: Option<String>,
}

#[derive(Debug)]
pub enum ImportProcessError {
    AdultContent,
    MissingField(&'static str),
    Other(String),
}

impl ImportProcessError {
    pub fn message(&self) -> String {
        match self {
            Self::AdultContent => "Adult content is not allowed.".to_string(),
            Self::MissingField(f) => format!("Missing required field: {f}"),
            Self::Other(m) => m.clone(),
        }
    }
}

fn data_str<'a>(data: &'a Value, key: &str) -> Option<&'a str> {
    data.get(key)
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
}

fn contribution_titles_indicate_adult(data: &Value) -> bool {
    for key in ["name", "title", "torrent_name"] {
        if let Some(t) = data_str(data, key) {
            if is_adult_content(t) {
                return true;
            }
        }
    }
    if let Some(files) = data.get("file_data").and_then(|v| v.as_array()) {
        for file in files {
            if let Some(fname) = file.get("filename").and_then(|v| v.as_str()) {
                if is_adult_content(fname) {
                    return true;
                }
            }
        }
    }
    false
}

/// Process an approved contribution's import payload (`is_public` should be true).
pub async fn process_contribution_import(
    state: &AppState,
    contribution_type: &str,
    data: &mut Value,
    contributor_user_id: Option<i64>,
    contributor_username: &str,
) -> Result<ImportProcessResult, ImportProcessError> {
    data["is_public"] = json!(true);

    if contribution_titles_indicate_adult(data) {
        return Err(ImportProcessError::AdultContent);
    }

    match contribution_type {
        "torrent" => process_torrent(state, data, contributor_user_id, contributor_username).await,
        "nzb" => process_nzb(state, data, contributor_user_id, contributor_username).await,
        "youtube" => process_youtube(state, data, contributor_user_id, contributor_username).await,
        "http" => process_http(state, data, contributor_user_id, contributor_username).await,
        "acestream" => {
            process_acestream(state, data, contributor_user_id, contributor_username).await
        }
        "telegram" => process_telegram(state, data).await,
        other => Err(ImportProcessError::Other(format!(
            "Unsupported contribution type: {other}"
        ))),
    }
}

async fn process_torrent(
    state: &AppState,
    data: &Value,
    user_id: Option<i64>,
    username: &str,
) -> Result<ImportProcessResult, ImportProcessError> {
    let info_hash = data_str(data, "info_hash")
        .ok_or(ImportProcessError::MissingField("info_hash"))?
        .to_lowercase();

    let name = data_str(data, "name")
        .or_else(|| data_str(data, "title"))
        .unwrap_or("Unknown");
    let meta_type = data_str(data, "meta_type").unwrap_or("movie");
    let meta_id = data_str(data, "meta_id")
        .map(str::to_string)
        .unwrap_or_else(|| format!("user_{}", &info_hash[..8.min(info_hash.len())]));

    let is_anonymous = data
        .get("is_anonymous")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    if let Some(existing) = sqlx::query_scalar::<_, i32>(
        "SELECT stream_id FROM torrent_stream WHERE info_hash = $1 LIMIT 1",
    )
    .bind(&info_hash)
    .fetch_optional(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?
    {
        let want_public = data
            .get("is_public")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        if want_public {
            publish_stream(&state.pool, existing, true).await?;
            let mid = resolve_media(state, meta_id.as_str(), meta_type, name, data, None).await;
            if let Some(mid) = mid {
                let _ = import_helpers::link_stream_to_media(&state.pool, existing, crate::db::MediaId(mid)).await;
            }
            return Ok(ImportProcessResult {
                status: "success",
                stream_id: Some(existing as i64),
                message: Some("Existing torrent stream published".to_string()),
            });
        }
        return Ok(ImportProcessResult {
            status: "exists",
            stream_id: Some(existing as i64),
            message: Some("Torrent already exists in database".to_string()),
        });
    }

    let parsed = if meta_type == "sports" || parser::is_sports_title(name) {
        parser::parse_sports_title(name)
    } else {
        parser::parse_title(name)
    };
    let effective_meta_type = if meta_type == "sports" {
        "sports"
    } else {
        meta_type
    };
    let file_rows: Vec<Value> = data
        .get("file_data")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let prefetch = import_helpers::prefetch_torrent_import_metadata(
        &state.http,
        state.config.tmdb_api_key.as_deref(),
        state.config.tvdb_api_key.as_deref(),
        effective_meta_type,
        &meta_id,
        name,
        data_str(data, "sports_category"),
        &file_rows,
    )
    .await;
    let media_id = resolve_media(
        state,
        &meta_id,
        effective_meta_type,
        name,
        data,
        Some(&prefetch),
    )
    .await;

    let is_proper = data
        .get("is_proper")
        .and_then(|v| v.as_bool())
        .unwrap_or(parsed.is_proper);
    let is_repack = data
        .get("is_repack")
        .and_then(|v| v.as_bool())
        .unwrap_or(parsed.is_repack);
    let is_remastered = data
        .get("is_remastered")
        .and_then(|v| v.as_bool())
        .unwrap_or(parsed.is_remastered);
    let is_upscaled = data
        .get("is_upscaled")
        .and_then(|v| v.as_bool())
        .unwrap_or(parsed.is_upscaled);
    let is_extended = data
        .get("is_extended")
        .and_then(|v| v.as_bool())
        .unwrap_or(parsed.is_extended);
    let is_complete = data
        .get("is_complete")
        .and_then(|v| v.as_bool())
        .unwrap_or(parsed.is_complete);
    let is_dubbed = data
        .get("is_dubbed")
        .and_then(|v| v.as_bool())
        .unwrap_or(parsed.is_dubbed);
    let is_subbed = data
        .get("is_subbed")
        .and_then(|v| v.as_bool())
        .unwrap_or(parsed.is_subbed);
    let release_group = data_str(data, "release_group").or(parsed.release_group.as_deref());

    let stream_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO stream(
               stream_type, name, source, uploader, uploader_user_id,
               resolution, codec, quality, bit_depth, release_group,
               is_proper, is_repack, is_remastered, is_upscaled,
               is_extended, is_complete, is_dubbed, is_subbed, is_active, is_blocked, is_public,
               playback_count, created_at
           ) VALUES(
               $1, $2, 'Contribution Stream', $3, $4,
               $5, $6, $7, $8, $9,
               $10, $11, $12, $13, $14, $15, $16, $17,
               true, false, $18, 0, NOW()
           ) RETURNING id"#,
    )
    .bind(StreamType::Torrent)
    .bind(name)
    .bind(&uploader_name)
    .bind(uploader_user_id)
    .bind(data_str(data, "resolution").or(parsed.resolution.as_deref()))
    .bind(data_str(data, "codec").or(parsed.codec.as_deref()))
    .bind(data_str(data, "quality").or(parsed.quality.as_deref()))
    .bind(data_str(data, "bit_depth"))
    .bind(release_group)
    .bind(is_proper)
    .bind(is_repack)
    .bind(is_remastered)
    .bind(is_upscaled)
    .bind(is_extended)
    .bind(is_complete)
    .bind(is_dubbed)
    .bind(is_subbed)
    .bind(
        data.get("is_public")
            .and_then(|v| v.as_bool())
            .unwrap_or(true),
    )
    .fetch_one(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    let total_size = data.get("total_size").and_then(|v| v.as_i64()).unwrap_or(0);
    let file_count = data.get("file_count").and_then(|v| v.as_i64()).unwrap_or(1) as i32;

    sqlx::query(
        r#"INSERT INTO torrent_stream(stream_id, info_hash, total_size, torrent_type, file_count, created_at)
           VALUES($1, $2, $3, $4, $5, NOW())"#,
    )
    .bind(stream_id)
    .bind(&info_hash)
    .bind(total_size)
    .bind(TorrentType::Public)
    .bind(file_count)
    .execute(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    if let Some(mid) = media_id {
        let _ = import_helpers::link_stream_to_media(&state.pool, stream_id, crate::db::MediaId(mid)).await;
    }

    if !file_rows.is_empty() {
        import_helpers::insert_torrent_import_files(
            &state.pool,
            &state.http,
            state.config.tmdb_api_key.as_deref(),
            state.config.tvdb_api_key.as_deref(),
            stream_id,
            effective_meta_type,
            media_id,
            &file_rows,
            data_str(data, "sports_category"),
            &prefetch,
        )
        .await
        .map_err(|e| {
            if e.contains("Adult content") {
                ImportProcessError::AdultContent
            } else {
                ImportProcessError::Other(e)
            }
        })?;
    }

    apply_contribution_stream_extras(state, stream_id, data, media_id, true).await?;

    Ok(ImportProcessResult {
        status: "success",
        stream_id: Some(stream_id as i64),
        message: None,
    })
}

async fn process_nzb(
    state: &AppState,
    data: &Value,
    user_id: Option<i64>,
    username: &str,
) -> Result<ImportProcessResult, ImportProcessError> {
    let nzb_guid =
        data_str(data, "nzb_guid").ok_or(ImportProcessError::MissingField("nzb_guid"))?;
    let name = data_str(data, "name")
        .or_else(|| data_str(data, "title"))
        .unwrap_or("Unknown");
    let meta_type = data_str(data, "meta_type").unwrap_or("movie");
    let meta_id = data_str(data, "meta_id")
        .map(str::to_string)
        .unwrap_or_else(|| import_helpers::synthetic_import_meta_id("nzb", nzb_guid));

    if let Some(existing) = sqlx::query_scalar::<_, i32>(
        "SELECT stream_id FROM usenet_stream WHERE nzb_guid = $1 LIMIT 1",
    )
    .bind(nzb_guid)
    .fetch_optional(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?
    {
        let want_public = data
            .get("is_public")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        if want_public {
            publish_stream(&state.pool, existing, true).await?;
            if let Some(mid) = resolve_media(state, &meta_id, meta_type, name, data, None).await {
                let _ = import_helpers::link_stream_to_media(&state.pool, existing, crate::db::MediaId(mid)).await;
            }
            return Ok(ImportProcessResult {
                status: "success",
                stream_id: Some(existing as i64),
                message: Some("Existing NZB stream published".to_string()),
            });
        }
        return Ok(ImportProcessResult {
            status: "exists",
            stream_id: Some(existing as i64),
            message: Some("NZB already exists in database".to_string()),
        });
    }

    let is_anonymous = data
        .get("is_anonymous")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    let media_id = resolve_media(state, &meta_id, meta_type, name, data, None).await;

    let parsed = parser::parse_title(name);
    let stream_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO stream(stream_type, name, source, uploader, uploader_user_id,
               resolution, codec, quality, is_proper, is_repack, release_group,
               is_active, is_blocked, is_public, playback_count, created_at)
           VALUES($1, $2, $3, $4, $5,
               $6, $7, $8, $9, $10, $11,
               true, false, true, 0, NOW()) RETURNING id"#,
    )
    .bind(StreamType::Usenet)
    .bind(name)
    .bind(data_str(data, "indexer").unwrap_or("User Import"))
    .bind(&uploader_name)
    .bind(uploader_user_id)
    .bind(data_str(data, "resolution").or(parsed.resolution.as_deref()))
    .bind(data_str(data, "codec").or(parsed.codec.as_deref()))
    .bind(data_str(data, "quality").or(parsed.quality.as_deref()))
    .bind(
        data.get("is_proper")
            .and_then(|v| v.as_bool())
            .unwrap_or(parsed.is_proper),
    )
    .bind(
        data.get("is_repack")
            .and_then(|v| v.as_bool())
            .unwrap_or(parsed.is_repack),
    )
    .bind(data_str(data, "group_name").or(parsed.release_group.as_deref()))
    .fetch_one(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    let nzb_url = data_str(data, "nzb_url");
    let size = data.get("total_size").and_then(|v| v.as_i64());

    sqlx::query(
        r#"INSERT INTO usenet_stream(stream_id, nzb_guid, nzb_url, size, indexer, is_passworded)
           VALUES($1, $2, $3, $4, $5, false)"#,
    )
    .bind(stream_id)
    .bind(nzb_guid)
    .bind(nzb_url)
    .bind(size)
    .bind(data_str(data, "indexer").unwrap_or("User Import"))
    .execute(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    if let Some(mid) = media_id {
        let _ = import_helpers::link_stream_to_media(&state.pool, stream_id, crate::db::MediaId(mid)).await;
    }

    apply_contribution_stream_extras(state, stream_id, data, media_id, false).await?;

    Ok(ImportProcessResult {
        status: "success",
        stream_id: Some(stream_id as i64),
        message: None,
    })
}

async fn process_http(
    state: &AppState,
    data: &Value,
    user_id: Option<i64>,
    username: &str,
) -> Result<ImportProcessResult, ImportProcessError> {
    let url = data_str(data, "url").ok_or(ImportProcessError::MissingField("url"))?;
    let title = data_str(data, "title").unwrap_or("HTTP Stream");
    let meta_type = data_str(data, "meta_type").unwrap_or("movie");
    let meta_id = data_str(data, "meta_id")
        .map(str::to_string)
        .unwrap_or_else(|| import_helpers::synthetic_import_meta_id("http", url));

    let is_anonymous = data
        .get("is_anonymous")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    let media_id = resolve_media(state, &meta_id, meta_type, title, data, None).await;

    if let Some(mid) = media_id {
        if let Some(existing) = sqlx::query_scalar::<_, i32>(
            "SELECT hs.stream_id FROM http_stream hs
             JOIN stream_media_link sml ON sml.stream_id = hs.stream_id
             WHERE hs.url = $1 AND sml.media_id = $2 LIMIT 1",
        )
        .bind(url)
        .bind(mid)
        .fetch_optional(&state.pool)
        .await
        .map_err(|e| ImportProcessError::Other(e.to_string()))?
        {
            publish_stream(&state.pool, existing, true).await?;
            return Ok(ImportProcessResult {
                status: "exists",
                stream_id: Some(existing as i64),
                message: Some("HTTP stream already exists for this media".to_string()),
            });
        }
    }

    let stream_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO stream(stream_type, name, source, uploader, uploader_user_id, is_active, is_blocked, is_public, playback_count, created_at)
           VALUES($1, $2, 'user_import', $3, $4, true, false, true, 0, NOW()) RETURNING id"#,
    )
    .bind(StreamType::Http)
    .bind(title)
    .bind(&uploader_name)
    .bind(uploader_user_id)
    .fetch_one(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    let behavior_hints = data.get("behavior_hints").map(|v| v.to_string());
    sqlx::query(
        "INSERT INTO http_stream (stream_id, url, format, behavior_hints) VALUES ($1, $2, $3, $4::jsonb)",
    )
    .bind(stream_id)
    .bind(url)
    .bind(data_str(data, "format"))
    .bind(behavior_hints.as_deref())
    .execute(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    if let Some(mid) = media_id {
        let _ = import_helpers::link_stream_to_media(&state.pool, stream_id, crate::db::MediaId(mid)).await;
    }

    apply_contribution_stream_extras(state, stream_id, data, media_id, false).await?;

    Ok(ImportProcessResult {
        status: "success",
        stream_id: Some(stream_id as i64),
        message: None,
    })
}

async fn process_youtube(
    state: &AppState,
    data: &Value,
    user_id: Option<i64>,
    username: &str,
) -> Result<ImportProcessResult, ImportProcessError> {
    let video_id =
        data_str(data, "video_id").ok_or(ImportProcessError::MissingField("video_id"))?;
    let title = data_str(data, "title").unwrap_or("YouTube Video");
    let meta_type = data_str(data, "meta_type").unwrap_or("movie");
    let meta_id = data_str(data, "meta_id")
        .map(str::to_string)
        .unwrap_or_else(|| import_helpers::synthetic_import_meta_id("youtube", video_id));

    if let Some(existing) = sqlx::query_scalar::<_, i32>(
        "SELECT stream_id FROM youtube_stream WHERE video_id = $1 LIMIT 1",
    )
    .bind(video_id)
    .fetch_optional(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?
    {
        publish_stream(&state.pool, existing, true).await?;
        if let Some(mid) = resolve_media(state, &meta_id, meta_type, title, data, None).await {
            let _ = import_helpers::link_stream_to_media(&state.pool, existing, crate::db::MediaId(mid)).await;
        }
        return Ok(ImportProcessResult {
            status: "success",
            stream_id: Some(existing as i64),
            message: Some("Existing YouTube stream published".to_string()),
        });
    }

    let is_anonymous = data
        .get("is_anonymous")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    let media_id = resolve_media(state, &meta_id, meta_type, title, data, None).await;

    let stream_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO stream(stream_type, name, source, uploader, uploader_user_id, is_active, is_blocked, is_public, playback_count, created_at)
           VALUES($1, $2, 'youtube', $3, $4, true, false, true, 0, NOW()) RETURNING id"#,
    )
    .bind(StreamType::Youtube)
    .bind(title)
    .bind(&uploader_name)
    .bind(uploader_user_id)
    .fetch_one(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    sqlx::query(
        "INSERT INTO youtube_stream (stream_id, video_id, channel_id, channel_name, duration_seconds, is_live) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(stream_id)
    .bind(video_id)
    .bind(data.get("channel_id").and_then(|v| v.as_str()))
    .bind(data.get("channel_name").and_then(|v| v.as_str()))
    .bind(data.get("duration_seconds").and_then(|v| v.as_i64()))
    .bind(data.get("is_live").and_then(|v| v.as_bool()).unwrap_or(false))
    .execute(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    if let Some(mid) = media_id {
        let _ = import_helpers::link_stream_to_media(&state.pool, stream_id, crate::db::MediaId(mid)).await;
    }

    apply_contribution_stream_extras(state, stream_id, data, media_id, false).await?;

    Ok(ImportProcessResult {
        status: "success",
        stream_id: Some(stream_id as i64),
        message: None,
    })
}

async fn process_acestream(
    state: &AppState,
    data: &Value,
    user_id: Option<i64>,
    username: &str,
) -> Result<ImportProcessResult, ImportProcessError> {
    let content_id =
        data_str(data, "content_id").ok_or(ImportProcessError::MissingField("content_id"))?;
    let title = data_str(data, "name")
        .or_else(|| data_str(data, "title"))
        .unwrap_or("AceStream");
    let meta_type = data_str(data, "meta_type").unwrap_or("tv");
    let meta_id = data_str(data, "meta_id")
        .map(str::to_string)
        .unwrap_or_else(|| import_helpers::synthetic_import_meta_id("acestream", content_id));

    if let Some(existing) = sqlx::query_scalar::<_, i32>(
        "SELECT stream_id FROM acestream_stream WHERE content_id = $1 LIMIT 1",
    )
    .bind(content_id)
    .fetch_optional(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?
    {
        publish_stream(&state.pool, existing, true).await?;
        if let Some(mid) = resolve_media(state, &meta_id, meta_type, title, data, None).await {
            let _ = import_helpers::link_stream_to_media(&state.pool, existing, crate::db::MediaId(mid)).await;
        }
        return Ok(ImportProcessResult {
            status: "success",
            stream_id: Some(existing as i64),
            message: Some("Existing AceStream published".to_string()),
        });
    }

    let is_anonymous = data
        .get("is_anonymous")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    let media_id = resolve_media(state, &meta_id, meta_type, title, data, None).await;

    let stream_id: i32 = sqlx::query_scalar(
        r#"INSERT INTO stream(stream_type, name, source, uploader, uploader_user_id, is_active, is_blocked, is_public, playback_count, created_at)
           VALUES($1, $2, 'user_import', $3, $4, true, false, true, 0, NOW()) RETURNING id"#,
    )
    .bind(StreamType::Acestream)
    .bind(title)
    .bind(&uploader_name)
    .bind(uploader_user_id)
    .fetch_one(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    sqlx::query(
        "INSERT INTO acestream_stream (stream_id, content_id, info_hash) VALUES ($1, $2, $3)",
    )
    .bind(stream_id)
    .bind(content_id)
    .bind(data_str(data, "info_hash"))
    .execute(&state.pool)
    .await
    .map_err(|e| ImportProcessError::Other(e.to_string()))?;

    if let Some(mid) = media_id {
        let _ = import_helpers::link_stream_to_media(&state.pool, stream_id, crate::db::MediaId(mid)).await;
    }

    apply_contribution_stream_extras(state, stream_id, data, media_id, false).await?;

    Ok(ImportProcessResult {
        status: "success",
        stream_id: Some(stream_id as i64),
        message: None,
    })
}

async fn process_telegram(
    state: &AppState,
    data: &Value,
) -> Result<ImportProcessResult, ImportProcessError> {
    let file_unique_id = data.get("file_unique_id").and_then(|v| v.as_str());
    let file_id = data.get("file_id").and_then(|v| v.as_str());

    if file_unique_id.is_none() && file_id.is_none() {
        return Err(ImportProcessError::MissingField("file_unique_id"));
    }

    let stream_id: Option<i32> = if let Some(fid) = file_unique_id {
        sqlx::query_scalar(
            "SELECT stream_id FROM telegram_stream WHERE file_unique_id = $1 LIMIT 1",
        )
        .bind(fid)
        .fetch_optional(&state.pool)
        .await
    } else if let Some(fid) = file_id {
        sqlx::query_scalar("SELECT stream_id FROM telegram_stream WHERE file_id = $1 LIMIT 1")
            .bind(fid)
            .fetch_optional(&state.pool)
            .await
    } else {
        Ok(None)
    }
    .map_err(|e| ImportProcessError::Other(e.to_string()))?
    .flatten();

    let Some(stream_id) = stream_id else {
        return Err(ImportProcessError::Other(
            "Telegram stream not found for this contribution".to_string(),
        ));
    };

    publish_stream(&state.pool, stream_id, true).await?;

    Ok(ImportProcessResult {
        status: "success",
        stream_id: Some(stream_id as i64),
        message: Some("Existing Telegram stream published".to_string()),
    })
}

fn contribution_string_list_alt(data: &Value, keys: &[&str]) -> Vec<String> {
    for key in keys {
        let vals = contribution_string_list(data, key);
        if !vals.is_empty() {
            return vals;
        }
    }
    vec![]
}

async fn apply_contribution_stream_extras(
    state: &AppState,
    stream_id: i32,
    data: &Value,
    media_id: Option<i32>,
    include_trackers: bool,
) -> Result<(), ImportProcessError> {
    let languages = contribution_string_list(data, "languages");
    if !languages.is_empty() {
        link_stream_languages(&state.pool, stream_id, &languages)
            .await
            .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    }

    let audio = contribution_string_list_alt(data, &["audio_formats", "audio"]);
    if !audio.is_empty() {
        link_stream_audio_formats(&state.pool, stream_id, &audio)
            .await
            .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    }

    let hdr = contribution_string_list_alt(data, &["hdr_formats", "hdr"]);
    if !hdr.is_empty() {
        link_stream_hdr_formats(&state.pool, stream_id, &hdr)
            .await
            .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    }

    let channels = contribution_string_list(data, "channels");
    if !channels.is_empty() {
        link_stream_audio_channels(&state.pool, stream_id, &channels)
            .await
            .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    }

    if include_trackers {
        let trackers = contribution_string_list(data, "trackers");
        if !trackers.is_empty() {
            link_torrent_trackers(&state.pool, stream_id, &trackers)
                .await
                .map_err(|e| ImportProcessError::Other(e.to_string()))?;
        }
    }

    if let Some(mid) = media_id {
        let mut catalogs = contribution_string_list(data, "catalogs");
        if let Some(sports_cat) = data_str(data, "sports_category") {
            if !catalogs.iter().any(|c| c == sports_cat) {
                catalogs.insert(0, sports_cat.to_string());
            }
        }
        if !catalogs.is_empty() {
            link_media_catalogs(&state.pool, mid, &catalogs)
                .await
                .map_err(|e| ImportProcessError::Other(e.to_string()))?;
        }
    }

    Ok(())
}

async fn resolve_media(
    state: &AppState,
    meta_id: &str,
    meta_type: &str,
    title: &str,
    data: &Value,
    prefetch: Option<&crate::scrapers::media_resolve::ImportMetadataCache>,
) -> Option<i32> {
    import_helpers::resolve_media_for_import(
        &state.pool,
        &state.http,
        state.config.tmdb_api_key.as_deref(),
        state.config.tvdb_api_key.as_deref(),
        meta_id,
        meta_type,
        ImportMediaOverrides {
            title: Some(title),
            poster: data_str(data, "poster"),
            background: data_str(data, "background"),
            release_date: data_str(data, "release_date"),
            year: data.get("year").and_then(|v| v.as_i64()).map(|y| y as i32),
        },
        prefetch,
    )
    .await
}

async fn publish_stream(
    pool: &PgPool,
    stream_id: i32,
    is_public: bool,
) -> Result<(), ImportProcessError> {
    sqlx::query("UPDATE stream SET is_public = $1, updated_at = NOW() WHERE id = $2")
        .bind(is_public)
        .bind(stream_id)
        .execute(pool)
        .await
        .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    Ok(())
}

pub fn append_review_note(existing: Option<&str>, note: &str) -> String {
    match existing {
        Some(s) if !s.is_empty() => format!("{s}\n{note}"),
        _ => note.to_string(),
    }
}
