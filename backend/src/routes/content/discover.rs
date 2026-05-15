/// Discover/trending feed endpoints — require JWT authentication.
///
/// Routes (prefix /api/v1/discover):
///   GET /trending          → discover_trending
///   GET /list              → discover_list
///   GET /watch-providers   → discover_watch_providers
///   GET /provider-feed     → discover_provider_feed
///   GET /anime             → discover_anime
///   GET /search            → discover_search
///   GET /tvdb-filter       → discover_tvdb_filter
///   GET /mdblist           → discover_mdblist
///   GET /verify-tmdb-key   → verify_tmdb_key
use std::sync::Arc;

use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth ─────────────────────────────────────────────────────────────────────

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

// ─── TMDB key resolution ──────────────────────────────────────────────────────

/// Fetch TMDB API key for a user: check user profile first, then server fallback.
async fn resolve_tmdb_key(state: &AppState, user_id: i64) -> Option<String> {
    // Try user profile first
    let user_key: Option<String> = sqlx::query_scalar(
        "SELECT config->'tmc'->>'ak' FROM user_profiles WHERE user_id = $1 AND is_default = true",
    )
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    .ok()
    .flatten()
    .flatten()
    .filter(|s: &String| !s.is_empty());

    if user_key.is_some() {
        return user_key;
    }

    // Fall back to server key if allowed
    if state.config.discover_allow_server_key {
        return state.config.tmdb_api_key.clone();
    }

    None
}

fn tmdb_key_required_error() -> Response {
    (
        StatusCode::PRECONDITION_FAILED,
        Json(json!({
            "code": "tmdb_key_required",
            "message": "Add your TMDB API key in Settings to use the Discover feature."
        })),
    )
        .into_response()
}

fn discover_disabled_error() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(json!({"detail": "Discover feature is disabled on this instance."})),
    )
        .into_response()
}

// ─── TMDB item normalization ──────────────────────────────────────────────────

fn normalize_tmdb_item(item: &Value) -> Value {
    let media_type_raw = item["media_type"].as_str().unwrap_or("");
    let media_type = if media_type_raw == "tv" {
        "series"
    } else if media_type_raw == "movie" {
        "movie"
    } else if item.get("first_air_date").is_some() {
        "series"
    } else {
        "movie"
    };

    let title = item["title"]
        .as_str()
        .or_else(|| item["name"].as_str())
        .or_else(|| item["original_title"].as_str())
        .or_else(|| item["original_name"].as_str())
        .unwrap_or("")
        .to_string();

    let year_str = item["release_date"]
        .as_str()
        .or_else(|| item["first_air_date"].as_str())
        .unwrap_or("");
    let year = if year_str.len() >= 4 {
        year_str[..4].parse::<i32>().ok()
    } else {
        None
    };

    let poster = item["poster_path"]
        .as_str()
        .map(|p| format!("https://image.tmdb.org/t/p/w500{p}"));
    let backdrop = item["backdrop_path"]
        .as_str()
        .map(|p| format!("https://image.tmdb.org/t/p/w1280{p}"));

    let tmdb_id = item["id"]
        .as_i64()
        .map(|id| id.to_string())
        .unwrap_or_default();

    json!({
        "external_id": tmdb_id,
        "provider": "tmdb",
        "media_type": media_type,
        "title": title,
        "year": year,
        "poster": poster,
        "backdrop": backdrop,
    })
}

fn paginated_response(items: Vec<Value>, page: u64, total_pages: u64, total_results: u64) -> Value {
    json!({
        "items": items,
        "page": page,
        "total_pages": total_pages,
        "total_results": total_results,
        "db_index": {},
    })
}

// ─── Query param structs ──────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct TrendingQuery {
    #[serde(default = "default_media_type_all")]
    pub media_type: String,
    #[serde(default = "default_window")]
    pub window: String,
    pub language: Option<String>,
    #[serde(default = "default_page")]
    pub page: u64,
}

#[derive(Deserialize)]
pub struct ListQuery {
    #[serde(default = "default_kind_popular")]
    pub kind: String,
    #[serde(default = "default_media_type_movie")]
    pub media_type: String,
    pub language: Option<String>,
    #[serde(default = "default_page")]
    pub page: u64,
    pub region: Option<String>,
}

