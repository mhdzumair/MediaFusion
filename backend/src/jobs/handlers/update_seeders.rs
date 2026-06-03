use std::collections::HashMap;

use async_trait::async_trait;
use tokio::net::UdpSocket;
use tokio::time::{timeout, Duration};
use tracing::{debug, info, warn};

use crate::jobs::{
    enqueue::{enqueue_simple, EnqueueOpts},
    error::JobError,
    handler::{JobCtx, JobHandler},
};

pub struct UpdateSeeders;

const DEFAULT_TRACKERS_FALLBACK: &[&str] = &[
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
];

/// Parse a UDP tracker URL into (host, port).
fn parse_udp_tracker(url: &str) -> Option<(String, u16)> {
    let stripped = url.trim_start_matches("udp://");
    // Strip any path component (/announce etc.)
    let host_port = stripped.split('/').next()?;
    let (host, port_str) = host_port.rsplit_once(':')?;

    let port: u16 = port_str.parse().ok()?;
    Some((host.to_string(), port))
}

/// Decode a hex info_hash string into 20 bytes.
fn decode_info_hash(hex: &str) -> Option<[u8; 20]> {
    if hex.len() != 40 {
        return None;
    }
    let mut bytes = [0u8; 20];
    for (i, chunk) in hex.as_bytes().chunks(2).enumerate() {
        let hi = (chunk[0] as char).to_digit(16)?;
        let lo = (chunk[1] as char).to_digit(16)?;
        bytes[i] = ((hi << 4) | lo) as u8;
    }
    Some(bytes)
}

