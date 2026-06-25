/// Run import processors when contributions are approved (Python `_apply_contribution_review`).
use serde_json::{Value, json};
use sqlx::PgPool;

use crate::{
    db::TorrentType, parser, scrapers::media_resolve::ImportMediaOverrides, state::AppState,
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
        if let Some(t) = data_str(data, key)
            && is_adult_content(t) {
                return true;
            }
    }
    if let Some(files) = data.get("file_data").and_then(|v| v.as_array()) {
        for file in files {
            if let Some(fname) = file.get("filename").and_then(|v| v.as_str())
                && is_adult_content(fname) {
                    return true;
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
    let (uploader_name, _) = resolve_uploader_identity(
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
                let _ = import_helpers::link_stream_to_media(
                    &state.pool,
                    existing,
                    crate::db::MediaId(mid),
                )
                .await;
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
    let mut file_rows = file_rows;
    if effective_meta_type == "series" {
        if file_rows.is_empty() {
            file_rows.push(json!({
                "index": 0,
                "filename": name,
                "size": data.get("total_size").and_then(|v| v.as_i64()),
            }));
        }
        import_helpers::enrich_series_file_episodes(&mut file_rows, name);
    }
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

    let total_size = data.get("total_size").and_then(|v| v.as_i64()).unwrap_or(0);
    let torrent_type = data
        .get("torrent_type")
        .and_then(|v| v.as_str())
        .map(crate::scrapers::torrent_metadata::parse_torrent_type_str)
        .unwrap_or(TorrentType::Public);
    let torrent_file_bytes = data
        .get("torrent_file")
        .and_then(|v| v.as_str())
        .and_then(|s| base64::Engine::decode(&base64::engine::general_purpose::STANDARD, s).ok());
    let torrent_file = crate::scrapers::torrent_metadata::torrent_file_for_storage(
        torrent_type,
        torrent_file_bytes,
    );

    let mut base = crate::db::StreamStoreBase::from_parsed(
        name.to_string(),
        "Contribution Stream".to_string(),
        &parsed,
    );
    base.resolution = data_str(data, "resolution")
        .map(str::to_string)
        .or(parsed.resolution.clone());
    base.codec = data_str(data, "codec")
        .map(str::to_string)
        .or(parsed.codec.clone());
    base.quality = data_str(data, "quality")
        .map(str::to_string)
        .or(parsed.quality.clone());
    base.bit_depth = data_str(data, "bit_depth").map(str::to_string);
    base.release_group = release_group.map(str::to_string);
    base.is_proper = is_proper;
    base.is_repack = is_repack;
    base.is_remastered = is_remastered;
    base.is_upscaled = is_upscaled;
    base.is_extended = is_extended;
    base.is_complete = is_complete;
    base.is_dubbed = is_dubbed;
    base.is_subbed = is_subbed;
    base.uploader = Some(uploader_name.clone());
    base.uploader_user_id = import_helpers::uploader_user_id_for_stream(is_anonymous, user_id);
    base.is_public = data
        .get("is_public")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);

    let announce_list: Vec<String> = data
        .get("announce_list")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let normalized = crate::db::TorrentStoreInput {
        base,
        info_hash: info_hash.to_string(),
        total_size,
        seeders: None,
        torrent_type,
        torrent_file,
        announce_list,
        files: vec![],
    };

    let media_type =
        crate::db::MediaType::from_wire(effective_meta_type).unwrap_or(crate::db::MediaType::Movie);
    let opts = media_id.map_or_else(
        || crate::db::StoreStreamOpts {
            media_id: crate::db::MediaId(0),
            media_type,
            season: None,
            episode: None,
            episode_end: None,
            link_source: crate::db::LinkSource::User,
            is_primary: true,
            is_verified: false,
        },
        |mid| crate::db::StoreStreamOpts::user_import(crate::db::MediaId(mid), media_type),
    );

    let result = crate::db::store_torrent_stream(&state.pool, &normalized, &opts)
        .await
        .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    let stream_id = result.stream_id().0;

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

    if let Some(mid) = media_id
        && effective_meta_type == "series" {
            let fallback = data_str(data, "title").unwrap_or(name);
            import_helpers::ensure_series_episode_metadata(
                &state.pool,
                mid as i64,
                &file_rows,
                fallback,
            )
            .await;
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
                let _ = import_helpers::link_stream_to_media(
                    &state.pool,
                    existing,
                    crate::db::MediaId(mid),
                )
                .await;
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
    let (uploader_name, _) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    let media_id = resolve_media(state, &meta_id, meta_type, name, data, None).await;

    let parsed = parser::parse_title(name);
    let mut base = crate::db::StreamStoreBase::from_parsed(
        name.to_string(),
        data_str(data, "indexer")
            .unwrap_or("User Import")
            .to_string(),
        &parsed,
    );
    base.uploader = Some(uploader_name.clone());
    base.uploader_user_id = import_helpers::uploader_user_id_for_stream(is_anonymous, user_id);

    let normalized = crate::db::UsenetStoreInput {
        base,
        nzb_guid: nzb_guid.to_string(),
        nzb_url: data_str(data, "nzb_url").unwrap_or("").to_string(),
        size: data.get("total_size").and_then(|v| v.as_i64()).unwrap_or(0),
        indexer: data_str(data, "indexer")
            .unwrap_or("User Import")
            .to_string(),
        group_name: data_str(data, "group_name").map(str::to_string),
        is_passworded: false,
        files: vec![],
    };

    let media_type =
        crate::db::MediaType::from_wire(meta_type).unwrap_or(crate::db::MediaType::Movie);
    let opts = media_id.map_or_else(
        || crate::db::StoreStreamOpts {
            media_id: crate::db::MediaId(0),
            media_type,
            season: None,
            episode: None,
            episode_end: None,
            link_source: crate::db::LinkSource::User,
            is_primary: true,
            is_verified: false,
        },
        |mid| crate::db::StoreStreamOpts::user_import(crate::db::MediaId(mid), media_type),
    );

    let result = crate::db::store_usenet_stream(&state.pool, &normalized, &opts)
        .await
        .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    let stream_id = result.stream_id().0;

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
    let (uploader_name, _) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    let media_id = resolve_media(state, &meta_id, meta_type, title, data, None).await;

    if let Some(mid) = media_id
        && let Some(existing) = sqlx::query_scalar::<_, i32>(
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

    let base = crate::db::StreamStoreBase {
        name: title.to_string(),
        source: "user_import".to_string(),
        uploader: Some(uploader_name.clone()),
        uploader_user_id: import_helpers::uploader_user_id_for_stream(is_anonymous, user_id),
        is_public: true,
        ..Default::default()
    };

    let normalized = crate::db::HttpStoreInput {
        base,
        url: url.to_string(),
        format: data_str(data, "format").map(str::to_string),
        behavior_hints: data.get("behavior_hints").cloned(),
        drm_key_id: None,
        drm_key: None,
        extractor_name: None,
    };

    let media_type =
        crate::db::MediaType::from_wire(meta_type).unwrap_or(crate::db::MediaType::Movie);
    let opts = media_id.map_or_else(
        || crate::db::StoreStreamOpts {
            media_id: crate::db::MediaId(0),
            media_type,
            season: None,
            episode: None,
            episode_end: None,
            link_source: crate::db::LinkSource::User,
            is_primary: true,
            is_verified: false,
        },
        |mid| crate::db::StoreStreamOpts::user_import(crate::db::MediaId(mid), media_type),
    );

    let result = crate::db::store_http_stream(&state.pool, &normalized, &opts)
        .await
        .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    let stream_id = result.stream_id().0;

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
            let _ = import_helpers::link_stream_to_media(
                &state.pool,
                existing,
                crate::db::MediaId(mid),
            )
            .await;
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
    let (uploader_name, _) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    let media_id = resolve_media(state, &meta_id, meta_type, title, data, None).await;

    let base = crate::db::StreamStoreBase {
        name: title.to_string(),
        source: "youtube".to_string(),
        uploader: Some(uploader_name.clone()),
        uploader_user_id: import_helpers::uploader_user_id_for_stream(is_anonymous, user_id),
        is_public: true,
        ..Default::default()
    };

    let normalized = crate::db::YoutubeStoreInput {
        base,
        video_id: video_id.to_string(),
        channel_id: data
            .get("channel_id")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        channel_name: data
            .get("channel_name")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        duration_seconds: data
            .get("duration_seconds")
            .and_then(|v| v.as_i64())
            .map(|n| n as i32),
        is_live: data
            .get("is_live")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        is_premiere: false,
    };

    let media_type =
        crate::db::MediaType::from_wire(meta_type).unwrap_or(crate::db::MediaType::Movie);
    let opts = media_id.map_or_else(
        || crate::db::StoreStreamOpts {
            media_id: crate::db::MediaId(0),
            media_type,
            season: None,
            episode: None,
            episode_end: None,
            link_source: crate::db::LinkSource::User,
            is_primary: true,
            is_verified: false,
        },
        |mid| crate::db::StoreStreamOpts::user_import(crate::db::MediaId(mid), media_type),
    );

    let result = crate::db::store_youtube_stream(&state.pool, &normalized, &opts)
        .await
        .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    let stream_id = result.stream_id().0;

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
            let _ = import_helpers::link_stream_to_media(
                &state.pool,
                existing,
                crate::db::MediaId(mid),
            )
            .await;
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
    let (uploader_name, _) = resolve_uploader_identity(
        is_anonymous,
        data_str(data, "anonymous_display_name"),
        username,
        user_id.unwrap_or(0),
    );

    let media_id = resolve_media(state, &meta_id, meta_type, title, data, None).await;

    let base = crate::db::StreamStoreBase {
        name: title.to_string(),
        source: "user_import".to_string(),
        uploader: Some(uploader_name.clone()),
        uploader_user_id: import_helpers::uploader_user_id_for_stream(is_anonymous, user_id),
        is_public: true,
        ..Default::default()
    };

    let normalized = crate::db::AcestreamStoreInput {
        base,
        content_id: content_id.to_string(),
        info_hash: data_str(data, "info_hash").map(str::to_string),
    };

    let media_type =
        crate::db::MediaType::from_wire(meta_type).unwrap_or(crate::db::MediaType::Series);
    let opts = media_id.map_or_else(
        || crate::db::StoreStreamOpts {
            media_id: crate::db::MediaId(0),
            media_type,
            season: None,
            episode: None,
            episode_end: None,
            link_source: crate::db::LinkSource::User,
            is_primary: true,
            is_verified: false,
        },
        |mid| crate::db::StoreStreamOpts::user_import(crate::db::MediaId(mid), media_type),
    );

    let result = crate::db::store_acestream_stream(&state.pool, &normalized, &opts)
        .await
        .map_err(|e| ImportProcessError::Other(e.to_string()))?;
    let stream_id = result.stream_id().0;

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
        if let Some(sports_cat) = data_str(data, "sports_category")
            && !catalogs.iter().any(|c| c == sports_cat) {
                catalogs.insert(0, sports_cat.to_string());
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
        state.config.poster_nsfw_enabled,
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
