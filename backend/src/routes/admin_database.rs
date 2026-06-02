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
use hmac::{Hmac, KeyInit, Mac};
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

// ─── Query / Request types ────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct TableDataQuery {
    pub page: Option<i64>,
    pub per_page: Option<i64>,
    #[serde(alias = "order_by")]
    pub sort_by: Option<String>,
    #[serde(alias = "order_dir")]
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

#[derive(Deserialize)]
pub struct SlowQueriesQuery {
    pub limit: Option<i64>,
    pub min_calls: Option<i64>,
    /// Minimum mean execution time (ms). Queries faster than this are excluded. 0 = no floor.
    pub min_mean_time_ms: Option<f64>,
    pub order_by: Option<String>,
}

#[derive(Deserialize)]
pub struct MaintenanceTablesRequest {
    pub tables: Option<Vec<String>>,
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

    // Run all queries concurrently
    let (col_rows, idx_rows, fk_rows, pk_rows, fk_col_rows, row_count, size_human) = tokio::join!(
        // columns
        sqlx::query_as::<_, (String, String, bool, Option<String>)>(
            r#"SELECT column_name, data_type,
                      is_nullable = 'YES',
                      column_default
               FROM information_schema.columns
               WHERE table_name = $1 AND table_schema = 'public'
               ORDER BY ordinal_position"#,
        )
        .bind(&table_name)
        .fetch_all(&state.pool_ro),
        // indexes with columns via pg_catalog
        sqlx::query_as::<_, (String, bool, bool, String, String)>(
            r#"SELECT
                   i.relname,
                   ix.indisunique,
                   ix.indisprimary,
                   am.amname,
                   string_agg(a.attname, ',' ORDER BY k.ord)
               FROM pg_class t
               JOIN pg_index ix ON t.oid = ix.indrelid
               JOIN pg_class i ON ix.indexrelid = i.oid
               JOIN pg_am am ON i.relam = am.oid
               JOIN pg_namespace n ON t.relnamespace = n.oid
               JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
               JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum AND k.attnum > 0
               WHERE t.relname = $1 AND n.nspname = 'public'
               GROUP BY i.relname, ix.indisunique, ix.indisprimary, am.amname"#,
        )
        .bind(&table_name)
        .fetch_all(&state.pool_ro),
        // foreign key constraints
        sqlx::query_as::<_, (String, String, String, String)>(
            r#"SELECT
                   tc.constraint_name,
                   string_agg(kcu.column_name, ',' ORDER BY kcu.ordinal_position),
                   ccu.table_name,
                   string_agg(ccu.column_name, ',' ORDER BY kcu.ordinal_position)
               FROM information_schema.table_constraints tc
               JOIN information_schema.key_column_usage kcu
                   ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
               JOIN information_schema.constraint_column_usage ccu
                   ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
               WHERE tc.table_name = $1 AND tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
               GROUP BY tc.constraint_name, ccu.table_name"#,
        )
        .bind(&table_name)
        .fetch_all(&state.pool_ro),
        // primary key columns
        sqlx::query_as::<_, (String,)>(
            r#"SELECT kcu.column_name
               FROM information_schema.table_constraints tc
               JOIN information_schema.key_column_usage kcu
                   ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
               WHERE tc.table_name = $1 AND tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public'"#,
        )
        .bind(&table_name)
        .fetch_all(&state.pool_ro),
        // foreign key column names (to mark is_foreign_key on columns)
        sqlx::query_as::<_, (String, String, String)>(
            r#"SELECT kcu.column_name, ccu.table_name, ccu.column_name
               FROM information_schema.table_constraints tc
               JOIN information_schema.key_column_usage kcu
                   ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
               JOIN information_schema.constraint_column_usage ccu
                   ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
               WHERE tc.table_name = $1 AND tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'"#,
        )
        .bind(&table_name)
        .fetch_all(&state.pool_ro),
        // row count + size
        async {
            sqlx::query_scalar::<_, i64>(
                "SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = $1",
            )
            .bind(&table_name)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .unwrap_or(0)
        },
        async {
            sqlx::query_scalar::<_, String>(
                "SELECT pg_size_pretty(pg_total_relation_size(quote_ident($1)))",
            )
            .bind(&table_name)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .unwrap_or_else(|| "0 bytes".to_string())
        },
    );

