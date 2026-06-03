//! Re-parse stream release names with PTT and fill missing metadata columns / link tables.

use std::collections::HashMap;

use sqlx::PgPool;
use tracing::debug;

use super::stream_links::{
    link_stream_audio_channels, link_stream_audio_formats, link_stream_hdr_formats,
    link_stream_languages,
};
use super::stream_model::StreamStoreBase;
use super::types::StreamType;

/// One stream row eligible for metadata backfill.
#[derive(Debug, Clone, sqlx::FromRow)]
pub struct StreamBackfillRow {
    pub id: i32,
    pub name: String,
    pub resolution: Option<String>,
    pub codec: Option<String>,
    pub quality: Option<String>,
    pub release_group: Option<String>,
    pub has_languages: bool,
    pub has_hdr: bool,
    pub has_audio: bool,
    pub has_channels: bool,
}

#[derive(Debug, Default, Clone, Copy)]
struct StreamLinkFlags {
    has_languages: bool,
    has_hdr: bool,
    has_audio: bool,
    has_channels: bool,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct StreamBackfillCandidate {
    id: i32,
    name: String,
    resolution: Option<String>,
    codec: Option<String>,
    quality: Option<String>,
    release_group: Option<String>,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct StreamBackfillStats {
    pub examined: u32,
    pub updated_columns: u32,
    pub linked_languages: u32,
    pub linked_hdr: u32,
    pub linked_audio: u32,
    pub linked_channels: u32,
    pub skipped_empty_parse: u32,
}

fn is_blank(opt: &Option<String>) -> bool {
    opt.as_ref().is_none_or(|s| s.trim().is_empty())
}

async fn load_stream_link_flags(
    pool: &PgPool,
    stream_ids: &[i32],
) -> Result<HashMap<i32, StreamLinkFlags>, sqlx::Error> {
    let mut flags = stream_ids
        .iter()
        .map(|&id| (id, StreamLinkFlags::default()))
        .collect::<HashMap<_, _>>();

    if stream_ids.is_empty() {
        return Ok(flags);
    }

    mark_linked_streams(
        pool,
        stream_ids,
        "SELECT DISTINCT stream_id FROM stream_language_link WHERE stream_id = ANY($1)",
        |entry| entry.has_languages = true,
        &mut flags,
    )
    .await?;
    mark_linked_streams(
        pool,
        stream_ids,
        "SELECT DISTINCT stream_id FROM stream_hdr_link WHERE stream_id = ANY($1)",
        |entry| entry.has_hdr = true,
        &mut flags,
    )
    .await?;
    mark_linked_streams(
        pool,
        stream_ids,
        "SELECT DISTINCT stream_id FROM stream_audio_link WHERE stream_id = ANY($1)",
        |entry| entry.has_audio = true,
        &mut flags,
    )
    .await?;
    mark_linked_streams(
        pool,
        stream_ids,
        "SELECT DISTINCT stream_id FROM stream_channel_link WHERE stream_id = ANY($1)",
        |entry| entry.has_channels = true,
        &mut flags,
    )
    .await?;

    Ok(flags)
}

async fn mark_linked_streams<F>(
    pool: &PgPool,
    stream_ids: &[i32],
    query: &str,
    mut mark: F,
    flags: &mut HashMap<i32, StreamLinkFlags>,
) -> Result<(), sqlx::Error>
where
    F: FnMut(&mut StreamLinkFlags),
{
    let linked_ids: Vec<i32> = sqlx::query_scalar(query)
        .bind(stream_ids)
        .fetch_all(pool)
        .await?;

    for id in linked_ids {
        if let Some(entry) = flags.get_mut(&id) {
            mark(entry);
        }
    }

    Ok(())
}

/// Fetch a page of streams that still lack parsed metadata.
///
/// Uses keyset pagination (`after_id`) so each page stays O(limit) regardless of progress.
pub async fn fetch_streams_for_backfill(
    pool: &PgPool,
    stream_types: &[StreamType],
    only_missing: bool,
    limit: i64,
    after_id: i32,
) -> Result<Vec<StreamBackfillRow>, sqlx::Error> {
    let candidates = sqlx::query_as::<_, StreamBackfillCandidate>(
        r#"
        SELECT
            s.id,
            s.name,
            s.resolution,
            s.codec,
            s.quality,
            s.release_group
        FROM stream s
        WHERE s.stream_type = ANY($1::streamtype[])
          AND NOT s.is_blocked
          AND s.name IS NOT NULL
          AND TRIM(s.name) <> ''
          AND s.id > $2
          AND (
            NOT $3
            OR s.resolution IS NULL OR TRIM(s.resolution) = ''
            OR s.codec IS NULL OR TRIM(s.codec) = ''
            OR s.quality IS NULL OR TRIM(s.quality) = ''
            OR s.release_group IS NULL OR TRIM(s.release_group) = ''
            OR NOT EXISTS (SELECT 1 FROM stream_language_link sll WHERE sll.stream_id = s.id)
            OR NOT EXISTS (SELECT 1 FROM stream_hdr_link shl WHERE shl.stream_id = s.id)
            OR NOT EXISTS (SELECT 1 FROM stream_audio_link sal WHERE sal.stream_id = s.id)
            OR NOT EXISTS (SELECT 1 FROM stream_channel_link scl WHERE scl.stream_id = s.id)
          )
        ORDER BY s.id
        LIMIT $4
        "#,
    )
    .bind(stream_types)
    .bind(after_id)
    .bind(only_missing)
    .bind(limit)
    .fetch_all(pool)
    .await?;

    if candidates.is_empty() {
        return Ok(Vec::new());
    }

    let stream_ids: Vec<i32> = candidates.iter().map(|row| row.id).collect();
    let link_flags = load_stream_link_flags(pool, &stream_ids).await?;

    Ok(candidates
        .into_iter()
        .map(|row| {
            let flags = link_flags.get(&row.id).copied().unwrap_or_default();
            StreamBackfillRow {
                id: row.id,
                name: row.name,
                resolution: row.resolution,
                codec: row.codec,
                quality: row.quality,
                release_group: row.release_group,
                has_languages: flags.has_languages,
                has_hdr: flags.has_hdr,
                has_audio: flags.has_audio,
                has_channels: flags.has_channels,
            }
        })
        .collect())
}

/// Parse `name` with PTT and apply missing column + link-table updates for one stream.
pub async fn backfill_stream_row(
    pool: &PgPool,
    row: &StreamBackfillRow,
    only_missing: bool,
) -> Result<StreamBackfillStats, sqlx::Error> {
    let mut stats = StreamBackfillStats {
        examined: 1,
        ..Default::default()
    };

    let parsed = crate::parser::parse_title(&row.name);
    let has_parse_data = parsed.resolution.is_some()
        || parsed.quality.is_some()
        || parsed.codec.is_some()
        || !parsed.languages.is_empty()
        || !parsed.hdr.is_empty()
        || !parsed.audio.is_empty()
        || !parsed.channels.is_empty();

    if !has_parse_data {
        stats.skipped_empty_parse = 1;
        return Ok(stats);
    }

    let resolution = if only_missing && !is_blank(&row.resolution) {
        row.resolution.clone()
    } else {
        parsed.resolution.clone().or_else(|| row.resolution.clone())
    };
    let codec = if only_missing && !is_blank(&row.codec) {
        row.codec.clone()
    } else {
        parsed.codec.clone().or_else(|| row.codec.clone())
    };
    let quality = if only_missing && !is_blank(&row.quality) {
        row.quality.clone()
    } else {
        parsed.quality.clone().or_else(|| row.quality.clone())
    };
    let release_group = if only_missing && !is_blank(&row.release_group) {
        row.release_group.clone()
    } else {
        parsed
            .release_group
            .clone()
            .or_else(|| row.release_group.clone())
    };

    let columns_changed = (resolution.as_deref() != row.resolution.as_deref())
        || (codec.as_deref() != row.codec.as_deref())
        || (quality.as_deref() != row.quality.as_deref())
        || (release_group.as_deref() != row.release_group.as_deref());

    if columns_changed || !only_missing {
        sqlx::query(
            r#"
            UPDATE stream SET
                resolution = $2,
                codec = $3,
                quality = $4,
                release_group = $5,
                is_proper = CASE WHEN $6 THEN $7 ELSE is_proper END,
                is_repack = CASE WHEN $6 THEN $8 ELSE is_repack END,
                is_extended = CASE WHEN $6 THEN $9 ELSE is_extended END,
                is_complete = CASE WHEN $6 THEN $10 ELSE is_complete END,
                is_dubbed = CASE WHEN $6 THEN $11 ELSE is_dubbed END,
                is_subbed = CASE WHEN $6 THEN $12 ELSE is_subbed END,
                is_remastered = CASE WHEN $6 THEN $13 ELSE is_remastered END,
                is_upscaled = CASE WHEN $6 THEN $14 ELSE is_upscaled END,
                updated_at = NOW()
            WHERE id = $1
            "#,
        )
        .bind(row.id)
        .bind(&resolution)
        .bind(&codec)
        .bind(&quality)
        .bind(&release_group)
        .bind(!only_missing)
        .bind(parsed.is_proper)
        .bind(parsed.is_repack)
        .bind(parsed.is_extended)
        .bind(parsed.is_complete)
        .bind(parsed.is_dubbed)
        .bind(parsed.is_subbed)
        .bind(parsed.is_remastered)
        .bind(parsed.is_upscaled)
        .execute(pool)
        .await?;
        if columns_changed {
            stats.updated_columns = 1;
        }
    }

    let base = StreamStoreBase {
        name: row.name.clone(),
        source: String::new(),
        resolution,
        codec,
        quality,
        release_group,
        languages: parsed.languages.clone(),
        hdr_formats: parsed.hdr.clone(),
        audio_formats: parsed.audio.clone(),
        audio_channels: parsed.channels.clone(),
        is_proper: parsed.is_proper,
        is_repack: parsed.is_repack,
        is_extended: parsed.is_extended,
        is_complete: parsed.is_complete,
        is_dubbed: parsed.is_dubbed,
        is_subbed: parsed.is_subbed,
        is_remastered: parsed.is_remastered,
        is_upscaled: parsed.is_upscaled,
        ..StreamStoreBase::default()
    };

    if (!only_missing || !row.has_languages) && !base.languages.is_empty() {
        link_stream_languages(pool, row.id, &base.languages).await?;
        stats.linked_languages = 1;
    }
    if (!only_missing || !row.has_hdr) && !base.hdr_formats.is_empty() {
        link_stream_hdr_formats(pool, row.id, &base.hdr_formats).await?;
        stats.linked_hdr = 1;
    }
    if (!only_missing || !row.has_audio) && !base.audio_formats.is_empty() {
        link_stream_audio_formats(pool, row.id, &base.audio_formats).await?;
        stats.linked_audio = 1;
    }
    if (!only_missing || !row.has_channels) && !base.audio_channels.is_empty() {
        link_stream_audio_channels(pool, row.id, &base.audio_channels).await?;
        stats.linked_channels = 1;
    }

    debug!(
        stream_id = row.id,
        langs = base.languages.len(),
        hdr = base.hdr_formats.len(),
        "stream_backfill applied"
    );

    Ok(stats)
}

/// Backfill a batch; returns aggregate stats.
pub async fn backfill_stream_batch(
    pool: &PgPool,
    rows: &[StreamBackfillRow],
    only_missing: bool,
) -> Result<StreamBackfillStats, sqlx::Error> {
    let mut total = StreamBackfillStats::default();
    for row in rows {
        let s = backfill_stream_row(pool, row, only_missing).await?;
        total.examined += s.examined;
        total.updated_columns += s.updated_columns;
        total.linked_languages += s.linked_languages;
        total.linked_hdr += s.linked_hdr;
        total.linked_audio += s.linked_audio;
        total.linked_channels += s.linked_channels;
        total.skipped_empty_parse += s.skipped_empty_parse;
    }
    Ok(total)
}

/// Default stream types parsed from release names (torrent + usenet).
pub fn default_backfill_stream_types() -> Vec<StreamType> {
    vec![StreamType::Torrent, StreamType::Usenet]
}
