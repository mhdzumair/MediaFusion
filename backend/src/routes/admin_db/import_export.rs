/// Export (CSV/JSON/SQL download) and Import (multipart CSV/JSON) handlers.
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use crate::state::AppState;
use axum::{
    extract::{Multipart, Path, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};

use super::{forbidden, validate_admin};

#[derive(Deserialize)]
pub struct ExportQuery {
    pub format: Option<String>,
    pub include_schema: Option<bool>,
    pub include_data: Option<bool>,
    pub limit: Option<i64>,
}

/// GET /api/v1/admin/db/tables/{table}/export
pub async fn export_table(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(table): Path<String>,
    Query(params): Query<ExportQuery>,
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

    // Check table exists
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables \
         WHERE table_name = $1 AND table_schema = 'public')",
    )
    .bind(&table)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Table not found"})),
        )
            .into_response();
    }

    let format = params.format.as_deref().unwrap_or("csv");
    let include_schema = params.include_schema.unwrap_or(true);
    let include_data = params.include_data.unwrap_or(true);
    let limit = params.limit.unwrap_or(10000).clamp(1, 100000);

    // Fetch column info for schema export
    let col_rows = sqlx::query_as::<_, (String, String, bool, Option<String>)>(
        "SELECT column_name, data_type, (is_nullable = 'YES'), column_default \
         FROM information_schema.columns \
         WHERE table_name = $1 AND table_schema = 'public' \
         ORDER BY ordinal_position",
    )
    .bind(&table)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    // Fetch rows for data export
    let rows: Vec<Value> = if include_data {
        let data_sql = format!(
            "SELECT row_to_json(t) FROM (SELECT * FROM \"{}\" ORDER BY ctid LIMIT {}) t",
            table, limit
        );
        sqlx::query_scalar::<_, Value>(&data_sql)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default()
    } else {
        Vec::new()
    };

    match format {
        "csv" => {
            let mut csv_data: Vec<u8> = Vec::new();
            {
                let mut wtr = csv::Writer::from_writer(&mut csv_data);

                // Write header
                let col_names: Vec<String> =
                    col_rows.iter().map(|(n, _, _, _)| n.clone()).collect();
                wtr.write_record(&col_names).ok();

                // Write rows
                for row in &rows {
                    if let Some(obj) = row.as_object() {
                        let record: Vec<String> = col_names
                            .iter()
                            .map(|col| {
                                obj.get(col)
                                    .map(|v| match v {
                                        Value::Null => String::new(),
                                        Value::String(s) => s.clone(),
                                        other => other.to_string(),
                                    })
                                    .unwrap_or_default()
                            })
                            .collect();
                        wtr.write_record(&record).ok();
                    }
                }
                wtr.flush().ok();
            }

            (
                [
                    (header::CONTENT_TYPE, "text/csv; charset=utf-8"),
                    (
                        header::CONTENT_DISPOSITION,
                        &format!("attachment; filename=\"{}.csv\"", table),
                    ),
                ],
                csv_data,
            )
                .into_response()
        }
        "json" => {
            let json_body = serde_json::to_vec_pretty(&json!({
                "table": table,
                "row_count": rows.len(),
                "rows": rows,
            }))
            .unwrap_or_default();

            (
                [
                    (header::CONTENT_TYPE, "application/json"),
                    (
                        header::CONTENT_DISPOSITION,
                        &format!("attachment; filename=\"{}.json\"", table),
                    ),
                ],
                json_body,
            )
                .into_response()
        }
        "sql" => {
            let mut sql_out = String::new();

            if include_schema {
                sql_out.push_str(&format!("-- Table: {}\n", table));
                sql_out.push_str(&format!("CREATE TABLE IF NOT EXISTS \"{}\" (\n", table));
                let col_defs: Vec<String> = col_rows
                    .iter()
                    .map(|(name, dtype, nullable, default)| {
                        let mut def = format!("  \"{}\" {}", name, dtype);
                        if !nullable {
                            def.push_str(" NOT NULL");
                        }
                        if let Some(d) = default {
                            def.push_str(&format!(" DEFAULT {}", d));
                        }
                        def
                    })
                    .collect();
                sql_out.push_str(&col_defs.join(",\n"));
                sql_out.push_str("\n);\n\n");
            }

            if include_data {
                let col_names: Vec<String> =
                    col_rows.iter().map(|(n, _, _, _)| n.clone()).collect();
                let cols_str = col_names
                    .iter()
                    .map(|c| format!("\"{}\"", c))
                    .collect::<Vec<_>>()
                    .join(", ");

                for row in &rows {
                    if let Some(obj) = row.as_object() {
                        let values: Vec<String> = col_names
                            .iter()
                            .map(|col| {
                                match obj.get(col) {
                                    None | Some(Value::Null) => "NULL".to_string(),
                                    Some(Value::Bool(b)) => b.to_string().to_uppercase(),
                                    Some(Value::Number(n)) => n.to_string(),
                                    Some(Value::String(s)) => {
                                        // Escape single quotes
                                        format!("'{}'", s.replace('\'', "''"))
                                    }
                                    Some(other) => {
                                        format!("'{}'", other.to_string().replace('\'', "''"))
                                    }
                                }
                            })
                            .collect();
                        sql_out.push_str(&format!(
                            "INSERT INTO \"{}\" ({}) VALUES ({});\n",
                            table,
                            cols_str,
                            values.join(", ")
                        ));
                    }
                }
            }

            (
                [
                    (header::CONTENT_TYPE, "text/plain; charset=utf-8"),
                    (
                        header::CONTENT_DISPOSITION,
                        &format!("attachment; filename=\"{}.sql\"", table),
                    ),
                ],
                sql_out.into_bytes(),
            )
                .into_response()
        }
        _ => (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "format must be one of: csv, json, sql"})),
        )
            .into_response(),
    }
}

