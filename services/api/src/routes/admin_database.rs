/// Admin database management endpoints.
///
/// Proxies complex DB admin operations to the Python service
/// and implements lightweight Rust-native versions where practical.
/// All routes require admin JWT role.
///
/// Routes (prefix /api/v1/admin/db):
///   GET    /stats                          → db_stats
///   GET    /tables                         → db_tables
///   GET    /tables/{table_name}/schema     → get_table_schema
///   GET    /tables/{table_name}/data       → get_table_data
///   POST   /tables/{table_name}/delete-rows→ delete_table_rows
///   POST   /maintenance/vacuum             → run_vacuum
///   POST   /maintenance/analyze            → run_analyze
///   POST   /maintenance/reindex/{table}    → run_reindex
///   GET    /maintenance/bloat              → get_bloat_stats
///   GET    /slow-queries                   → get_slow_queries
///   GET    /orphans/streams                → detect_orphan_streams
///   GET    /orphans/media                  → detect_orphan_media
///   POST   /orphans/cleanup                → cleanup_orphans
///   POST   /export/table/{table_name}      → export_table
///   POST   /import/table                   → import_table
///   GET    /indexes                        → list_indexes
///   POST   /indexes/rebuild                → rebuild_indexes
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helper ──────────────────────────────────────────────────────────────

fn validate_admin(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
    let token = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(str::to_string)?;
    let dot = token.rfind('.')?;
    let (payload_str, sig) = token.split_at(dot);
    let sig = &sig[1..];
    let mut mac = Hmac::<Sha256>::new_from_slice(secret_key.as_bytes()).ok()?;
    mac.update(payload_str.as_bytes());
    let expected: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if expected != sig {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(payload_str).ok()?;
    let data: Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    if data["role"].as_str() != Some("admin") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

fn forbidden() -> axum::response::Response {
    (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response()
}

// ─── Proxy helper ─────────────────────────────────────────────────────────────

async fn proxy_to_python(
    state: &AppState,
    method: reqwest::Method,
    path: &str,
    headers: &HeaderMap,
    body: Option<Value>,
) -> axum::response::Response {
    let py_url = match &state.config.python_proxy_url {
        Some(u) => u.clone(),
        None => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"detail": "Background service unavailable"})),
            )
                .into_response();
        }
    };
    let url = format!("{py_url}{path}");
    let mut req = state.http.request(method, &url);
    if let Some(auth) = headers.get("authorization") {
        req = req.header("authorization", auth);
    }
    if let Some(b) = body {
        req = req.json(&b);
    }
    match req.send().await {
        Ok(r) => {
            let status = StatusCode::from_u16(r.status().as_u16())
                .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
            let body: Value = r.json().await.unwrap_or(json!({}));
            (status, Json(body)).into_response()
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"detail": e.to_string()})),
        )
            .into_response(),
    }
}

// ─── Query / Request types ────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct TableDataQuery {
    pub page: Option<i64>,
    pub per_page: Option<i64>,
    pub sort_by: Option<String>,
    pub sort_order: Option<String>,
    pub search: Option<String>,
}

#[derive(Deserialize)]
pub struct DeleteRowsRequest {
    pub ids: Vec<Value>,
    pub id_column: Option<String>,
}

#[derive(Deserialize)]
pub struct RebuildIndexRequest {
    pub table_name: Option<String>,
    pub index_name: Option<String>,
}

// ─── Rust-native DB endpoints ─────────────────────────────────────────────────

