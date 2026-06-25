/// Generic bulk delete and bulk update for any table.
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use axum::{
    Json,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::state::AppState;

use super::{filters::quote_ident, forbidden, validate_admin};

#[derive(Deserialize)]
pub struct BulkDeleteRequest {
    pub table: String,
    pub ids: Vec<Value>,
    pub id_column: Option<String>,
    pub cascade: Option<bool>,
}

#[derive(Deserialize)]
pub struct BulkUpdateRequest {
    pub table: String,
    pub ids: Vec<Value>,
    pub id_column: Option<String>,
    pub updates: HashMap<String, Value>,
}

/// POST /api/v1/admin/db/bulk/delete
pub async fn bulk_delete(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkDeleteRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if !body.table.chars().all(|c| c.is_alphanumeric() || c == '_') {
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
            Json(json!({"detail": "Invalid id_column"})),
        )
            .into_response();
    }

    if body.ids.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "'ids' must not be empty"})),
        )
            .into_response();
    }

    // Normalize IDs to strings for text-cast comparison
    let id_strings: Vec<String> = body
        .ids
        .iter()
        .map(|v| match v {
            Value::String(s) => s.clone(),
            Value::Number(n) => n.to_string(),
            other => other.to_string(),
        })
        .collect();

    let started = Instant::now();

    // If cascade=true, delete child records first using FK graph
    if body.cascade.unwrap_or(false)
        && let Err(e) = cascade_delete(&state, &body.table, id_col, &id_strings).await {
            tracing::error!("bulk_delete cascade error: {e}");
            // If it's a FK constraint violation, return 409
            if e.contains("foreign key") || e.contains("violates") {
                return (StatusCode::CONFLICT, Json(json!({"detail": e}))).into_response();
            }
        }

    let ids_json = serde_json::to_string(&id_strings).unwrap_or_else(|_| "[]".to_string());
    let sql = format!(
        "DELETE FROM {} WHERE {}::text = ANY(SELECT jsonb_array_elements_text($1::jsonb))",
        quote_ident(&body.table),
        quote_ident(id_col)
    );

    match sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
        .bind(&ids_json)
        .execute(&state.pool)
        .await
    {
        Ok(r) => {
            let elapsed_ms = started.elapsed().as_millis() as i64;
            Json(json!({
                "success": true,
                "rows_affected": r.rows_affected(),
                "execution_time_ms": elapsed_ms,
                "errors": [],
            }))
            .into_response()
        }
        Err(e) => {
            tracing::error!("bulk_delete {}: {e}", body.table);
            let status =
                if e.to_string().contains("foreign key") || e.to_string().contains("violates") {
                    StatusCode::CONFLICT
                } else {
                    StatusCode::INTERNAL_SERVER_ERROR
                };
            (status, Json(json!({"detail": e.to_string()}))).into_response()
        }
    }
}

