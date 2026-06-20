/// Egress self-heal watchdog.
///
/// Probes multiple independent hosts through the same HTTP client (and proxy) that
/// real traffic uses. A probe cycle is considered **failed** only when ALL targets
/// return a transport error (connect/timeout/request) — a single-provider blip or
/// HTTP 4xx/5xx doesn't count.
///
/// After `fail_threshold` consecutive fully-failed cycles the process exits so k8s
/// can restart the pod and re-establish the gost tunnel.
use std::time::Duration;

use tokio_util::sync::CancellationToken;
use tracing::{debug, error, info, warn};

use crate::util::http::is_transport_error;

/// Default probe targets: two independent debrid API hosts + a neutral endpoint.
/// All probed with HEAD (lightweight — no body). A 401/403/429 still means egress works.
const DEFAULT_PROBE_URLS: &[&str] = &[
    "https://api.real-debrid.com/rest/1.0/time",
    "https://api.alldebrid.com/v4/ping",
    "https://one.one.one.one",
];

pub struct WatchdogConfig {
    pub interval_secs: u64,
    pub fail_threshold: u32,
    /// Comma-separated override; falls back to DEFAULT_PROBE_URLS when None/empty.
    pub probe_urls_override: Option<String>,
}

pub async fn run(http: reqwest::Client, cfg: WatchdogConfig, cancel: Option<CancellationToken>) {
    let probe_urls: Vec<String> = cfg
        .probe_urls_override
        .as_deref()
        .filter(|s| !s.is_empty())
        .map(|s| s.split(',').map(|u| u.trim().to_owned()).collect())
        .unwrap_or_else(|| DEFAULT_PROBE_URLS.iter().map(|s| s.to_string()).collect());

    info!(
        interval_secs = cfg.interval_secs,
        fail_threshold = cfg.fail_threshold,
        targets = ?probe_urls,
        "egress watchdog started"
    );

    let mut consecutive_failures: u32 = 0;
    let interval = Duration::from_secs(cfg.interval_secs);

    loop {
        // Sleep, but wake immediately on cancellation.
        match &cancel {
            Some(tok) => {
                tokio::select! {
                    _ = tok.cancelled() => {
                        info!("egress watchdog stopping (cancelled)");
                        return;
                    }
                    _ = tokio::time::sleep(interval) => {}
                }
            }
            None => tokio::time::sleep(interval).await,
        }

        if probe_cycle(&http, &probe_urls).await {
            if consecutive_failures > 0 {
                info!(
                    consecutive_failures,
                    "egress watchdog: connectivity restored"
                );
            }
            consecutive_failures = 0;
            debug!("egress watchdog: probe cycle ok");
        } else {
            consecutive_failures += 1;
            warn!(
                consecutive_failures,
                threshold = cfg.fail_threshold,
                "egress watchdog: all probe targets unreachable"
            );

            if consecutive_failures >= cfg.fail_threshold {
                error!(
                    consecutive_failures,
                    targets = ?probe_urls,
                    "egress watchdog: sustained egress failure — restarting process to recover tunnel"
                );
                // Signal graceful shutdown first (worker); API has no cancel token so
                // we fall through to exit(1) in both cases after the cancellation fires.
                if let Some(tok) = &cancel {
                    tok.cancel();
                    // Give the rest of the process a moment to notice the signal
                    // before we force-exit, in case graceful shutdown is fast.
                    tokio::time::sleep(Duration::from_secs(2)).await;
                }
                std::process::exit(1);
            }
        }
    }
}

/// Returns `true` if at least one target responds without a transport error.
async fn probe_cycle(http: &reqwest::Client, urls: &[String]) -> bool {
    let mut any_ok = false;
    for url in urls {
        match http
            .head(url.as_str())
            .timeout(Duration::from_secs(10))
            .send()
            .await
        {
            Ok(_) => {
                // Any HTTP response (even 4xx/5xx) means egress is working.
                any_ok = true;
            }
            Err(e) if is_transport_error(&e) => {
                debug!(url, root = %crate::util::http::root_cause(&e), "egress probe: transport error");
            }
            Err(_) => {
                // Non-transport error (decode, redirect loop, etc.) — egress itself is fine.
                any_ok = true;
            }
        }
    }
    any_ok
}