/// GET /api/v1/admin/db/stats
pub async fn db_stats(headers: HeaderMap, State(state): State<Arc<AppState>>) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let (
        version,
        db_name,
        size_pretty,
        total_bytes,
        active_conn,
        max_conn,
        deadlocks,
        commits,
        rollbacks,
    ) = tokio::join!(
        async {
            sqlx::query_scalar::<_, String>("SELECT version()")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or_else(|_| "unknown".into())
        },
        async {
            sqlx::query_scalar::<_, String>("SELECT current_database()")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or_else(|_| "unknown".into())
        },
        async {
            sqlx::query_scalar::<_, String>(
                "SELECT pg_size_pretty(pg_database_size(current_database()))",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or_else(|_| "unknown".into())
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT pg_database_size(current_database())")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>("SELECT current_setting('max_connections')::int")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT xact_commit FROM pg_stat_database WHERE datname = current_database()",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT xact_rollback FROM pg_stat_database WHERE datname = current_database()",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0)
        },
    );

    let cache_hit = sqlx::query_scalar::<_, f64>(
        "SELECT CASE WHEN (blks_hit + blks_read) > 0 THEN round(blks_hit::numeric / (blks_hit + blks_read) * 100, 2) ELSE 0 END FROM pg_stat_database WHERE datname = current_database()"
    )
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0.0);

    Json(json!({
        "version": version,
        "database_name": db_name,
        "size_human": size_pretty,
        "total_size_bytes": total_bytes,
        "connection_count": active_conn,
        "max_connections": max_conn,
        "cache_hit_ratio": cache_hit,
        "uptime_seconds": 0,
        "active_queries": active_conn,
        "deadlocks": deadlocks,
        "transactions_committed": commits,
        "transactions_rolled_back": rollbacks,
    }))
    .into_response()
}