#[derive(Deserialize)]
pub struct WatchProvidersQuery {
    #[serde(default = "default_media_type_movie")]
    pub media_type: String,
    pub region: Option<String>,
}

#[derive(Deserialize)]
pub struct ProviderFeedQuery {
    #[serde(default = "default_media_type_movie")]
    pub media_type: String,
    pub provider_id: Option<String>,
    pub region: Option<String>,
    pub sort_by: Option<String>,
    pub language: Option<String>,
    #[serde(default = "default_page")]
    pub page: u64,
}

#[derive(Deserialize)]
pub struct AnimeQuery {
    #[serde(default = "default_kind_trending")]
    pub kind: String,
    pub season: Option<String>,
    pub year: Option<u32>,
    #[serde(default = "default_source_anilist")]
    pub source: String,
    #[serde(default = "default_page")]
    pub page: u64,
}

#[derive(Deserialize)]
pub struct SearchQuery {
    pub query: Option<String>,
    #[serde(default = "default_media_type_all")]
    pub media_type: String,
    pub language: Option<String>,
    #[serde(default = "default_page")]
    pub page: u64,
}

#[derive(Deserialize)]
pub struct TvdbFilterQuery {
    #[serde(default = "default_media_type_movie")]
    pub media_type: String,
    pub sort: Option<String>,
    pub sort_type: Option<String>,
    #[serde(default = "default_page")]
    pub page: u64,
}

#[derive(Deserialize)]
pub struct MdblistQuery {
    pub list_id: Option<String>,
    pub catalog_type: Option<String>,
    #[serde(default = "default_page")]
    pub page: u64,
}

#[derive(Deserialize)]
pub struct VerifyTmdbKeyQuery {
    pub api_key: Option<String>,
}

fn default_media_type_all() -> String {
    "all".to_string()
}
fn default_media_type_movie() -> String {
    "movie".to_string()
}
fn default_window() -> String {
    "week".to_string()
}
fn default_page() -> u64 {
    1
}
fn default_kind_popular() -> String {
    "popular".to_string()
}
fn default_kind_trending() -> String {
    "trending".to_string()
}
fn default_source_anilist() -> String {
    "anilist".to_string()
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/discover/trending
pub async fn discover_trending(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<TrendingQuery>,
) -> Response {
    if !state.config.discover_enabled {
        return discover_disabled_error();
    }
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let api_key = match resolve_tmdb_key(&state, user_id).await {
        Some(k) => k,
        None => return tmdb_key_required_error(),
    };

    let media_type = &params.media_type;
    let window = &params.window;
    let mut url = format!(
        "https://api.themoviedb.org/3/trending/{media_type}/{window}?api_key={api_key}&page={}",
        params.page
    );
    if let Some(ref lang) = params.language {
        url.push_str(&format!("&language={lang}"));
    }

    let resp = match state.http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("discover_trending: TMDB request failed: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API error"})),
            )
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return (
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"detail": "TMDB API returned error"})),
        )
            .into_response();
    }

    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("discover_trending: failed to parse TMDB response: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API parse error"})),
            )
                .into_response();
        }
    };

    let items = data["results"]
        .as_array()
        .map(|arr| arr.iter().map(normalize_tmdb_item).collect())
        .unwrap_or_default();
    let page = data["page"].as_u64().unwrap_or(params.page);
    let total_pages = data["total_pages"].as_u64().unwrap_or(1);
    let total_results = data["total_results"].as_u64().unwrap_or(0);

    Json(paginated_response(items, page, total_pages, total_results)).into_response()
}

