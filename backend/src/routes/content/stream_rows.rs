//! Shared stream row SQL fragments and JSON mapping for catalog browse and owner streams.

use chrono::{DateTime, Utc};
use serde_json::{json, Value};

use crate::db::StreamType;

/// Aggregated link-table columns reused by catalog and owner stream queries.
pub const STREAM_LINK_AGG_COLS: &str = r#"
    (SELECT STRING_AGG(af.name, '|' ORDER BY af.name)
     FROM stream_audio_link sal JOIN audio_format af ON af.id = sal.audio_format_id
     WHERE sal.stream_id = s.id) AS audio_formats,
    (SELECT STRING_AGG(ac.name, '|' ORDER BY ac.name)
     FROM stream_channel_link scl JOIN audio_channel ac ON ac.id = scl.channel_id
     WHERE scl.stream_id = s.id) AS channels,
    (SELECT STRING_AGG(hf.name, '|' ORDER BY hf.name)
     FROM stream_hdr_link shl JOIN hdr_format hf ON hf.id = shl.hdr_format_id
     WHERE shl.stream_id = s.id) AS hdr_formats,
    (SELECT STRING_AGG(l.name, ' + ' ORDER BY l.name)
     FROM stream_language_link sll JOIN language l ON l.id = sll.language_id
     WHERE sll.stream_id = s.id) AS languages,
    s.is_remastered,
    s.is_upscaled,
    s.is_proper,
    s.is_repack,
    s.is_extended,
    s.is_complete,
    s.is_dubbed,
    s.is_subbed"#;

/// Core stream columns shared by catalog browse queries.
pub const STREAM_BASE_COLS: &str = r#"
    s.id,
    s.name,
    s.stream_type,
    s.source,
    s.resolution,
    s.quality,
    s.codec,
    s.bit_depth,
    ts.seeders,
    s.uploader,
    s.release_group"#;

#[derive(sqlx::FromRow)]
pub struct BrowseStreamRow {
    pub id: i32,
    pub name: String,
    pub stream_type: StreamType,
    pub source: Option<String>,
    pub resolution: Option<String>,
    pub quality: Option<String>,
    pub codec: Option<String>,
    pub bit_depth: Option<String>,
    pub seeders: Option<i32>,
    pub uploader: Option<String>,
    pub release_group: Option<String>,
    pub filename: Option<String>,
    pub file_size: Option<i64>,
    pub info_hash: Option<String>,
    pub yt_id: Option<String>,
    pub audio_formats: Option<String>,
    pub channels: Option<String>,
    pub hdr_formats: Option<String>,
    pub languages: Option<String>,
    pub is_remastered: bool,
    pub is_upscaled: bool,
    pub is_proper: bool,
    pub is_repack: bool,
    pub is_extended: bool,
    pub is_complete: bool,
    pub is_dubbed: bool,
    pub is_subbed: bool,
    pub created_at: Option<DateTime<Utc>>,
}

#[derive(sqlx::FromRow)]
pub struct MyStreamRow {
    pub id: i32,
    pub name: String,
    pub stream_type: StreamType,
    pub source: Option<String>,
    pub resolution: Option<String>,
    pub quality: Option<String>,
    pub codec: Option<String>,
    pub bit_depth: Option<String>,
    pub seeders: Option<i32>,
    pub uploader: Option<String>,
    pub release_group: Option<String>,
    pub filename: Option<String>,
    pub file_size: Option<i64>,
    pub info_hash: Option<String>,
    pub yt_id: Option<String>,
    pub audio_formats: Option<String>,
    pub channels: Option<String>,
    pub hdr_formats: Option<String>,
    pub languages: Option<String>,
    pub is_remastered: bool,
    pub is_upscaled: bool,
    pub is_proper: bool,
    pub is_repack: bool,
    pub is_extended: bool,
    pub is_complete: bool,
    pub is_dubbed: bool,
    pub is_subbed: bool,
    pub is_blocked: bool,
    pub is_active: bool,
    pub is_public: bool,
    pub media_id: Option<i32>,
    pub media_title: Option<String>,
    pub media_type: Option<String>,
    pub media_poster_url: Option<String>,
    pub media_imdb_id: Option<String>,
    pub file_count: Option<i64>,
    pub created_at: Option<DateTime<Utc>>,
}

pub struct StreamLinkArrays {
    pub audio: Vec<Value>,
    pub channels: Vec<Value>,
    pub hdr: Vec<Value>,
    pub languages: Vec<Value>,
}

pub fn format_size(bytes: i64) -> String {
    if bytes <= 0 {
        return String::new();
    }
    const UNITS: [&str; 5] = ["B", "KB", "MB", "GB", "TB"];
    let mut v = bytes as f64;
    let mut i = 0usize;
    while v >= 1000.0 && i < UNITS.len() - 1 {
        v /= 1000.0;
        i += 1;
    }
    if i == 0 {
        format!("{bytes} B")
    } else {
        format!("{v:.1} {}", UNITS[i])
    }
}

pub fn parse_stream_link_arrays(
    audio_formats: Option<&str>,
    channels: Option<&str>,
    hdr_formats: Option<&str>,
    languages: Option<&str>,
) -> StreamLinkArrays {
    StreamLinkArrays {
        audio: audio_formats
            .map(|s| s.split('|').map(|x| json!(x)).collect())
            .unwrap_or_default(),
        channels: channels
            .map(|s| s.split('|').map(|x| json!(x)).collect())
            .unwrap_or_default(),
        hdr: hdr_formats
            .map(|s| s.split('|').map(|x| json!(x)).collect())
            .unwrap_or_default(),
        languages: languages
            .map(|s| s.split(" + ").map(|x| json!(x)).collect())
            .unwrap_or_default(),
    }
}

