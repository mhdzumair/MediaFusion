use std::{collections::HashMap, sync::Arc};

use axum::{
    extract::{Path, State},
    response::{IntoResponse, Json},
};
use hmac::{Hmac, KeyInit, Mac};
use serde_json::{json, Value};
use sha2::Sha256;

use crate::{cache, crypto, db::genres, models::user_data::UserData, state::AppState};

type HmacSha256 = Hmac<Sha256>;

// ─── Static catalog definitions (mirrors manifest.json.j2) ───────────────────

struct CatalogMeta {
    catalog_type: &'static str,
    name: &'static str,
    /// None  = use genres[type]; Some([]) = no genre extra; Some(list) = fixed genres
    fixed_genres: Option<&'static [&'static str]>,
    is_search: bool,
}

const LIVE_SPORT_GENRES: &[&str] = &[
    "American Football",
    "Athletics",
    "Aussie Rules",
    "Baseball",
    "Basketball",
    "Bowling",
    "Boxing",
    "Cricket",
    "Cycling",
    "Dart",
    "Floorball",
    "Football",
    "Futsal",
    "GAA",
    "Golf",
    "Gymnastics",
    "Handball",
    "Hockey",
    "Horse Racing",
    "Lacrosse",
    "MMA",
    "Motor Sport",
    "Netball",
    "Other Sports",
    "Rugby/AFL",
    "Squash",
    "Tennis",
    "Volleyball",
];
const FIGHTING_GENRES: &[&str] = &["WWE", "UFC"];

fn catalog_meta(id: &str) -> Option<CatalogMeta> {
    macro_rules! c {
        ($t:expr, $n:expr) => {
            Some(CatalogMeta {
                catalog_type: $t,
                name: $n,
                fixed_genres: None,
                is_search: false,
            })
        };
        ($t:expr, $n:expr, genres: $g:expr) => {
            Some(CatalogMeta {
                catalog_type: $t,
                name: $n,
                fixed_genres: Some($g),
                is_search: false,
            })
        };
        ($t:expr, $n:expr, search) => {
            Some(CatalogMeta {
                catalog_type: $t,
                name: $n,
                fixed_genres: Some(&[]),
                is_search: true,
            })
        };
    }

    match id {
        "mediafusion_search_movies" => c!("movie", "Movies", search),
        "mediafusion_search_series" => c!("series", "Series", search),
        "mediafusion_search_tv" => c!("tv", "Live TV", search),
        "my_library_movies" => c!("movie", "My Library Movies"),
        "my_library_series" => c!("series", "My Library Series"),
        "my_library_tv" => c!("tv", "My Library TV"),
        "tamil_hdrip" => c!("movie", "Tamil HD Movies"),
        "tamil_tcrip" => c!("movie", "Tamil TCRip Movies"),
        "tamil_old" => c!("movie", "Tamil Old Movies"),
        "tamil_dubbed" => c!("movie", "Tamil Dubbed Movies"),
        "tamil_series" => c!("series", "Tamil Series"),
        "malayalam_tcrip" => c!("movie", "Malayalam TCRip Movies"),
        "malayalam_hdrip" => c!("movie", "Malayalam HD Movies"),
        "malayalam_old" => c!("movie", "Malayalam Old Movies"),
        "malayalam_dubbed" => c!("movie", "Malayalam Dubbed Movies"),
        "malayalam_series" => c!("series", "Malayalam Series"),
        "telugu_tcrip" => c!("movie", "Telugu TCRip Movies"),
        "telugu_hdrip" => c!("movie", "Telugu HD Movies"),
        "telugu_old" => c!("movie", "Telugu Old Movies"),
        "telugu_dubbed" => c!("movie", "Telugu Dubbed Movies"),
        "telugu_series" => c!("series", "Telugu Series"),
        "hindi_tcrip" => c!("movie", "Hindi TCRip Movies"),
        "hindi_hdrip" => c!("movie", "Hindi HD Movies"),
        "hindi_old" => c!("movie", "Hindi Old Movies"),
        "hindi_dubbed" => c!("movie", "Hindi Dubbed Movies"),
        "hindi_series" => c!("series", "Hindi Series"),
        "kannada_tcrip" => c!("movie", "Kannada TCRip Movies"),
        "kannada_hdrip" => c!("movie", "Kannada HD Movies"),
        "kannada_old" => c!("movie", "Kannada Old Movies"),
        "kannada_dubbed" => c!("movie", "Kannada Dubbed Movies"),
        "kannada_series" => c!("series", "Kannada Series"),
        "english_hdrip" => c!("movie", "English HD Movies"),
        "english_tcrip" => c!("movie", "English TCRip Movies"),
        "english_series" => c!("series", "English Series"),
        "bangla_movies" => c!("series", "Bangla Movies"),
        "bangla_series" => c!("movie", "Bangla Series"),
        "punjabi_movies" => c!("movie", "Punjabi Movies"),
        "punjabi_series" => c!("series", "Punjabi Series"),
        "arabic_movies" => c!("movie", "Arabic Movies"),
        "arabic_series" => c!("series", "Arabic Series"),
        "live_tv" => c!("tv", "Live TV"),
        "formula_racing" => c!("series", "Formula Racing", genres: &[]),
        "motogp_racing" => c!("series", "MotoGP Racing", genres: &[]),
        "american_football" => c!("movie", "American Football", genres: &[]),
        "basketball" => c!("movie", "Basketball", genres: &[]),
        "baseball" => c!("movie", "Baseball", genres: &[]),
        "football" => c!("movie", "Football", genres: &[]),
        "hockey" => c!("movie", "Hockey", genres: &[]),
        "rugby" => c!("movie", "Rugby/AFL", genres: &[]),
        "fighting" => c!("movie", "Fighting (WWE, UFC)", genres: FIGHTING_GENRES),
        "tgx_movie" => c!("movie", "TGx Movies"),
        "tgx_series" => c!("series", "TGx Series"),
        "contribution_movies" => c!("movie", "Contribution Movies"),
        "contribution_series" => c!("series", "Contribution Series"),
        "other_sports" => c!("movie", "Other Sports", genres: &[]),
        "live_sport_events" => c!("events", "Live Sport Events", genres: LIVE_SPORT_GENRES),
        "prowlarr_movies" => c!("movie", "Prowlarr Scraped Movies"),
        "prowlarr_series" => c!("series", "Prowlarr Scraped Series"),
        _ => None,
    }
}