/// GET /api/v1/discover/list
pub async fn discover_list(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListQuery>,
) -> Response {
    if !state.config.discover_enabled {
        return discover_disabled_error();
    }
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let api_key = match resolve_tmdb_key(&state, user_id).await {
        Some(k) => k,
        None => return tmdb_key_required_error(),
    };

    let media_type = &params.media_type;
    let kind = &params.kind;
    let mut url = format!(
        "https://api.themoviedb.org/3/{media_type}/{kind}?api_key={api_key}&page={}",
        params.page
    );
    if let Some(ref lang) = params.language {
        url.push_str(&format!("&language={lang}"));
    }
    if let Some(ref region) = params.region {
        url.push_str(&format!("&region={region}"));
    }

    let resp = match state.http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("discover_list: TMDB request failed: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API error"})),
            )
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return (
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"detail": "TMDB API returned error"})),
        )
            .into_response();
    }

    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("discover_list: failed to parse TMDB response: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API parse error"})),
            )
                .into_response();
        }
    };

    // Inject media_type for normalization since list endpoints don't include it in items
    let mt = media_type.as_str();
    let items = data["results"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|item| {
                    let mut i = item.clone();
                    if i.get("media_type").is_none() {
                        i["media_type"] = json!(mt);
                    }
                    normalize_tmdb_item(&i)
                })
                .collect()
        })
        .unwrap_or_default();
    let page = data["page"].as_u64().unwrap_or(params.page);
    let total_pages = data["total_pages"].as_u64().unwrap_or(1);
    let total_results = data["total_results"].as_u64().unwrap_or(0);

    Json(paginated_response(items, page, total_pages, total_results)).into_response()
}

/// GET /api/v1/discover/watch-providers
pub async fn discover_watch_providers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<WatchProvidersQuery>,
) -> Response {
    if !state.config.discover_enabled {
        return discover_disabled_error();
    }
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let api_key = match resolve_tmdb_key(&state, user_id).await {
        Some(k) => k,
        None => return tmdb_key_required_error(),
    };

    let media_type = &params.media_type;
    let mut url =
        format!("https://api.themoviedb.org/3/{media_type}/watch/providers?api_key={api_key}");
    if let Some(ref region) = params.region {
        url.push_str(&format!("&watch_region={region}"));
    }

    let resp = match state.http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("discover_watch_providers: TMDB request failed: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API error"})),
            )
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return (
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"detail": "TMDB API returned error"})),
        )
            .into_response();
    }

    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("discover_watch_providers: failed to parse TMDB response: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API parse error"})),
            )
                .into_response();
        }
    };

    let providers = data["results"].clone();
    Json(json!({
        "providers": providers,
        "region": params.region,
    }))
    .into_response()
}

/// GET /api/v1/discover/provider-feed
pub async fn discover_provider_feed(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ProviderFeedQuery>,
) -> Response {
    if !state.config.discover_enabled {
        return discover_disabled_error();
    }
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let api_key = match resolve_tmdb_key(&state, user_id).await {
        Some(k) => k,
        None => return tmdb_key_required_error(),
    };

    let media_type = &params.media_type;
    let mut url = format!(
        "https://api.themoviedb.org/3/discover/{media_type}?api_key={api_key}&page={}",
        params.page
    );
    if let Some(ref pid) = params.provider_id {
        url.push_str(&format!("&with_watch_providers={pid}"));
    }
    if let Some(ref region) = params.region {
        url.push_str(&format!("&watch_region={region}"));
    }
    if let Some(ref sort_by) = params.sort_by {
        url.push_str(&format!("&sort_by={sort_by}"));
    }
    if let Some(ref lang) = params.language {
        url.push_str(&format!("&language={lang}"));
    }

    let resp = match state.http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("discover_provider_feed: TMDB request failed: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API error"})),
            )
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return (
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"detail": "TMDB API returned error"})),
        )
            .into_response();
    }

    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("discover_provider_feed: failed to parse TMDB response: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API parse error"})),
            )
                .into_response();
        }
    };

    let mt = media_type.as_str();
    let items = data["results"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|item| {
                    let mut i = item.clone();
                    if i.get("media_type").is_none() {
                        i["media_type"] = json!(mt);
                    }
                    normalize_tmdb_item(&i)
                })
                .collect()
        })
        .unwrap_or_default();
    let page = data["page"].as_u64().unwrap_or(params.page);
    let total_pages = data["total_pages"].as_u64().unwrap_or(1);
    let total_results = data["total_results"].as_u64().unwrap_or(0);

    Json(paginated_response(items, page, total_pages, total_results)).into_response()
}