/// GET /api/v1/admin/db/tables
pub async fn db_tables(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let rows = sqlx::query_as::<
        _,
        (
            String,
            String,
            i64,
            String,
            i64,
            String,
            i64,
            Option<String>,
            Option<String>,
        ),
    >(
        r#"SELECT
            t.schemaname,
            t.relname as tablename,
            t.n_live_tup as row_count,
            pg_size_pretty(pg_total_relation_size(t.schemaname||'.'||t.relname)) as size_human,
            pg_total_relation_size(t.schemaname||'.'||t.relname) as size_bytes,
            pg_size_pretty(pg_indexes_size(t.schemaname||'.'||t.relname)) as index_size_human,
            pg_indexes_size(t.schemaname||'.'||t.relname) as index_size_bytes,
            to_char(t.last_autovacuum, 'YYYY-MM-DD HH24:MI:SS') as last_autovacuum,
            to_char(t.last_autoanalyze, 'YYYY-MM-DD HH24:MI:SS') as last_autoanalyze
           FROM pg_stat_user_tables t
           ORDER BY pg_total_relation_size(t.schemaname||'.'||t.relname) DESC
           LIMIT 100"#,
    )
    .fetch_all(&state.pool_ro)
    .await;

    match rows {
        Ok(tables) => {
            let total_size: i64 = tables.iter().map(|t| t.4).sum();
            let items: Vec<Value> = tables
                .into_iter()
                .map(
                    |(
                        schema,
                        name,
                        row_count,
                        size_human,
                        size_bytes,
                        idx_size_h,
                        idx_size_b,
                        last_av,
                        last_aa,
                    )| {
                        json!({
                            "name": name,
                            "schema_name": schema,
                            "row_count": row_count,
                            "size_human": size_human,
                            "size_bytes": size_bytes,
                            "index_size_human": idx_size_h,
                            "index_size_bytes": idx_size_b,
                            "last_autovacuum": last_av,
                            "last_autoanalyze": last_aa,
                        })
                    },
                )
                .collect();

            let total_count = items.len();
            let total_size_pretty = format_bytes(total_size);
            Json(json!({
                "tables": items,
                "total_count": total_count,
                "total_size_human": total_size_pretty,
                "total_size_bytes": total_size,
            }))
            .into_response()
        }
        Err(e) => {
            tracing::error!("db_tables: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

fn format_bytes(bytes: i64) -> String {
    if bytes < 1024 {
        format!("{bytes} B")
    } else if bytes < 1024 * 1024 {
        format!("{:.1} KB", bytes as f64 / 1024.0)
    } else if bytes < 1024 * 1024 * 1024 {
        format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
    } else {
        format!("{:.2} GB", bytes as f64 / (1024.0 * 1024.0 * 1024.0))
    }
}

/// GET /api/v1/admin/db/tables/{table_name}/schema
pub async fn get_table_schema(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table_name): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    // Column info
    let columns = sqlx::query_as::<_, (String, String, bool, Option<String>)>(
        r#"SELECT column_name, data_type, is_nullable = 'YES' as nullable, column_default
           FROM information_schema.columns
           WHERE table_name = $1 AND table_schema = 'public'
           ORDER BY ordinal_position"#,
    )
    .bind(&table_name)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    // Index info
    let indexes = sqlx::query_as::<_, (String, bool, bool, String)>(
        r#"SELECT indexname, indisunique, indisprimary,
                  pg_get_indexdef(indexrelid) as indexdef
           FROM pg_indexes
           JOIN pg_index ON pg_index.indexrelid = (SELECT oid FROM pg_class WHERE relname = pg_indexes.indexname LIMIT 1)
           WHERE tablename = $1 AND schemaname = 'public'"#,
    )
    .bind(&table_name)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let row_count: i64 = sqlx::query_scalar::<_, i64>(
        "SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = $1",
    )
    .bind(&table_name)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None)
    .unwrap_or(0);

    let col_items: Vec<Value> = columns
        .into_iter()
        .map(|(name, dtype, nullable, default)| {
            json!({
                "name": name,
                "data_type": dtype,
                "is_nullable": nullable,
                "default_value": default,
            })
        })
        .collect();

    let idx_items: Vec<Value> = indexes
        .into_iter()
        .map(|(name, is_unique, is_primary, def)| {
            json!({
                "name": name,
                "is_unique": is_unique,
                "is_primary": is_primary,
                "definition": def,
            })
        })
        .collect();

    Json(json!({
        "name": table_name,
        "schema_name": "public",
        "columns": col_items,
        "indexes": idx_items,
        "row_count": row_count,
    }))
    .into_response()
}

/// GET /api/v1/admin/db/tables/{table_name}/data
pub async fn get_table_data(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table_name): Path<String>,
    Query(params): Query<TableDataQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    // Validate table name (only alphanumeric + underscore)
    if !table_name.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid table name"})),
        )
            .into_response();
    }

    let page = params.page.unwrap_or(1).max(1);
    let per_page = params.per_page.unwrap_or(25).clamp(1, 100);
    let offset = (page - 1) * per_page;

    let sort_col = params.sort_by.as_deref().unwrap_or("id");
    let sort_order = if params.sort_order.as_deref() == Some("asc") {
        "ASC"
    } else {
        "DESC"
    };

    // Validate sort column (only alphanumeric + underscore)
    if !sort_col.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid sort column"})),
        )
            .into_response();
    }

    let total: i64 = sqlx::query_scalar(&format!("SELECT COUNT(*) FROM {table_name}"))
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

    let query = format!(
        "SELECT row_to_json(t) FROM (SELECT * FROM {table_name} ORDER BY {sort_col} {sort_order} LIMIT {per_page} OFFSET {offset}) t"
    );

    let rows: Vec<Value> = sqlx::query_scalar::<_, Value>(&query)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

    let pages = (total + per_page - 1) / per_page;

    Json(json!({
        "table": table_name,
        "columns": [],
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }))
    .into_response()
}

