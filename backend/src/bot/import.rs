//! Execute confirmed imports via the unified contribution pipeline.

use serde_json::{Value, json};

use crate::{
    db::telegram as tg_db,
    routes::content::{contribution_processors, import_helpers},
    state::AppState,
};

use super::{
    api::BotApi,
    forwarded,
    metadata::{episode_info, metadata_value, selected_languages},
    model::{ContentType, ConversationState},
};

pub async fn execute_import(
    state: &AppState,
    api: &BotApi,
    conv: &ConversationState,
) -> Result<String, String> {
    let mf_user_id =
        tg_db::resolve_mediafusion_user_id(&state.pool, &state.redis, conv.user_id).await;

    let Some(uid) = mf_user_id else {
        return Err("Link your MediaFusion account with `/login` before importing.".to_string());
    };

    let user_info = import_helpers::fetch_user_info(&state.pool, i64::from(i32::from(uid)))
        .await
        .ok_or_else(|| "User not found".to_string())?;

    import_helpers::enforce_upload_permissions(
        &state.pool,
        &state.redis,
        i64::from(i32::from(uid)),
        user_info.uploads_restricted,
        &user_info.role,
    )
    .await
    .map_err(|(_, m)| m)?;

    let uid_i64 = i64::from(i32::from(uid));
    let is_privileged = matches!(user_info.role.as_str(), "moderator" | "admin");
    let is_anonymous = user_info.contribute_anonymously;
    let auto_approve = import_helpers::should_auto_approve_import(
        is_privileged,
        user_info.is_active,
        is_anonymous,
    );

    let content_type = conv.content_type.ok_or("Missing content type")?;
    let (contrib_type, mut data) =
        build_contribution_data(state, api, conv, uid_i64, is_anonymous)?;

    if content_type == ContentType::Video {
        forwarded::store_forwarded_video(state, api, conv, uid_i64, &user_info, &data).await?;
    }

    let target_id = data
        .get("info_hash")
        .or_else(|| data.get("meta_id"))
        .and_then(|v| v.as_str())
        .map(str::to_string);

    let contribution_id = import_helpers::create_contribution_record(
        &state.pool,
        Some(uid_i64),
        contrib_type,
        target_id.as_deref(),
        &data,
        auto_approve,
        is_privileged,
    )
    .await
    .map_err(|e| e.to_string())?;

    if auto_approve {
        match contribution_processors::process_contribution_import(
            state,
            contrib_type,
            &mut data,
            Some(uid_i64),
            &user_info.username,
        )
        .await
        {
            Ok(result) => {
                import_helpers::award_contribution_points(&state.pool, uid_i64, contrib_type).await;
                Ok(format!(
                    "✅ *Import Successful!*\n\nContribution `{contribution_id}` approved.\n{}",
                    result.message.unwrap_or_default()
                ))
            }
            Err(e) => Err(e.message()),
        }
    } else {
        if let (Some(token), Some(chat_id)) = (
            state.config.telegram_bot_token.as_deref(),
            state.config.telegram_chat_id.as_deref(),
        ) {
            import_helpers::notify_pending_contribution(
                &state.http,
                token,
                chat_id,
                &state.config.host_url,
                contrib_type,
                &user_info.username,
                &data,
            )
            .await;
        }
        Ok(format!(
            "📋 *Submitted for Review*\n\nYour {} import is pending moderator approval.",
            contrib_type
        ))
    }
}

fn merge_fields(mut base: Value, common: &Value) -> Value {
    if let (Some(base_obj), Some(common_obj)) = (base.as_object_mut(), common.as_object()) {
        for (key, value) in common_obj {
            base_obj.insert(key.clone(), value.clone());
        }
    }
    base
}