/// GET /api/v1/discover/anime
pub async fn discover_anime(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(_params): Query<AnimeQuery>,
) -> Response {
    if !state.config.discover_enabled {
        return discover_disabled_error();
    }
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let graphql_query = json!({
        "query": "query { Page(page: 1, perPage: 20) { media(type: ANIME, sort: TRENDING_DESC) { id title { english romaji } coverImage { large } } } }"
    });

    let resp = match state
        .http
        .post("https://graphql.anilist.co")
        .json(&graphql_query)
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("discover_anime: AniList request failed: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "AniList API error"})),
            )
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return (
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"detail": "AniList API returned error"})),
        )
            .into_response();
    }

    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("discover_anime: failed to parse AniList response: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "AniList API parse error"})),
            )
                .into_response();
        }
    };

    let empty = vec![];
    let media_list = data["data"]["Page"]["media"].as_array().unwrap_or(&empty);

    let items: Vec<Value> = media_list
        .iter()
        .map(|item| {
            let id = item["id"]
                .as_i64()
                .map(|i| i.to_string())
                .unwrap_or_default();
            let title = item["title"]["english"]
                .as_str()
                .or_else(|| item["title"]["romaji"].as_str())
                .unwrap_or("")
                .to_string();
            let poster = item["coverImage"]["large"].as_str().map(str::to_string);
            json!({
                "provider": "anilist",
                "external_id": id,
                "media_type": "series",
                "title": title,
                "poster": poster,
            })
        })
        .collect();

    let total = items.len() as u64;
    Json(json!({
        "items": items,
        "page": 1,
        "total_pages": 1,
        "total_results": total,
        "db_index": {},
    }))
    .into_response()
}

/// GET /api/v1/discover/search
pub async fn discover_search(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<SearchQuery>,
) -> Response {
    if !state.config.discover_enabled {
        return discover_disabled_error();
    }
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let api_key = match resolve_tmdb_key(&state, user_id).await {
        Some(k) => k,
        None => return tmdb_key_required_error(),
    };

    let q = match params.query {
        Some(ref q) if !q.is_empty() => q.clone(),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "query parameter required"})),
            )
                .into_response()
        }
    };

    let url = if params.media_type == "all" {
        format!(
            "https://api.themoviedb.org/3/search/multi?api_key={api_key}&query={q}&page={}",
            params.page
        )
    } else {
        let mt = &params.media_type;
        format!(
            "https://api.themoviedb.org/3/search/{mt}?api_key={api_key}&query={q}&page={}",
            params.page
        )
    };

    let mut url = url;
    if let Some(ref lang) = params.language {
        url.push_str(&format!("&language={lang}"));
    }

    let resp = match state.http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("discover_search: TMDB request failed: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API error"})),
            )
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return (
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"detail": "TMDB API returned error"})),
        )
            .into_response();
    }

    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("discover_search: failed to parse TMDB response: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TMDB API parse error"})),
            )
                .into_response();
        }
    };

    let mt = params.media_type.as_str();
    let items = data["results"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter(|item| item["media_type"].as_str() != Some("person"))
                .map(|item| {
                    let mut i = item.clone();
                    if i.get("media_type").is_none() || i["media_type"].as_str() == Some("") {
                        i["media_type"] = json!(mt);
                    }
                    normalize_tmdb_item(&i)
                })
                .collect()
        })
        .unwrap_or_default();
    let page = data["page"].as_u64().unwrap_or(params.page);
    let total_pages = data["total_pages"].as_u64().unwrap_or(1);
    let total_results = data["total_results"].as_u64().unwrap_or(0);

    Json(paginated_response(items, page, total_pages, total_results)).into_response()
}

