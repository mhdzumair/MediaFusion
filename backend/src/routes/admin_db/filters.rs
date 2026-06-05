/// Filter/search WHERE-clause builder for dynamic table queries.
use std::collections::HashMap;

use serde::Deserialize;

#[derive(Deserialize, Clone)]
pub struct FilterCondition {
    pub column: String,
    pub operator: String,
    pub value: Option<String>,
}

/// Build a WHERE clause fragment for use with dynamic sqlx queries.
/// Returns (where_sql, bind_values) where bind_values are bound in order starting at start_idx.
/// Caller must bind values via .bind() in the returned order.
pub fn build_where(
    col_types: &HashMap<String, String>, // column_name -> data_type
    filters_json: Option<&str>,          // JSON array [{column,operator,value}]
    search: Option<&str>,
    // Legacy single-filter support
    filter_column: Option<&str>,
    filter_operator: Option<&str>,
    filter_value: Option<&str>,
    start_idx: i32, // first $n index (usually 1)
) -> (String, Vec<String>) {
    let mut clauses: Vec<String> = Vec::new();
    let mut values: Vec<String> = Vec::new();
    let mut idx = start_idx;

    // Parse filters from JSON array
    let mut filter_conditions: Vec<FilterCondition> = Vec::new();
    if let Some(json) = filters_json {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(json) {
            if let Some(arr) = v.as_array() {
                for f in arr {
                    if let Some(col) = f.get("column").and_then(|c| c.as_str()) {
                        filter_conditions.push(FilterCondition {
                            column: col.to_string(),
                            operator: f
                                .get("operator")
                                .and_then(|o| o.as_str())
                                .unwrap_or("equals")
                                .to_string(),
                            value: f
                                .get("value")
                                .and_then(|v| v.as_str())
                                .map(|s| s.to_string()),
                        });
                    }
                }
            }
        }
    }
    // Legacy fallback
    if filter_conditions.is_empty() {
        if let Some(fc) = filter_column {
            if col_types.contains_key(fc) {
                filter_conditions.push(FilterCondition {
                    column: fc.to_string(),
                    operator: filter_operator.unwrap_or("equals").to_string(),
                    value: filter_value.map(|s| s.to_string()),
                });
            }
        }
    }

    // Search across text-like columns
    if let Some(q) = search {
        if !q.is_empty() {
            let text_cols: Vec<String> = col_types
                .iter()
                .filter(|(_, t)| {
                    let t = t.to_lowercase();
                    t.contains("text") || t.contains("char") || t.contains("varchar") || t == "uuid"
                })
                .map(|(c, _)| format!("CAST({} AS TEXT) ILIKE ${}", quote_ident(c), idx))
                .collect();
            if !text_cols.is_empty() {
                clauses.push(format!("({})", text_cols.join(" OR ")));
                values.push(format!("%{}%", q));
                idx += 1;
            }
        }
    }

    // Apply filter conditions
    for fc in &filter_conditions {
        if !col_types.contains_key(&fc.column) {
            continue; // skip unknown columns
        }
        let col = quote_ident(&fc.column);
        let col_type = col_types
            .get(&fc.column)
            .map(|s| s.to_lowercase())
            .unwrap_or_default();
        let is_array = col_type.contains("array") || col_type.starts_with('_');

        let (clause_opt, bound_val) = match fc.operator.as_str() {
            "is_null" => (Some(format!("{} IS NULL", col)), None),
            "is_not_null" => (Some(format!("{} IS NOT NULL", col)), None),
            "json_is_null" => (
                Some(format!("({} IS NULL OR {}::text = 'null')", col, col)),
                None,
            ),
            "json_is_not_null" => (
                Some(format!("({} IS NOT NULL AND {}::text != 'null')", col, col)),
                None,
            ),
            "array_empty" if is_array => (
                Some(format!("(COALESCE(array_length({}, 1), 0) = 0)", col)),
                None,
            ),
            "array_not_empty" if is_array => {
                (Some(format!("(array_length({}, 1) > 0)", col)), None)
            }
            op => {
                if let Some(val) = &fc.value {
                    match op {
                        "equals" => (
                            Some(format!("CAST({} AS TEXT) = ${}", col, idx)),
                            Some(val.clone()),
                        ),
                        "not_equals" => (
                            Some(format!("CAST({} AS TEXT) != ${}", col, idx)),
                            Some(val.clone()),
                        ),
                        "contains" => (
                            Some(format!("CAST({} AS TEXT) ILIKE ${}", col, idx)),
                            Some(format!("%{}%", val)),
                        ),
                        "starts_with" => (
                            Some(format!("CAST({} AS TEXT) ILIKE ${}", col, idx)),
                            Some(format!("{}%", val)),
                        ),
                        "ends_with" => (
                            Some(format!("CAST({} AS TEXT) ILIKE ${}", col, idx)),
                            Some(format!("%{}", val)),
                        ),
                        "gt" => (
                            Some(format!(
                                "{} > CAST(${} AS {})",
                                col,
                                idx,
                                pg_cast_type(&col_type)
                            )),
                            Some(val.clone()),
                        ),
                        "gte" => (
                            Some(format!(
                                "{} >= CAST(${} AS {})",
                                col,
                                idx,
                                pg_cast_type(&col_type)
                            )),
                            Some(val.clone()),
                        ),
                        "lt" => (
                            Some(format!(
                                "{} < CAST(${} AS {})",
                                col,
                                idx,
                                pg_cast_type(&col_type)
                            )),
                            Some(val.clone()),
                        ),
                        "lte" => (
                            Some(format!(
                                "{} <= CAST(${} AS {})",
                                col,
                                idx,
                                pg_cast_type(&col_type)
                            )),
                            Some(val.clone()),
                        ),
                        "array_contains" if is_array => (
                            Some(format!("${} = ANY({}::text[])", idx, col)),
                            Some(val.clone()),
                        ),
                        "array_not_contains" if is_array => (
                            Some(format!("NOT (${} = ANY({}::text[]))", idx, col)),
                            Some(val.clone()),
                        ),
                        "array_length_eq" if is_array => (
                            Some(format!(
                                "COALESCE(array_length({}, 1), 0) = CAST(${} AS INT)",
                                col, idx
                            )),
                            Some(val.clone()),
                        ),
                        "array_length_gt" if is_array => (
                            Some(format!(
                                "COALESCE(array_length({}, 1), 0) > CAST(${} AS INT)",
                                col, idx
                            )),
                            Some(val.clone()),
                        ),
                        _ => (None, None),
                    }
                } else {
                    (None, None)
                }
            }
        };

        if let Some(clause) = clause_opt {
            clauses.push(clause);
            if let Some(v) = bound_val {
                values.push(v);
                idx += 1;
            }
        }
    }

    let where_sql = if clauses.is_empty() {
        String::new()
    } else {
        format!(" WHERE {}", clauses.join(" AND "))
    };

    (where_sql, values)
}

