use serde_json::Value;

/// Default listing pages per scheduled run (cron payload is `{}`).
pub const DEFAULT_LISTING_PAGES: u32 = 1;
pub const MAX_LISTING_PAGES: u32 = 100;

/// When `true`, scrape every listing page until pagination ends (ext.to legacy behaviour).
pub fn parse_scrape_all(args: &Value) -> bool {
    args.get("scrape_all")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

/// Parse `pages` / `total_pages` and `start_page` from a spider job payload.
pub fn parse_listing_page_args(args: &Value) -> (u32, u32) {
    let pages = args
        .get("pages")
        .or_else(|| args.get("total_pages"))
        .and_then(|v| v.as_u64())
        .unwrap_or(DEFAULT_LISTING_PAGES as u64)
        .clamp(1, MAX_LISTING_PAGES as u64) as u32;
    let start_page = args
        .get("start_page")
        .and_then(|v| v.as_u64())
        .unwrap_or(1)
        .max(1) as u32;
    (pages, start_page)
}

/// Effective page cap: unlimited when `scrape_all` is set, otherwise `pages`.
pub fn effective_page_limit(args: &Value) -> u32 {
    if parse_scrape_all(args) {
        u32::MAX
    } else {
        parse_listing_page_args(args).0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn defaults_to_one_page() {
        assert_eq!(parse_listing_page_args(&json!({})), (1, 1));
    }

    #[test]
    fn scrape_all_removes_cap() {
        assert_eq!(effective_page_limit(&json!({"scrape_all": true})), u32::MAX);
        assert_eq!(effective_page_limit(&json!({"pages": 5})), 5);
    }
}
