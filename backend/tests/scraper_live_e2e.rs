//! Live scraper smoke test: fetches EZTV RSS, resolves media, persists via `stream_store`.
//! Requires network, Postgres, and optional TMDB/Cinemeta keys from `.env`.

use mediafusion_api::jobs::handlers::spiders::eztv_rss::{parse_eztv_rss, EztvRssCrawl};
use mediafusion_api::{
    config::AppConfig,
    jobs::{handler::JobCtx, JobHandler},
    scrapers::media_resolve,
    state::AppState,
};
use tokio_util::sync::CancellationToken;

const FEED_URL: &str = "https://eztv.re/ezrss.xml";
const MAX_ITEMS: usize = 5;

#[tokio::test]
async fn eztv_rss_live_scrape_persists_streams() {
    let config = AppConfig::from_env();
    let state = AppState::build(config)
        .await
        .expect("build AppState from .env");

    let before: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM torrent_stream ts \
         JOIN stream st ON st.id = ts.stream_id \
         WHERE st.source = 'EZTV'",
    )
    .fetch_one(&state.pool)
    .await
    .unwrap_or(0);

    let client = &state.http;
    let text = client
        .get(FEED_URL)
        .timeout(std::time::Duration::from_secs(45))
        .send()
        .await
        .expect("fetch eztv rss")
        .error_for_status()
        .expect("eztv http ok")
        .text()
        .await
        .expect("eztv body");

    let rss_items = parse_eztv_rss(&text);
    assert!(
        !rss_items.is_empty(),
        "EZTV feed must return at least one item"
    );

    let mut persisted = 0usize;
    let mut processed = 0usize;

    for item in rss_items.iter().take(MAX_ITEMS * 4) {
        if persisted >= MAX_ITEMS {
            break;
        }

        let info_hash = match &item.info_hash {
            Some(h) if h.len() == 40 => h.to_lowercase(),
            _ => continue,
        };
        let title = item.title.trim();
        if title.is_empty() {
            continue;
        }
        if mediafusion_api::parser::contains_adult_keywords(title) {
            continue;
        }

        processed += 1;
        let parsed = mediafusion_api::parser::parse_title(title);
        let is_series = !parsed.seasons.is_empty() || !parsed.episodes.is_empty();
        let media_type = if is_series { "series" } else { "movie" };
        let files = if is_series {
            mediafusion_api::scrapers::prowlarr::build_series_files(&parsed, None, None)
        } else {
            vec![]
        };

        let stream = mediafusion_api::scrapers::ScrapedStream {
            info_hash,
            name: title.to_string(),
            source: "EZTV".to_string(),
            seeders: item.seeds,
            size: item.enclosure_size,
            parsed,
            files,
            is_cached: false,
            torrent_type: mediafusion_api::db::TorrentType::Public,
            torrent_file: None,
            announce_list: vec![],
        };

        let cfg = &state.config;
        let Some(meta) = media_resolve::search_meta_for_scraped(
            &state.pool,
            client,
            &stream,
            is_series,
            cfg.tmdb_api_key.as_deref(),
            cfg.imdb_cinemeta_fallback_enabled,
            &cfg.anime_metadata_source_order,
            &cfg.metadata_primary_source,
        )
        .await
        else {
            eprintln!("skip (no media): {title}");
            continue;
        };

        mediafusion_api::scrapers::stream_convert::write_back_torrents(
            &state.pool,
            std::slice::from_ref(&stream),
            &meta,
            media_type,
            None,
            None,
        )
        .await;

        let exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM torrent_stream WHERE info_hash = $1)")
                .bind(&stream.info_hash)
                .fetch_one(&state.pool)
                .await
                .expect("hash exists");

        assert!(exists, "stream must be in DB after write_back for {title}");
        persisted += 1;
        eprintln!(
            "persisted: {} media_id={} type={}",
            stream.info_hash, meta.media_id.0, media_type
        );
    }

    let after: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM torrent_stream ts \
         JOIN stream st ON st.id = ts.stream_id \
         WHERE st.source = 'EZTV'",
    )
    .fetch_one(&state.pool)
    .await
    .unwrap_or(0);

    eprintln!(
        "eztv live: processed={processed} persisted={persisted} eztv_rows before={before} after={after}"
    );

    assert!(
        persisted > 0,
        "expected at least one EZTV stream persisted (processed {processed} candidates)"
    );
    assert!(
        after >= before + persisted as i64,
        "torrent_stream count should increase"
    );
}

/// Full job handler path (processes the entire feed; very slow due to per-item rate limits).
#[tokio::test]
#[ignore = "processes full EZTV feed (~5s/item); run with --ignored when needed"]
async fn eztv_rss_job_handler_live() {
    let config = AppConfig::from_env();
    let state = AppState::build(config).await.expect("build AppState");

    let before: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM torrent_stream ts \
         JOIN stream st ON st.id = ts.stream_id \
         WHERE st.source = 'EZTV'",
    )
    .fetch_one(&state.pool)
    .await
    .unwrap_or(0);

    let ctx = JobCtx {
        job_id: 0,
        attempt: 1,
        state: state.clone(),
        cancel: CancellationToken::new(),
    };

    EztvRssCrawl
        .run(serde_json::json!({}), ctx)
        .await
        .expect("eztv job handler");

    let after: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM torrent_stream ts \
         JOIN stream st ON st.id = ts.stream_id \
         WHERE st.source = 'EZTV'",
    )
    .fetch_one(&state.pool)
    .await
    .unwrap_or(0);

    eprintln!("eztv job handler: eztv rows before={before} after={after}");
    assert!(
        after > before,
        "job handler should insert at least one new EZTV torrent"
    );
}
