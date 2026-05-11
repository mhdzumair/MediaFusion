use std::str::FromStr;
/// Background scheduler — mirrors workers/scheduler.py.
///
/// Each enabled job spawns a dedicated tokio task that:
///   1. Parses its cron expression at startup.
///   2. Sleeps until the next scheduled tick.
///   3. Enqueues the corresponding taskiq actor via Redis Streams.
///   4. Loops back to step 2.
///
/// The taskiq wire format (XADD + task-record SET) is identical to the
/// `enqueue_taskiq` helper already used in admin_scrapers.rs.
use std::sync::Arc;

use chrono::Utc;
use cron::Schedule;
use fred::prelude::{KeysInterface, ListInterface, StreamsInterface};
use serde_json::{json, Value};
use tracing::{debug, info, warn};

use crate::state::AppState;

// ── Wire format ───────────────────────────────────────────────────────────────

async fn enqueue(state: &Arc<AppState>, actor_name: &str, queue: &str, kwargs: Value) {
    let task_id = uuid::Uuid::new_v4().simple().to_string();
    let queue_key = format!("mediafusion:taskiq:{queue}");

    let mut kw_map = match kwargs {
        Value::Object(m) => m,
        _ => serde_json::Map::new(),
    };
    kw_map.insert("_taskiq_task_id".into(), json!(task_id));

    let msg = json!({
        "task_id": task_id,
        "task_name": actor_name,
        "labels": { "queue_name": queue_key },
        "labels_types": null,
        "args": [],
        "kwargs": Value::Object(kw_map.clone()),
    });
    let msg_json = match serde_json::to_string(&msg) {
        Ok(s) => s,
        Err(e) => {
            warn!("scheduler: serialize {actor_name}: {e}");
            return;
        }
    };

    if let Err(e) = state
        .redis
        .xadd::<String, _, _, _, _>(
            &queue_key,
            false,
            None,
            "*",
            vec![("data", msg_json.as_str())],
        )
        .await
    {
        warn!("scheduler: xadd {actor_name}: {e}");
        return;
    }

    // Remove the internal marker from the stored record
    kw_map.remove("_taskiq_task_id");
    let record = json!({
        "task_id": task_id,
        "actor_name": actor_name,
        "queue_name": queue_key,
        "args_payload": [],
        "kwargs_payload": Value::Object(kw_map),
        "status": "pending",
        "created_at": Utc::now().to_rfc3339(),
    });
    let record_json = match serde_json::to_string(&record) {
        Ok(s) => s,
        Err(e) => {
            warn!("scheduler: serialize record {actor_name}: {e}");
            return;
        }
    };
    let task_key = format!("mediafusion:taskiq:task:{task_id}");
    if let Err(e) = state
        .redis
        .set::<String, _, _>(
            &task_key,
            record_json.as_str(),
            Some(fred::types::Expiration::EX(7 * 24 * 3600)),
            None,
            false,
        )
        .await
    {
        warn!("scheduler: set record {actor_name}: {e}");
    }
    let _: Result<i64, _> = state
        .redis
        .lpush("mediafusion:taskiq:tasks:recent", task_id.as_str())
        .await;
    let _: Result<(), _> = state
        .redis
        .ltrim("mediafusion:taskiq:tasks:recent", 0, 1999)
        .await;

    debug!("scheduler: enqueued {actor_name} → {queue} (task_id={task_id})");
}

// ── Cron expression conversion ────────────────────────────────────────────────
//
// Python/Unix cron uses 5 fields: min hour dom month dow
// The `cron` crate v0.12 requires 7 fields:  sec min hour dom month dow year
// Convert by prepending "0" (fire at :00 seconds) and appending "*" (any year).

fn to_cron_schedule(five_field: &str) -> Result<Schedule, cron::error::Error> {
    let seven_field = format!("0 {} *", five_field);
    Schedule::from_str(&seven_field)
}

