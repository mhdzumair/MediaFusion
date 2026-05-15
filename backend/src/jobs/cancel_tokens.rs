use std::collections::HashMap;
use std::sync::Mutex;

use tokio_util::sync::CancellationToken;

static TOKENS: Mutex<Option<HashMap<i64, CancellationToken>>> = Mutex::new(None);

fn map() -> std::sync::MutexGuard<'static, Option<HashMap<i64, CancellationToken>>> {
    TOKENS.lock().unwrap()
}

pub fn register(job_id: i64, token: CancellationToken) {
    let mut guard = map();
    guard.get_or_insert_with(HashMap::new).insert(job_id, token);
}

pub fn deregister(job_id: i64) {
    let mut guard = map();
    if let Some(m) = guard.as_mut() {
        m.remove(&job_id);
    }
}

pub fn get(job_id: i64) -> Option<CancellationToken> {
    map().as_ref()?.get(&job_id).cloned()
}