    let col_rows = col_rows.unwrap_or_default();
    let idx_rows = idx_rows.unwrap_or_default();
    let fk_rows = fk_rows.unwrap_or_default();
    let pk_rows = pk_rows.unwrap_or_default();
    let fk_col_rows = fk_col_rows.unwrap_or_default();

    let pk_cols: std::collections::HashSet<String> = pk_rows.into_iter().map(|(c,)| c).collect();
    let fk_col_map: std::collections::HashMap<String, String> = fk_col_rows
        .into_iter()
        .map(|(col, ref_tbl, ref_col)| (col, format!("{ref_tbl}.{ref_col}")))
        .collect();

    let col_items: Vec<Value> = col_rows
        .into_iter()
        .map(|(name, dtype, nullable, default)| {
            let is_pk = pk_cols.contains(&name);
            let fk_ref = fk_col_map.get(&name).cloned();
            json!({
                "name": name,
                "data_type": dtype,
                "is_nullable": nullable,
                "default_value": default,
                "is_primary_key": is_pk,
                "is_foreign_key": fk_ref.is_some(),
                "foreign_key_ref": fk_ref,
            })
        })
        .collect();

    let idx_items: Vec<Value> = idx_rows
        .into_iter()
        .map(|(name, is_unique, is_primary, index_type, cols_str)| {
            let cols: Vec<&str> = cols_str.split(',').collect();
            json!({
                "name": name,
                "columns": cols,
                "is_unique": is_unique,
                "is_primary": is_primary,
                "index_type": index_type,
            })
        })
        .collect();

    let fk_items: Vec<Value> = fk_rows
        .into_iter()
        .map(|(name, cols_str, ref_table, ref_cols_str)| {
            let cols: Vec<&str> = cols_str.split(',').collect();
            let ref_cols: Vec<&str> = ref_cols_str.split(',').collect();
            json!({
                "name": name,
                "columns": cols,
                "referenced_table": ref_table,
                "referenced_columns": ref_cols,
            })
        })
        .collect();

    Json(json!({
        "name": table_name,
        "schema_name": "public",
        "columns": col_items,
        "indexes": idx_items,
        "foreign_keys": fk_items,
        "row_count": row_count,
        "size_human": size_human,
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

    // Fetch column names to determine a valid sort column and populate the columns field
    let col_names: Vec<String> = sqlx::query_scalar(
        "SELECT column_name FROM information_schema.columns \
         WHERE table_name = $1 AND table_schema = 'public' ORDER BY ordinal_position",
    )
    .bind(&table_name)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    if col_names.is_empty() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Table not found or has no columns"})),
        )
            .into_response();
    }

    // Determine sort column: use requested, fall back to 'id', then first indexed column
    let requested_sort = params.sort_by.as_deref().unwrap_or("id");
    if !requested_sort
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_')
    {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid sort column"})),
        )
            .into_response();
    }

    // Only sort if the requested column exists; otherwise use ctid (physical order, no sort cost)
    let sort_col = if col_names.iter().any(|c| c == requested_sort) {
        requested_sort.to_string()
    } else {
        "ctid".to_string()
    };

    let sort_order = if params.sort_order.as_deref() == Some("asc") {
        "ASC"
    } else {
        "DESC"
    };