/// POST /api/v1/admin/db/import/preview
pub async fn import_preview(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    mut multipart: Multipart,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let mut file_bytes: Option<Vec<u8>> = None;
    let mut table: Option<String> = None;
    let mut format: Option<String> = None;

    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        match name.as_str() {
            "file" => {
                file_bytes = field.bytes().await.ok().map(|b| b.to_vec());
            }
            "table" => {
                table = field.text().await.ok();
            }
            "format" => {
                format = field.text().await.ok();
            }
            _ => {}
        }
    }

    let table = match table {
        Some(t) if !t.is_empty() => t,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'table' field"})),
            )
                .into_response();
        }
    };

    if !table.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid table name"})),
        )
            .into_response();
    }

    let file_bytes = match file_bytes {
        Some(b) if !b.is_empty() => b,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing or empty 'file' field"})),
            )
                .into_response();
        }
    };

    let fmt = format.as_deref().unwrap_or("csv");

    // Fetch table columns
    let table_columns: Vec<(String, String)> = sqlx::query_as(
        "SELECT column_name, data_type FROM information_schema.columns \
         WHERE table_name = $1 AND table_schema = 'public' ORDER BY ordinal_position",
    )
    .bind(&table)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let table_col_names: Vec<String> = table_columns.iter().map(|(n, _)| n.clone()).collect();

    // Parse file
    let (detected_columns, sample_rows, total_rows, validation_errors, warnings) = match fmt {
        "json" => parse_json_file(&file_bytes),
        _ => parse_csv_file(&file_bytes),
    };

    // Auto-map columns (case-insensitive)
    let mut column_mapping: HashMap<String, String> = HashMap::new();
    for dc in &detected_columns {
        let lower_dc = dc.to_lowercase();
        for tc in &table_col_names {
            if tc.to_lowercase() == lower_dc {
                column_mapping.insert(dc.clone(), tc.clone());
                break;
            }
        }
    }

    // Check for unmapped columns
    let mut extra_warnings = warnings;
    for dc in &detected_columns {
        if !column_mapping.contains_key(dc) {
            extra_warnings.push(format!("Column '{}' not found in table '{}'", dc, table));
        }
    }

    Json(json!({
        "total_rows": total_rows,
        "sample_rows": sample_rows,
        "detected_columns": detected_columns,
        "table_columns": table_col_names,
        "column_mapping": column_mapping,
        "validation_errors": validation_errors,
        "warnings": extra_warnings,
    }))
    .into_response()
}

