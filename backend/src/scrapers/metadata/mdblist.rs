//! MDBList list ingestion — fetch list items and store via the metadata funnel.

use fred::clients::Client as RedisClient;
use sqlx::PgPool;
use tracing::{info, warn};

use crate::{
    cache,
    db::{MediaType, StoreMediaOpts, store_media},
    models::user_data::MdbListItem,
};

use super::{FetchCtx, fetch_normalized};

const MDBLIST_BASE: &str = "https://api.mdblist.com";
const MDBLIST_BATCH_SIZE: i64 = 200;

fn has_valid_imdb_id(item: &serde_json::Value) -> Option<&str> {
    let imdb_id = item.get("imdb_id")?.as_str()?;
    imdb_id.starts_with("tt").then_some(imdb_id)
}

fn list_items_key(list: &MdbListItem, genre: Option<&str>) -> String {
    format!(
        "mdblist:all_ids:{}:{}:{}:sort_{}:order_{}",
        list.id,
        list.catalog_type,
        genre.unwrap_or("all"),
        list.sort,
        list.order
    )
}

/// Fetch every IMDb id from an MDBList (paginated), with a 1-hour Redis cache.
pub async fn fetch_all_list_imdb_ids(
    http: &reqwest::Client,
    redis: &RedisClient,
    api_key: &str,
    list: &MdbListItem,
    genre: Option<&str>,
) -> Vec<String> {
    let cache_key = list_items_key(list, genre);
    if let Some(cached) = cache::get_json(redis, &cache_key).await {
        if let Some(ids) = cached.as_array() {
            return ids
                .iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect();
        }
    }

    let is_series = list.catalog_type == "series";
    let items_key = if is_series { "shows" } else { "movies" };
    let mut all_imdb_ids = Vec::new();
    let mut offset = 0i64;

    loop {
        let mut url = format!(
            "{MDBLIST_BASE}/lists/{}/items?apikey={api_key}&limit={MDBLIST_BATCH_SIZE}&offset={offset}&append_to_response=genre&sort={}&order={}",
            list.id, list.sort, list.order
        );
        if let Some(g) = genre.filter(|g| !g.is_empty()) {
            url.push_str(&format!("&filter_genre={g}"));
        }

        let resp = match http
            .get(&url)
            .timeout(std::time::Duration::from_secs(30))
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => r,
            Ok(r) => {
                warn!("mdblist all_ids list={}: HTTP {}", list.id, r.status());
                break;
            }
            Err(e) => {
                warn!(
                    error_kind = crate::util::http::transport_error_kind(&e),
                    "mdblist all_ids list={}: request failed: {e}", list.id
                );
                break;
            }
        };

        let data: serde_json::Value = match resp.json().await {
            Ok(v) => v,
            Err(e) => {
                warn!("mdblist all_ids list={}: parse error: {e}", list.id);
                break;
            }
        };

        let items = match data[items_key].as_array() {
            Some(a) if !a.is_empty() => a,
            _ => break,
        };

        let batch_ids: Vec<String> = items
            .iter()
            .filter_map(has_valid_imdb_id)
            .map(str::to_string)
            .collect();
        if batch_ids.is_empty() {
            break;
        }

        all_imdb_ids.extend(batch_ids);
        offset += MDBLIST_BATCH_SIZE;

        if items.len() < MDBLIST_BATCH_SIZE as usize {
            break;
        }
    }

    if !all_imdb_ids.is_empty() {
        let payload: Vec<serde_json::Value> = all_imdb_ids
            .iter()
            .cloned()
            .map(serde_json::Value::String)
            .collect();
        cache::set_json(redis, &cache_key, &serde_json::Value::Array(payload), 3600).await;
    }

    all_imdb_ids
}

/// Paginate an MDBList and ingest each valid IMDb id through the IMDb provider + funnel.
pub async fn ingest_list(
    pool: &PgPool,
    http: &reqwest::Client,
    ctx: &FetchCtx<'_>,
    list_id: &str,
    catalog_name: &str,
    media_type: &str,
) -> Result<u32, String> {
    let api_key = ctx
        .mdblist_api_key
        .ok_or_else(|| "MDBLIST_API_KEY not configured".to_string())?;

    let is_series = media_type == "series";
    let mt = if is_series {
        MediaType::Series
    } else {
        MediaType::Movie
    };

    let mut offset = 0;
    let limit = 200;
    let mut ingested = 0u32;

    loop {
        let url = format!(
            "{MDBLIST_BASE}/lists/{list_id}/items?apikey={api_key}&limit={limit}&offset={offset}"
        );
        let resp = http
            .get(&url)
            .timeout(std::time::Duration::from_secs(30))
            .send()
            .await
            .map_err(|e| format!("MDBList request failed: {e}"))?;

        if !resp.status().is_success() {
            return Err(format!("MDBList HTTP {}", resp.status()));
        }

        let data: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| format!("MDBList parse error: {e}"))?;

        let key = if is_series { "shows" } else { "movies" };
        let items = match data[key].as_array() {
            Some(a) if !a.is_empty() => a,
            _ => break,
        };

        for item in items {
            let Some(imdb_id) = item["imdb_id"].as_str() else {
                continue;
            };
            if !imdb_id.starts_with("tt") {
                continue;
            }

            let mut meta = match fetch_normalized(http, ctx, "imdb", imdb_id, is_series).await {
                Some(m) => m,
                None => {
                    warn!("mdblist: could not fetch metadata for {imdb_id}");
                    continue;
                }
            };
            meta.media_type = mt;
            meta.catalogs.push(catalog_name.to_string());

            if let Err(e) = store_media(pool, &meta, StoreMediaOpts::default()).await {
                warn!("mdblist: store_media({imdb_id}): {e}");
                continue;
            }
            ingested += 1;
        }

        if items.len() < limit {
            break;
        }
        offset += limit;
    }

    info!("mdblist: ingested {ingested} items from list {list_id} into catalog '{catalog_name}'");
    Ok(ingested)
}