/// GET /api/v1/discover/tvdb-filter
pub async fn discover_tvdb_filter(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<TvdbFilterQuery>,
) -> Response {
    if !state.config.discover_enabled {
        return discover_disabled_error();
    }
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    // Get user's TVDB key
    let tvdb_key: Option<String> = sqlx::query_scalar(
        "SELECT config->'tvdbc'->>'ak' FROM user_profiles WHERE user_id = $1 AND is_default = true",
    )
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    .ok()
    .flatten()
    .flatten()
    .filter(|s: &String| !s.is_empty());

    let tvdb_key = match tvdb_key {
        Some(k) => k,
        None => {
            return (
                StatusCode::PRECONDITION_FAILED,
                Json(json!({
                    "code": "tvdb_key_required",
                    "message": "Add your TVDB API key in Settings to use this feature."
                })),
            )
                .into_response();
        }
    };

    let media_segment = if params.media_type == "movie" {
        "movies"
    } else {
        "series"
    };
    let mut url = format!(
        "https://api4.thetvdb.com/v4/{media_segment}/filter?page={}",
        params.page
    );
    if let Some(ref sort) = params.sort {
        url.push_str(&format!("&sort={sort}"));
    }
    if let Some(ref sort_type) = params.sort_type {
        url.push_str(&format!("&sortType={sort_type}"));
    }

    let resp = match state
        .http
        .get(&url)
        .header("Authorization", format!("Bearer {tvdb_key}"))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("discover_tvdb_filter: TVDB request failed: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TVDB API error"})),
            )
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return (
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"detail": "TVDB API returned error"})),
        )
            .into_response();
    }

    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("discover_tvdb_filter: failed to parse TVDB response: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "TVDB API parse error"})),
            )
                .into_response();
        }
    };

    let items = data["data"]
        .as_array()
        .map(|arr| arr.to_vec())
        .unwrap_or_default();
    let total = items.len() as u64;

    Json(json!({
        "items": items,
        "page": params.page,
        "total_pages": 1,
        "total_results": total,
        "db_index": {},
    }))
    .into_response()
}

/// GET /api/v1/discover/mdblist
pub async fn discover_mdblist(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<MdblistQuery>,
) -> Response {
    if !state.config.discover_enabled {
        return discover_disabled_error();
    }
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let mdb_key: Option<String> = sqlx::query_scalar(
        "SELECT config->'mdblistc'->>'ak' FROM user_profiles WHERE user_id = $1 AND is_default = true",
    )
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    .ok()
    .flatten()
    .flatten()
    .filter(|s: &String| !s.is_empty());

    let mdb_key = match mdb_key {
        Some(k) => k,
        None => {
            return (
                StatusCode::PRECONDITION_FAILED,
                Json(json!({
                    "code": "mdblist_key_required",
                    "message": "Add your MDBList API key in Settings to use this feature."
                })),
            )
                .into_response();
        }
    };

    let list_id = match params.list_id {
        Some(ref id) if !id.is_empty() => id.clone(),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "list_id parameter required"})),
            )
                .into_response()
        }
    };

    let page = params.page.max(1);
    let offset = (page - 1) * 20;
    let url = format!(
        "https://mdblist.com/api/lists/{list_id}/items?apikey={mdb_key}&limit=20&offset={offset}"
    );

    let resp = match state.http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("discover_mdblist: MDBList request failed: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "MDBList API error"})),
            )
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return (
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({"detail": "MDBList API returned error"})),
        )
            .into_response();
    }

    let data: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("discover_mdblist: failed to parse MDBList response: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "MDBList API parse error"})),
            )
                .into_response();
        }
    };

    let items = data["movies"]
        .as_array()
        .or_else(|| data["shows"].as_array())
        .or_else(|| data.as_array())
        .cloned()
        .unwrap_or_default();
    let total = items.len() as u64;

    Json(json!({
        "items": items,
        "page": page,
        "total_pages": 1,
        "total_results": total,
        "db_index": {},
    }))
    .into_response()
}

/// GET /api/v1/discover/verify-tmdb-key
pub async fn verify_tmdb_key(
    State(state): State<Arc<AppState>>,
    Query(params): Query<VerifyTmdbKeyQuery>,
) -> Response {
    let api_key = match params.api_key {
        Some(ref k) if !k.is_empty() => k.clone(),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "api_key query param required"})),
            )
                .into_response()
        }
    };

    let url = format!("https://api.themoviedb.org/3/configuration?api_key={api_key}");
    let resp = match state.http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("verify_tmdb_key: TMDB request failed: {e}");
            return Json(json!({"valid": false})).into_response();
        }
    };

    Json(json!({"valid": resp.status().is_success()})).into_response()
}