// ─── HMAC cache key ───────────────────────────────────────────────────────────

fn manifest_cache_key(version: &str, secret_key_raw: &str, user_data: &UserData) -> String {
    let payload = serde_json::to_string(user_data).unwrap_or_default();
    let mut mac =
        HmacSha256::new_from_slice(secret_key_raw.as_bytes()).expect("HMAC accepts any key");
    mac.update(payload.as_bytes());
    let digest = mac.finalize().into_bytes();
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    format!("manifest:rs:{version}:{hex}")
}

// ─── Manifest builder ─────────────────────────────────────────────────────────

fn build_manifest(
    config: &crate::config::AppConfig,
    user_data: &UserData,
    genres: &HashMap<String, Vec<String>>,
) -> Value {
    let suffix = user_data.addon_name_suffix();
    let addon_name = format!("{}{suffix}", config.addon_name);
    let addon_id = format!(
        "stremio.addons.{}",
        addon_name.to_lowercase().replace(' ', "")
    );

    // Resources
    let mut resources: Vec<Value> = Vec::new();
    if user_data.enable_catalogs || !user_data.watchlist_providers().is_empty() {
        resources.push(json!("catalog"));
    }
    resources.push(json!({
        "name": "stream",
        "types": ["movie","series","tv","events"],
        "idPrefixes": ["tt","tmdb:","tvdb:","mal:","mf","dl"]
    }));
    let meta_prefixes: Vec<&str> = if user_data.enable_imdb_metadata {
        vec!["tt", "tmdb:", "tvdb:", "mal:", "mf", "dl"]
    } else {
        vec!["mf", "dl"]
    };
    resources.push(json!({
        "name": "meta",
        "types": ["movie","series","tv","events"],
        "idPrefixes": meta_prefixes
    }));

    // Watchlist catalogs (served by Python; Rust returns 404 on those requests)
    let mut catalogs: Vec<Value> = Vec::new();
    for (service, short_name) in user_data.watchlist_providers() {
        catalogs.push(json!({
            "id": format!("{service}_watchlist_movies"),
            "name": format!("{short_name} Watchlist"),
            "type": "movie",
            "extra": [{"name":"skip","isRequired":false}]
        }));
        catalogs.push(json!({
            "id": format!("{service}_watchlist_series"),
            "name": format!("{short_name} Watchlist"),
            "type": "series",
            "extra": [{"name":"skip","isRequired":false}]
        }));
    }

    // Regular catalogs — source of truth is catalog_configs (cc), not selected_catalogs (sc)
    if user_data.enable_catalogs {
        for cfg in user_data.catalog_configs.iter().filter(|c| c.enabled) {
            let cid = &cfg.catalog_id;
            if cid.starts_with("mdblist_") {
                continue;
            }
            if cid.starts_with("my_library_") && user_data.user_id.is_none() {
                continue;
            }
            let Some(meta) = catalog_meta(cid) else {
                continue;
            };

            let genre_opts: Vec<&str> = match meta.fixed_genres {
                Some(fixed) => fixed.to_vec(),
                None => genres
                    .get(meta.catalog_type)
                    .map(|v| v.iter().map(String::as_str).collect())
                    .unwrap_or_default(),
            };

            let mut extra: Vec<Value> = if meta.is_search {
                vec![json!({"name":"search","isRequired":true})]
            } else {
                vec![json!({"name":"skip","isRequired":false})]
            };
            if !genre_opts.is_empty() {
                extra.push(json!({"name":"genre","isRequired":false,"options":genre_opts}));
            }

            catalogs.push(json!({
                "id": cid,
                "type": meta.catalog_type,
                "name": meta.name,
                "extra": extra,
            }));
        }
    }

    let mut manifest = json!({
        "id": addon_id,
        "version": config.addon_version,
        "name": addon_name,
        "description": config.addon_description,
        "logo": config.logo_url,
        "behaviorHints": {"configurable":true,"configurationRequired":false},
        "resources": resources,
        "types": ["movie","series","tv","events"],
        "catalogs": catalogs,
    });
    if let Some(email) = &config.contact_email {
        manifest["contactEmail"] = json!(email);
    }
    manifest
}

