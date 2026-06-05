/// Maintenance operations: VACUUM, ANALYZE, REINDEX, bloat stats, indexes.
use std::sync::Arc;
use std::time::Instant;

use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::state::AppState;

use super::{forbidden, validate_admin};

#[derive(Deserialize)]
pub struct MaintenanceRequest {
    pub tables: Option<Vec<String>>,
    pub operation: Option<String>, // vacuum, analyze, vacuum_analyze, reindex
    pub full: Option<bool>,
}

#[derive(Deserialize)]
pub struct RebuildIndexRequest {
    pub table_name: Option<String>,
    pub index_name: Option<String>,
}

/// POST /api/v1/admin/db/maintenance/vacuum
pub async fn run_vacuum(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    body: Option<Json<MaintenanceRequest>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let req = body.map(|Json(r)| r).unwrap_or(MaintenanceRequest {
        tables: None,
        operation: None,
        full: None,
    });
    let full = req.full.unwrap_or(false);
    let operation = req.operation.as_deref().unwrap_or("vacuum_analyze");

    // Get tables to process
    let tables = match get_tables(&state, req.tables).await {
        Ok(t) => t,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e})),
            )
                .into_response();
        }
    };

    let started = Instant::now();
    let mut processed: Vec<String> = Vec::new();
    let mut errors: Vec<String> = Vec::new();

    for table_name in &tables {
        if !table_name.chars().all(|c| c.is_alphanumeric() || c == '_') {
            errors.push(format!("Invalid table name: {table_name}"));
            continue;
        }

        let sql = match operation {
            "vacuum" if full => format!("VACUUM FULL \"{}\"", table_name),
            "vacuum" => format!("VACUUM \"{}\"", table_name),
            "analyze" => format!("ANALYZE \"{}\"", table_name),
            "vacuum_analyze" if full => format!("VACUUM (FULL, ANALYZE) \"{}\"", table_name),
            _ => format!("VACUUM ANALYZE \"{}\"", table_name),
        };

        match sqlx::query(&sql).execute(&state.pool).await {
            Ok(_) => processed.push(table_name.clone()),
            Err(e) => {
                tracing::warn!("maintenance vacuum {table_name}: {e}");
                errors.push(format!("{table_name}: {e}"));
            }
        }
    }

    let elapsed_ms = started.elapsed().as_millis() as i64;
    let message = if errors.is_empty() {
        format!("{} completed for {} table(s)", operation, processed.len())
    } else {
        format!(
            "{} completed for {} table(s) with {} error(s)",
            operation,
            processed.len(),
            errors.len()
        )
    };

    Json(json!({
        "status": "success",
        "success": true,
        "operation": operation,
        "tables_processed": processed,
        "execution_time_ms": elapsed_ms,
        "message": message,
        "errors": errors,
    }))
    .into_response()
}

/// POST /api/v1/admin/db/maintenance/analyze
pub async fn run_analyze(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    body: Option<Json<MaintenanceRequest>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let req = body.map(|Json(r)| r).unwrap_or(MaintenanceRequest {
        tables: None,
        operation: None,
        full: None,
    });

    let tables = match get_tables(&state, req.tables).await {
        Ok(t) => t,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e})),
            )
                .into_response();
        }
    };

    let started = Instant::now();
    let mut processed: Vec<String> = Vec::new();
    let mut errors: Vec<String> = Vec::new();

    for table_name in &tables {
        if !table_name.chars().all(|c| c.is_alphanumeric() || c == '_') {
            errors.push(format!("Invalid table name: {table_name}"));
            continue;
        }

        let sql = format!("ANALYZE \"{}\"", table_name);
        match sqlx::query(&sql).execute(&state.pool).await {
            Ok(_) => processed.push(table_name.clone()),
            Err(e) => {
                tracing::warn!("maintenance analyze {table_name}: {e}");
                errors.push(format!("{table_name}: {e}"));
            }
        }
    }

    let elapsed_ms = started.elapsed().as_millis() as i64;

    Json(json!({
        "status": "success",
        "success": true,
        "operation": "analyze",
        "tables_processed": processed,
        "execution_time_ms": elapsed_ms,
        "message": format!("ANALYZE completed for {} table(s)", processed.len()),
        "errors": errors,
    }))
    .into_response()
}