    let (total, rows) = tokio::join!(
        async {
            // Use stats estimate for large tables — exact COUNT(*) on 75M+ row tables is seconds
            sqlx::query_scalar::<_, i64>(
                "SELECT GREATEST(n_live_tup, 0) FROM pg_stat_user_tables WHERE relname = $1",
            )
            .bind(&table_name)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .unwrap_or(0)
        },
        async {
            let q = format!(
                "SELECT row_to_json(t) FROM \
                 (SELECT * FROM {table_name} ORDER BY {sort_col} {sort_order} \
                 LIMIT {per_page} OFFSET {offset}) t"
            );
            sqlx::query_scalar::<_, Value>(&q)
                .fetch_all(&state.pool_ro)
                .await
                .unwrap_or_default()
        },
    );

    let pages = (total + per_page - 1) / per_page;

    Json(json!({
        "table": table_name,
        "columns": col_names,
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

/// POST /api/v1/admin/db/maintenance/reindex
pub async fn run_reindex(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    body: Option<Json<MaintenanceTablesRequest>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let tables: Vec<String> = body
        .and_then(|Json(req)| req.tables)
        .unwrap_or_default();

    if tables.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "At least one table name is required for REINDEX"})),
        )
            .into_response();
    }

    for table_name in &tables {
        if !table_name.chars().all(|c| c.is_alphanumeric() || c == '_') {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("Invalid table name: {table_name}")})),
            )
                .into_response();
        }
    }

    let mut processed = Vec::new();
    for table_name in tables {
        let query = format!("REINDEX TABLE {table_name}");
        match sqlx::query(&query).execute(&state.pool).await {
            Ok(_) => processed.push(table_name),
            Err(e) => {
                tracing::error!("reindex {table_name}: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": e.to_string()})),
                )
                    .into_response();
            }
        }
    }

    Json(json!({
        "status": "success",
        "message": format!("REINDEX TABLE completed for {} table(s)", processed.len()),
        "tables_processed": processed,
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

/// GET /api/v1/admin/db/slow-queries
pub async fn get_slow_queries(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<SlowQueriesQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let limit = params.limit.unwrap_or(20).clamp(1, 100);
    let min_calls = params.min_calls.unwrap_or(5).max(1);
    let min_mean_time_ms = params.min_mean_time_ms.unwrap_or(5.0).max(0.0);
    let order_by = params.order_by.as_deref().unwrap_or("mean_exec_time");
    let valid_order = ["mean_exec_time", "max_exec_time", "total_exec_time"];
    if !valid_order.contains(&order_by) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "detail": format!(
                    "order_by must be one of: {}",
                    valid_order.join(", ")
                )
            })),
        )
            .into_response();
    }

    let ext_installed: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')",
    )
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(false);

    if !ext_installed {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({
                "detail": "pg_stat_statements is not available. \
                    Add shared_preload_libraries=pg_stat_statements to PostgreSQL, restart the server, \
                    then run migration 0014 or: CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
            })),
        )
            .into_response();
    }

    let sql = format!(
        r#"SELECT
               queryid,
               LEFT(query, 500) AS query_preview,
               calls,
               round(total_exec_time::numeric, 2)::float8 AS total_exec_time_ms,
               round(mean_exec_time::numeric, 2)::float8 AS mean_exec_time_ms,
               round(min_exec_time::numeric, 2)::float8 AS min_exec_time_ms,
               round(max_exec_time::numeric, 2)::float8 AS max_exec_time_ms,
               round(stddev_exec_time::numeric, 2)::float8 AS stddev_exec_time_ms,
               rows,
               round(
                   100.0 * shared_blks_hit /
                   NULLIF(shared_blks_hit + shared_blks_read, 0), 1
               )::float8 AS cache_hit_pct,
               shared_blks_read,
               shared_blks_hit,
               temp_blks_read,
               temp_blks_written
           FROM pg_stat_statements
           WHERE calls >= $1
             AND ($3 <= 0 OR mean_exec_time >= $3)
             AND query NOT ILIKE '%pg_stat_statements%'
             AND query !~* '^\\s*(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\\s*;?\\s*$'
             AND query NOT ILIKE 'SET %'
             AND query NOT ILIKE 'SHOW %'
             AND query NOT ILIKE 'DISCARD %'
             AND query NOT ILIKE 'DEALLOCATE %'
             AND query NOT ILIKE 'SELECT 1%'
           ORDER BY {order_by} DESC
           LIMIT $2"#
    );

    let rows = match sqlx::query(&sql)
        .bind(min_calls)
        .bind(limit)
        .bind(min_mean_time_ms)
        .fetch_all(&state.pool_ro)
        .await
    {
        Ok(rows) => rows,
        Err(e) => {
            tracing::error!("slow-queries: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response();
        }
    };

    let queries: Vec<Value> = rows
        .into_iter()
        .map(|row| {
            use sqlx::Row;
            json!({
                "queryid": row.try_get::<i64, _>("queryid").ok(),
                "query_preview": row.try_get::<String, _>("query_preview").unwrap_or_default(),
                "calls": row.try_get::<i64, _>("calls").unwrap_or(0),
                "total_exec_time_ms": row.try_get::<f64, _>("total_exec_time_ms").unwrap_or(0.0),
                "mean_exec_time_ms": row.try_get::<f64, _>("mean_exec_time_ms").unwrap_or(0.0),
                "min_exec_time_ms": row.try_get::<f64, _>("min_exec_time_ms").unwrap_or(0.0),
                "max_exec_time_ms": row.try_get::<f64, _>("max_exec_time_ms").unwrap_or(0.0),
                "stddev_exec_time_ms": row.try_get::<f64, _>("stddev_exec_time_ms").unwrap_or(0.0),
                "rows": row.try_get::<i64, _>("rows").unwrap_or(0),
                "cache_hit_pct": row.try_get::<Option<f64>, _>("cache_hit_pct").ok().flatten(),
                "shared_blks_read": row.try_get::<i64, _>("shared_blks_read").unwrap_or(0),
                "shared_blks_hit": row.try_get::<i64, _>("shared_blks_hit").unwrap_or(0),
                "temp_blks_read": row.try_get::<i64, _>("temp_blks_read").unwrap_or(0),
                "temp_blks_written": row.try_get::<i64, _>("temp_blks_written").unwrap_or(0),
            })
        })
        .collect();

    Json(json!({
        "order_by": order_by,
        "min_calls": min_calls,
        "min_mean_time_ms": min_mean_time_ms,
        "count": queries.len(),
        "queries": queries,
    }))
    .into_response()
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
    let result = sqlx::query(
        "DELETE FROM stream_media_link sml \
         WHERE NOT EXISTS (SELECT 1 FROM media m WHERE m.id = sml.media_id)",
    )
    .execute(&state.pool)
    .await;

    match result {
        Ok(r) => {
            Json(json!({"status": "success", "cleaned_rows": r.rows_affected()})).into_response()
        }
        Err(e) => {
            tracing::error!("cleanup_orphans error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error during orphan cleanup"})),
            )
                .into_response()
        }
    }
}