// ── Per-job spawn helper ──────────────────────────────────────────────────────

fn spawn_job<F, Fut>(name: &'static str, crontab: String, state: Arc<AppState>, f: F)
where
    F: Fn(Arc<AppState>) -> Fut + Send + 'static,
    Fut: std::future::Future<Output = ()> + Send + 'static,
{
    let schedule = match to_cron_schedule(&crontab) {
        Ok(s) => s,
        Err(e) => {
            warn!("scheduler: invalid crontab for {name} ({crontab:?}): {e}");
            return;
        }
    };

    tokio::spawn(async move {
        info!("scheduler: {name} registered with crontab {crontab}");
        loop {
            let now = Utc::now();
            let Some(next) = schedule.upcoming(Utc).next() else {
                break;
            };
            let delay = (next - now).to_std().unwrap_or_default();
            tokio::time::sleep(delay).await;
            debug!("scheduler: firing {name}");
            f(Arc::clone(&state)).await;
        }
    });
}

// ── Spider helper ─────────────────────────────────────────────────────────────

async fn enqueue_spider(
    state: Arc<AppState>,
    spider_name: &'static str,
    crontab: &str,
    scrape_all: Option<&'static str>,
) {
    let mut kw = serde_json::Map::new();
    kw.insert("spider_name".into(), json!(spider_name));
    kw.insert("crontab_expression".into(), json!(crontab));
    if let Some(v) = scrape_all {
        kw.insert("scrape_all".into(), json!(v));
    }
    enqueue(&state, "run_spider", "scrapy", Value::Object(kw)).await;
}

// ── Public entry point ────────────────────────────────────────────────────────

/// Spawn all enabled scheduler jobs. Called once from `main` after AppState is built.
pub fn start(state: Arc<AppState>) {
    let cfg = &state.config;

    if cfg.disable_all_scheduler {
        info!("scheduler: DISABLE_ALL_SCHEDULER=true — all jobs suppressed");
        return;
    }

    // ── Scrapy spiders ────────────────────────────────────────────────────────

    macro_rules! spider {
        ($name:literal, $crontab:expr, $disabled:expr) => {
            spider!($name, $crontab, $disabled, None)
        };
        ($name:literal, $crontab:expr, $disabled:expr, $scrape_all:expr) => {
            if !$disabled {
                let crontab = $crontab.clone();
                let crontab2 = crontab.clone();
                spawn_job($name, crontab, Arc::clone(&state), move |s| {
                    let ct = crontab2.clone();
                    async move { enqueue_spider(s, $name, &ct, $scrape_all).await }
                });
            }
        };
    }

    spider!(
        "tamilmv",
        cfg.tamilmv_scheduler_crontab,
        cfg.disable_tamilmv_scheduler
    );
    spider!(
        "tamil_blasters",
        cfg.tamil_blasters_scheduler_crontab,
        cfg.disable_tamil_blasters_scheduler
    );
    spider!(
        "formula_ext",
        cfg.formula_ext_scheduler_crontab,
        cfg.disable_formula_ext_scheduler,
        Some("false")
    );
    spider!(
        "motogp_ext",
        cfg.motogp_ext_scheduler_crontab,
        cfg.disable_motogp_ext_scheduler,
        Some("false")
    );
    spider!(
        "wwe_ext",
        cfg.wwe_ext_scheduler_crontab,
        cfg.disable_wwe_ext_scheduler,
        Some("false")
    );
    spider!(
        "ufc_ext",
        cfg.ufc_ext_scheduler_crontab,
        cfg.disable_ufc_ext_scheduler,
        Some("false")
    );
    spider!(
        "movies_tv_ext",
        cfg.movies_tv_ext_scheduler_crontab,
        cfg.disable_movies_tv_ext_scheduler,
        Some("false")
    );
    spider!(
        "nowmetv",
        cfg.nowmetv_scheduler_crontab,
        cfg.disable_nowmetv_scheduler
    );
    spider!(
        "nowsports",
        cfg.nowsports_scheduler_crontab,
        cfg.disable_nowsports_scheduler
    );
    spider!(
        "tamilultra",
        cfg.tamilultra_scheduler_crontab,
        cfg.disable_tamilultra_scheduler
    );
    spider!(
        "sport_video",
        cfg.sport_video_scheduler_crontab,
        cfg.disable_sport_video_scheduler,
        Some("false")
    );
    spider!(
        "dlhd",
        cfg.dlhd_scheduler_crontab,
        cfg.disable_dlhd_scheduler
    );
    spider!(
        "arab_torrents",
        cfg.arab_torrents_scheduler_crontab,
        cfg.disable_arab_torrents_scheduler
    );
    spider!(
        "x1337",
        cfg.x1337_scheduler_crontab,
        cfg.disable_x1337_scheduler,
        Some("false")
    );
    spider!(
        "thepiratebay",
        cfg.thepiratebay_scheduler_crontab,
        cfg.disable_thepiratebay_scheduler,
        Some("false")
    );
    spider!(
        "rutor",
        cfg.rutor_scheduler_crontab,
        cfg.disable_rutor_scheduler,
        Some("false")
    );
    spider!(
        "limetorrents",
        cfg.limetorrents_scheduler_crontab,
        cfg.disable_limetorrents_scheduler,
        Some("false")
    );
    spider!(
        "yts",
        cfg.yts_scheduler_crontab,
        cfg.disable_yts_scheduler,
        Some("false")
    );
    spider!(
        "bt4g",
        cfg.bt4g_scheduler_crontab,
        cfg.disable_bt4g_scheduler,
        Some("false")
    );
    spider!(
        "nyaa",
        cfg.nyaa_scheduler_crontab,
        cfg.disable_nyaa_scheduler,
        Some("false")
    );
    spider!(
        "animetosho",
        cfg.animetosho_scheduler_crontab,
        cfg.disable_animetosho_scheduler,
        Some("false")
    );
    spider!(
        "subsplease",
        cfg.subsplease_scheduler_crontab,
        cfg.disable_subsplease_scheduler,
        Some("false")
    );
    spider!(
        "animepahe",
        cfg.animepahe_scheduler_crontab,
        cfg.disable_animepahe_scheduler,
        Some("false")
    );
    spider!(
        "bt52",
        cfg.bt52_scheduler_crontab,
        cfg.disable_bt52_scheduler,
        Some("false")
    );
    spider!(
        "uindex",
        cfg.uindex_scheduler_crontab,
        cfg.disable_uindex_scheduler,
        Some("false")
    );
    spider!(
        "eztv_rss",
        cfg.eztv_rss_scheduler_crontab,
        cfg.disable_eztv_rss_scheduler
    );

    // ── Feed scrapers & simple tasks ─────────────────────────────────────────
    //
    // Most tasks accept **kwargs and Python passes crontab_expression in kwargs,
    // so we mirror that. Exceptions:
    //   - run_all_integration_syncs: no **kwargs in signature, must not get extra kwargs.

    // with_crontab: passes crontab_expression in kwargs (matches Python)
    macro_rules! simple_job {
        ($name:literal, $actor:literal, $queue:literal, $crontab:expr, $disabled:expr) => {
            if !$disabled {
                let crontab = $crontab.clone();
                let ct_kw = crontab.clone();
                spawn_job($name, crontab, Arc::clone(&state), move |s| {
                    let ct = ct_kw.clone();
                    async move { enqueue(&s, $actor, $queue, json!({"crontab_expression": ct})).await; }
                });
            }
        };
        // always-enabled variant (no disable flag)
        ($name:literal, $actor:literal, $queue:literal, $crontab:expr) => {
            {
                let crontab = $crontab.clone();
                let ct_kw = crontab.clone();
                spawn_job($name, crontab, Arc::clone(&state), move |s| {
                    let ct = ct_kw.clone();
                    async move { enqueue(&s, $actor, $queue, json!({"crontab_expression": ct})).await; }
                });
            }
        };
    }

    // no_kwargs: does NOT pass crontab_expression (for tasks with no **kwargs)
    macro_rules! simple_job_no_kwargs {
        ($name:literal, $actor:literal, $queue:literal, $crontab:expr, $disabled:expr) => {
            if !$disabled {
                let crontab = $crontab.clone();
                spawn_job($name, crontab, Arc::clone(&state), |s| async move {
                    enqueue(&s, $actor, $queue, json!({})).await;
                });
            }
        };
    }

    simple_job!(
        "prowlarr_feed_scraper",
        "run_prowlarr_feed_scraper",
        "scrapy",
        cfg.prowlarr_feed_scraper_crontab,
        cfg.disable_prowlarr_feed_scraper
    );
    simple_job!(
        "jackett_feed_scraper",
        "run_jackett_feed_scraper",
        "scrapy",
        cfg.jackett_feed_scraper_crontab,
        cfg.disable_jackett_feed_scraper
    );
    simple_job!(
        "rss_feed_scraper",
        "run_rss_feed_scraper",
        "scrapy",
        cfg.rss_feed_scraper_crontab,
        cfg.disable_rss_feed_scraper
    );

    // ── Background scrapers ───────────────────────────────────────────────────

    // DMM hashlist scraper reuses the same disable flag as the live scraper
    if !cfg.disable_dmm_hashlist_scraper {
        simple_job!(
            "dmm_hashlist_scraper",
            "run_dmm_hashlist_scraper",
            "scrapy",
            cfg.dmm_hashlist_scraper_crontab
        );
    }

    simple_job!(
        "youtube_background_scraper",
        "run_youtube_background_scraper",
        "scrapy",
        cfg.youtube_background_scraper_crontab,
        cfg.disable_youtube_background_scraper
    );
    simple_job!(
        "acestream_background_scraper",
        "run_acestream_background_scraper",
        "scrapy",
        cfg.acestream_background_scraper_crontab,
        cfg.disable_acestream_background_scraper
    );
    simple_job!(
        "telegram_background_scraper",
        "run_telegram_background_scraper",
        "scrapy",
        cfg.telegram_background_scraper_crontab,
        cfg.disable_telegram_background_scraper
    );

    // ── Maintenance tasks ─────────────────────────────────────────────────────

    simple_job!(
        "validate_tv_streams_in_db",
        "validate_tv_streams_in_db",
        "default",
        cfg.validate_tv_streams_in_db_crontab,
        cfg.disable_validate_tv_streams_in_db
    );
    simple_job!(
        "update_torrent_seeders",
        "update_torrent_seeders",
        "default",
        cfg.update_seeders_crontab,
        cfg.disable_update_seeders
    );
    simple_job!(
        "cleanup_expired_scraper_task",
        "cleanup_expired_scraper_task",
        "priority",
        cfg.cleanup_expired_scraper_task_crontab
    );
    simple_job!(
        "cleanup_expired_cache",
        "cleanup_expired_cache",
        "priority",
        cfg.cleanup_expired_cache_task_crontab
    );
    simple_job!(
        "run_background_search",
        "run_background_search",
        "default",
        cfg.background_search_crontab
    );

    // run_all_integration_syncs has no **kwargs — must not receive extra keyword args
    simple_job_no_kwargs!(
        "run_all_integration_syncs",
        "run_all_integration_syncs",
        "default",
        cfg.integration_sync_crontab,
        cfg.disable_integration_sync_scheduler
    );

    // Discover pre-warm: only when discover is enabled and a TMDB key is set
    if cfg.discover_enabled && cfg.tmdb_api_key.is_some() {
        simple_job!(
            "discover_prewarm",
            "run_discover_prewarm",
            "default",
            "0 4 * * *".to_string()
        );
    }

    info!("scheduler: all jobs registered");
}
