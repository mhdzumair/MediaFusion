//! Shared SQL filter helpers for metadata/stream suggestion list endpoints.

/// Build uploader/reviewer filter clauses and their bind values.
pub fn build_suggestion_user_filters(
    next_idx: &mut i32,
    uploader_query: Option<&str>,
    reviewer_query: Option<&str>,
) -> (String, Vec<String>) {
    let mut clause = String::new();
    let mut binds = Vec::new();

    if let Some(q) = uploader_query.map(str::trim).filter(|s| !s.is_empty()) {
        let pattern = format!("%{}%", q);
        if let Ok(uid) = q.parse::<i32>() {
            clause.push_str(&format!(
                " AND (user_id = ${} OR user_id IN (SELECT id FROM users WHERE username ILIKE ${}))",
                *next_idx,
                *next_idx + 1
            ));
            binds.push(uid.to_string());
            binds.push(pattern);
            *next_idx += 2;
        } else {
            clause.push_str(&format!(
                " AND user_id IN (SELECT id FROM users WHERE username ILIKE ${})",
                *next_idx
            ));
            binds.push(pattern);
            *next_idx += 1;
        }
    }

    if let Some(q) = reviewer_query.map(str::trim).filter(|s| !s.is_empty()) {
        if q.eq_ignore_ascii_case("auto") {
            clause.push_str(" AND reviewed_by = 'auto'");
        } else {
            let pattern = format!("%{}%", q);
            if let Ok(uid) = q.parse::<i32>() {
                clause.push_str(&format!(
                    " AND (reviewed_by = ${} OR reviewed_by IN (SELECT id::text FROM users WHERE username ILIKE ${}))",
                    *next_idx,
                    *next_idx + 1
                ));
                binds.push(uid.to_string());
                binds.push(pattern);
                *next_idx += 2;
            } else {
                clause.push_str(&format!(
                    " AND reviewed_by IN (SELECT id::text FROM users WHERE username ILIKE ${})",
                    *next_idx
                ));
                binds.push(pattern);
                *next_idx += 1;
            }
        }
    }

    (clause, binds)
}

pub const PENDING_FIRST_ORDER: &str =
    " ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, created_at DESC";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uploader_filter_builds_username_pattern() {
        let mut next_idx = 1;
        let (clause, binds) = build_suggestion_user_filters(&mut next_idx, Some("mhdzumair"), None);
        assert!(clause.contains("username ILIKE $1"));
        assert_eq!(binds, vec!["%mhdzumair%"]);
        assert_eq!(next_idx, 2);
    }

    #[test]
    fn reviewer_auto_filter_has_no_extra_binds() {
        let mut next_idx = 1;
        let (clause, binds) = build_suggestion_user_filters(&mut next_idx, None, Some("auto"));
        assert!(clause.contains("reviewed_by = 'auto'"));
        assert!(binds.is_empty());
        assert_eq!(next_idx, 1);
    }
}
