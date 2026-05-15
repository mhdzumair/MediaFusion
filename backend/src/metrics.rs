use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, RwLock};

#[allow(clippy::type_complexity)]
pub struct Metrics {
    /// Flat counters: (method, route, status) → count
    pub requests: RwLock<HashMap<(String, String, u16), u64>>,
    /// Total in-flight gauge (approximate with AtomicU64)
    pub in_flight: AtomicU64,
    /// Duration sum and count: (method, route, status) → (sum_ms, count)
    pub durations: RwLock<HashMap<(String, String, u16), (f64, u64)>>,
}

impl Metrics {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            requests: RwLock::new(HashMap::new()),
            in_flight: AtomicU64::new(0),
            durations: RwLock::new(HashMap::new()),
        })
    }

    pub fn record_request(&self, method: &str, route: &str, status: u16, duration_ms: f64) {
        let key = (method.to_string(), route.to_string(), status);
        if let Ok(mut map) = self.requests.write() {
            *map.entry(key.clone()).or_insert(0) += 1;
        }
        if let Ok(mut map) = self.durations.write() {
            let e = map.entry(key).or_insert((0.0, 0));
            e.0 += duration_ms;
            e.1 += 1;
        }
    }

    pub fn in_flight_count(&self) -> u64 {
        self.in_flight.load(Ordering::Relaxed)
    }
}

impl Default for Metrics {
    fn default() -> Self {
        Self {
            requests: RwLock::new(HashMap::new()),
            in_flight: AtomicU64::new(0),
            durations: RwLock::new(HashMap::new()),
        }
    }
}
