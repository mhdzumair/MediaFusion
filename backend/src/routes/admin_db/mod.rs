/// Admin database management endpoints.
///
/// Routes (prefix /api/v1/admin/db):
///   GET    /tables/{table}/schema            → get_table_schema
///   GET    /tables/{table}/data              → get_table_data
///   GET    /tables/{table}/export            → import_export::export_table
///   GET    /tables/{table}/rows/{id}/related → introspect::get_related_rows
///   DELETE /tables/{table}/rows/{id}         → delete_table_row (single)
///   GET    /orphans                          → orphans::detect_orphans_combined
///   POST   /orphans/cleanup                  → orphans::cleanup_orphans
///   GET    /orphans/streams                  → orphans::detect_orphan_streams
///   GET    /orphans/media                    → orphans::detect_orphan_media
///   GET    /slow-queries                     → get_slow_queries
///   POST   /slow-queries/reset               → reset_slow_queries
///   POST   /maintenance/vacuum               → maintenance::run_vacuum
///   POST   /maintenance/analyze              → maintenance::run_analyze
///   POST   /maintenance/reindex              → maintenance::run_reindex
///   GET    /maintenance/bloat                → maintenance::get_bloat_stats
///   GET    /indexes                          → maintenance::list_indexes
///   POST   /indexes/rebuild                  → maintenance::rebuild_indexes
///   POST   /bulk/delete                      → bulk::bulk_delete
///   POST   /bulk/update                      → bulk::bulk_update
///   POST   /import/preview                   → import_export::import_preview
///   POST   /import/execute                   → import_export::import_execute
pub mod bulk;
pub mod filters;
pub mod import_export;
pub mod introspect;
pub mod maintenance;
pub mod orphans;

use std::collections::HashMap;
use std::sync::Arc;

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::Sha256;

use crate::state::AppState;

use filters::{build_where, quote_ident};

// ─── Auth helper ──────────────────────────────────────────────────────────────