/// BEP-15 UDP tracker scrape.
///
/// Returns a map of info_hash (hex) → seeders for all hashes that the tracker
/// reported.  Missing entries mean the tracker did not respond for that hash.
async fn udp_scrape(tracker_url: &str, info_hashes: &[String]) -> HashMap<String, i32> {
    let Some((host, port)) = parse_udp_tracker(tracker_url) else {
        return HashMap::new();
    };

    let addr = format!("{host}:{port}");
    let socket = match UdpSocket::bind("0.0.0.0:0").await {
        Ok(s) => s,
        Err(e) => {
            warn!("update_seeders: UDP bind failed for {tracker_url}: {e}");
            return HashMap::new();
        }
    };

    if let Err(e) = socket.connect(&addr).await {
        warn!("update_seeders: UDP connect to {addr} failed: {e}");
        return HashMap::new();
    }

    let transaction_id: u32 = rand::random::<u32>();

    // ── Step 1: CONNECT ───────────────────────────────────────────────────────
    let mut connect_req = [0u8; 16];
    connect_req[..8].copy_from_slice(&0x41727101980u64.to_be_bytes());
    connect_req[8..12].copy_from_slice(&0u32.to_be_bytes()); // action = 0 (connect)
    connect_req[12..16].copy_from_slice(&transaction_id.to_be_bytes());

    let send_result = timeout(Duration::from_secs(5), socket.send(&connect_req)).await;
    if send_result.is_err() || send_result.unwrap().is_err() {
        debug!("update_seeders: connect send timeout/error for {addr}");
        return HashMap::new();
    }

    let mut connect_resp = [0u8; 16];
    let recv_result = timeout(Duration::from_secs(5), socket.recv(&mut connect_resp)).await;
    if recv_result.is_err() {
        debug!("update_seeders: connect recv timeout for {addr}");
        return HashMap::new();
    }
    if recv_result.unwrap().is_err() {
        return HashMap::new();
    }

    let resp_action = u32::from_be_bytes(connect_resp[0..4].try_into().unwrap_or_default());
    let resp_tid = u32::from_be_bytes(connect_resp[4..8].try_into().unwrap_or_default());
    if resp_action != 0 || resp_tid != transaction_id {
        warn!("update_seeders: unexpected connect response from {addr}");
        return HashMap::new();
    }

    let connection_id = i64::from_be_bytes(connect_resp[8..16].try_into().unwrap_or_default());

    // ── Step 2: SCRAPE (max 74 hashes per request to stay under 1480 bytes) ──
    let mut results: HashMap<String, i32> = HashMap::new();

    for chunk in info_hashes.chunks(74) {
        let decoded: Vec<(String, [u8; 20])> = chunk
            .iter()
            .filter_map(|h| decode_info_hash(h).map(|b| (h.clone(), b)))
            .collect();

        if decoded.is_empty() {
            continue;
        }

        let scrape_tid: u32 = rand::random::<u32>();
        let mut scrape_req = vec![0u8; 16 + decoded.len() * 20];
        scrape_req[..8].copy_from_slice(&connection_id.to_be_bytes());
        scrape_req[8..12].copy_from_slice(&2u32.to_be_bytes()); // action = 2 (scrape)
        scrape_req[12..16].copy_from_slice(&scrape_tid.to_be_bytes());
        for (i, (_, hash)) in decoded.iter().enumerate() {
            let offset = 16 + i * 20;
            scrape_req[offset..offset + 20].copy_from_slice(hash);
        }

        let send_result = timeout(Duration::from_secs(5), socket.send(&scrape_req)).await;
        if send_result.is_err() || send_result.unwrap().is_err() {
            debug!("update_seeders: scrape send timeout/error for {addr}");
            continue;
        }

        // Max response: 8 (header) + 74*12 = 896 bytes
        let mut scrape_resp = vec![0u8; 8 + decoded.len() * 12];
        let recv_result = timeout(Duration::from_secs(5), socket.recv(&mut scrape_resp)).await;
        let n = match recv_result {
            Ok(Ok(n)) => n,
            _ => {
                debug!("update_seeders: scrape recv timeout for {addr}");
                continue;
            }
        };

        if n < 8 {
            continue;
        }

        let resp_action = u32::from_be_bytes(scrape_resp[0..4].try_into().unwrap_or_default());
        let resp_tid = u32::from_be_bytes(scrape_resp[4..8].try_into().unwrap_or_default());
        if resp_action != 2 || resp_tid != scrape_tid {
            warn!("update_seeders: unexpected scrape response from {addr}");
            continue;
        }

        let available_entries = (n - 8) / 12;
        for (i, (hash_hex, _)) in decoded.iter().enumerate().take(available_entries) {
            let offset = 8 + i * 12;
            if offset + 12 > n {
                break;
            }
            let seeders = u32::from_be_bytes(
                scrape_resp[offset..offset + 4]
                    .try_into()
                    .unwrap_or_default(),
            ) as i32;
            // completed is at offset+4, leechers at offset+8 — not needed here
            results.insert(hash_hex.clone(), seeders);
        }
    }

    results
}

/// Scrape seeders for a batch of torrents across all available trackers.
///
/// Returns a map of info_hash → best (max) seeders count seen.
async fn scrape_seeders(torrent_rows: &[(i32, String, Vec<String>)]) -> HashMap<String, i32> {
    let mut best: HashMap<String, i32> = HashMap::new();

    // Collect the set of tracker URLs to query.
    // Per-torrent tracker URLs are interleaved with bundled defaults from trackers.json.
    let bundled: Vec<String> = crate::util::trackers::all_trackers();
    let mut tracker_set: Vec<String> = if bundled.is_empty() {
        DEFAULT_TRACKERS_FALLBACK
            .iter()
            .map(|s| s.to_string())
            .collect()
    } else {
        bundled
    };

    for (_, _, trackers) in torrent_rows {
        for t in trackers {
            if t.starts_with("udp://") && !tracker_set.contains(t) {
                tracker_set.push(t.clone());
            }
        }
    }

    let all_hashes: Vec<String> = torrent_rows.iter().map(|(_, h, _)| h.clone()).collect();

    for tracker_url in &tracker_set {
        let tracker_results = udp_scrape(tracker_url, &all_hashes).await;
        for (hash, seeders) in tracker_results {
            let entry = best.entry(hash).or_insert(0);
            *entry = (*entry).max(seeders);
        }
    }

    best
}

