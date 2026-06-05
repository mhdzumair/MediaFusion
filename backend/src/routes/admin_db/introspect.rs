/// FK-graph introspection helpers and related-records handler.
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row;

use crate::state::AppState;

use super::{filters::quote_ident, forbidden, validate_admin};

#[derive(Deserialize)]
pub struct RelatedRowsQuery {
    pub id_column: Option<String>,
}

/// GET /api/v1/admin/db/tables/{table}/rows/{id}/related
pub async fn get_related_rows(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((table, id)): Path<(String, String)>,
    Query(params): Query<RelatedRowsQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    if !table.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid table name"})),
        )
            .into_response();
    }

    let id_col = params.id_column.as_deref().unwrap_or("id");
    if !id_col.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid id_column"})),
        )
            .into_response();
    }

    // Fetch outgoing FKs: columns in this table that reference other tables
    let outgoing_fks = sqlx::query(
        r#"SELECT
               kcu.column_name,
               ccu.table_name AS referenced_table,
               ccu.column_name AS referenced_column
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
               ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema = kcu.table_schema
           JOIN information_schema.constraint_column_usage ccu
               ON tc.constraint_name = ccu.constraint_name
               AND tc.table_schema = ccu.table_schema
           WHERE tc.table_name = $1
             AND tc.constraint_type = 'FOREIGN KEY'
             AND tc.table_schema = 'public'"#,
    )
    .bind(&table)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    // Fetch incoming FKs: other tables that reference this table
    let incoming_fks = sqlx::query(
        r#"SELECT
               tc.table_name AS referencing_table,
               kcu.column_name AS referencing_column,
               ccu.column_name AS referenced_column
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
    .bind(&table)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut references: Vec<Value> = Vec::new();

    // Process outgoing FKs: fetch preview of the referenced row
    for fk_row in &outgoing_fks {
        let fk_col: String = fk_row.try_get("column_name").unwrap_or_default();
        let ref_table: String = fk_row.try_get("referenced_table").unwrap_or_default();
        let ref_col: String = fk_row.try_get("referenced_column").unwrap_or_default();

        // Get the FK value from the current row
        let val_sql = format!(
            "SELECT {}::text FROM {} WHERE {}::text = $1 LIMIT 1",
            quote_ident(&fk_col),
            quote_ident(&table),
            quote_ident(id_col)
        );
        let fk_value: Option<String> = sqlx::query_scalar::<_, String>(&val_sql)
            .bind(&id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

        let (preview, row_count) = if let Some(ref fkv) = fk_value {
            // Fetch preview columns of the referenced row
            let preview_sql = format!(
                "SELECT row_to_json(t) FROM \
                 (SELECT * FROM {} WHERE {}::text = $1 LIMIT 1) t",
                quote_ident(&ref_table),
                quote_ident(&ref_col)
            );
            let preview_row: Option<Value> = sqlx::query_scalar::<_, Value>(&preview_sql)
                .bind(fkv)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);
            let count = if preview_row.is_some() { 1i64 } else { 0i64 };
            (preview_row, count)
        } else {
            (None, 0i64)
        };

        references.push(json!({
            "direction": "outgoing",
            "table": table,
            "column": fk_col,
            "referenced_table": ref_table,
            "referenced_column": ref_col,
            "row_count": row_count,
            "preview": preview,
            "navigation_value": fk_value,
        }));
    }

    // Process incoming FKs: count rows that reference this row
    for fk_row in &incoming_fks {
        let ref_table: String = fk_row.try_get("referencing_table").unwrap_or_default();
        let ref_col: String = fk_row.try_get("referencing_column").unwrap_or_default();
        let local_col: String = fk_row.try_get("referenced_column").unwrap_or_default();

        let count_sql = format!(
            "SELECT COUNT(*) FROM {} WHERE {}::text = $1",
            quote_ident(&ref_table),
            quote_ident(&ref_col)
        );
        let count: i64 = sqlx::query_scalar::<_, i64>(&count_sql)
            .bind(&id)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        references.push(json!({
            "direction": "incoming",
            "table": ref_table,
            "column": ref_col,
            "referenced_table": table,
            "referenced_column": local_col,
            "row_count": count,
            "preview": null,
            "navigation_value": null,
        }));
    }

    Json(json!({
        "table": table,
        "row_id": id,
        "references": references,
    }))
    .into_response()
}