const ALLOWED_TABLES: &[&str] = &[
    "media",
    "stream",
    "torrent_stream",
    "http_stream",
    "youtube_stream",
    "acestream_stream",
    "rss_feed",
    "media_external_id",
    "stream_media_link",
    "users",
    "user_profiles",
];

/// POST /api/v1/admin/db/export/table/{table_name}
pub async fn export_table(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table_name): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if !ALLOWED_TABLES.contains(&table_name.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Table not allowed for export"})),
        )
            .into_response();
    }

    let query = format!("SELECT row_to_json(t) FROM {table_name} t LIMIT 10000");
    let rows: Vec<Value> = sqlx::query_scalar::<_, Value>(&query)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

    let row_count = rows.len();
    Json(json!({
        "table": table_name,
        "row_count": row_count,
        "rows": rows,
    }))
    .into_response()
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

    let table = match body.get("table").and_then(|v| v.as_str()) {
        Some(t) => t.to_string(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'table' field"})),
            )
                .into_response();
        }
    };

    if !ALLOWED_TABLES.contains(&table.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Table not allowed for import"})),
        )
            .into_response();
    }

    let rows = match body.get("rows").and_then(|v| v.as_array()) {
        Some(r) if !r.is_empty() => r.clone(),
        Some(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "rows array is empty"})),
            )
                .into_response();
        }
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing or invalid 'rows' field"})),
            )
                .into_response();
        }
    };

    let row_count = rows.len();
    let rows_json = serde_json::to_string(&rows).unwrap_or_else(|_| "[]".to_string());
    let sql = format!(
        "INSERT INTO {table} SELECT * FROM json_populate_recordset(null::{table}, $1::json)"
    );

    match sqlx::query(&sql)
        .bind(&rows_json)
        .execute(&state.pool)
        .await
    {
        Ok(_) => (
            StatusCode::ACCEPTED,
            Json(json!({
                "detail": "Import accepted",
                "table": table,
                "row_count": row_count,
            })),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("import_table {table}: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response()
        }
    }
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

// ── Legacy /api/v1/admin/db/ path alias stubs ─────────────────────────────────

/// GET /api/v1/admin/db/tables/{table}/export — alias for export_table with different param order
pub async fn export_table_by_path(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table): Path<String>,
    Query(_params): Query<serde_json::Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"error": "Forbidden"}))).into_response();
    }

    if !ALLOWED_TABLES.contains(&table.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Table not allowed for export"})),
        )
            .into_response();
    }

    let query = format!("SELECT row_to_json(t) FROM {table} t LIMIT 10000");
    let rows: Vec<Value> = sqlx::query_scalar::<_, Value>(&query)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

    let row_count = rows.len();
    Json(json!({
        "table": table,
        "row_count": row_count,
        "rows": rows,
    }))
    .into_response()
}

