/// On-demand scrape trigger / status endpoints.
///
/// Routes (prefix /api/v1/scraping):
///   GET  /scrapers               → list_scrapers
///   GET  /{media_id}/status      → get_scrape_status_by_media (auth)
///   POST /{media_id}/scrape      → trigger_scrape_by_media    (auth, proxied to Python)
use std::sync::Arc;

use axum::{
    extract::{Path, Query, Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use fred::prelude::SortedSetsInterface;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
    let token = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(str::to_string)?;
    let dot = token.rfind('.')?;
    let (payload_str, sig) = token.split_at(dot);
    let sig = &sig[1..];
    let mut mac = Hmac::<Sha256>::new_from_slice(secret_key.as_bytes()).ok()?;
    mac.update(payload_str.as_bytes());
    let expected: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if expected != sig {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(payload_str).ok()?;
    let data: Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

#[derive(Deserialize)]
pub struct ScrapeStatusQuery {
    pub media_type: Option<String>,
    pub season: Option<i32>,
    pub episode: Option<i32>,
}

struct ScraperDef {
    id: &'static str,
    name: &'static str,
    description: &'static str,
    requires_debrid: bool,
    ttl: i64,
    enabled: bool,
}

/// Build the full list of scrapers based on AppConfig and optional user context.
///
/// `user_services`: set of service names the user has configured (e.g. "torbox", "easynews")
/// `has_newznab`: whether user has any newznab indexers configured
fn build_scrapers(
    state: &AppState,
    user_services: &[String],
    has_newznab: bool,
) -> Vec<ScraperDef> {
    let cfg = &state.config;
    let prowlarr_ttl = cfg.prowlarr_search_ttl;
    let user_has = |svc: &str| user_services.iter().any(|s| s == svc);

    vec![
        ScraperDef {
            id: "prowlarr",
            name: "Prowlarr",
            description: "Search via Prowlarr indexer manager",
            requires_debrid: false,
            ttl: prowlarr_ttl,
            enabled: cfg.is_scrap_from_prowlarr,
        },
        ScraperDef {
            id: "zilean",
            name: "Zilean",
            description: "Search Zilean DMM database",
            requires_debrid: false,
            ttl: cfg.zilean_search_ttl,
            enabled: cfg.is_scrap_from_zilean,
        },
        ScraperDef {
            id: "dmm_hashlist",
            name: "DMM Hashlist",
            description: "Background ingestion of DMM hashlists for stream/cache coverage",
            requires_debrid: false,
            ttl: cfg.dmm_hashlist_sync_ttl,
            enabled: cfg.is_scrap_from_dmm_hashlist && !cfg.disable_dmm_hashlist_scraper,
        },
        ScraperDef {
            id: "torrentio",
            name: "Torrentio",
            description: "Search Torrentio addon (requires debrid)",
            requires_debrid: true,
            ttl: cfg.torrentio_search_ttl,
            enabled: cfg.is_scrap_from_torrentio,
        },
        ScraperDef {
            id: "mediafusion",
            name: "MediaFusion",
            description: "Search MediaFusion addon (requires debrid)",
            requires_debrid: true,
            ttl: cfg.mediafusion_search_ttl,
            enabled: cfg.is_scrap_from_mediafusion,
        },
        ScraperDef {
            id: "public_indexers",
            name: "Public Indexers",
            description: "Search native Scrapling-backed public indexers (movies/series/anime)",
            requires_debrid: false,
            ttl: cfg.public_indexers_search_ttl,
            enabled: cfg.is_scrap_from_public_indexers,
        },
        ScraperDef {
            id: "jackett",
            name: "Jackett",
            description: "Search via Jackett indexer",
            requires_debrid: false,
            ttl: cfg.jackett_search_ttl,
            enabled: cfg.is_scrap_from_jackett,
        },
        ScraperDef {
            id: "torznab",
            name: "Custom Torznab",
            description: "Search via custom Torznab endpoints",
            requires_debrid: false,
            ttl: cfg.jackett_search_ttl, // same TTL as Jackett
            enabled: cfg.is_scrap_from_torznab,
        },
        ScraperDef {
            id: "torbox_search",
            name: "TorBox Search",
            description: "Search TorBox API for torrents and Usenet (requires TorBox)",
            requires_debrid: true,
            ttl: cfg.torbox_search_ttl,
            enabled: user_has("torbox"),
        },
        ScraperDef {
            id: "newznab",
            name: "Newznab Indexers",
            description: "Search Newznab-compatible indexers for Usenet content",
            requires_debrid: false,
            ttl: prowlarr_ttl,
            enabled: has_newznab,
        },
        ScraperDef {
            id: "easynews",
            name: "Easynews",
            description: "Search Easynews for direct Usenet streaming",
            requires_debrid: false,
            ttl: prowlarr_ttl,
            enabled: user_has("easynews"),
        },
        ScraperDef {
            id: "public_usenet_indexers",
            name: "Public Usenet Indexers",
            description: "Search public Usenet sites (e.g. Binsearch) for NZB links",
            requires_debrid: false,
            ttl: cfg.public_usenet_search_ttl,
            enabled: cfg.is_scrap_from_public_usenet_indexers,
        },
    ]
}

/// GET /api/v1/scraping/scrapers — public list of globally-enabled scrapers
pub async fn list_scrapers(State(state): State<Arc<AppState>>, _req: Request) -> Response {
    let scrapers = build_scrapers(&state, &[], false);
    let list: Vec<Value> = scrapers
        .iter()
        .filter(|s| s.enabled)
        .map(|s| {
            json!({
                "id": s.id,
                "name": s.name,
                "enabled": s.enabled,
                "requires_debrid": s.requires_debrid,
                "ttl": s.ttl,
                "description": s.description,
            })
        })
        .collect();
    Json(list).into_response()
}

/// GET /api/v1/scraping/{media_id}/status
pub async fn get_scrape_status_by_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    Query(params): Query<ScrapeStatusQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let media_type = params.media_type.as_deref().unwrap_or("movie");
    let season = params.season;
    let episode = params.episode;

    // ── Media row ──────────────────────────────────────────────────────────────
    let row: Option<(String, Option<chrono::DateTime<chrono::Utc>>)> =
        sqlx::query_as::<_, (String, Option<chrono::DateTime<chrono::Utc>>)>(
            "SELECT title, last_scraped_at FROM media WHERE id = $1 LIMIT 1",
        )
        .bind(media_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    let (title, last_scraped_at) = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("Media with ID {media_id} not found.")})),
            )
                .into_response();
        }
    };

    // ── External IDs → meta_id ─────────────────────────────────────────────────
    let ext_rows: Vec<(String, String)> = sqlx::query_as::<_, (String, String)>(
        "SELECT provider, external_id FROM media_external_id WHERE media_id = $1",
    )
    .bind(media_id)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut meta_id: Option<String> = None;
    for (provider, ext_id) in &ext_rows {
        match provider.as_str() {
            "imdb" => {
                meta_id = Some(ext_id.clone());
                break;
            }
            "tmdb" if meta_id.is_none() => meta_id = Some(format!("tmdb:{ext_id}")),
            "tvdb" if meta_id.is_none() => meta_id = Some(format!("tvdb:{ext_id}")),
            "mal" if meta_id.is_none() => meta_id = Some(format!("mal:{ext_id}")),
            _ => {}
        }
    }
    let meta_id = meta_id.unwrap_or_else(|| format!("mf:{media_id}"));

    // ── User role → is_moderator ───────────────────────────────────────────────
    let role: Option<String> =
        sqlx::query_scalar::<_, String>("SELECT role::text FROM users WHERE id = $1")
            .bind(user_id as i32)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);
    let is_moderator = matches!(role.as_deref(), Some("MODERATOR") | Some("ADMIN"));

    // ── User profile → has_debrid + streaming services ─────────────────────────
    let profile_row: Option<(Option<Value>, Option<String>)> =
        sqlx::query_as::<_, (Option<Value>, Option<String>)>(
            "SELECT config, encrypted_secrets FROM user_profiles WHERE user_id = $1 AND is_default = TRUE LIMIT 1",
        )
        .bind(user_id as i32)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    let (has_debrid, user_services, has_newznab) = if let Some((cfg_opt, enc_opt)) = profile_row {
        let mut config = cfg_opt.unwrap_or(json!({}));
        if let Some(enc_str) = enc_opt.as_deref().filter(|s| !s.is_empty()) {
            let secrets =
                crate::crypto::profile::decrypt_secrets(enc_str, &state.config.secret_key);
            crate::crypto::profile::merge_secrets(&mut config, &secrets);
        }

        let sps = config
            .get("sps")
            .or_else(|| config.get("streaming_providers"))
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        const DEBRID_SERVICES: &[&str] = &[
            "alldebrid",
            "debridlink",
            "offcloud",
            "pikpak",
            "premiumize",
            "realdebrid",
            "seedr",
            "torbox",
            "stremthru",
            "easydebrid",
            "debrider",
        ];

        let mut has_debrid = false;
        let mut services: Vec<String> = Vec::new();
        for p in &sps {
            let svc = p
                .get("sv")
                .or_else(|| p.get("service"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let enabled = p
                .get("en")
                .or_else(|| p.get("enabled"))
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            let has_creds = ["tk", "token", "pw", "password", "em", "email"]
                .iter()
                .any(|k| {
                    p.get(*k)
                        .and_then(|v| v.as_str())
                        .map(|s| !s.is_empty())
                        .unwrap_or(false)
                });
            if !svc.is_empty() {
                services.push(svc.to_string());
            }
            if enabled && has_creds && DEBRID_SERVICES.contains(&svc) {
                has_debrid = true;
            }
        }

        // Check for newznab indexers in indexer config
        let has_newznab = config
            .get("ic")
            .and_then(|ic| ic.get("nz"))
            .and_then(|nz| nz.as_array())
            .map(|arr| {
                arr.iter()
                    .any(|idx| idx.get("en").and_then(|v| v.as_bool()).unwrap_or(true))
            })
            .unwrap_or(false);

        (has_debrid, services, has_newznab)
    } else {
        (false, vec![], false)
    };

    // ── Build scraper list ─────────────────────────────────────────────────────
    let scrapers = build_scrapers(&state, &user_services, has_newznab);
    let enabled_scrapers: Vec<&ScraperDef> = scrapers.iter().filter(|s| s.enabled).collect();

    // ── Redis cooldown checks ─────────────────────────────────────────────────
    let cache_key = if media_type == "series" {
        format!(
            "series:{meta_id}:{}:{}",
            season.unwrap_or(0),
            episode.unwrap_or(0)
        )
    } else {
        format!("movie:{meta_id}")
    };

    let now = Utc::now().timestamp();
    let mut scraper_statuses = serde_json::Map::new();
    let mut can_scrape_any = false;
    let mut min_cooldown: Option<i64> = None;

    for sc in &enabled_scrapers {
        let score: Option<f64> = state
            .redis
            .zscore(sc.id, cache_key.as_str())
            .await
            .unwrap_or(None);

        let (last_scraped, cooldown_remaining, can_scrape) = if let Some(ts) = score {
            let time_since = now - ts as i64;
            let remaining = (sc.ttl - time_since).max(0);
            let scraped_iso = chrono::DateTime::<chrono::Utc>::from_timestamp(ts as i64, 0)
                .map(|dt| dt.to_rfc3339())
                .unwrap_or_default();
            (Some(scraped_iso), remaining, remaining == 0)
        } else {
            (None, 0i64, true)
        };

        // For debrid-requiring scrapers, also factor in has_debrid
        let effective_can_scrape = can_scrape && (!sc.requires_debrid || has_debrid);
        if effective_can_scrape {
            can_scrape_any = true;
        }
        if cooldown_remaining > 0 && (!sc.requires_debrid || has_debrid) {
            min_cooldown = Some(match min_cooldown {
                Some(prev) => prev.min(cooldown_remaining),
                None => cooldown_remaining,
            });
        }

        scraper_statuses.insert(
            sc.id.to_string(),
            json!({
                "last_scraped": last_scraped,
                "cooldown_remaining": cooldown_remaining,
                "can_scrape": can_scrape,
                "ttl": sc.ttl,
                "enabled": true,
                "requires_debrid": sc.requires_debrid,
            }),
        );
    }

    // available_scrapers: enabled scrapers the user can actually use
    let available_scrapers: Vec<Value> = enabled_scrapers
        .iter()
        .filter(|s| !s.requires_debrid || has_debrid)
        .map(|s| {
            json!({
                "id": s.id,
                "name": s.name,
                "enabled": true,
                "requires_debrid": s.requires_debrid,
                "ttl": s.ttl,
                "description": s.description,
            })
        })
        .collect();

    Json(json!({
        "media_id": media_id,
        "title": title,
        "last_scraped_at": last_scraped_at,
        "cooldown_remaining": min_cooldown,
        "can_scrape": can_scrape_any || last_scraped_at.is_none(),
        "scraper_statuses": scraper_statuses,
        "available_scrapers": available_scrapers,
        "is_moderator": is_moderator,
        "has_debrid": has_debrid,
    }))
    .into_response()
}

#[derive(Deserialize)]
pub struct ScrapeRequest {
    pub media_type: String,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    #[serde(default)]
    pub force: bool,
    pub scrapers: Option<Vec<String>>,
}

/// POST /api/v1/scraping/{media_id}/scrape
pub async fn trigger_scrape_by_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    Json(req): Json<ScrapeRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let media_type = req.media_type.as_str();

    // ── User role ─────────────────────────────────────────────────────────────
    let role: Option<String> =
        sqlx::query_scalar::<_, String>("SELECT role::text FROM users WHERE id = $1")
            .bind(user_id as i32)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);
    let is_moderator = matches!(role.as_deref(), Some("MODERATOR") | Some("ADMIN"));

    // ── External IDs → imdb_id + meta_id ─────────────────────────────────────
    let ext_rows: Vec<(String, String)> = sqlx::query_as::<_, (String, String)>(
        "SELECT provider, external_id FROM media_external_id WHERE media_id = $1",
    )
    .bind(media_id)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut imdb_id: Option<String> = None;
    let mut meta_id: Option<String> = None;
    for (provider, ext_id) in &ext_rows {
        match provider.as_str() {
            "imdb" => {
                imdb_id = Some(ext_id.clone());
                meta_id = Some(ext_id.clone());
                break;
            }
            "tmdb" if meta_id.is_none() => meta_id = Some(format!("tmdb:{ext_id}")),
            "tvdb" if meta_id.is_none() => meta_id = Some(format!("tvdb:{ext_id}")),
            "mal" if meta_id.is_none() => meta_id = Some(format!("mal:{ext_id}")),
            _ => {}
        }
    }
    let meta_id = meta_id.unwrap_or_else(|| format!("mf:{media_id}"));
    let imdb_id_str = imdb_id.as_deref().unwrap_or("");

    // ── SearchMeta ────────────────────────────────────────────────────────────
    let meta = match crate::db::media::get_media_meta(&state.pool_ro, media_id as i64, imdb_id_str)
        .await
    {
        Ok(Some(m)) => m,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("Media {media_id} not found")})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("trigger_scrape get_media_meta: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "DB error fetching media"})),
            )
                .into_response();
        }
    };

    // ── User profile → UserData ───────────────────────────────────────────────
    let profile_row: Option<(Option<Value>, Option<String>)> =
        sqlx::query_as::<_, (Option<Value>, Option<String>)>(
            "SELECT config, encrypted_secrets FROM user_profiles WHERE user_id = $1 AND is_default = TRUE LIMIT 1",
        )
        .bind(user_id as i32)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    let user_data: crate::models::user_data::UserData =
        if let Some((cfg_opt, enc_opt)) = profile_row {
            let mut config = cfg_opt.unwrap_or(json!({}));
            if let Some(enc_str) = enc_opt.as_deref().filter(|s| !s.is_empty()) {
                let secrets =
                    crate::crypto::profile::decrypt_secrets(enc_str, &state.config.secret_key);
                crate::crypto::profile::merge_secrets(&mut config, &secrets);
            }
            if let Some(obj) = config.as_object_mut() {
                obj.insert("uid".into(), json!(user_id));
            }
            serde_json::from_value(config).unwrap_or_default()
        } else {
            crate::models::user_data::UserData::default()
        };

    let scope = format!("user:{user_id}");

    // ── Redis cache_key for cooldown tracking ─────────────────────────────────
    let cache_key = if media_type == "series" {
        format!(
            "series:{meta_id}:{}:{}",
            req.season.unwrap_or(0),
            req.episode.unwrap_or(0)
        )
    } else {
        format!("movie:{meta_id}")
    };

    // Force + moderator: clear all cooldown markers so scrapers will run
    if req.force && is_moderator {
        let scrapers = build_scrapers(&state, &[], false);
        for sc in &scrapers {
            let _: Result<(), _> = state.redis.zrem(sc.id, &cache_key).await;
        }
    }

    // ── Run scrapers ──────────────────────────────────────────────────────────
    let streams = crate::scrapers::orchestrator::run_forced(
        &state,
        &user_data,
        &meta,
        media_type,
        req.season,
        req.episode,
        &scope,
    )
    .await;

    let usenet_streams = crate::scrapers::orchestrator::run_usenet(
        &state,
        &user_data,
        &meta,
        media_type,
        req.season,
        req.episode,
        &scope,
    )
    .await;

    // ── Update last_scraped_at ────────────────────────────────────────────────
    let _ = sqlx::query("UPDATE media SET last_scraped_at = NOW() WHERE id = $1")
        .bind(media_id)
        .execute(&state.pool)
        .await;

    let streams_found = streams.len() + usenet_streams.len();

    let user_service_names: Vec<String> = user_data
        .streaming_providers
        .iter()
        .map(|p| p.service.clone())
        .collect();
    let has_newznab = user_data
        .indexer_config
        .as_ref()
        .map(|ic| ic.newznab_indexers.iter().any(|n| n.enabled))
        .unwrap_or(false);
    let scrapers = build_scrapers(&state, &user_service_names, has_newznab);
    let scrapers_used: Vec<String> = scrapers
        .iter()
        .filter(|s| s.enabled)
        .map(|s| s.id.to_string())
        .collect();

    Json(json!({
        "status": "success",
        "message": format!("Scraping completed for '{}'", meta.title),
        "media_id": media_id,
        "title": meta.title,
        "streams_found": streams_found,
        "scraped_at": chrono::Utc::now().to_rfc3339(),
        "scrapers_used": scrapers_used,
        "scrapers_skipped": serde_json::json!([]),
    }))
    .into_response()
}

/// GET /api/v1/scraping/status — legacy alias without media_id
pub async fn get_scrape_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    _req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"detail": "media_id path parameter required"})),
    )
        .into_response()
}

/// POST /api/v1/scraping/trigger — legacy alias without media_id
pub async fn trigger_scrape(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    _req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"detail": "media_id path parameter required"})),
    )
        .into_response()
}
