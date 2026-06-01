//! MDBList list ingestion — fetch list items and store via the metadata funnel.

use sqlx::PgPool;
use tracing::{info, warn};

use crate::db::{store_media, MediaType, StoreMediaOpts};

use super::{fetch_normalized, FetchCtx};

const MDBLIST_BASE: &str = "https://api.mdblist.com";

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
