//! Captures `info`/`warn`/`error` tracing events during job execution and persists
//! them as a `summary` row in `job_events` for the admin task log UI.

use std::cell::RefCell;

use sqlx::PgPool;
use tracing::{Event, Level, Subscriber};
use tracing_subscriber::{Layer, layer::Context};

use super::runner::job_write_event;

const MAX_LINES: usize = 200;
const MAX_TEXT_BYTES: usize = 32_768;

tokio::task_local! {
    static JOB_LOG_BUFFER: RefCell<Vec<CapturedLine>>;
}

#[derive(Debug, Clone, serde::Serialize)]
struct CapturedLine {
    level: &'static str,
    message: String,
}

/// Run `fut` with a task-local log buffer; flush captured lines when the future completes.
pub async fn with_capture<F, T>(pool: &PgPool, job_id: i64, fut: F) -> T
where
    F: std::future::Future<Output = T>,
{
    JOB_LOG_BUFFER
        .scope(RefCell::new(Vec::new()), async {
            let result = fut.await;
            flush(pool, job_id).await;
            result
        })
        .await
}

async fn flush(pool: &PgPool, job_id: i64) {
    let lines: Vec<CapturedLine> = JOB_LOG_BUFFER
        .try_with(|buf| buf.borrow().clone())
        .unwrap_or_default();

    if lines.is_empty() {
        return;
    }

    let text = format_summary_text(&lines);
    job_write_event(
        pool,
        job_id,
        "summary",
        Some(serde_json::json!({ "text": text, "lines": lines })),
    )
    .await;
}

fn format_summary_text(lines: &[CapturedLine]) -> String {
    let mut out = String::new();
    for line in lines {
        let entry = format!("[{}] {}", line.level, line.message);
        if out.len() + entry.len() + 1 > MAX_TEXT_BYTES {
            out.push_str("\n… (truncated)");
            break;
        }
        if !out.is_empty() {
            out.push('\n');
        }
        out.push_str(&entry);
    }
    out
}

fn push_line(level: &'static str, message: String) {
    let _ = JOB_LOG_BUFFER.try_with(|b| {
        let mut guard = b.borrow_mut();
        if guard.len() >= MAX_LINES {
            return;
        }
        guard.push(CapturedLine { level, message });
    });
}

/// Tracing layer — active only inside [`with_capture`] scopes.
pub struct JobLogCaptureLayer;

impl<S: Subscriber> Layer<S> for JobLogCaptureLayer {
    fn on_event(&self, event: &Event<'_>, _ctx: Context<'_, S>) {
        let level = event.metadata().level();
        if *level > Level::INFO {
            return;
        }

        let module = event.metadata().module_path().unwrap_or("");
        if !module.starts_with("mediafusion_api") {
            return;
        }

        // Skip runner/scheduler noise; keep handler output.
        if module.contains("jobs::runner")
            || module.contains("jobs::scheduler")
            || module.contains("jobs::log_capture")
        {
            return;
        }

        let mut visitor = MessageVisitor::default();
        event.record(&mut visitor);
        if visitor.message.is_empty() {
            return;
        }

        let level_label = match *level {
            Level::ERROR => "ERROR",
            Level::WARN => "WARN",
            Level::INFO => "INFO",
            Level::DEBUG => "DEBUG",
            Level::TRACE => "TRACE",
        };

        push_line(level_label, visitor.message);
    }
}

#[derive(Default)]
struct MessageVisitor {
    message: String,
}

impl tracing::field::Visit for MessageVisitor {
    fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
        if field.name() == "message" {
            self.message = value.to_string();
        } else if self.message.is_empty() {
            self.message = format!("{}: {}", field.name(), value);
        }
    }

    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
        if field.name() == "message" {
            self.message = format!("{value:?}");
        } else if self.message.is_empty() {
            self.message = format!("{}: {value:?}", field.name());
        }
    }

    fn record_error(
        &mut self,
        field: &tracing::field::Field,
        value: &(dyn std::error::Error + 'static),
    ) {
        if field.name() == "message" || self.message.is_empty() {
            self.message = value.to_string();
        }
    }
}

/// Format a job event for admin API responses.
pub fn event_to_json(
    event: &str,
    detail: Option<serde_json::Value>,
    at: Option<String>,
) -> serde_json::Value {
    serde_json::json!({
        "event": event,
        "detail": detail_text(detail.as_ref()),
        "at": at,
    })
}

pub fn detail_text(detail: Option<&serde_json::Value>) -> Option<String> {
    match detail? {
        serde_json::Value::String(s) => Some(s.clone()),
        serde_json::Value::Object(obj) => obj
            .get("text")
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .or_else(|| {
                obj.get("message")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
            }),
        other => Some(other.to_string()),
    }
}

/// Extract human-readable summary text from a job's event list (for API responses).
pub fn summary_from_events(events: &[serde_json::Value]) -> Option<String> {
    for event in events {
        let Some(kind) = event.get("event").and_then(|v| v.as_str()) else {
            continue;
        };
        if kind != "summary" {
            continue;
        }
        if let Some(text) = detail_text(event.get("detail")) {
            return Some(text);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn summary_from_summary_event() {
        let events = vec![serde_json::json!({
            "event": "summary",
            "detail": { "text": "line one\nline two" },
        })];
        assert_eq!(
            summary_from_events(&events).as_deref(),
            Some("line one\nline two")
        );
    }
}