/// POST /api/v1/admin/db/import/execute
pub async fn import_execute(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    mut multipart: Multipart,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let mut file_bytes: Option<Vec<u8>> = None;
    let mut table: Option<String> = None;
    let mut format: Option<String> = None;
    let mut mode: Option<String> = None;
    let mut column_mapping_str: Option<String> = None;
    let mut skip_errors = false;

    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        match name.as_str() {
            "file" => {
                file_bytes = field.bytes().await.ok().map(|b| b.to_vec());
            }
            "table" => {
                table = field.text().await.ok();
            }
            "format" => {
                format = field.text().await.ok();
            }
            "mode" => {
                mode = field.text().await.ok();
            }
            "column_mapping" => {
                column_mapping_str = field.text().await.ok();
            }
            "skip_errors" => {
                skip_errors = field
                    .text()
                    .await
                    .ok()
                    .map(|s| s == "true")
                    .unwrap_or(false);
            }
            _ => {}
        }
    }

    let table = match table {
        Some(t) if !t.is_empty() => t,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'table' field"})),
            )
                .into_response();
        }
    };

    if !table.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid table name"})),
        )
            .into_response();
    }

    let file_bytes = match file_bytes {
        Some(b) if !b.is_empty() => b,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing or empty 'file' field"})),
            )
                .into_response();
        }
    };

    let fmt = format.as_deref().unwrap_or("csv");
    let import_mode = mode.as_deref().unwrap_or("insert");

    // Parse column mapping
    let column_mapping: HashMap<String, String> = column_mapping_str
        .as_deref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or_default();

    // Parse all rows from file
    let (detected_columns, all_rows, _, _, _) = match fmt {
        "json" => parse_json_file(&file_bytes),
        _ => parse_csv_file(&file_bytes),
    };

    // Fetch table columns for validation
    let table_col_types: Vec<(String, String)> = sqlx::query_as(
        "SELECT column_name, data_type FROM information_schema.columns \
         WHERE table_name = $1 AND table_schema = 'public' ORDER BY ordinal_position",
    )
    .bind(&table)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let table_col_names: Vec<String> = table_col_types.iter().map(|(n, _)| n.clone()).collect();

    // Build effective mapping (file_col → table_col)
    let effective_mapping: HashMap<String, String> = if column_mapping.is_empty() {
        // Auto-map case-insensitively
        let mut m = HashMap::new();
        for dc in &detected_columns {
            let lower_dc = dc.to_lowercase();
            for tc in &table_col_names {
                if tc.to_lowercase() == lower_dc {
                    m.insert(dc.clone(), tc.clone());
                    break;
                }
            }
        }
        m
    } else {
        column_mapping
    };

    let started = Instant::now();
    let mut rows_imported: i64 = 0;
    let rows_updated: i64 = 0;
    let mut rows_skipped: i64 = 0;
    let mut errors: Vec<String> = Vec::new();

    for (row_idx, sample_row) in all_rows.iter().enumerate() {
        if let Some(obj) = sample_row.as_object() {
            // Build column/value pairs using mapping
            let mut cols: Vec<String> = Vec::new();
            let mut vals: Vec<String> = Vec::new();

            for (file_col, table_col) in &effective_mapping {
                if let Some(v) = obj.get(file_col) {
                    cols.push(format!("\"{}\"", table_col));
                    vals.push(match v {
                        Value::Null => "NULL".to_string(),
                        Value::Bool(b) => b.to_string().to_uppercase(),
                        Value::Number(n) => n.to_string(),
                        Value::String(s) => format!("'{}'", s.replace('\'', "''")),
                        other => format!("'{}'", other.to_string().replace('\'', "''")),
                    });
                }
            }

            if cols.is_empty() {
                rows_skipped += 1;
                continue;
            }

            let sql = match import_mode {
                "upsert" => {
                    // Build ON CONFLICT DO UPDATE — need PK columns
                    format!(
                        "INSERT INTO \"{}\" ({}) VALUES ({}) ON CONFLICT DO NOTHING",
                        table,
                        cols.join(", "),
                        vals.join(", ")
                    )
                }
                "replace" => {
                    // Delete + insert
                    format!(
                        "INSERT INTO \"{}\" ({}) VALUES ({})",
                        table,
                        cols.join(", "),
                        vals.join(", ")
                    )
                }
                _ => {
                    // insert
                    format!(
                        "INSERT INTO \"{}\" ({}) VALUES ({})",
                        table,
                        cols.join(", "),
                        vals.join(", ")
                    )
                }
            };

            match sqlx::query(&sql).execute(&state.pool).await {
                Ok(r) => {
                    if r.rows_affected() > 0 {
                        rows_imported += 1;
                    } else {
                        rows_skipped += 1;
                    }
                }
                Err(e) => {
                    let err_msg = format!("Row {}: {}", row_idx + 1, e);
                    tracing::warn!("import row {}: {e}", row_idx + 1);
                    if skip_errors {
                        errors.push(err_msg);
                        rows_skipped += 1;
                    } else {
                        errors.push(err_msg);
                        let elapsed_ms = started.elapsed().as_millis() as i64;
                        return Json(json!({
                            "success": false,
                            "rows_imported": rows_imported,
                            "rows_updated": rows_updated,
                            "rows_skipped": rows_skipped,
                            "errors": errors,
                            "execution_time_ms": elapsed_ms,
                        }))
                        .into_response();
                    }
                }
            }
        }
    }

    let elapsed_ms = started.elapsed().as_millis() as i64;

    Json(json!({
        "success": errors.is_empty(),
        "rows_imported": rows_imported,
        "rows_updated": rows_updated,
        "rows_skipped": rows_skipped,
        "errors": errors,
        "execution_time_ms": elapsed_ms,
    }))
    .into_response()
}

