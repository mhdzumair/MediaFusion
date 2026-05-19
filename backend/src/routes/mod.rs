pub mod admin;
pub mod admin_database;
pub mod admin_extended;
pub mod admin_keyword_filters;
pub mod admin_metrics;
pub mod admin_scrapers;
pub mod auth;
pub mod catalog;
pub mod configure;
pub mod content;
pub mod downloads;
pub mod encrypt;
pub mod health;
pub mod indexers;
pub mod instance;
pub mod integrations;
pub mod kodi_setup;
pub mod kodi_stream;
pub mod manifest;
pub mod meta;
pub mod metrics;
pub mod moderator;
pub mod playback;
pub mod poster;
pub mod profiles;
pub mod rss;
pub mod stream;
pub mod streaming_provider;
pub mod telegram_playback;
pub mod telegram_webhook;
pub mod torznab;
pub mod usenet;
pub mod user_catalogs;
pub mod user_library;
pub mod user_management;
pub mod watch_history;
pub mod watchlist;

use std::sync::Arc;

use axum::{
    body::Body,
    http::{header, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{delete, get, patch, post, put},
    Router,
};
use tower_http::{
    compression::CompressionLayer,
    cors::CorsLayer,
    services::{ServeDir, ServeFile},
    timeout::TimeoutLayer,
};

use crate::api_error_middleware::api_error_middleware;
use crate::api_key_middleware::api_key_middleware;
use crate::make_trace_layer;
use crate::metrics_middleware::metrics_middleware;
use crate::state::AppState;
use crate::stremio_auth_middleware::stremio_auth_middleware;

async fn root_redirect() -> impl IntoResponse {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, "/app")
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

/// Sets `Cache-Control: no-cache` on HTML responses so browsers always revalidate
/// `index.html` after a deploy. Hashed asset files are left alone (immutable by
/// default via their content-addressed filenames).
async fn spa_cache_headers(response: Response) -> Response {
    let mut response = response;
    let is_html = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .map(|v| v.contains("text/html"))
        .unwrap_or(false);
    if is_html {
        response.headers_mut().insert(
            header::CACHE_CONTROL,
            HeaderValue::from_static("no-cache, no-store, must-revalidate"),
        );
    }
    response
}

pub fn router(state: Arc<AppState>) -> Router {
    let resources_dir = state.config.resources_dir.clone();
    let stream_timeout = std::time::Duration::from_secs(state.config.request_timeout);

    let api_router = Router::new()
        // ── Health ───────────────────────────────────────────────────────────
        .route("/health", get(health::handler))
        .route("/ready", get(health::handler))
        // ── Configure ────────────────────────────────────────────────────────
        .route("/configure", get(configure::handler))
        .route("/{secret_str}/configure", get(configure::handler))
        // ── Manifest ─────────────────────────────────────────────────────────
        .route("/manifest.json", get(manifest::public_manifest))
        .route("/{secret_str}/manifest.json", get(manifest::user_manifest))
        // ── Catalog (public + user) ───────────────────────────────────────────
        .route("/catalog/{media_type}/{*rest}", get(catalog::public_catalog))
        .route(
            "/{secret_str}/catalog/{media_type}/{*rest}",
            get(catalog::user_catalog),
        )
        // ── Meta ─────────────────────────────────────────────────────────────
        .route("/meta/{media_type}/{raw_id}", get(meta::public_meta))
        .route(
            "/{secret_str}/meta/{media_type}/{raw_id}",
            get(meta::user_meta),
        )
        // ── Stream ───────────────────────────────────────────────────────────
        .route("/stream/movie/{video_id}", get(stream::public_movie))
        .route("/stream/series/{video_id}", get(stream::public_series))
        .route("/stream/tv/{video_id}", get(stream::public_tv))
        .route("/{secret_str}/stream/movie/{video_id}", get(stream::movie))
        .route("/{secret_str}/stream/series/{video_id}", get(stream::series))
        .route("/{secret_str}/stream/tv/{video_id}", get(stream::tv))
        // ── Poster ───────────────────────────────────────────────────────────
        .route("/poster/{media_type}/{id_jpg}", get(poster::handler))
        // ── Kodi stream (with pagination) ─────────────────────────────────────
        .route("/kodi/stream/movie/{video_id}", get(kodi_stream::movie))
        .route("/kodi/stream/series/{video_id}", get(kodi_stream::series))
        .route("/{secret_str}/kodi/stream/movie/{video_id}", get(kodi_stream::user_movie))
        .route("/{secret_str}/kodi/stream/series/{video_id}", get(kodi_stream::user_series))
        // ── Torznab feed ─────────────────────────────────────────────────────
        .route("/torznab", get(torznab::handler))
        .route("/torznab/api", get(torznab::handler))
        // ── Encrypt / Decrypt user data ───────────────────────────────────────
        .route("/encrypt-user-data", post(encrypt::handler))
        .route("/encrypt-user-data/{existing_secret_str}", post(encrypt::handler))
        .route("/decrypt-user-data/{secret_str}", get(encrypt::decrypt_handler))
        // ── Usenet playback (public — credentials embedded in NZB URL) ────────
        .route("/usenet/{nzb_guid}", get(usenet::handler))
        // ── Streaming provider namespace ──────────────────────────────────────
        // Debrid playback proxy
        .route(
            "/streaming_provider/{secret_str}/playback/{provider_name}/{info_hash}",
            get(playback::handler_base),
        )
        .route(
            "/streaming_provider/{secret_str}/playback/{provider_name}/{info_hash}/{filename}",
            get(playback::handler_with_filename),
        )
        .route(
            "/streaming_provider/{secret_str}/playback/{provider_name}/{info_hash}/{season}/{episode}",
            get(playback::handler_seep),
        )
        .route(
            "/streaming_provider/{secret_str}/playback/{provider_name}/{info_hash}/{season}/{episode}/{filename}",
            get(playback::handler_seep_filename),
        )
        // Usenet NZB proxy (providers fetch NZB bytes through this endpoint)
        .route(
            "/streaming_provider/{secret_str}/usenet/nzb/{nzb_guid}",
            get(usenet::nzb_proxy_handler),
        )
        // Usenet via provider
        .route(
            "/streaming_provider/{secret_str}/usenet/{provider_name}/{nzb_guid}",
            get(usenet::provider_handler),
        )
        .route(
            "/streaming_provider/{secret_str}/usenet/{provider_name}/{nzb_guid}/{season}/{episode}",
            get(usenet::provider_seep_handler),
        )
        // Delete all watchlist
        .route(
            "/streaming_provider/{secret_str}/delete_all_watchlist",
            get(watchlist::delete_all_handler),
        )
        // Telegram playback
        .route(
            "/streaming_provider/{secret_str}/telegram/stream/{telegram_stream_id}",
            get(telegram_playback::handler_by_stream_id),
        )
        .route(
            "/streaming_provider/{secret_str}/telegram/{chat_id}/{message_id}",
            get(telegram_playback::handler_by_chat_message),
        )
        // ── Telegram bot webhook ──────────────────────────────────────────────
        .route("/api/v1/telegram/webhook", post(telegram_webhook::handler))
        // ── Streaming provider cache ──────────────────────────────────────────
        .route(
            "/streaming_provider/cache/status",
            post(streaming_provider::check_cache_status),
        )
        .route(
            "/streaming_provider/cache/submit",
            post(streaming_provider::submit_cached_hashes),
        )
        // ── Streaming provider OAuth / device-code auth ───────────────────────
        .route("/streaming_provider/realdebrid/get-device-code", get(streaming_provider::realdebrid_get_device_code))
        .route("/streaming_provider/realdebrid/authorize", post(streaming_provider::realdebrid_authorize))
        .route("/streaming_provider/debridlink/get-device-code", get(streaming_provider::debridlink_get_device_code))
        .route("/streaming_provider/debridlink/authorize", post(streaming_provider::debridlink_authorize))
        .route("/streaming_provider/premiumize/authorize", get(streaming_provider::premiumize_authorize))
        // ── User profiles ─────────────────────────────────────────────────────
        .route(
            "/api/v1/profiles/user-config",
            get(profiles::user_config),
        )
        .route(
            "/api/v1/profiles/rpdb-key",
            get(profiles::rpdb_key),
        )
        .route(
            "/api/v1/profiles",
            get(profiles::list_profiles).post(profiles::create_profile),
        )
        .route(
            "/api/v1/profiles/{id}",
            get(profiles::get_profile)
                .put(profiles::update_profile)
                .delete(profiles::delete_profile),
        )
        .route(
            "/api/v1/profiles/{id}/set-default",
            post(profiles::set_default),
        )
        .route(
            "/api/v1/profiles/{id}/reset-uuid",
            post(profiles::reset_uuid),
        )
        .route(
            "/api/v1/profiles/{id}/manifest-url",
            get(profiles::manifest_url),
        )
        .route(
            "/api/v1/profiles/{id}/kodi-addon",
            get(profiles::kodi_addon),
        )
        // ── Watch history ─────────────────────────────────────────────────────
        .route(
            "/api/v1/watch-history",
            get(watch_history::list_watch_history)
                .post(watch_history::create_watch_history)
                .delete(watch_history::clear_history),
        )
        .route(
            "/api/v1/watch-history/continue-watching",
            get(watch_history::continue_watching),
        )
        .route(
            "/api/v1/watch-history/track",
            post(watch_history::track_action),
        )
        .route(
            "/api/v1/watch-history/{id}",
            patch(watch_history::update_progress).delete(watch_history::delete_entry),
        )
        // ── User auth ─────────────────────────────────────────────────────────
        .route("/api/v1/auth/register", post(auth::register))
        .route("/api/v1/auth/login", post(auth::login))
        .route("/api/v1/auth/refresh", post(auth::refresh))
        .route("/api/v1/auth/logout", post(auth::logout))
        .route("/api/v1/auth/verify-email", post(auth::verify_email))
        .route("/api/v1/auth/resend-verification", post(auth::resend_verification))
        .route("/api/v1/auth/forgot-password", post(auth::forgot_password))
        .route("/api/v1/auth/reset-password", post(auth::reset_password))
        .route("/api/v1/auth/change-password", post(auth::change_password))
        .route("/api/v1/auth/me", get(auth::get_me).patch(auth::update_me).delete(auth::delete_account))
        .route("/api/v1/users/me", get(auth::get_me).patch(auth::update_me))
        // ── Torrent import ────────────────────────────────────────────────────
        .route("/api/v1/import/magnet/analyze", post(content::torrent_import::analyze_magnet))
        .route("/api/v1/import/torrent/analyze", post(content::torrent_import::analyze_torrent))
        .route("/api/v1/import/magnet", post(content::torrent_import::import_magnet))
        .route("/api/v1/import/torrent", post(content::torrent_import::import_torrent))
        // ── NZB import ────────────────────────────────────────────────────────
        .route("/api/v1/import/nzb/analyze/file", post(content::nzb_import::analyze_nzb_file))
        .route("/api/v1/import/nzb/analyze/url", post(content::nzb_import::analyze_nzb_url))
        .route("/api/v1/import/nzb", post(content::nzb_import::import_nzb))
        .route("/api/v1/import/nzb/url", post(content::nzb_import::import_nzb_url))
        .route("/api/v1/import/nzb/{guid}/download", get(content::nzb_import::download_nzb))
        // ── M3U import ────────────────────────────────────────────────────────
        .route("/api/v1/import/m3u/analyze", post(content::m3u_import::analyze_m3u))
        .route("/api/v1/import/m3u", post(content::m3u_import::import_m3u))
        .route("/api/v1/import/job/{job_id}", get(content::m3u_import::get_import_job_status))
        .route("/api/v1/import/iptv-settings", get(content::m3u_import::get_iptv_settings_handler))
        // ── HTTP stream import ────────────────────────────────────────────────
        .route("/api/v1/import/http/extractors", get(content::http_import::get_mediaflow_extractors))
        .route("/api/v1/import/http/analyze", post(content::http_import::analyze_http_url))
        .route("/api/v1/import/http", post(content::http_import::import_http_stream))
        // ── Xtream import ─────────────────────────────────────────────────────
        .route("/api/v1/import/xtream/analyze", post(content::xtream_import::analyze_xtream))
        .route("/api/v1/import/xtream", post(content::xtream_import::import_xtream))
        // ── AceStream import ──────────────────────────────────────────────────
        .route("/api/v1/import/acestream/analyze", post(content::acestream_import::analyze_acestream))
        .route("/api/v1/import/acestream", post(content::acestream_import::import_acestream))
        // ── YouTube import ────────────────────────────────────────────────────
        .route("/api/v1/import/youtube/analyze", post(content::youtube_import::analyze_youtube_url))
        .route("/api/v1/import/youtube", post(content::youtube_import::import_youtube_video))
        // ── IPTV source management ────────────────────────────────────────────
        .route("/api/v1/import/sources", get(content::iptv_sources::list_iptv_sources))
        .route(
            "/api/v1/import/sources/{source_id}",
            get(content::iptv_sources::get_iptv_source)
                .patch(content::iptv_sources::update_iptv_source)
                .delete(content::iptv_sources::delete_iptv_source),
        )
        .route("/api/v1/import/sources/{source_id}/sync", post(content::iptv_sources::sync_iptv_source))
        // ── Image upload ──────────────────────────────────────────────────────
        .route("/api/v1/import/images/upload", post(content::image_upload::upload_image))
        .route("/api/v1/import/images/{*key}", get(content::image_upload::get_uploaded_image))
        // ── Metadata reference data ───────────────────────────────────────────
        .route("/api/v1/metadata/reference/genres", get(content::reference::list_genres))
        .route("/api/v1/metadata/reference/catalogs", get(content::reference::list_catalogs))
        .route("/api/v1/metadata/reference/stars", get(content::reference::list_stars))
        .route("/api/v1/metadata/reference/parental-certificates", get(content::reference::list_parental_certificates))
        // ── User metadata (must be before /{media_id} catch-all) ─────────────
        .route("/api/v1/metadata/user", get(content::user_metadata::list_user_metadata).post(content::user_metadata::create_user_metadata))
        .route("/api/v1/metadata/user/search/all", get(content::user_metadata::search_all_metadata))
        .route("/api/v1/metadata/user/import", post(content::user_metadata::create_user_metadata))
        .route("/api/v1/metadata/user/{media_id}", get(content::user_metadata::get_user_metadata).put(content::user_metadata::update_user_metadata).delete(content::user_metadata::delete_user_metadata))
        .route("/api/v1/metadata/user/{media_id}/seasons", post(content::user_metadata::add_season_to_series))
        .route("/api/v1/metadata/user/{media_id}/episodes", post(content::user_metadata::add_episodes_to_series))
        .route("/api/v1/metadata/user/{media_id}/episodes/{episode_id}", put(content::user_metadata::update_episode).delete(content::user_metadata::delete_episode))
        .route("/api/v1/metadata/user/{media_id}/episodes/{episode_id}/admin", delete(content::user_metadata::admin_delete_episode))
        .route("/api/v1/metadata/user/{media_id}/seasons/{season_number}", delete(content::user_metadata::delete_season))
        .route("/api/v1/metadata/user/{media_id}/seasons/{season_number}/admin", delete(content::user_metadata::admin_delete_season))
        .route("/api/v1/metadata/user/import/preview", post(content::user_metadata::import_user_metadata_preview))
        // ── Metadata operations ───────────────────────────────────────────────
        .route("/api/v1/metadata/search", get(content::metadata_ops::search_metadata))
        .route("/api/v1/metadata/search-external", post(content::metadata_ops::search_external_metadata))
        .route("/api/v1/metadata/{media_id}/refresh", post(content::metadata_ops::refresh_metadata))
        .route("/api/v1/metadata/{media_id}/link", post(content::metadata_ops::link_external_id))
        .route("/api/v1/metadata/{media_id}/link-external", post(content::metadata_ops::link_external_id))
        .route("/api/v1/metadata/{media_id}/link-multiple", post(content::metadata_ops::link_multiple_external_ids))
        .route("/api/v1/metadata/{media_id}/migrate", post(content::metadata_ops::migrate_media_id))
        .route("/api/v1/metadata/{media_id}/suggest", post(content::suggestions::create_suggestion))
        .route("/api/v1/metadata/{media_id}", get(content::metadata_ops::get_media_metadata))
        // ── Contributions ─────────────────────────────────────────────────────
        .route("/api/v1/contributions", get(content::contributions::list_contributions).post(content::contributions::create_contribution))
        .route("/api/v1/contributions/me", get(content::suggestions::get_my_contribution_info))
        .route("/api/v1/contributions/stats", get(content::contributions::get_contribution_stats))
        .route("/api/v1/contributions/contributors", get(content::contributions::list_contribution_contributors))
        .route("/api/v1/contributions/review/pending", get(content::contributions::list_pending_contributions))
        .route("/api/v1/contributions/review/bulk", post(content::contributions::bulk_review_contributions))
        .route("/api/v1/contributions/review/stats", get(content::contributions::get_all_contribution_stats))
        .route("/api/v1/contributions/{contribution_id}", get(content::contributions::get_contribution).delete(content::contributions::delete_contribution))
        .route("/api/v1/contributions/{contribution_id}/review", patch(content::contributions::review_contribution))
        .route("/api/v1/contributions/{contribution_id}/flag-admin-review", patch(content::contributions::flag_contribution_for_admin_review))
        .route("/api/v1/contributions/{contribution_id}/reject-approved", patch(content::contributions::reject_approved_contribution))
        // ── Stream Linking ────────────────────────────────────────────────────
        .route("/api/v1/stream-links", post(content::stream_linking::create_stream_link))
        .route("/api/v1/stream-links/bulk", post(content::stream_linking::create_bulk_stream_links))
        .route("/api/v1/stream-links/search", get(content::stream_linking::search_unlinked_streams))
        .route("/api/v1/stream-links/files", put(content::stream_linking::update_file_links))
        .route("/api/v1/stream-links/needs-annotation", get(content::stream_linking::get_streams_needing_annotation))
        .route("/api/v1/stream-links/needs-annotation/{stream_id}/media/{media_id}/dismiss", post(content::stream_linking::dismiss_annotation_request))
        .route("/api/v1/stream-links/{link_id}", delete(content::stream_linking::delete_stream_link))
        .route("/api/v1/stream-links/stream/{stream_id}", get(content::stream_linking::get_media_for_stream))
        .route("/api/v1/stream-links/media/{media_id}", get(content::stream_linking::get_streams_for_media))
        .route("/api/v1/stream-links/files/{stream_id}", get(content::stream_linking::get_stream_file_links))
        .route("/api/v1/stream-links/stream/{stream_id}/files", get(content::stream_linking::get_stream_files_for_annotation))
        // ── Stream Suggestions ────────────────────────────────────────────────
        .route("/api/v1/stream-suggestions", get(content::stream_suggestions::list_my_stream_suggestions))
        .route("/api/v1/stream-suggestions/stats", get(content::stream_suggestions::get_stream_suggestion_stats))
        .route("/api/v1/stream-suggestions/pending", get(content::stream_suggestions::list_pending_stream_suggestions))
        .route("/api/v1/stream-suggestions/bulk-review", post(content::stream_suggestions::bulk_review_stream_suggestions))
        .route("/api/v1/stream-suggestions/{suggestion_id}", get(content::stream_suggestions::get_stream_suggestion).delete(content::stream_suggestions::delete_stream_suggestion))
        .route("/api/v1/stream-suggestions/{suggestion_id}/review", put(content::stream_suggestions::review_stream_suggestion))
        .route("/api/v1/stream-suggestions/{suggestion_id}/triage", patch(content::stream_suggestions::triage_stream_suggestion))
        .route("/api/v1/streams/{stream_id}/suggest", post(content::stream_suggestions::create_stream_suggestion))
        .route("/api/v1/streams/{stream_id}/signals", get(content::stream_suggestions::get_stream_signals))
        .route("/api/v1/streams/signals/bulk", post(content::stream_suggestions::bulk_stream_signals))
        .route("/api/v1/streams/{stream_id}/editable-fields", get(content::stream_suggestions::get_stream_editable_fields))
        .route("/api/v1/streams/{stream_id}/suggestions", get(content::stream_suggestions::list_stream_suggestions))
        .route("/api/v1/streams/{stream_id}/broken-status", get(content::stream_suggestions::get_stream_broken_status).patch(content::stream_suggestions::update_stream_broken_status))
        .route("/api/v1/stream-suggestions/{suggestion_id}/issue-triage", patch(content::stream_suggestions::triage_stream_suggestion))
        // ── Episode Suggestions ───────────────────────────────────────────────
        .route("/api/v1/episode/{episode_id}/suggest", post(content::episode_suggestions::create_episode_suggestion))
        .route("/api/v1/episode-suggestions", get(content::episode_suggestions::list_my_episode_suggestions))
        .route("/api/v1/episode-suggestions/stats", get(content::episode_suggestions::get_episode_suggestion_stats))
        .route("/api/v1/episode-suggestions/pending", get(content::episode_suggestions::list_pending_episode_suggestions))
        .route("/api/v1/episode-suggestions/bulk-review", post(content::episode_suggestions::bulk_review_episode_suggestions))
        .route("/api/v1/episode-suggestions/{suggestion_id}", get(content::episode_suggestions::get_episode_suggestion).delete(content::episode_suggestions::delete_episode_suggestion))
        .route("/api/v1/episode-suggestions/{suggestion_id}/review", put(content::episode_suggestions::review_episode_suggestion))
        // ── Old suggestions aliases ───────────────────────────────────────────
        .route("/api/v1/stream-suggestions/my", get(content::suggestions::list_my_suggestions))
        .route("/api/v1/suggestions", get(content::suggestions::list_my_suggestions))
        .route("/api/v1/suggestions/my", get(content::suggestions::list_my_suggestions))
        .route("/api/v1/suggestions/pending", get(content::suggestions::list_pending_suggestions))
        .route("/api/v1/suggestions/bulk-review", post(content::suggestions::bulk_review_suggestions))
        .route("/api/v1/suggestions/stats", get(content::suggestions::get_suggestion_stats))
        .route(
            "/api/v1/suggestions/{suggestion_id}",
            get(content::suggestions::get_suggestion)
                .delete(content::suggestions::delete_suggestion),
        )
        .route("/api/v1/suggestions/{suggestion_id}/review", put(content::suggestions::review_suggestion))
        // ── Scraping ──────────────────────────────────────────────────────────
        .route("/api/v1/scraping/scrapers", get(content::scraping::list_scrapers))
        .route("/api/v1/scraping/status", get(content::scraping::get_scrape_status))
        .route("/api/v1/scraping/trigger", post(content::scraping::trigger_scrape))
        .route("/api/v1/scraping/{media_id}/status", get(content::scraping::get_scrape_status_by_media))
        .route("/api/v1/scraping/{media_id}/scrape", post(content::scraping::trigger_scrape_by_media))
        // ── Discover ──────────────────────────────────────────────────────────
        .route("/api/v1/discover/trending", get(content::discover::discover_trending))
        .route("/api/v1/discover/list", get(content::discover::discover_list))
        .route("/api/v1/discover/watch-providers", get(content::discover::discover_watch_providers))
        .route("/api/v1/discover/provider-feed", get(content::discover::discover_provider_feed))
        .route("/api/v1/discover/anime", get(content::discover::discover_anime))
        .route("/api/v1/discover/search", get(content::discover::discover_search))
        .route("/api/v1/discover/tvdb-filter", get(content::discover::discover_tvdb_filter))
        .route("/api/v1/discover/mdblist", get(content::discover::discover_mdblist))
        .route("/api/v1/discover/verify-tmdb-key", get(content::discover::verify_tmdb_key))
        // ── Catalog browse ────────────────────────────────────────────────────
        .route("/api/v1/catalog/available", get(content::catalog_browse::get_available_catalogs))
        .route("/api/v1/catalog/genres", get(content::catalog_browse::get_genres))
        .route("/api/v1/catalog/search", get(content::catalog_browse::search_catalog))
        .route("/api/v1/catalog/{catalog_type}", get(content::catalog_browse::browse_catalog))
        .route("/api/v1/catalog/{catalog_type}/{media_id}", get(content::catalog_browse::get_media_detail))
        .route("/api/v1/catalog/{catalog_type}/{media_id}/streams", get(content::catalog_browse::get_media_streams))
        .route("/api/v1/catalog/{catalog_type}/{media_id}/streams/{stream_id}/report", post(content::catalog_browse::report_stream))
        // ── Content stream management ─────────────────────────────────────────
        .route(
            "/api/v1/streams/{stream_id}",
            delete(content::streams::delete_stream),
        )
        // ── Content voting ────────────────────────────────────────────────────
        .route(
            "/api/v1/streams/{stream_id}/vote",
            post(content::voting::vote_stream).delete(content::voting::delete_stream_vote),
        )
        .route(
            "/api/v1/streams/{stream_id}/votes",
            get(content::voting::get_stream_votes),
        )
        .route(
            "/api/v1/streams/votes/bulk",
            post(content::voting::bulk_stream_votes),
        )
        .route(
            "/api/v1/content/{media_id}/rate",
            post(content::voting::rate_content),
        )
        .route(
            "/api/v1/content/{media_id}/ratings",
            get(content::voting::get_content_ratings),
        )
        .route(
            "/api/v1/content/ratings/bulk",
            post(content::voting::bulk_content_ratings),
        )
        .route(
            "/api/v1/content/{media_id}/like",
            post(content::voting::like_content).delete(content::voting::unlike_content),
        )
        .route(
            "/api/v1/content/{media_id}/likes",
            get(content::voting::get_content_likes),
        )
        // ── Prometheus metrics ────────────────────────────────────────────────
        .route("/metrics", get(metrics::handler))
        .route("/api/v1/metrics", get(metrics::handler))
        // ── Admin ─────────────────────────────────────────────────────────────
        .route("/api/v1/admin/cache/stats", get(admin::cache_stats))
        .route("/api/v1/admin/cache/keys", get(admin::cache_keys))
        .route("/api/v1/admin/cache/key/{*key}", get(admin::cache_key_get).delete(admin::cache_key_delete))
        .route("/api/v1/admin/cache/clear", post(admin::cache_clear))
        .route("/api/v1/admin/cache/image/{*key}", get(admin::cache_image_get))
        .route("/api/v1/admin/db/stats", get(admin::db_stats))
        .route("/api/v1/admin/db/tables", get(admin::db_tables))
        // ── Admin DB Python-path aliases (/api/v1/admin/db/ → /api/v1/admin/database/) ──
        .route("/api/v1/admin/db/tables/{table}/schema", get(admin_database::get_table_schema))
        .route("/api/v1/admin/db/tables/{table}/data", get(admin_database::get_table_data))
        .route("/api/v1/admin/db/tables/{table}/export", get(admin_database::export_table_by_path))
        .route("/api/v1/admin/db/tables/{table}/rows/{id}/related", get(admin_database::get_related_rows))
        .route("/api/v1/admin/db/orphans", get(admin_database::detect_orphans_combined))
        .route("/api/v1/admin/db/orphans/cleanup", post(admin_database::cleanup_orphans))
        .route("/api/v1/admin/db/slow-queries", get(admin_database::get_slow_queries))
        .route("/api/v1/admin/db/slow-queries/reset", post(admin_database::reset_slow_queries))
        .route("/api/v1/admin/db/maintenance/vacuum", post(admin_database::run_vacuum))
        .route("/api/v1/admin/db/maintenance/analyze", post(admin_database::run_analyze))
        .route("/api/v1/admin/db/maintenance/reindex", post(admin_database::run_reindex))
        .route("/api/v1/admin/db/bulk/delete", post(admin_database::bulk_delete))
        .route("/api/v1/admin/db/bulk/update", post(admin_database::bulk_update))
        .route("/api/v1/admin/db/import/preview", post(admin_database::import_preview))
        .route("/api/v1/admin/db/import/execute", post(admin_database::import_execute))
        // ── Admin extended (metadata CRUD, exceptions, request metrics, source health) ─
        .route("/api/v1/admin/metadata/{media_id}", delete(admin_extended::delete_metadata))
        .route("/api/v1/admin/metadata/{media_id}/block", post(admin_extended::block_media))
        .route("/api/v1/admin/metadata/{media_id}/unblock", post(admin_extended::unblock_media))
        .route("/api/v1/admin/media/blocked", get(admin_extended::list_blocked_media))
        .route("/api/v1/admin/torrent-streams/{stream_id}/block", post(admin_extended::block_torrent_stream))
        .route("/api/v1/admin/contribution-settings", get(admin_extended::get_contribution_settings).put(admin_extended::update_contribution_settings))
        .route("/api/v1/admin/contribution-levels", get(admin_extended::get_contribution_levels))
        .route("/api/v1/admin/contribution-settings/reset", post(admin_extended::reset_contribution_settings))
        // ── Admin keyword filters ─────────────────────────────────────────────
        .route("/api/v1/admin/keyword-filters", get(admin_keyword_filters::list_keyword_filters).post(admin_keyword_filters::add_keyword_filter))
        .route("/api/v1/admin/keyword-filters/reload", post(admin_keyword_filters::reload_keyword_cache))
        .route("/api/v1/admin/keyword-filters/{id}", patch(admin_keyword_filters::toggle_keyword_filter).delete(admin_keyword_filters::delete_keyword_filter))
        .route("/api/v1/admin/keyword-whitelist", get(admin_keyword_filters::list_keyword_whitelist).post(admin_keyword_filters::add_whitelist_phrase))
        .route("/api/v1/admin/keyword-whitelist/{id}", delete(admin_keyword_filters::delete_whitelist_phrase))
        .route("/api/v1/admin/exceptions/status", get(admin_extended::get_exception_status))
        .route("/api/v1/admin/exceptions", get(admin_extended::list_exceptions).delete(admin_extended::clear_all_exceptions))
        .route("/api/v1/admin/exceptions/{fingerprint}", get(admin_extended::get_exception).delete(admin_extended::clear_single_exception))
        .route("/api/v1/admin/request-metrics/status", get(admin_extended::get_request_metrics_status))
        .route("/api/v1/admin/request-metrics/endpoints", get(admin_extended::list_endpoint_stats))
        .route("/api/v1/admin/request-metrics/endpoints/{method}/{*route}", get(admin_extended::get_endpoint_detail))
        .route("/api/v1/admin/request-metrics/recent", get(admin_extended::list_recent_requests))
        .route("/api/v1/admin/request-metrics", delete(admin_extended::clear_request_metrics))
        .route("/api/v1/admin/public-indexers/source-health", get(admin_extended::get_source_health))
        // ── Admin metrics ─────────────────────────────────────────────────────
        .route("/api/v1/admin/metrics/torrents/count", get(admin_metrics::get_torrents_count))
        .route("/api/v1/admin/metrics/torrents", get(admin_metrics::get_torrents_count))
        .route("/api/v1/admin/metrics/torrents/by-sources", get(admin_metrics::get_torrents_by_sources))
        .route("/api/v1/admin/metrics/torrents/by-source", get(admin_metrics::get_torrents_by_sources))
        .route("/api/v1/admin/metrics/torrents/sources", get(admin_metrics::get_torrents_by_sources))
        .route("/api/v1/admin/metrics/torrents/by-uploaders", get(admin_metrics::get_torrents_by_uploaders))
        .route("/api/v1/admin/metrics/torrents/uploaders", get(admin_metrics::get_torrents_by_uploaders))
        .route("/api/v1/admin/metrics/torrents/weekly-top-uploaders", get(admin_metrics::get_weekly_top_uploaders))
        .route("/api/v1/admin/metrics/torrents/uploaders/weekly/{week_date}", get(admin_metrics::get_weekly_top_uploaders))
        .route("/api/v1/admin/metrics/metadata/total", get(admin_metrics::get_total_metadata))
        .route("/api/v1/admin/metrics/metadata", get(admin_metrics::get_total_metadata))
        .route("/api/v1/admin/metrics/users", get(admin_metrics::get_user_stats))
        .route("/api/v1/admin/metrics/users/stats", get(admin_metrics::get_user_stats))
        .route("/api/v1/admin/metrics/contributions/stats", get(admin_metrics::get_contribution_stats))
        .route("/api/v1/admin/metrics/activity", get(admin_metrics::get_activity_stats))
        .route("/api/v1/admin/metrics/activity/stats", get(admin_metrics::get_activity_stats))
        .route("/api/v1/admin/metrics/redis", get(admin_metrics::redis_metrics))
        .route("/api/v1/admin/metrics/worker-memory", get(admin_metrics::get_worker_memory_metrics))
        .route("/api/v1/admin/metrics/workers/memory", get(admin_metrics::get_worker_memory_metrics))
        .route("/api/v1/admin/metrics/debrid-cache", get(admin_metrics::debrid_cache_metrics))
        .route("/api/v1/admin/metrics/scrapers", get(admin_metrics::get_scraper_latest_metrics))
        .route("/api/v1/admin/metrics/scrapers/latest", get(admin_metrics::get_scraper_latest_metrics))
        .route("/api/v1/admin/metrics/scrapers/aggregated", get(admin_metrics::get_scraper_aggregated_metrics))
        .route("/api/v1/admin/metrics/scrapers/history", get(admin_metrics::get_scraper_history))
        .route("/api/v1/admin/metrics/scrapers/searches", get(admin_metrics::get_search_run_metrics))
        .route("/api/v1/admin/metrics/scrapy-schedulers", get(admin_metrics::get_scrapy_schedulers))
        .route("/api/v1/admin/metrics/scrapers/{scraper_name}", get(admin_metrics::get_scraper_by_name).delete(admin_metrics::delete_scraper_metrics))
        .route("/api/v1/admin/metrics/scrapers/{scraper_name}/history", get(admin_metrics::get_scraper_name_history))
        .route("/api/v1/admin/metrics/scrapers/{scraper_name}/latest", get(admin_metrics::get_scraper_name_latest))
        .route("/api/v1/admin/metrics/scrapers/{scraper_name}/metrics", get(admin_metrics::get_scraper_name_metrics).delete(admin_metrics::delete_scraper_metrics))
        .route("/api/v1/admin/metrics/schedulers/last-run", get(admin_metrics::get_schedulers_last_run))
        .route("/api/v1/admin/metrics/prometheus", get(admin_metrics::prometheus_metrics))
        .route("/api/v1/admin/metrics/system-overview", get(admin_metrics::get_system_overview))
        .route("/api/v1/admin/metrics/system/overview", get(admin_metrics::get_system_overview))
        .route("/api/v1/admin/metrics/search-run", get(admin_metrics::get_search_run_metrics))
        // ── Admin database ────────────────────────────────────────────────────
        .route("/api/v1/admin/database/stats", get(admin_database::db_stats))
        .route("/api/v1/admin/database/tables", get(admin_database::db_tables))
        .route("/api/v1/admin/database/tables/{table}/schema", get(admin_database::get_table_schema))
        .route("/api/v1/admin/database/tables/{table}/data", get(admin_database::get_table_data))
        .route("/api/v1/admin/database/tables/{table}/rows", delete(admin_database::delete_table_rows))
        .route("/api/v1/admin/database/vacuum", post(admin_database::run_vacuum))
        .route("/api/v1/admin/database/analyze", post(admin_database::run_analyze))
        .route("/api/v1/admin/database/reindex", post(admin_database::run_reindex))
        .route("/api/v1/admin/database/bloat", get(admin_database::get_bloat_stats))
        .route("/api/v1/admin/database/slow-queries", get(admin_database::get_slow_queries))
        .route("/api/v1/admin/database/orphan-streams", get(admin_database::detect_orphan_streams))
        .route("/api/v1/admin/database/orphan-media", get(admin_database::detect_orphan_media))
        .route("/api/v1/admin/database/indexes", get(admin_database::list_indexes))
        .route("/api/v1/admin/database/indexes/rebuild", post(admin_database::rebuild_indexes))
        .route("/api/v1/admin/database/cleanup-orphans", post(admin_database::cleanup_orphans))
        .route("/api/v1/admin/database/export/{table}", get(admin_database::export_table))
        .route("/api/v1/admin/database/import/{table}", post(admin_database::import_table))
        // ── Admin scrapers ────────────────────────────────────────────────────
        .route("/api/v1/admin/scrapers/spiders", get(admin_scrapers::list_spiders))
        .route("/api/v1/admin/scrapers/run", post(admin_scrapers::run_scraper))
        .route("/api/v1/admin/scrapers/block-torrent", post(admin_scrapers::block_torrent))
        .route("/api/v1/admin/scrapers/unblock-torrent", post(admin_scrapers::unblock_torrent))
        .route("/api/v1/admin/scrapers/catalogs", get(admin_scrapers::get_catalog_data))
        .route("/api/v1/admin/scrapers/status", get(admin_scrapers::get_scraper_status))
        .route("/api/v1/admin/scrapers/dmm-hashlist/status", get(admin_scrapers::get_dmm_hashlist_status))
        .route("/api/v1/admin/scrapers/dmm-hashlist/run", post(admin_scrapers::run_dmm_hashlist))
        .route("/api/v1/admin/scrapers/dmm-hashlist/run-full", post(admin_scrapers::run_dmm_hashlist_full))
        .route("/api/v1/admin/scrapers/migrate-media", post(admin_scrapers::migrate_media))
        .route("/api/v1/admin/scrapers/migrate-id", post(admin_scrapers::migrate_id))
        .route("/api/v1/admin/scrapers/update-images", post(admin_scrapers::update_media_images))
        .route("/api/v1/admin/scrapers/update-imdb/{meta_id}", get(admin_scrapers::refresh_imdb_data))
        .route("/api/v1/admin/scrapers/torrent/{info_hash}", delete(admin_scrapers::delete_torrent))
        .route("/api/v1/admin/scrapers/add-tv-metadata", post(admin_scrapers::add_tv_metadata))
        // ── Admin schedulers ──────────────────────────────────────────────────
        .route("/api/v1/admin/schedulers", get(admin_scrapers::list_schedulers))
        .route("/api/v1/admin/schedulers/stats", get(admin_scrapers::get_scheduler_stats))
        .route("/api/v1/admin/schedulers/{job_id}", get(admin_scrapers::get_scheduler_job))
        .route("/api/v1/admin/schedulers/{job_id}/run", post(admin_scrapers::run_scheduler_job))
        .route("/api/v1/admin/schedulers/{job_id}/run-inline", post(admin_scrapers::run_scheduler_job_inline))
        .route("/api/v1/admin/schedulers/{job_id}/history", get(admin_scrapers::get_job_history))
        // ── Admin tasks ───────────────────────────────────────────────────────
        .route("/api/v1/admin/tasks/overview", get(admin_scrapers::get_task_overview))
        .route("/api/v1/admin/tasks", get(admin_scrapers::list_tasks))
        .route("/api/v1/admin/tasks/stream", get(admin_scrapers::stream_task_snapshots))
        .route("/api/v1/admin/tasks/bulk-cancel", post(admin_scrapers::bulk_cancel_tasks))
        .route("/api/v1/admin/tasks/bulk-retry", post(admin_scrapers::bulk_retry_tasks))
        .route("/api/v1/admin/tasks/{task_id}", get(admin_scrapers::get_task_detail))
        .route("/api/v1/admin/tasks/{task_id}/retry", post(admin_scrapers::retry_task))
        .route("/api/v1/admin/tasks/{task_id}/cancel", post(admin_scrapers::cancel_task))
        // ── Admin telegram ────────────────────────────────────────────────────
        .route("/api/v1/admin/telegram/stats", get(admin_scrapers::get_telegram_stats))
        .route("/api/v1/admin/telegram/migrate", post(admin_scrapers::migrate_single_stream))
        .route("/api/v1/admin/telegram/migrate/bulk", post(admin_scrapers::migrate_bulk_streams))
        .route("/api/v1/admin/telegram/exportable", get(admin_scrapers::get_exportable_streams))
        // Top-level Python-compat telegram aliases
        .route("/telegram/stats", get(admin_scrapers::get_telegram_stats))
        .route("/telegram/exportable", get(admin_scrapers::get_exportable_streams))
        .route("/telegram/migrate", post(admin_scrapers::migrate_single_stream))
        .route("/telegram/migrate/bulk", post(admin_scrapers::migrate_bulk_streams))
        // ── Moderator metadata ────────────────────────────────────────────────
        .route("/api/v1/moderator/metadata", get(moderator::moderator_list_metadata))
        .route("/api/v1/moderator/metadata/search-external", post(moderator::moderator_search_external_metadata))
        .route("/api/v1/moderator/metadata/{media_id}", get(moderator::moderator_get_metadata))
        .route("/api/v1/moderator/metadata/{media_id}/fetch-external", post(moderator::moderator_fetch_external_metadata))
        .route("/api/v1/moderator/metadata/{media_id}/apply-external", post(moderator::moderator_apply_external_metadata))
        .route("/api/v1/moderator/metadata/{media_id}/migrate-id", post(moderator::moderator_migrate_metadata_id))
        // ── Instance / app info ───────────────────────────────────────────────
        .route("/api/v1/instance/info", get(instance::get_instance_info))
        .route("/api/v1/instance/app-config", get(instance::get_app_config))
        .route("/api/v1/instance/constants", get(instance::get_system_constants))
        .route("/api/v1/instance/setup/create-admin", post(instance::create_initial_admin))
        // ── Kodi setup ────────────────────────────────────────────────────────
        .route("/api/v1/kodi/generate-setup-code", post(kodi_setup::generate_setup_code))
        .route("/api/v1/kodi/qr-code/{code}", get(kodi_setup::get_qr_code))
        .route("/api/v1/kodi/qr-code/{secret_str}/{code}", get(kodi_setup::get_qr_code_with_secret))
        .route("/api/v1/kodi/associate-manifest", post(kodi_setup::associate_manifest))
        .route("/api/v1/kodi/get-manifest/{code}", get(kodi_setup::get_manifest))
        // ── RSS feeds (admin) ─────────────────────────────────────────────────
        .route("/api/v1/admin/rss", get(rss::list_rss_feeds).post(rss::create_rss_feed))
        .route("/api/v1/admin/rss/{id}", get(rss::get_rss_feed).put(rss::update_rss_feed).delete(rss::delete_rss_feed))
        .route("/api/v1/admin/rss/{id}/test", post(rss::test_rss_feed))
        .route("/api/v1/admin/rss/{id}/scrape", post(rss::run_rss_feed_scraper))
        .route("/api/v1/admin/rss/bulk-activate", post(rss::activate_deactivate_feeds))
        .route("/api/v1/admin/rss/bulk-import", post(rss::bulk_import_rss_feeds))
        // ── RSS feeds (user) ──────────────────────────────────────────────────
        .route("/api/v1/user/rss", get(rss::user_list_rss_feeds).post(rss::user_create_rss_feed))
        .route("/api/v1/user/rss/{id}", get(rss::user_get_rss_feed).put(rss::user_update_rss_feed).delete(rss::user_delete_rss_feed))
        .route("/api/v1/user/rss/{id}/test", post(rss::user_test_rss_feed))
        .route("/api/v1/user/rss/{id}/test-url", post(rss::user_test_rss_feed_url))
        .route("/api/v1/user/rss/{id}/scrape", post(rss::user_scrape_single_feed))
        .route("/api/v1/user/rss/run-all", post(rss::user_run_all_scrapers))
        .route("/api/v1/user/rss/status", get(rss::user_get_scheduler_status))
        .route("/api/v1/user/rss/bulk-update", post(rss::user_bulk_update_feed_status))
        // ── RSS feeds (user) — Python hyphenated path aliases ─────────────────
        .route("/api/v1/user-rss/feeds", get(rss::user_list_rss_feeds).post(rss::user_create_rss_feed))
        .route("/api/v1/user-rss/feeds/{id}", get(rss::user_get_rss_feed).put(rss::user_update_rss_feed).delete(rss::user_delete_rss_feed))
        .route("/api/v1/user-rss/feeds/{id}/test", post(rss::user_test_rss_feed))
        .route("/api/v1/user-rss/feeds/{id}/scrape", post(rss::user_scrape_single_feed))
        .route("/api/v1/user-rss/feeds/run-all", post(rss::user_run_all_scrapers))
        .route("/api/v1/user-rss/feeds/bulk-status", post(rss::user_bulk_update_feed_status))
        .route("/api/v1/user-rss/scheduler-status", get(rss::user_get_scheduler_status))
        // ── Downloads ─────────────────────────────────────────────────────────
        .route("/api/v1/downloads", get(downloads::list_downloads).post(downloads::create_download).delete(downloads::clear_downloads))
        .route("/api/v1/downloads/{id}", get(downloads::get_download).delete(downloads::delete_download))
        .route("/api/v1/downloads/{id}/retry", post(downloads::retry_download))
        // ── Indexers ──────────────────────────────────────────────────────────
        .route("/api/v1/indexers", get(indexers::list_indexers).post(indexers::create_indexer))
        .route("/api/v1/indexers/{id}", get(indexers::get_indexer).put(indexers::update_indexer).delete(indexers::delete_indexer))
        .route("/api/v1/indexers/{id}/test", post(indexers::test_indexer))
        // Python path aliases for indexers (/api/v1/profile/indexers/*)
        .route("/api/v1/profile/indexers/global-status", get(indexers::get_global_indexer_status))
        .route("/api/v1/profile/indexers/prowlarr/test", post(indexers::test_prowlarr_connection))
        .route("/api/v1/profile/indexers/jackett/test", post(indexers::test_jackett_connection))
        .route("/api/v1/profile/indexers/torznab/test", post(indexers::test_torznab_endpoint))
        .route("/api/v1/profile/indexers/newznab/test", post(indexers::test_newznab_indexer))
        // ── User library ──────────────────────────────────────────────────────
        // Static paths must come before parameterized paths
        .route("/api/v1/library/stats", get(user_library::get_library_stats))
        .route("/api/v1/library/bulk", post(user_library::bulk_library_operation))
        .route("/api/v1/library/check/{media_id}", get(user_library::get_library_status))
        .route("/api/v1/library/by-media-id/{media_id}", delete(user_library::remove_from_library_by_media_id))
        // Python-compat: POST /api/v1/library with body {media_id, catalog_type}
        .route("/api/v1/library", get(user_library::list_library).post(user_library::add_to_library))
        .route("/api/v1/library/{item_id}", get(user_library::get_library_item).post(user_library::add_to_library).delete(user_library::remove_from_library))
        .route("/api/v1/library/{media_id}/status", get(user_library::get_library_status))
        // ── User catalogs ─────────────────────────────────────────────────────
        // Static paths must come before parameterized paths
        .route("/api/v1/user/catalogs/public", get(user_catalogs::list_public_catalogs))
        .route("/api/v1/user/catalogs/subscribed", get(user_catalogs::list_subscribed_catalogs))
        .route("/api/v1/user/catalogs/share/{uuid}", get(user_catalogs::get_catalog_by_share_link))
        .route("/api/v1/user/catalogs", get(user_catalogs::list_user_catalogs).post(user_catalogs::create_user_catalog))
        .route("/api/v1/user/catalogs/{id}", get(user_catalogs::get_user_catalog).put(user_catalogs::update_user_catalog).patch(user_catalogs::update_user_catalog).delete(user_catalogs::delete_user_catalog))
        .route("/api/v1/user/catalogs/{id}/items/reorder", put(user_catalogs::reorder_items))
        .route("/api/v1/user/catalogs/{id}/items", get(user_catalogs::list_catalog_items).post(user_catalogs::add_catalog_item))
        .route("/api/v1/user/catalogs/{id}/items/{media_id}", delete(user_catalogs::remove_catalog_item))
        .route("/api/v1/user/catalogs/{id}/subscribe", post(user_catalogs::subscribe_catalog).delete(user_catalogs::unsubscribe_catalog))
        .route("/api/v1/user/catalogs/{id}/subscribed", get(user_catalogs::check_subscription))
        // ── User management (admin) ───────────────────────────────────────────
        .route("/api/v1/users", get(user_management::list_users))
        .route("/api/v1/users/{user_id}", get(user_management::get_user).patch(user_management::update_user).delete(user_management::delete_user))
        .route("/api/v1/users/{user_id}/role", patch(user_management::update_user_role))
        .route("/api/v1/users/{user_id}/send-upload-warning", post(user_management::send_upload_warning))
        // ── Watchlist ─────────────────────────────────────────────────────────
        .route("/api/v1/watchlist/providers", get(watchlist::get_providers))
        .route("/api/v1/watchlist/{provider}/missing", get(user_library::get_missing_torrents))
        .route("/api/v1/watchlist/{provider}/import/advanced", post(user_library::advanced_import_torrents))
        .route("/api/v1/watchlist/{provider}/import", post(user_library::import_torrents))
        .route("/api/v1/watchlist/{provider}/remove", post(user_library::remove_torrent_from_debrid))
        .route("/api/v1/watchlist/{provider}/clear-all", post(user_library::clear_all_torrents_from_debrid))
        .route("/api/v1/watchlist/{provider}", get(watchlist::get_watchlist))
        // ── Integrations (Trakt/SIMKL + Telegram channels) ───────────────────
        .route("/api/v1/integrations", get(integrations::list_integrations))
        .route("/api/v1/integrations/oauth/{platform}/url", get(integrations::get_oauth_url))
        .route("/api/v1/integrations/simkl/callback", get(integrations::simkl_oauth_callback))
        .route("/api/v1/integrations/trakt/connect", post(integrations::connect_trakt))
        .route("/api/v1/integrations/simkl/connect", post(integrations::connect_simkl))
        .route("/api/v1/integrations/sync-all", post(integrations::trigger_sync_all))
        .route("/api/v1/integrations/{platform}/status", get(integrations::get_sync_status))
        .route("/api/v1/integrations/{platform}/disconnect", delete(integrations::disconnect_integration))
        .route("/api/v1/integrations/{platform}/settings", patch(integrations::update_integration_settings))
        .route("/api/v1/integrations/{platform}/sync", post(integrations::trigger_sync))
        // ── Telegram channel management ───────────────────────────────────────
        .route("/api/v1/telegram/status", get(integrations::get_telegram_status))
        .route("/api/v1/telegram/config", get(integrations::get_telegram_config).patch(integrations::update_telegram_config))
        .route("/api/v1/telegram/channels", post(integrations::add_telegram_channel))
        .route("/api/v1/telegram/channels/{channel_id}", delete(integrations::remove_telegram_channel).patch(integrations::update_telegram_channel))
        .route("/api/v1/telegram/validate", post(integrations::validate_telegram_channel))
        .route("/api/v1/telegram/login", get(integrations::telegram_login))
        .route("/api/v1/telegram/unlink", delete(integrations::telegram_unlink))
        // ── Middleware ───────────────────────────────────────────────────────
        .layer(axum::middleware::from_fn_with_state(
            Arc::clone(&state),
            api_key_middleware,
        ))
        .layer(axum::middleware::from_fn_with_state(
            Arc::clone(&state),
            stremio_auth_middleware,
        ))
        .layer(axum::middleware::from_fn_with_state(
            Arc::clone(&state),
            metrics_middleware,
        ))
        .layer(axum::middleware::from_fn(api_error_middleware))
        .layer(CompressionLayer::new())
        .layer(TimeoutLayer::with_status_code(
            axum::http::StatusCode::GATEWAY_TIMEOUT,
            stream_timeout,
        ))
        .layer(make_trace_layer!())
        .layer(CorsLayer::permissive())
        .with_state(state.clone());

    let frontend_dist_dir = state.config.frontend_dist_dir.clone();
    let index_html = std::path::Path::new(&frontend_dist_dir)
        .join("index.html")
        .to_string_lossy()
        .into_owned();

    // ServeDir serves real files (assets, etc.) directly; unmatched paths fall
    // back to index.html so React Router handles client-side navigation.
    let spa_service = ServeDir::new(&frontend_dist_dir).fallback(ServeFile::new(&index_html));

    Router::new()
        .route("/", get(root_redirect))
        .merge(api_router)
        .nest_service("/app", spa_service)
        .nest_service("/static", ServeDir::new(resources_dir))
        .layer(axum::middleware::map_response(spa_cache_headers))
        .layer(CorsLayer::permissive())
}