/// POST /api/v1/admin/db/tables/{table_name}/delete-rows
pub async fn delete_table_rows(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table_name): Path<String>,
    Json(body): Json<DeleteRowsRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if !table_name.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid table name"})),
        )
            .into_response();
    }

    let id_col = body.id_column.as_deref().unwrap_or("id");
    if !id_col.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid id column"})),
        )
            .into_response();
    }

    if body.ids.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "No IDs provided"})),
        )
            .into_response();
    }

    // Build ids as JSON array for ANY operator
    let ids_json = serde_json::to_string(&body.ids).unwrap_or_else(|_| "[]".to_string());

    let query = format!(
        "DELETE FROM {table_name} WHERE {id_col}::text = ANY(SELECT jsonb_array_elements_text($1::jsonb))"
    );

    match sqlx::query(&query)
        .bind(&ids_json)
        .execute(&state.pool)
        .await
    {
        Ok(r) => Json(json!({
            "deleted": r.rows_affected(),
            "message": format!("Deleted {} row(s) from {table_name}", r.rows_affected()),
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("delete_table_rows {table_name}: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/admin/db/maintenance/vacuum
pub async fn run_vacuum(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    match sqlx::query("VACUUM ANALYZE").execute(&state.pool).await {
        Ok(_) => Json(json!({"status": "success", "message": "VACUUM ANALYZE completed"}))
            .into_response(),
        Err(e) => {
            tracing::error!("vacuum: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/admin/db/maintenance/analyze
pub async fn run_analyze(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    match sqlx::query("ANALYZE").execute(&state.pool).await {
        Ok(_) => Json(json!({"status": "success", "message": "ANALYZE completed"})).into_response(),
        Err(e) => {
            tracing::error!("analyze: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/admin/db/maintenance/reindex/{table}
pub async fn run_reindex(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table_name): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if !table_name.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid table name"})),
        )
            .into_response();
    }

    let query = format!("REINDEX TABLE {table_name}");
    match sqlx::query(&query).execute(&state.pool).await {
        Ok(_) => Json(json!({"status": "success", "message": format!("REINDEX TABLE {table_name} completed")})).into_response(),
        Err(e) => {
            tracing::error!("reindex {table_name}: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response()
        }
    }
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

/// GET /api/v1/admin/db/slow-queries
pub async fn get_slow_queries(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let rows = sqlx::query_as::<_, (String, i64, f64, f64)>(
        r#"SELECT query, calls, mean_exec_time, total_exec_time
           FROM pg_stat_statements
           ORDER BY mean_exec_time DESC
           LIMIT 20"#,
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let items: Vec<Value> = rows
        .into_iter()
        .map(|(query, calls, mean_time, total_time)| {
            json!({
                "query": &query[..query.len().min(500)],
                "calls": calls,
                "mean_exec_time_ms": mean_time,
                "total_exec_time_ms": total_time,
            })
        })
        .collect();

    Json(json!({"slow_queries": items})).into_response()
}

/// GET /api/v1/admin/db/orphans/streams
pub async fn detect_orphan_streams(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    // Streams that have no link to any media
    let count: i64 = sqlx::query_scalar(
        r#"SELECT COUNT(*) FROM stream s
           WHERE NOT EXISTS (SELECT 1 FROM stream_media_link sml WHERE sml.stream_id = s.id)
             AND NOT EXISTS (SELECT 1 FROM stream_file sf JOIN file_media_link fml ON fml.file_id = sf.id WHERE sf.stream_id = s.id)"#,
    )
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    Json(json!({"orphan_streams_count": count})).into_response()
}

/// GET /api/v1/admin/db/orphans/media
pub async fn detect_orphan_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    // Media with zero total_streams
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM media WHERE total_streams = 0")
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

    Json(json!({"orphan_media_count": count})).into_response()
}

/// POST /api/v1/admin/db/orphans/cleanup
pub async fn cleanup_orphans(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/db/orphans/cleanup",
        &headers,
        None,
    )
    .await
}

/// POST /api/v1/admin/db/export/table/{table_name}
pub async fn export_table(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table_name): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/db/export/table/{table_name}");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, None).await
}

/// POST /api/v1/admin/db/import/table
pub async fn import_table(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        "/api/v1/admin/db/import/table",
        &headers,
        Some(body),
    )
    .await
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

    Json(json!({"indexes": items, "total": items.len()})).into_response()
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
        let query = format!("REINDEX INDEX {idx}");
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
        let query = format!("REINDEX TABLE {table}");
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