pub fn validate_admin(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
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

pub fn forbidden() -> axum::response::Response {
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
    pub filters: Option<String>, // JSON array: [{column, operator, value}]
    // Legacy single-filter params
    pub filter_column: Option<String>,
    pub filter_operator: Option<String>,
    pub filter_value: Option<String>,
}

#[derive(Deserialize)]
pub struct DeleteRowsRequest {
    pub ids: Vec<Value>,
    pub id_column: Option<String>,
}

#[derive(Deserialize)]
pub struct SlowQueriesQuery {
    pub limit: Option<i64>,
    pub min_calls: Option<i64>,
    /// Minimum mean execution time (ms). Queries faster than this are excluded.
    pub min_mean_time_ms: Option<f64>,
    pub order_by: Option<String>,
}

// ─── Table schema ─────────────────────────────────────────────────────────────

/// GET /api/v1/admin/db/tables/{table_name}/schema
pub async fn get_table_schema(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table_name): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let (col_rows, idx_rows, fk_rows, pk_rows, fk_col_rows, row_count, size_human) = tokio::join!(
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
        sqlx::query_as::<_, (String,)>(
            r#"SELECT kcu.column_name
               FROM information_schema.table_constraints tc
               JOIN information_schema.key_column_usage kcu
                   ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
               WHERE tc.table_name = $1 AND tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public'"#,
        )
        .bind(&table_name)
        .fetch_all(&state.pool_ro),
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
        async {
            // Section A fix: GREATEST(reltuples, n_live_tup, 0)
            sqlx::query_scalar::<_, i64>(
                "SELECT GREATEST(c.reltuples::bigint, COALESCE(s.n_live_tup, 0), 0) \
                 FROM pg_stat_user_tables s \
                 JOIN pg_class c ON c.relname = s.relname \
                 JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = s.schemaname \
                 WHERE s.relname = $1 AND s.schemaname = 'public'",
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
    let fk_col_map: HashMap<String, String> = fk_col_rows
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

// ─── Table data ───────────────────────────────────────────────────────────────

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

    if !table_name.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid table name"})),
        )
            .into_response();
    }

    let page = params.page.unwrap_or(1).max(1);
    // Section B: per_page default 50, max 500
    let per_page = params.per_page.unwrap_or(50).clamp(1, 500);
    let offset = (page - 1) * per_page;

    // Fetch column names + data types
    let col_rows: Vec<(String, String)> = sqlx::query_as(
        "SELECT column_name, data_type FROM information_schema.columns \
         WHERE table_name = $1 AND table_schema = 'public' ORDER BY ordinal_position",
    )
    .bind(&table_name)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    if col_rows.is_empty() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Table not found or has no columns"})),
        )
            .into_response();
    }

    let col_names: Vec<String> = col_rows.iter().map(|(n, _)| n.clone()).collect();
    let col_types: HashMap<String, String> = col_rows.into_iter().collect();

    // Determine sort column
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

    let sort_col = if col_names.iter().any(|c| c == requested_sort) {
        requested_sort.to_string()
    } else if col_names.iter().any(|c| c == "id") {
        "id".to_string()
    } else {
        "ctid".to_string()
    };

    // Section B: default sort ASC
    let sort_order = if params.sort_order.as_deref() == Some("desc")
        || params.sort_order.as_deref() == Some("DESC")
    {
        "DESC"
    } else {
        "ASC"
    };

    // Build WHERE clause
    let (where_sql, bind_values) = build_where(
        &col_types,
        params.filters.as_deref(),
        params.search.as_deref(),
        params.filter_column.as_deref(),
        params.filter_operator.as_deref(),
        params.filter_value.as_deref(),
        1,
    );

    let has_filter = !where_sql.is_empty();

    let (total, rows) = tokio::join!(
        async {
            if has_filter {
                // Section B: exact COUNT when filtered
                let count_sql = format!(
                    "SELECT COUNT(*) FROM {}{}",
                    quote_ident(&table_name),
                    where_sql
                );
                let mut q = sqlx::query_scalar::<_, i64>(sqlx::AssertSqlSafe(count_sql.as_str()));
                for v in &bind_values {
                    q = q.bind(v);
                }
                q.fetch_one(&state.pool_ro).await.unwrap_or(0)
            } else {
                // Section A: fast estimate with GREATEST(reltuples, n_live_tup, 0)
                sqlx::query_scalar::<_, i64>(
                    "SELECT GREATEST(c.reltuples::bigint, COALESCE(s.n_live_tup, 0), 0) \
                     FROM pg_stat_user_tables s \
                     JOIN pg_class c ON c.relname = s.relname \
                     JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = s.schemaname \
                     WHERE s.relname = $1 AND s.schemaname = 'public'",
                )
                .bind(&table_name)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None)
                .unwrap_or(0)
            }
        },
        async {
            let (where_sql2, bind_values2) = build_where(
                &col_types,
                params.filters.as_deref(),
                params.search.as_deref(),
                params.filter_column.as_deref(),
                params.filter_operator.as_deref(),
                params.filter_value.as_deref(),
                1,
            );
            let data_sql = format!(
                "SELECT row_to_json(t) FROM \
                 (SELECT * FROM {}{} ORDER BY {} {} LIMIT {} OFFSET {}) t",
                quote_ident(&table_name),
                where_sql2,
                quote_ident(&sort_col),
                sort_order,
                per_page,
                offset
            );
            let mut q = sqlx::query_scalar::<_, Value>(sqlx::AssertSqlSafe(data_sql.as_str()));
            for v in &bind_values2 {
                q = q.bind(v);
            }
            q.fetch_all(&state.pool_ro).await.unwrap_or_default()
        },
    );

    // Section A: pages never 0 for empty table
    let pages = if total == 0 {
        1
    } else {
        (total + per_page - 1) / per_page
    };

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

// ─── Delete rows ──────────────────────────────────────────────────────────────

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

    let ids_json = serde_json::to_string(&body.ids).unwrap_or_else(|_| "[]".to_string());

    let query = format!(
        "DELETE FROM {} WHERE {}::text = ANY(SELECT jsonb_array_elements_text($1::jsonb))",
        quote_ident(&table_name),
        quote_ident(id_col)
    );

    match sqlx::query(sqlx::AssertSqlSafe(query.as_str()))
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

// ─── Slow queries ─────────────────────────────────────────────────────────────

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
    // Section J: default order_by = total_exec_time
    let order_by = params.order_by.as_deref().unwrap_or("total_exec_time");
    // Section J: expanded valid_order to include calls and rows
    let valid_order = [
        "mean_exec_time",
        "max_exec_time",
        "total_exec_time",
        "calls",
        "rows",
    ];
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

    // Section J: fix regex - use \s (single backslash in raw string = correct POSIX whitespace)
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
             AND query !~* '^\s*(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\s*;?\s*$'
             AND query NOT ILIKE 'SET %'
             AND query NOT ILIKE 'SHOW %'
             AND query NOT ILIKE 'DISCARD %'
             AND query NOT ILIKE 'DEALLOCATE %'
             AND query NOT ILIKE 'SELECT 1%'
           ORDER BY {order_by} DESC
           LIMIT $2"#
    );

    let rows = match sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
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

/// POST /api/v1/admin/db/slow-queries/reset
pub async fn reset_slow_queries(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
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