// ─── Route handlers ───────────────────────────────────────────────────────────

pub async fn public_manifest(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    serve_manifest(state, UserData::default()).await
}

pub async fn user_manifest(
    Path(secret_str): Path<String>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let user_data = serde_json::from_value::<UserData>(
        crypto::resolve_user_data(
            &secret_str,
            &state.config.secret_key,
            &state.pool,
            &state.redis,
        )
        .await,
    )
    .unwrap_or_default();
    serve_manifest(state, user_data).await
}

async fn serve_manifest(state: Arc<AppState>, user_data: UserData) -> impl IntoResponse {
    let cache_key = manifest_cache_key(
        &state.config.addon_version,
        &state.config.secret_key_raw,
        &user_data,
    );
    let ttl = state.config.meta_cache_ttl.min(300);

    if let Some(cached) = cache::get_json(&state.redis, &cache_key).await {
        return Json(cached).into_response();
    }

    const GENRES_KEY: &str = "genres:all_by_type:rs";
    let genres: HashMap<String, Vec<String>> =
        if let Some(v) = cache::get_json(&state.redis, GENRES_KEY).await {
            serde_json::from_value(v).unwrap_or_default()
        } else {
            let g = genres::get_all_genres_by_type(&state.pool_ro).await;
            let gv = serde_json::to_value(&g).unwrap_or_default();
            cache::set_json(&state.redis, GENRES_KEY, &gv, 3600).await;
            g
        };
    let genres = {
        let keyword_filters = state
            .keyword_filters
            .read()
            .unwrap_or_else(|e| e.into_inner());
        keyword_filters.filter_genres_by_type(genres)
    };

    let manifest = build_manifest(&state.config, &user_data, &genres);
    cache::set_json(&state.redis, &cache_key, &manifest, ttl).await;
    Json(manifest).into_response()
}