#[async_trait]
impl JobHandler for UpdateSeeders {
    const QUEUE: &'static str = "update_seeders";
    const CONCURRENCY: usize = 2;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let page = args.get("page").and_then(|v| v.as_i64()).unwrap_or(0);
        let page_size = args.get("page_size").and_then(|v| v.as_i64()).unwrap_or(50) as i32;

        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        // ── Fetch the current page of stale torrents ──────────────────────────
        let rows = sqlx::query(
            r#"
            SELECT ts.id, ts.info_hash,
                   COALESCE(array_agg(t.url) FILTER (WHERE t.url IS NOT NULL), '{}') AS tracker_urls
            FROM torrent_stream ts
            LEFT JOIN torrent_tracker_link ttl ON ttl.torrent_stream_id = ts.id
            LEFT JOIN tracker t ON t.id = ttl.tracker_id
            JOIN stream s ON s.id = ts.stream_id
            WHERE ts.seeders IS NULL
              AND s.updated_at < NOW() - INTERVAL '7 days'
            GROUP BY ts.id, ts.info_hash
            LIMIT $1 OFFSET $2
            "#,
        )
        .bind(page_size)
        .bind((page * page_size as i64) as i32)
        .fetch_all(&ctx.state.pool_ro)
        .await?;

        if rows.is_empty() {
            info!("update_seeders: page {page} — no more stale torrents, done");
            return Ok(());
        }

        let count = rows.len();
        info!("update_seeders: page {page} — processing {count} torrents");

        // Map rows into typed tuples: (id, info_hash, tracker_urls)
        let torrent_data: Vec<(i32, String, Vec<String>)> = rows
            .iter()
            .map(|row| {
                use sqlx::Row;
                let id: i32 = row.try_get("id").unwrap_or(0);
                let info_hash: String = row.try_get("info_hash").unwrap_or_default();
                let tracker_urls: Vec<String> = row.try_get("tracker_urls").unwrap_or_default();
                (id, info_hash, tracker_urls)
            })
            .collect();

        if ctx.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        // ── Scrape seeders ────────────────────────────────────────────────────
        let seeder_map = scrape_seeders(&torrent_data).await;

        if seeder_map.is_empty() {
            info!("update_seeders: page {page} — trackers returned no data");
        } else {
            // ── Bulk update ───────────────────────────────────────────────────
            let (hash_list, seeders_list): (Vec<String>, Vec<i32>) = seeder_map.into_iter().unzip();

            sqlx::query(
                r#"
                UPDATE torrent_stream
                SET seeders     = results.seeders,
                    updated_at  = NOW()
                FROM (
                    SELECT UNNEST($1::text[]) AS info_hash,
                           UNNEST($2::int[])  AS seeders
                ) AS results
                WHERE torrent_stream.info_hash = results.info_hash
                "#,
            )
            .bind(&hash_list)
            .bind(&seeders_list)
            .execute(&ctx.state.pool)
            .await?;

            info!(
                "update_seeders: page {page} — updated {} torrent seeder counts",
                hash_list.len()
            );
        }

        // ── Enqueue next page ────────────────────────────────────────────────
        let next_page = page + 1;
        let next_payload = serde_json::json!({
            "page": next_page,
            "page_size": page_size,
        });

        enqueue_simple(
            &ctx.state.pool,
            Self::QUEUE,
            &next_payload,
            EnqueueOpts {
                dedupe_key: Some(format!("update_seeders:page:{next_page}")),
                ..Default::default()
            },
        )
        .await?;

        debug!("update_seeders: enqueued page {next_page}");
        Ok(())
    }
}
