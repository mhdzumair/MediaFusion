//! Title-based search query templates for indexer scrapers (Prowlarr / Jackett).
//! Ported from Python `IndexerBaseScraper.MOVIE/SERIES_SEARCH_QUERY_TEMPLATES`.

pub fn movie_title_queries(title: &str, year: Option<i32>) -> Vec<String> {
    let year = year.unwrap_or(0);
    vec![
        format!("{title} ({year})"),
        format!("{title} {year}"),
        title.to_string(),
    ]
}

pub fn series_title_queries(title: &str, season: i32, episode: i32) -> Vec<String> {
    vec![
        format!("{title} S{season:02}E{episode:02}"),
        format!("{title} Season {season} Episode {episode}"),
        format!("{title} {season}x{episode}"),
        format!("{title} S{season:02}"),
        title.to_string(),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn movie_queries_include_year_variants() {
        let queries = movie_title_queries("Inception", Some(2010));
        assert_eq!(queries[0], "Inception (2010)");
        assert_eq!(queries[1], "Inception 2010");
        assert_eq!(queries[2], "Inception");
    }

    #[test]
    fn series_queries_cover_common_formats() {
        let queries = series_title_queries("Breaking Bad", 1, 1);
        assert!(queries.contains(&"Breaking Bad S01E01".to_string()));
        assert!(queries.contains(&"Breaking Bad 1x1".to_string()));
    }
}