pub fn stream_row_base_json(
    id: i32,
    name: &str,
    stream_type: StreamType,
    source: Option<&str>,
    resolution: Option<&str>,
    quality: Option<&str>,
    codec: Option<&str>,
    bit_depth: Option<&str>,
    seeders: Option<i32>,
    uploader: Option<&str>,
    release_group: Option<&str>,
    filename: Option<&str>,
    file_size: Option<i64>,
    info_hash: Option<&str>,
    yt_id: Option<&str>,
    link_arrays: &StreamLinkArrays,
    flags: &StreamFlags,
) -> Value {
    let stream_type_wire = stream_type.as_wire().to_lowercase();
    let file_size_val = file_size.unwrap_or(0);
    let audio_out = if link_arrays.audio.is_empty() {
        json!(null)
    } else {
        json!(link_arrays.audio)
    };
    let channels_out = if link_arrays.channels.is_empty() {
        json!(null)
    } else {
        json!(link_arrays.channels)
    };
    let hdr_out = if link_arrays.hdr.is_empty() {
        json!(null)
    } else {
        json!(link_arrays.hdr)
    };
    let lang_out = if link_arrays.languages.is_empty() {
        json!([])
    } else {
        json!(link_arrays.languages)
    };

    json!({
        "id": id,
        "info_hash": info_hash,
        "yt_id": yt_id,
        "ytId": yt_id,
        "name": name,
        "stream_name": name,
        "stream_type": stream_type_wire,
        "resolution": resolution,
        "quality": quality,
        "codec": codec,
        "bit_depth": bit_depth,
        "audio_formats": audio_out,
        "channels": channels_out,
        "hdr_formats": hdr_out,
        "source": source,
        "languages": lang_out,
        "size": format_size(file_size_val),
        "size_bytes": file_size,
        "seeders": seeders,
        "uploader": uploader,
        "release_group": release_group,
        "filename": filename,
        "is_remastered": flags.is_remastered,
        "is_upscaled": flags.is_upscaled,
        "is_proper": flags.is_proper,
        "is_repack": flags.is_repack,
        "is_extended": flags.is_extended,
        "is_complete": flags.is_complete,
        "is_dubbed": flags.is_dubbed,
        "is_subbed": flags.is_subbed,
    })
}

pub struct StreamFlags {
    pub is_remastered: bool,
    pub is_upscaled: bool,
    pub is_proper: bool,
    pub is_repack: bool,
    pub is_extended: bool,
    pub is_complete: bool,
    pub is_dubbed: bool,
    pub is_subbed: bool,
}

impl From<&BrowseStreamRow> for StreamFlags {
    fn from(row: &BrowseStreamRow) -> Self {
        Self {
            is_remastered: row.is_remastered,
            is_upscaled: row.is_upscaled,
            is_proper: row.is_proper,
            is_repack: row.is_repack,
            is_extended: row.is_extended,
            is_complete: row.is_complete,
            is_dubbed: row.is_dubbed,
            is_subbed: row.is_subbed,
        }
    }
}

impl From<&MyStreamRow> for StreamFlags {
    fn from(row: &MyStreamRow) -> Self {
        Self {
            is_remastered: row.is_remastered,
            is_upscaled: row.is_upscaled,
            is_proper: row.is_proper,
            is_repack: row.is_repack,
            is_extended: row.is_extended,
            is_complete: row.is_complete,
            is_dubbed: row.is_dubbed,
            is_subbed: row.is_subbed,
        }
    }
}

pub fn my_stream_row_to_json(row: &MyStreamRow) -> Value {
    let link_arrays = parse_stream_link_arrays(
        row.audio_formats.as_deref(),
        row.channels.as_deref(),
        row.hdr_formats.as_deref(),
        row.languages.as_deref(),
    );
    let mut base = stream_row_base_json(
        row.id,
        &row.name,
        row.stream_type,
        row.source.as_deref(),
        row.resolution.as_deref(),
        row.quality.as_deref(),
        row.codec.as_deref(),
        row.bit_depth.as_deref(),
        row.seeders,
        row.uploader.as_deref(),
        row.release_group.as_deref(),
        row.filename.as_deref(),
        row.file_size,
        row.info_hash.as_deref(),
        row.yt_id.as_deref(),
        &link_arrays,
        &StreamFlags::from(row),
    );
    if let Some(obj) = base.as_object_mut() {
        obj.insert("is_blocked".into(), json!(row.is_blocked));
        obj.insert("is_active".into(), json!(row.is_active));
        obj.insert("is_public".into(), json!(row.is_public));
        obj.insert("media_id".into(), json!(row.media_id));
        obj.insert("media_title".into(), json!(row.media_title));
        obj.insert("media_type".into(), json!(row.media_type));
        obj.insert("media_poster_url".into(), json!(row.media_poster_url));
        obj.insert("media_imdb_id".into(), json!(row.media_imdb_id));
        obj.insert("file_count".into(), json!(row.file_count));
        obj.insert(
            "created_at".into(),
            json!(row.created_at.map(|dt| dt.to_rfc3339())),
        );
    }
    base
}