/// GET /api/v1/admin/db/orphans — combined orphan detection
pub async fn detect_orphans_combined(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"error": "Forbidden"}))).into_response();
    }

    // 30s hard cap — NOT EXISTS on millions of rows can still be slow without FK indexes
    let timeout = "SET LOCAL statement_timeout = '30s'";
    let (orphan_sml_by_media, orphan_sml_by_stream, orphan_http_streams) = tokio::join!(
        async {
            let mut tx = state.pool_ro.begin().await.ok()?;
            sqlx::query(timeout).execute(&mut *tx).await.ok()?;
            let n: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM stream_media_link sml \
                 WHERE NOT EXISTS (SELECT 1 FROM media m WHERE m.id = sml.media_id)",
            )
            .fetch_one(&mut *tx)
            .await
            .ok()?;
            Some(n)
        },
        async {
            let mut tx = state.pool_ro.begin().await.ok()?;
            sqlx::query(timeout).execute(&mut *tx).await.ok()?;
            let n: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM stream_media_link sml \
                 WHERE NOT EXISTS (SELECT 1 FROM stream s WHERE s.id = sml.stream_id)",
            )
            .fetch_one(&mut *tx)
            .await
            .ok()?;
            Some(n)
        },
        async {
            let mut tx = state.pool_ro.begin().await.ok()?;
            sqlx::query(timeout).execute(&mut *tx).await.ok()?;
            let n: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM http_stream hs \
                 WHERE NOT EXISTS (SELECT 1 FROM stream s WHERE s.id = hs.stream_id)",
            )
            .fetch_one(&mut *tx)
            .await
            .ok()?;
            Some(n)
        },
    );
    let orphan_sml_by_media = orphan_sml_by_media.unwrap_or(-1);
    let orphan_sml_by_stream = orphan_sml_by_stream.unwrap_or(-1);
    let orphan_http_streams = orphan_http_streams.unwrap_or(-1);

    let known = [
        orphan_sml_by_media,
        orphan_sml_by_stream,
        orphan_http_streams,
    ];
    let total: i64 = known.iter().filter(|&&v| v >= 0).sum();
    let timed_out = known.iter().any(|&v| v < 0);
    Json(json!({
        "orphan_stream_media_links_by_media": orphan_sml_by_media,
        "orphan_stream_media_links_by_stream": orphan_sml_by_stream,
        "orphan_http_streams": orphan_http_streams,
        "total": total,
        "timed_out": timed_out,
    }))
    .into_response()
}