/// POST /api/v1/admin/db/bulk/update
pub async fn bulk_update(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkUpdateRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if !body.table.chars().all(|c| c.is_alphanumeric() || c == '_') {
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
            Json(json!({"detail": "Invalid id_column"})),
        )
            .into_response();
    }

    // Validate update column names
    for col in body.updates.keys() {
        if !col.chars().all(|c| c.is_alphanumeric() || c == '_') {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("Invalid column name: {col}")})),
            )
                .into_response();
        }
    }

    if body.ids.is_empty() || body.updates.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "'ids' and 'updates' must not be empty"})),
        )
            .into_response();
    }

    let id_strings: Vec<String> = body
        .ids
        .iter()
        .map(|v| match v {
            Value::String(s) => s.clone(),
            Value::Number(n) => n.to_string(),
            other => other.to_string(),
        })
        .collect();

    let started = Instant::now();
    let mut total_updated: u64 = 0;
    let mut errors: Vec<String> = Vec::new();

    let ids_json = serde_json::to_string(&id_strings).unwrap_or_else(|_| "[]".to_string());

    for (col, val) in &body.updates {
        // Build SET clause with value as text and cast back for the column
        let set_clause = format!("{} = $1", quote_ident(col));
        let sql = format!(
            "UPDATE {} SET {} WHERE {}::text = ANY(SELECT jsonb_array_elements_text($2::jsonb))",
            quote_ident(&body.table),
            set_clause,
            quote_ident(id_col)
        );

        // Bind the value as the appropriate type
        let result = match val {
            Value::Bool(b) => {
                sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                    .bind(b)
                    .bind(&ids_json)
                    .execute(&state.pool)
                    .await
            }
            Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                        .bind(i)
                        .bind(&ids_json)
                        .execute(&state.pool)
                        .await
                } else if let Some(f) = n.as_f64() {
                    sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                        .bind(f)
                        .bind(&ids_json)
                        .execute(&state.pool)
                        .await
                } else {
                    sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                        .bind(n.to_string())
                        .bind(&ids_json)
                        .execute(&state.pool)
                        .await
                }
            }
            Value::String(s) => {
                sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                    .bind(s)
                    .bind(&ids_json)
                    .execute(&state.pool)
                    .await
            }
            Value::Null => {
                let null_sql = format!(
                    "UPDATE {} SET {} = NULL WHERE {}::text = ANY(SELECT jsonb_array_elements_text($1::jsonb))",
                    quote_ident(&body.table),
                    quote_ident(col),
                    quote_ident(id_col)
                );
                sqlx::query(sqlx::AssertSqlSafe(null_sql.as_str()))
                    .bind(&ids_json)
                    .execute(&state.pool)
                    .await
            }
            _ => {
                // JSON value — store as text
                let json_str = val.to_string();
                sqlx::query(sqlx::AssertSqlSafe(sql.as_str()))
                    .bind(json_str)
                    .bind(&ids_json)
                    .execute(&state.pool)
                    .await
            }
        };

        match result {
            Ok(r) => total_updated += r.rows_affected(),
            Err(e) => {
                tracing::error!("bulk_update {col}: {e}");
                errors.push(format!("{col}: {e}"));
            }
        }
    }

    let elapsed_ms = started.elapsed().as_millis() as i64;
    let success = errors.is_empty();

    Json(json!({
        "success": success,
        "rows_affected": total_updated,
        "execution_time_ms": elapsed_ms,
        "errors": errors,
    }))
    .into_response()
}

/// Recursively delete child records using FK graph before deleting the parent.
async fn cascade_delete(
    state: &Arc<AppState>,
    table: &str,
    _id_col: &str,
    ids: &[String],
) -> Result<(), String> {
    // Find tables that reference this table
    let referencing: Vec<(String, String)> = sqlx::query_as(
        r#"SELECT
               tc.table_name AS child_table,
               kcu.column_name AS child_column
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
               ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema = kcu.table_schema
           JOIN information_schema.constraint_column_usage ccu
               ON tc.constraint_name = ccu.constraint_name
               AND tc.table_schema = ccu.table_schema
           WHERE ccu.table_name = $1
             AND tc.constraint_type = 'FOREIGN KEY'
             AND tc.table_schema = 'public'"#,
    )
    .bind(table)
    .fetch_all(&state.pool_ro)
    .await
    .map_err(|e| e.to_string())?;

    let ids_json = serde_json::to_string(ids).map_err(|e| e.to_string())?;

    for (child_table, child_col) in &referencing {
        if !child_table.chars().all(|c| c.is_alphanumeric() || c == '_') {
            continue;
        }
        if !child_col.chars().all(|c| c.is_alphanumeric() || c == '_') {
            continue;
        }
        let del_sql = format!(
            "DELETE FROM {} WHERE {}::text = ANY(SELECT jsonb_array_elements_text($1::jsonb))",
            quote_ident(child_table),
            quote_ident(child_col)
        );
        sqlx::query(sqlx::AssertSqlSafe(del_sql.as_str()))
            .bind(&ids_json)
            .execute(&state.pool)
            .await
            .map_err(|e| e.to_string())?;
    }

    Ok(())
}
