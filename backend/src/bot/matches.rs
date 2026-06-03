//! Metadata match search and inline-keyboard selection.

use serde_json::{json, Value};

use crate::{routes::content::import_helpers, state::AppState};

use super::{
    callback::CallbackAction,
    metadata::{episode_info, metadata_value, selected_languages},
    model::{ContentType, ConversationState},
    text,
};

pub async fn show_media_type_picker(
    state: &AppState,
    user_id: i64,
    content_type: ContentType,
    preview: &str,
) -> (String, Value) {
    let label = super::detect::content_type_label(content_type);
    let escaped_preview = text::escape_markdown(preview);
    let msg = text::content_preview(label, &escaped_preview);
    let keyboard = json!({
        "inline_keyboard": [
            [
                {"text": "🎬 Movie", "callback_data": CallbackAction::MediaType { user_id, media_type: "movie".into() }.encode(state).await},
                {"text": "📺 Series", "callback_data": CallbackAction::MediaType { user_id, media_type: "series".into() }.encode(state).await},
            ],
            [
                {"text": "⚽ Sports", "callback_data": CallbackAction::MediaType { user_id, media_type: "sports".into() }.encode(state).await},
            ],
            [
                {"text": "❌ Cancel", "callback_data": CallbackAction::Cancel { user_id }.encode(state).await},
            ],
        ]
    });
    (msg, keyboard)
}