/// POST /api/v1/admin/db/slow-queries/reset
pub async fn reset_slow_queries(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"error": "Forbidden"}))).into_response();
    }
    let ext_installed: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')",
    )
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(false);

    if !ext_installed {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail": "pg_stat_statements extension is not installed."})),
        )
            .into_response();
    }

    match sqlx::query("SELECT pg_stat_statements_reset()")
        .execute(&state.pool)
        .await
    {
        Ok(_) => Json(json!({
            "status": "ok",
            "message": "pg_stat_statements stats reset successfully."
        }))
        .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": e.to_string()})),
        )
            .into_response(),
    }
}

/// POST /api/v1/admin/db/bulk/delete
pub async fn bulk_delete(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"error": "Forbidden"}))).into_response();
    }

    const ALLOWED_TABLES: &[&str] = &["media", "streams", "torrent_streams"];

    let table = match body.get("table").and_then(|v| v.as_str()) {
        Some(t) => t.to_string(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'table' field"})),
            )
                .into_response()
        }
    };

    if !ALLOWED_TABLES.contains(&table.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Table not allowed for bulk delete"})),
        )
            .into_response();
    }

    let ids: Vec<i64> = match body.get("ids").and_then(|v| v.as_array()) {
        Some(arr) => arr.iter().filter_map(|v| v.as_i64()).collect(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing or invalid 'ids' field"})),
            )
                .into_response()
        }
    };

    if ids.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "'ids' must not be empty"})),
        )
            .into_response();
    }

    let sql = format!("DELETE FROM {} WHERE id = ANY($1)", table);
    match sqlx::query(&sql).bind(&ids).execute(&state.pool).await {
        Ok(r) => Json(json!({
            "status": "success",
            "deleted_count": r.rows_affected(),
            "table": table,
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("bulk_delete error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/admin/db/bulk/update
pub async fn bulk_update(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"error": "Forbidden"}))).into_response();
    }

    // Only allow specific tables and columns
    let allowed_columns: std::collections::HashMap<&str, &[&str]> = [
        ("media", ["is_blocked"].as_slice()),
        ("streams", ["is_blocked"].as_slice()),
    ]
    .iter()
    .cloned()
    .collect();

    let table = match body.get("table").and_then(|v| v.as_str()) {
        Some(t) => t.to_string(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'table' field"})),
            )
                .into_response()
        }
    };

    let allowed_cols = match allowed_columns.get(table.as_str()) {
        Some(cols) => *cols,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Table not allowed for bulk update"})),
            )
                .into_response()
        }
    };

    let ids: Vec<i64> = match body.get("ids").and_then(|v| v.as_array()) {
        Some(arr) => arr.iter().filter_map(|v| v.as_i64()).collect(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing or invalid 'ids' field"})),
            )
                .into_response()
        }
    };

    let updates = match body.get("updates").and_then(|v| v.as_object()) {
        Some(u) => u.clone(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing or invalid 'updates' field"})),
            )
                .into_response()
        }
    };

    if ids.is_empty() || updates.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "'ids' and 'updates' must not be empty"})),
        )
            .into_response();
    }

    let mut total_updated: u64 = 0;
    for (col, val) in &updates {
        if !allowed_cols.contains(&col.as_str()) {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("Column '{}' not allowed for this table", col)})),
            )
                .into_response();
        }

        let bool_val = match val.as_bool() {
            Some(b) => b,
            None => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": format!("Value for '{}' must be a boolean", col)})),
                )
                    .into_response()
            }
        };

        let sql = format!("UPDATE {} SET {} = $1 WHERE id = ANY($2)", table, col);
        match sqlx::query(&sql)
            .bind(bool_val)
            .bind(&ids)
            .execute(&state.pool)
            .await
        {
            Ok(r) => total_updated += r.rows_affected(),
            Err(e) => {
                tracing::error!("bulk_update error: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": e.to_string()})),
                )
                    .into_response();
            }
        }
    }

    Json(json!({
        "status": "success",
        "updated_count": total_updated,
    }))
    .into_response()
}