/// Quote a PostgreSQL identifier safely (wraps in double-quotes, escapes internal quotes).
pub fn quote_ident(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}

fn pg_cast_type(col_type: &str) -> &str {
    if col_type.contains("int") {
        "BIGINT"
    } else if col_type.contains("float")
        || col_type.contains("numeric")
        || col_type.contains("double")
    {
        "DOUBLE PRECISION"
    } else if col_type.contains("bool") {
        "BOOLEAN"
    } else if col_type.contains("timestamp") {
        "TIMESTAMP WITH TIME ZONE"
    } else if col_type.contains("date") {
        "DATE"
    } else {
        "TEXT"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_types(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    #[test]
    fn test_empty_no_clause() {
        let types = make_types(&[("id", "integer"), ("name", "text")]);
        let (sql, vals) = build_where(&types, None, None, None, None, None, 1);
        assert!(sql.is_empty());
        assert!(vals.is_empty());
    }

    #[test]
    fn test_equals_operator() {
        let types = make_types(&[("name", "text")]);
        let filters = r#"[{"column":"name","operator":"equals","value":"foo"}]"#;
        let (sql, vals) = build_where(&types, Some(filters), None, None, None, None, 1);
        assert!(sql.contains("CAST(\"name\" AS TEXT) = $1"));
        assert_eq!(vals, vec!["foo"]);
    }

    #[test]
    fn test_is_null() {
        let types = make_types(&[("name", "text")]);
        let filters = r#"[{"column":"name","operator":"is_null"}]"#;
        let (sql, vals) = build_where(&types, Some(filters), None, None, None, None, 1);
        assert!(sql.contains("IS NULL"));
        assert!(vals.is_empty());
    }

    #[test]
    fn test_search() {
        let types = make_types(&[("name", "text"), ("id", "integer")]);
        let (sql, vals) = build_where(&types, None, Some("hello"), None, None, None, 1);
        assert!(sql.contains("ILIKE $1"));
        assert_eq!(vals[0], "%hello%");
    }

    #[test]
    fn test_unknown_column_skipped() {
        let types = make_types(&[("name", "text")]);
        let filters = r#"[{"column":"nonexistent","operator":"equals","value":"x"}]"#;
        let (sql, vals) = build_where(&types, Some(filters), None, None, None, None, 1);
        assert!(sql.is_empty());
        assert!(vals.is_empty());
    }
}