pub async fn show_matches(state: &AppState, conv: &ConversationState) -> (String, Value) {
    let user_id = conv.user_id;
    let matches = conv.matches.clone().unwrap_or_default();
    let analysis = conv.analysis_result.clone().unwrap_or(json!({}));
    let title = analysis
        .get("parsed_title")
        .or_else(|| analysis.get("torrent_name"))
        .or_else(|| analysis.get("file_name"))
        .and_then(|v| v.as_str())
        .unwrap_or("Unknown");

    let mut rows: Vec<Value> = Vec::new();
    for m in matches.iter().take(8) {
        let ext_id = m
            .get("external_id")
            .or_else(|| m.get("imdb_id"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let mtitle = m.get("title").and_then(|v| v.as_str()).unwrap_or("?");
        let year = m
            .get("year")
            .and_then(|v| v.as_i64())
            .map(|y| format!(" ({y})"))
            .unwrap_or_default();
        let btn_text = format!(
            "{}{}",
            text::escape_markdown(mtitle),
            text::escape_markdown(&year)
        );
        rows.push(json!([{
            "text": btn_text.chars().take(40).collect::<String>(),
            "callback_data": CallbackAction::Match {
                user_id,
                external_id: ext_id.to_string(),
            }
            .encode(state)
            .await,
        }]));
    }

    rows.push(json!([
        {"text": "🔍 Search by Title", "callback_data": CallbackAction::SearchTitle { user_id }.encode(state).await},
        {"text": "✏️ Manual ID", "callback_data": CallbackAction::Manual { user_id }.encode(state).await},
    ]));
    rows.push(json!([{
        "text": "❌ Cancel",
        "callback_data": CallbackAction::Cancel { user_id }.encode(state).await,
    }]));

    let escaped_title = text::escape_markdown(title);
    let msg = format!(
        "🔍 *Select Match*\n\n*Content:* `{escaped_title}`\n\nChoose the correct media or search manually:"
    );
    (msg, json!({ "inline_keyboard": rows }))
}

pub async fn search_by_title(
    state: &AppState,
    title: &str,
    year: Option<i32>,
    media_type: &str,
) -> Vec<Value> {
    import_helpers::search_analyze_matches(state, title, year, media_type).await
}

pub async fn resolve_external_id(
    state: &AppState,
    external_id: &str,
    media_type: &str,
    fallback_title: &str,
    year: Option<i32>,
) -> Option<Value> {
    let matches =
        import_helpers::search_analyze_matches(state, fallback_title, year, media_type).await;
    if let Some(found) = matches
        .iter()
        .find(|m| {
            m.get("external_id")
                .and_then(|v| v.as_str())
                .map(|id| id.eq_ignore_ascii_case(external_id))
                .unwrap_or(false)
        })
        .cloned()
    {
        return Some(found);
    }

    import_helpers::lookup_import_media_id_with_fallback(
        &state.pool,
        external_id,
        media_type,
        fallback_title,
        year,
    )
    .await
    .map(|media_id| {
        json!({
            "media_id": media_id,
            "external_id": external_id,
            "title": fallback_title,
            "year": year,
            "type": media_type,
        })
    })
}

pub async fn show_metadata_review(state: &AppState, conv: &ConversationState) -> (String, Value) {
    let user_id = conv.user_id;
    let sel = conv.selected_match.clone().unwrap_or(json!({}));
    let analysis = conv.analysis_result.clone().unwrap_or(json!({}));
    let overrides = &conv.metadata_overrides;
    let media_type = conv.media_type.as_deref().unwrap_or("movie");
    let is_series = media_type == "series";
    let is_sports = media_type == "sports";

    let title = overrides
        .get("title")
        .or(sel.get("title"))
        .or(analysis.get("parsed_title"))
        .and_then(|v| v.as_str())
        .unwrap_or("Unknown");
    let year = sel
        .get("year")
        .and_then(|v| v.as_i64())
        .map(|y| format!(" ({y})"))
        .unwrap_or_default();

    let ext_id = sel
        .get("external_id")
        .and_then(|v| v.as_str())
        .unwrap_or("N/A");

    let size = analysis
        .get("total_size_readable")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .or_else(|| {
            analysis
                .get("total_size")
                .and_then(|v| v.as_i64())
                .map(text::bytes_readable)
        })
        .unwrap_or_else(|| "Unknown".to_string());

    let resolution = metadata_value("resolution", &analysis, overrides);
    let quality = metadata_value("quality", &analysis, overrides);
    let codec = metadata_value("codec", &analysis, overrides);
    let audio = metadata_value("audio", &analysis, overrides);
    let languages_list = selected_languages(&analysis, overrides);
    let languages = if languages_list.is_empty() {
        "Auto".to_string()
    } else {
        languages_list.join(", ")
    };

    let (season_number, episode_number, _) = episode_info(&analysis, overrides);
    let mut episode_line = String::new();
    if is_series {
        let s_display = season_number
            .map(|n| n.to_string())
            .unwrap_or_else(|| "?".into());
        let e_display = episode_number
            .map(|n| n.to_string())
            .unwrap_or_else(|| "?".into());
        episode_line = format!("📺 *Season:* {s_display} | *Episode:* {e_display}\n");
    }

    let poster_line = if conv.custom_poster_url.is_some() {
        "🖼️ *Poster:* Custom ✓\n"
    } else if is_sports {
        "🖼️ *Poster:* Auto (sports)\n"
    } else {
        ""
    };

    let escaped_title = text::escape_markdown(title);
    let escaped_ext_id = text::escape_markdown(ext_id);
    let msg = if is_sports {
        let category = conv.sports_category.as_deref().unwrap_or("Sports");
        format!(
            "📋 *Review Import Details*\n\n\
             🏆 *{escaped_title}*{year}\n\
             ⚽ *Category:* {category}\n\
             🆔 `{escaped_ext_id}`\n\n\
             📦 *Size:* {size}\n\
             📐 *Resolution:* {resolution}\n\
             🎞 *Quality:* {quality}\n\
             💿 *Codec:* {codec}\n\
             🔊 *Audio:* {audio}\n\
             🌐 *Languages:* {languages}\n\
             {poster_line}\n\
             _Tap a field to edit, or confirm to import._"
        )
    } else {
        format!(
            "📋 *Review Import Details*\n\n\
             🎬 *{escaped_title}*{year}\n\
             🆔 `{escaped_ext_id}`\n\n\
             {episode_line}\
             📦 *Size:* {size}\n\
             📐 *Resolution:* {resolution}\n\
             🎞 *Quality:* {quality}\n\
             💿 *Codec:* {codec}\n\
             🔊 *Audio:* {audio}\n\
             🌐 *Languages:* {languages}\n\
             {poster_line}\n\
             _Tap a field to edit, or confirm to import._"
        )
    };

    let poster_btn_text = if conv.custom_poster_url.is_some() {
        "🖼️ ✓ Poster"
    } else {
        "🖼️ Add Poster"
    };

    let mut rows: Vec<Value> = Vec::new();
    if is_series {
        let s_btn = season_number
            .map(|n| format!("S{n}"))
            .unwrap_or_else(|| "S?".to_string());
        let e_btn = episode_number
            .map(|n| format!("E{n}"))
            .unwrap_or_else(|| "E?".to_string());
        rows.push(json!([
            {"text": format!("📺 {s_btn}"), "callback_data": CallbackAction::MetaEdit { user_id, field: "season_number".into() }.encode(state).await},
            {"text": format!("📺 {e_btn}"), "callback_data": CallbackAction::MetaEdit { user_id, field: "episode_number".into() }.encode(state).await},
        ]));
    }

    rows.push(json!([
        {"text": format!("📐 {resolution}"), "callback_data": CallbackAction::MetaEdit { user_id, field: "resolution".into() }.encode(state).await},
        {"text": format!("🎞 {quality}"), "callback_data": CallbackAction::MetaEdit { user_id, field: "quality".into() }.encode(state).await},
    ]));
    rows.push(json!([
        {"text": format!("💿 {codec}"), "callback_data": CallbackAction::MetaEdit { user_id, field: "codec".into() }.encode(state).await},
        {"text": format!("🔊 {audio}"), "callback_data": CallbackAction::MetaEdit { user_id, field: "audio".into() }.encode(state).await},
    ]));
    rows.push(json!([
        {"text": format!("🌐 {languages}"), "callback_data": CallbackAction::MetaEdit { user_id, field: "languages".into() }.encode(state).await},
        {"text": poster_btn_text, "callback_data": CallbackAction::AddPoster { user_id }.encode(state).await},
    ]));
    rows.push(json!([{"text": "✅ Confirm Import", "callback_data": CallbackAction::Confirm { user_id }.encode(state).await}]));
    rows.push(json!([
        {"text": "◀️ Back", "callback_data": CallbackAction::Back { user_id }.encode(state).await},
        {"text": "❌ Cancel", "callback_data": CallbackAction::Cancel { user_id }.encode(state).await},
    ]));
    if conv.batch_item_id.is_some() {
        rows.push(json!([{
            "text": "← Back to batch",
            "callback_data": CallbackAction::BatchSummary { user_id }.encode(state).await,
        }]));
    }

    (msg, json!({ "inline_keyboard": rows }))
}

pub fn parse_external_id_from_text(text: &str) -> Option<String> {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| {
        regex::Regex::new(r"(?i)(?:tt\d{7,8}|(?:tmdb|tvdb|mal|kitsu):\d+)").unwrap()
    });
    re.find(text.trim()).map(|m| m.as_str().to_string())
}

pub fn sports_categories() -> &'static [(&'static str, &'static str)] {
    &[
        ("football", "⚽ Football"),
        ("basketball", "🏀 Basketball"),
        ("mma", "🥊 MMA"),
        ("boxing", "🥊 Boxing"),
        ("wrestling", "🤼 Wrestling"),
        ("motorsport", "🏎 Motorsport"),
        ("tennis", "🎾 Tennis"),
        ("hockey", "🏒 Hockey"),
        ("baseball", "⚾ Baseball"),
        ("other", "🏆 Other"),
    ]
}

pub async fn show_sports_category_picker(state: &AppState, user_id: i64) -> (String, Value) {
    let mut rows = Vec::new();
    for chunk in sports_categories().chunks(2) {
        let mut row = Vec::new();
        for (key, label) in chunk {
            row.push(json!({
                "text": label,
                "callback_data": CallbackAction::Sport {
                    user_id,
                    category: (*key).to_string(),
                }
                .encode(state)
                .await,
            }));
        }
        rows.push(json!(row));
    }
    rows.push(json!([{
        "text": "❌ Cancel",
        "callback_data": CallbackAction::Cancel { user_id }.encode(state).await,
    }]));
    (
        "⚽ *Select Sports Category*".to_string(),
        json!({ "inline_keyboard": rows }),
    )
}
