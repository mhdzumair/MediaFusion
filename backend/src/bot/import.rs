//! Execute confirmed imports via the unified contribution pipeline.

use serde_json::{json, Value};

use crate::{
    db::telegram as tg_db,
    routes::content::{
        contribution_processors, import_helpers,
    },
    state::AppState,
};

use super::{
    api::BotApi,
    forwarded,
    model::{ContentType, ConversationState},
};

pub async fn execute_import(
    state: &AppState,
    api: &BotApi,
    conv: &ConversationState,
) -> Result<String, String> {
    let mf_user_id = tg_db::resolve_mediafusion_user_id(
        &state.pool,
        &state.redis,
        conv.user_id,
    )
    .await;

    let user_info = if let Some(uid) = mf_user_id {
        import_helpers::fetch_user_info(&state.pool, uid)
            .await
            .ok_or_else(|| "User not found".to_string())?
    } else {
        return Err("Link your MediaFusion account with `/login` before importing.".to_string());
    };

    if mf_user_id.is_some() {
        import_helpers::enforce_upload_permissions(
            &state.pool,
            &state.redis,
            mf_user_id.unwrap(),
            user_info.uploads_restricted,
            &user_info.role,
        )
        .await
        .map_err(|(_, m)| m)?;
    }

    let uid = mf_user_id.unwrap();
    let is_privileged = matches!(user_info.role.as_str(), "moderator" | "admin");
    let is_anonymous = user_info.contribute_anonymously;
    let auto_approve =
        import_helpers::should_auto_approve_import(is_privileged, user_info.is_active, is_anonymous);

    let content_type = conv.content_type.ok_or("Missing content type")?;
    let (contrib_type, mut data) = build_contribution_data(state, api, conv, uid, is_anonymous)?;

    if content_type == ContentType::Video {
        forwarded::store_forwarded_video(state, api, conv, uid, &user_info, &data).await?;
    }

    let target_id = data
        .get("info_hash")
        .or_else(|| data.get("meta_id"))
        .and_then(|v| v.as_str())
        .map(str::to_string);

    let contribution_id = import_helpers::create_contribution_record(
        &state.pool,
        Some(uid),
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
            Some(uid),
            &user_info.username,
        )
        .await
        {
            Ok(result) => {
                import_helpers::award_contribution_points(&state.pool, uid, contrib_type).await;
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

fn build_contribution_data(
    _state: &AppState,
    _api: &BotApi,
    conv: &ConversationState,
    _user_id: i64,
    is_anonymous: bool,
) -> Result<(&'static str, Value), String> {
    let analysis = conv.analysis_result.clone().unwrap_or(json!({}));
    let selected = conv.selected_match.clone().unwrap_or(json!({}));
    let media_type = conv.media_type.as_deref().unwrap_or("movie");
    let meta_id = selected
        .get("external_id")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let title = selected
        .get("title")
        .or_else(|| analysis.get("parsed_title"))
        .and_then(|v| v.as_str())
        .unwrap_or("Unknown");

    let is_public = import_helpers::stream_is_public_on_submit(true, true);

    let content_type = conv.content_type.ok_or("Missing content type")?;
    match content_type {
        ContentType::Magnet => Ok((
            "torrent",
            json!({
                "info_hash": analysis.get("info_hash").and_then(|v| v.as_str()),
                "name": analysis.get("torrent_name").and_then(|v| v.as_str()).unwrap_or(title),
                "title": title,
                "meta_type": media_type,
                "meta_id": meta_id,
                "total_size": analysis.get("total_size"),
                "file_data": analysis.get("files").cloned().unwrap_or(json!([])),
                "resolution": analysis.get("resolution"),
                "codec": analysis.get("codec"),
                "quality": analysis.get("quality"),
                "is_anonymous": is_anonymous,
                "is_public": is_public,
                "sports_category": conv.sports_category,
                "contributor_user_id": conv.user_id,
            }),
        )),
        ContentType::TorrentFile | ContentType::TorrentUrl => Ok((
            "torrent",
            json!({
                "info_hash": analysis.get("info_hash").and_then(|v| v.as_str()),
                "name": analysis.get("torrent_name").and_then(|v| v.as_str()).unwrap_or(title),
                "title": title,
                "meta_type": media_type,
                "meta_id": meta_id,
                "total_size": analysis.get("total_size"),
                "file_data": analysis.get("files").cloned().unwrap_or(json!([])),
                "is_anonymous": is_anonymous,
                "is_public": is_public,
                "sports_category": conv.sports_category,
            }),
        )),
        ContentType::Nzb => Ok((
            "nzb",
            json!({
                "url": conv.raw_input.as_str(),
                "title": title,
                "meta_type": media_type,
                "meta_id": meta_id,
                "is_anonymous": is_anonymous,
                "is_public": is_public,
            }),
        )),
        ContentType::Youtube => Ok((
            "youtube",
            json!({
                "url": conv.raw_input.get("url").and_then(|v| v.as_str()),
                "video_id": conv.raw_input.get("video_id").and_then(|v| v.as_str()),
                "title": title,
                "meta_type": media_type,
                "meta_id": meta_id,
                "is_anonymous": is_anonymous,
                "is_public": is_public,
            }),
        )),
        ContentType::Http => Ok((
            "http",
            json!({
                "url": conv.raw_input.as_str(),
                "title": title,
                "meta_type": media_type,
                "meta_id": meta_id,
                "is_anonymous": is_anonymous,
                "is_public": is_public,
            }),
        )),
        ContentType::Acestream => Ok((
            "acestream",
            json!({
                "acestream_id": conv.raw_input.as_str(),
                "title": title,
                "meta_type": media_type,
                "meta_id": meta_id,
                "is_anonymous": is_anonymous,
                "is_public": is_public,
            }),
        )),
        ContentType::Video => Ok((
            "telegram",
            json!({
                "file_id": conv.raw_input.get("file_id").and_then(|v| v.as_str()),
                "file_unique_id": conv.raw_input.get("file_unique_id").and_then(|v| v.as_str()),
                "file_name": conv.raw_input.get("file_name").and_then(|v| v.as_str()),
                "meta_id": meta_id,
                "meta_type": media_type,
                "title": title,
                "is_public": is_public,
                "user_id": conv.user_id,
            }),
        )),
    }
}