/// POST /api/v1/admin/db/import/preview
pub async fn import_preview(
    headers: HeaderMap,
    State(_state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &_state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"error": "Forbidden"}))).into_response();
    }

    let table = match body.get("table").and_then(|v| v.as_str()) {
        Some(t) => t.to_string(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'table' field"})),
            )
                .into_response();
        }
    };

    if !ALLOWED_TABLES.contains(&table.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Table not allowed for import"})),
        )
            .into_response();
    }

    let rows = body
        .get("rows")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let row_count = rows.len();
    let preview: Vec<_> = rows.iter().take(5).cloned().collect();

    Json(json!({
        "table": table,
        "row_count": row_count,
        "preview": preview,
        "status": "preview_only",
    }))
    .into_response()
}

/// POST /api/v1/admin/db/import/execute
pub async fn import_execute(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"error": "Forbidden"}))).into_response();
    }

    let table = match body.get("table").and_then(|v| v.as_str()) {
        Some(t) => t.to_string(),
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'table' field"})),
            )
                .into_response();
        }
    };

    if !ALLOWED_TABLES.contains(&table.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Table not allowed for import"})),
        )
            .into_response();
    }

    let rows = match body.get("rows").and_then(|v| v.as_array()) {
        Some(r) if !r.is_empty() => r.clone(),
        Some(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "rows array is empty"})),
            )
                .into_response();
        }
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing or invalid 'rows' field"})),
            )
                .into_response();
        }
    };

    let row_count = rows.len();
    let rows_json = serde_json::to_string(&rows).unwrap_or_else(|_| "[]".to_string());
    let sql = format!(
        "INSERT INTO {table} SELECT * FROM json_populate_recordset(null::{table}, $1::json)"
    );

    match sqlx::query(&sql)
        .bind(&rows_json)
        .execute(&state.pool)
        .await
    {
        Ok(_) => Json(json!({
            "detail": "Import executed",
            "table": table,
            "row_count": row_count,
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("import_execute {table}: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response()
        }
    }
}

/// GET /api/v1/admin/db/tables/{table}/rows/{id}/related
pub async fn get_related_rows(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((table, id)): Path<(String, i64)>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"error": "Forbidden"}))).into_response();
    }

    if !ALLOWED_TABLES.contains(&table.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Table not allowed"})),
        )
            .into_response();
    }

    match table.as_str() {
        "media" => {
            let count: i64 =
                sqlx::query_scalar("SELECT COUNT(*) FROM stream_media_link WHERE media_id = $1")
                    .bind(id)
                    .fetch_one(&state.pool_ro)
                    .await
                    .unwrap_or(0);
            Json(json!({
                "table": table,
                "id": id,
                "related": {
                    "stream_media_link": count,
                }
            }))
            .into_response()
        }
        "stream" => {
            let count: i64 =
                sqlx::query_scalar("SELECT COUNT(*) FROM stream_media_link WHERE stream_id = $1")
                    .bind(id)
                    .fetch_one(&state.pool_ro)
                    .await
                    .unwrap_or(0);
            Json(json!({
                "table": table,
                "id": id,
                "related": {
                    "stream_media_link": count,
                }
            }))
            .into_response()
        }
        _ => Json(json!({
            "message": "Related rows lookup not supported for this table",
            "table": table,
            "id": id,
        }))
        .into_response(),
    }
}