/// POST /api/v1/admin/db/maintenance/reindex
pub async fn run_reindex(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    body: Option<Json<MaintenanceRequest>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let req = body.map(|Json(r)| r).unwrap_or(MaintenanceRequest {
        tables: None,
        operation: None,
        full: None,
    });

    let tables = match get_tables(&state, req.tables).await {
        Ok(t) => t,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e})),
            )
                .into_response();
        }
    };

    if tables.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "No tables available for REINDEX"})),
        )
            .into_response();
    }

    let started = Instant::now();
    let mut processed: Vec<String> = Vec::new();
    let mut errors: Vec<String> = Vec::new();

    for table_name in &tables {
        if !table_name.chars().all(|c| c.is_alphanumeric() || c == '_') {
            errors.push(format!("Invalid table name: {table_name}"));
            continue;
        }

        let sql = format!("REINDEX TABLE \"{}\"", table_name);
        match sqlx::query(&sql).execute(&state.pool).await {
            Ok(_) => processed.push(table_name.clone()),
            Err(e) => {
                tracing::warn!("maintenance reindex {table_name}: {e}");
                errors.push(format!("{table_name}: {e}"));
            }
        }
    }

    let elapsed_ms = started.elapsed().as_millis() as i64;

    Json(json!({
        "status": "success",
        "success": true,
        "operation": "reindex",
        "tables_processed": processed,
        "execution_time_ms": elapsed_ms,
        "message": format!("REINDEX TABLE completed for {} table(s)", processed.len()),
        "errors": errors,
    }))
    .into_response()
}

/// GET /api/v1/admin/db/maintenance/bloat
pub async fn get_bloat_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let rows = sqlx::query_as::<_, (String, i64, i64, f64)>(
        r#"SELECT relname,
                  n_dead_tup,
                  n_live_tup,
                  CASE WHEN n_live_tup > 0
                       THEN round(n_dead_tup::numeric / n_live_tup * 100, 2)
                       ELSE 0 END as bloat_ratio
           FROM pg_stat_user_tables
           WHERE n_dead_tup > 0
           ORDER BY n_dead_tup DESC
           LIMIT 20"#,
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let items: Vec<Value> = rows
        .into_iter()
        .map(|(table, dead, live, ratio)| {
            json!({
                "table": table,
                "dead_tuples": dead,
                "live_tuples": live,
                "bloat_ratio_pct": ratio,
            })
        })
        .collect();

    Json(json!({"tables": items})).into_response()
}

/// GET /api/v1/admin/db/indexes
pub async fn list_indexes(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let rows = sqlx::query_as::<_, (String, String, String, bool, bool, i64)>(
        r#"SELECT i.indexname, i.tablename, i.indexdef,
                  ix.indisunique, ix.indisprimary,
                  COALESCE(s.idx_scan, 0) as idx_scan
           FROM pg_indexes i
           LEFT JOIN pg_index ix ON ix.indexrelid = (SELECT oid FROM pg_class WHERE relname = i.indexname LIMIT 1)
           LEFT JOIN pg_stat_user_indexes s ON s.indexrelname = i.indexname
           WHERE i.schemaname = 'public'
           ORDER BY i.tablename, i.indexname"#,
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let items: Vec<Value> = rows
        .into_iter()
        .map(|(name, table, def, is_unique, is_primary, scans)| {
            json!({
                "name": name,
                "table": table,
                "definition": def,
                "is_unique": is_unique,
                "is_primary": is_primary,
                "index_scans": scans,
            })
        })
        .collect();

    let total = items.len();
    Json(json!({"indexes": items, "total": total})).into_response()
}

/// POST /api/v1/admin/db/indexes/rebuild
pub async fn rebuild_indexes(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<RebuildIndexRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if let Some(ref idx) = body.index_name {
        if !idx.chars().all(|c| c.is_alphanumeric() || c == '_') {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid index name"})),
            )
                .into_response();
        }
        let query = format!("REINDEX INDEX \"{}\"", idx);
        match sqlx::query(&query).execute(&state.pool).await {
            Ok(_) => {
                return Json(json!({"status": "success", "message": format!("Reindexed {idx}")}))
                    .into_response()
            }
            Err(e) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": e.to_string()})),
                )
                    .into_response();
            }
        }
    }

    if let Some(ref table) = body.table_name {
        if !table.chars().all(|c| c.is_alphanumeric() || c == '_') {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid table name"})),
            )
                .into_response();
        }
        let query = format!("REINDEX TABLE \"{}\"", table);
        match sqlx::query(&query).execute(&state.pool).await {
            Ok(_) => {
                return Json(
                    json!({"status": "success", "message": format!("Reindexed table {table}")}),
                )
                .into_response()
            }
            Err(e) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": e.to_string()})),
                )
                    .into_response();
            }
        }
    }

    (
        StatusCode::BAD_REQUEST,
        Json(json!({"detail": "Provide table_name or index_name"})),
    )
        .into_response()
}

/// Helper: get the list of tables to process, falling back to all public tables.
async fn get_tables(
    state: &Arc<AppState>,
    tables: Option<Vec<String>>,
) -> Result<Vec<String>, String> {
    match tables {
        Some(t) if !t.is_empty() => Ok(t),
        _ => {
            // All public user tables
            let all: Vec<String> = sqlx::query_scalar(
                "SELECT table_name FROM information_schema.tables \
                 WHERE table_schema = 'public' AND table_type = 'BASE TABLE' \
                 ORDER BY table_name",
            )
            .fetch_all(&state.pool_ro)
            .await
            .map_err(|e| e.to_string())?;
            Ok(all)
        }
    }
}