// ── Parse helpers ─────────────────────────────────────────────────────────────

type ParseResult = (
    Vec<String>, // detected_columns
    Vec<Value>,  // sample_rows (up to 5)
    usize,       // total_rows
    Vec<String>, // validation_errors
    Vec<String>, // warnings
);

fn parse_csv_file(bytes: &[u8]) -> ParseResult {
    let mut rdr = csv::Reader::from_reader(bytes);
    let headers: Vec<String> = rdr
        .headers()
        .map(|h| h.iter().map(|s| s.to_string()).collect())
        .unwrap_or_default();

    let mut all_rows: Vec<Value> = Vec::new();
    let mut errors: Vec<String> = Vec::new();

    for (i, result) in rdr.records().enumerate() {
        match result {
            Ok(record) => {
                let obj: serde_json::Map<String, Value> = headers
                    .iter()
                    .zip(record.iter())
                    .map(|(k, v)| (k.clone(), Value::String(v.to_string())))
                    .collect();
                all_rows.push(Value::Object(obj));
            }
            Err(e) => {
                errors.push(format!("Row {}: {}", i + 1, e));
            }
        }
    }

    let total = all_rows.len();
    let sample: Vec<Value> = all_rows.iter().take(5).cloned().collect();

    (headers, sample, total, errors, Vec::new())
}

fn parse_json_file(bytes: &[u8]) -> ParseResult {
    match serde_json::from_slice::<Value>(bytes) {
        Ok(Value::Array(arr)) => {
            let headers: Vec<String> = arr
                .first()
                .and_then(|v| v.as_object())
                .map(|obj| obj.keys().cloned().collect())
                .unwrap_or_default();
            let total = arr.len();
            let sample: Vec<Value> = arr.iter().take(5).cloned().collect();
            (headers, sample, total, Vec::new(), Vec::new())
        }
        Ok(Value::Object(obj)) => {
            // Single object wrapped
            let headers: Vec<String> = obj.keys().cloned().collect();
            let row = Value::Object(obj);
            (headers, vec![row], 1, Vec::new(), Vec::new())
        }
        Ok(_) => (
            Vec::new(),
            Vec::new(),
            0,
            vec!["JSON file must be an array of objects".to_string()],
            Vec::new(),
        ),
        Err(e) => (
            Vec::new(),
            Vec::new(),
            0,
            vec![format!("JSON parse error: {}", e)],
            Vec::new(),
        ),
    }
}
