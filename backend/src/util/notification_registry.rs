//! Decoupled notification dispatch (mirrors Python `utils/notification_registry.py`).

use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, OnceLock, RwLock};

type FileAnnotationHandler =
    Arc<dyn Fn(String, String) -> Pin<Box<dyn Future<Output = ()> + Send>> + Send + Sync>;

static FILE_ANNOTATION_HANDLERS: OnceLock<RwLock<Vec<FileAnnotationHandler>>> = OnceLock::new();

fn handlers() -> &'static RwLock<Vec<FileAnnotationHandler>> {
    FILE_ANNOTATION_HANDLERS.get_or_init(|| RwLock::new(Vec::new()))
}

/// Register a handler invoked when torrent file annotation is requested.
pub fn register_file_annotation_handler(handler: FileAnnotationHandler) {
    handlers().write().unwrap().push(handler);
}

/// Notify all registered handlers that manual file→episode annotation is needed.
pub async fn send_file_annotation_request(info_hash: &str, name: &str) {
    let list: Vec<FileAnnotationHandler> = handlers().read().unwrap().clone();
    if list.is_empty() {
        tracing::debug!("No file annotation handlers registered, skipping notification");
        return;
    }
    for handler in list {
        handler(info_hash.to_string(), name.to_string()).await;
    }
}