fn build_contribution_data(
    _state: &AppState,
    _api: &BotApi,
    conv: &ConversationState,
    _user_id: i64,
    is_anonymous: bool,
) -> Result<(&'static str, Value), String> {
    let analysis = conv.analysis_result.clone().unwrap_or(json!({}));
    let selected = conv.selected_match.clone().unwrap_or(json!({}));
    let overrides = &conv.metadata_overrides;
    let media_type = conv.media_type.as_deref().unwrap_or("movie");
    let meta_type = if media_type == "series" {
        "series"
    } else {
        "movie"
    };
    let meta_id = selected
        .get("external_id")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let title = selected
        .get("title")
        .or_else(|| analysis.get("parsed_title"))
        .and_then(|v| v.as_str())
        .unwrap_or("Unknown");

    let field_or_analysis = |field: &str| -> Value {
        let value = metadata_value(field, &analysis, overrides);
        if value == "Auto" {
            analysis.get(field).cloned().unwrap_or(Value::Null)
        } else {
            json!(value)
        }
    };

    let languages = selected_languages(&analysis, overrides);
    let (season_number, episode_number, episode_end) = episode_info(&analysis, overrides);
    let is_public = import_helpers::stream_is_public_on_submit(true, true);

    let mut common = json!({
        "title": title,
        "meta_type": meta_type,
        "meta_id": meta_id,
        "resolution": field_or_analysis("resolution"),
        "quality": field_or_analysis("quality"),
        "codec": field_or_analysis("codec"),
        "audio": field_or_analysis("audio"),
        "languages": languages,
        "season_number": season_number,
        "episode_number": episode_number,
        "episode_end": episode_end,
        "is_anonymous": is_anonymous,
        "is_public": is_public,
        "sports_category": conv.sports_category,
    });
    if is_anonymous && let Some(name) = &conv.anonymous_display_name {
        common["anonymous_display_name"] = json!(name);
    }
    if let Some(poster) = &conv.custom_poster_url {
        common["custom_poster_url"] = json!(poster);
    }

    let content_type = conv.content_type.ok_or("Missing content type")?;
    match content_type {
        ContentType::Magnet => Ok((
            "torrent",
            merge_fields(
                json!({
                    "info_hash": analysis.get("info_hash").and_then(|v| v.as_str()),
                    "name": analysis.get("torrent_name").and_then(|v| v.as_str()).unwrap_or(title),
                    "total_size": analysis.get("total_size"),
                    "file_data": analysis.get("files").cloned().unwrap_or(json!([])),
                    "file_count": analysis.get("file_count").cloned().unwrap_or(json!(1)),
                    "contributor_user_id": conv.user_id,
                }),
                &common,
            ),
        )),
        ContentType::TorrentFile | ContentType::TorrentUrl => Ok((
            "torrent",
            merge_fields(
                json!({
                    "info_hash": analysis.get("info_hash").and_then(|v| v.as_str()),
                    "name": analysis.get("torrent_name").and_then(|v| v.as_str()).unwrap_or(title),
                    "total_size": analysis.get("total_size"),
                    "file_data": analysis.get("files").cloned().unwrap_or(json!([])),
                    "file_count": analysis.get("file_count").cloned().unwrap_or(json!(1)),
                }),
                &common,
            ),
        )),
        ContentType::Nzb => Ok((
            "nzb",
            merge_fields(
                json!({
                    "url": conv.raw_input.as_str().or_else(|| analysis.get("nzb_url").and_then(|v| v.as_str())),
                }),
                &common,
            ),
        )),
        ContentType::Youtube => Ok((
            "youtube",
            merge_fields(
                json!({
                    "url": conv.raw_input.get("url").and_then(|v| v.as_str())
                        .or_else(|| analysis.get("url").and_then(|v| v.as_str())),
                    "video_id": conv.raw_input.get("video_id").and_then(|v| v.as_str())
                        .or_else(|| analysis.get("video_id").and_then(|v| v.as_str())),
                }),
                &common,
            ),
        )),
        ContentType::Http => Ok((
            "http",
            merge_fields(
                json!({
                    "url": conv.raw_input.as_str().or_else(|| analysis.get("url").and_then(|v| v.as_str())),
                }),
                &common,
            ),
        )),
        ContentType::Acestream => Ok((
            "acestream",
            merge_fields(
                json!({
                    "acestream_id": conv.raw_input.as_str()
                        .or_else(|| analysis.get("content_id").and_then(|v| v.as_str())),
                }),
                &common,
            ),
        )),
        ContentType::Video => Ok((
            "telegram",
            merge_fields(
                json!({
                    "file_id": conv.raw_input.get("file_id").and_then(|v| v.as_str()),
                    "file_unique_id": conv.raw_input.get("file_unique_id").and_then(|v| v.as_str()),
                    "file_name": conv.raw_input.get("file_name").and_then(|v| v.as_str()),
                    "file_size": conv.raw_input.get("file_size").cloned()
                        .or_else(|| analysis.get("file_size").cloned()),
                    "user_id": conv.user_id,
                }),
                &common,
            ),
        )),
    }
}
