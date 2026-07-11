/// Torznab emulation route.
///
/// Exposes MediaFusion's torrent database as a Torznab feed for Sonarr/Radarr/Prowlarr.
/// Mirrors Python `api/routers/torznab/torznab.py`.
use std::sync::Arc;

use axum::{
    extract::{Query, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use chrono::{DateTime, Utc};
use serde::Deserialize;

use crate::{db::torznab as db, state::AppState};

// ─── Category tables ──────────────────────────────────────────────────────────

const MOVIE_CATS: &[(u32, &str)] = &[
    (2000, "Movies"),
    (2010, "Movies/Foreign"),
    (2020, "Movies/Other"),
    (2030, "Movies/SD"),
    (2040, "Movies/HD"),
    (2045, "Movies/UHD"),
    (2050, "Movies/BluRay"),
    (2060, "Movies/3D"),
];

const TV_CATS: &[(u32, &str)] = &[
    (5000, "TV"),
    (5010, "TV/Foreign"),
    (5020, "TV/SD"),
    (5030, "TV/HD"),
    (5040, "TV/Other"),
    (5045, "TV/UHD"),
    (5060, "TV/Sport"),
    (5070, "TV/Anime"),
];

// ─── Query params ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct TorznabParams {
    t: String,
    apikey: Option<String>,
    q: Option<String>,
    imdbid: Option<String>,
    tmdbid: Option<String>,
    season: Option<i32>,
    ep: Option<i32>,
    #[serde(default = "default_limit")]
    limit: i64,
    #[serde(default)]
    offset: i64,
}

fn default_limit() -> i64 {
    50
}

// ─── Handler ──────────────────────────────────────────────────────────────────

pub async fn handler(
    Query(params): Query<TorznabParams>,
    State(state): State<Arc<AppState>>,
) -> Response {
    let limit = params.limit.clamp(1, 100);

    // caps — no auth required
    if params.t == "caps" {
        return xml_response(build_caps(&state));
    }

    if !state.config.enable_torznab_api {
        return xml_error(503, "Torznab API is disabled on this server");
    }

    // Authentication check for private instances
    if !validate_apikey(
        params.apikey.as_deref(),
        state.config.api_password.as_deref(),
        state.config.is_public_instance,
    ) {
        return xml_error(100, "Invalid API key");
    }

    match resolve_search_action(&params) {
        SearchAction::ValidationSamples => {
            let samples = validation_samples(limit);
            xml_response(build_rss(
                &samples,
                &state.config.addon_name,
                &state.config.host_url,
            ))
        }
        SearchAction::MissingParameters => xml_error(
            200,
            "Missing search parameters (q, imdbid, or tmdbid required)",
        ),
        SearchAction::Search(query) => {
            let media_type = media_type_for_request(&params.t);
            let results = match query {
                TorznabQuery::Imdb(id) => {
                    db::search_by_imdb(
                        &state.pool_ro,
                        &id,
                        media_type,
                        params.season,
                        params.ep,
                        limit,
                    )
                    .await
                }
                TorznabQuery::Tmdb(id) => {
                    db::search_by_tmdb(
                        &state.pool_ro,
                        &id,
                        media_type,
                        params.season,
                        params.ep,
                        limit,
                    )
                    .await
                }
                TorznabQuery::Title(q) => {
                    db::search_by_title(&state.pool_ro, &q, media_type, None, limit).await
                }
            };

            let offset = params.offset as usize;
            let results = if offset > 0 {
                results.into_iter().skip(offset).collect()
            } else {
                results
            };

            xml_response(build_rss(
                &results,
                &state.config.addon_name,
                &state.config.host_url,
            ))
        }
    }
}

#[derive(Debug)]
enum SearchAction {
    ValidationSamples,
    MissingParameters,
    Search(TorznabQuery),
}

#[derive(Debug)]
enum TorznabQuery {
    Imdb(String),
    Tmdb(String),
    Title(String),
}

fn media_type_for_request(request_type: &str) -> Option<&str> {
    match request_type.to_ascii_lowercase().as_str() {
        "movie" => Some("movie"),
        "tvsearch" => Some("series"),
        _ => None,
    }
}

fn is_blank_query(query: Option<&str>) -> bool {
    query.is_none_or(|q| q.trim().is_empty())
}

fn resolve_search_action(params: &TorznabParams) -> SearchAction {
    if let Some(imdb) = params.imdbid.as_deref().filter(|id| !id.trim().is_empty()) {
        let id = if imdb.starts_with("tt") {
            imdb.to_string()
        } else {
            format!("tt{imdb}")
        };
        return SearchAction::Search(TorznabQuery::Imdb(id));
    }

    if let Some(tmdb) = params.tmdbid.as_deref().filter(|id| !id.trim().is_empty()) {
        return SearchAction::Search(TorznabQuery::Tmdb(tmdb.to_string()));
    }

    if is_blank_query(params.q.as_deref()) {
        if params.t.eq_ignore_ascii_case("search") {
            return SearchAction::ValidationSamples;
        }
        return SearchAction::MissingParameters;
    }

    SearchAction::Search(TorznabQuery::Title(
        params.q.as_deref().unwrap().trim().to_string(),
    ))
}

// ─── XML builders ─────────────────────────────────────────────────────────────

fn build_caps(state: &AppState) -> String {
    let c = &state.config;
    let email_attr = c
        .contact_email
        .as_deref()
        .map(|e| format!(r#" email="{e}""#))
        .unwrap_or_default();

    let mut cats = String::new();
    for (id, name) in MOVIE_CATS {
        cats.push_str(&format!(r#"<category id="{id}" name="{name}"/>"#));
    }
    for (id, name) in TV_CATS {
        cats.push_str(&format!(r#"<category id="{id}" name="{name}"/>"#));
    }

    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server version="{ver}" title="{name}"{email} url="{url}"/>
  <limits max="100" default="50"/>
  <registration available="yes" open="yes"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep,imdbid,tmdbid"/>
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>
  </searching>
  <categories>{cats}</categories>
</caps>"#,
        ver = xml_escape(&c.addon_version),
        name = xml_escape(&c.addon_name),
        email = email_attr,
        url = xml_escape(&c.host_url),
        cats = cats,
    )
}

fn validation_samples(limit: i64) -> Vec<db::TorznabRow> {
    let samples = vec![
        db::TorznabRow {
            info_hash: "1111111111111111111111111111111111111111".into(),
            name: "MediaFusion Validation Sample Movie 1080p".into(),
            total_size: Some(2_147_483_648),
            seeders: Some(250),
            leechers: Some(10),
            uploaded_at: Some(validation_uploaded_at()),
            resolution: Some("1080p".into()),
            media_type: "movie".into(),
            imdb_id: Some("tt0000001".into()),
            tmdb_id: Some("550".into()),
            source: Some("validation".into()),
            trackers: vec![],
        },
        db::TorznabRow {
            info_hash: "2222222222222222222222222222222222222222".into(),
            name: "MediaFusion Validation Sample Series S01E01 1080p".into(),
            total_size: Some(1_073_741_824),
            seeders: Some(180),
            leechers: Some(8),
            uploaded_at: Some(validation_uploaded_at()),
            resolution: Some("1080p".into()),
            media_type: "series".into(),
            imdb_id: Some("tt0000002".into()),
            tmdb_id: Some("1399".into()),
            source: Some("validation".into()),
            trackers: vec![],
        },
    ];

    let limit = usize::try_from(limit.clamp(1, 100)).unwrap_or(1);
    let count = limit.min(samples.len());
    samples.into_iter().take(count).collect()
}

fn validation_uploaded_at() -> DateTime<Utc> {
    DateTime::parse_from_rfc3339("2024-01-01T00:00:00Z")
        .expect("validation timestamp must be a valid RFC 3339 datetime")
        .with_timezone(&Utc)
}

fn build_rss(rows: &[db::TorznabRow], title: &str, host_url: &str) -> String {
    let mut items = String::new();

    for r in rows {
        let size = r.total_size.unwrap_or(0);
        let magnet = build_magnet(&r.info_hash, &r.name, &r.trackers);
        let category = category_for(&r.media_type, r.resolution.as_deref());

        let pub_date = r
            .uploaded_at
            .map(|dt| {
                // RFC 2822 date string: "Mon, 01 Jan 2024 00:00:00 +0000"
                format!("{}", dt.format("%a, %d %b %Y %H:%M:%S +0000"))
            })
            .unwrap_or_default();

        let pub_date_elem = if pub_date.is_empty() {
            String::new()
        } else {
            format!("<pubDate>{pub_date}</pubDate>")
        };

        let mut attrs = format!(
            r#"<torznab:attr name="category" value="{category}"/>
      <torznab:attr name="size" value="{size}"/>
      <torznab:attr name="infohash" value="{hash}"/>
      <torznab:attr name="magneturl" value="{magnet}"/>"#,
            category = category,
            size = size,
            hash = xml_escape(&r.info_hash),
            magnet = xml_escape(&magnet),
        );

        if let Some(s) = r.seeders {
            attrs.push_str(&format!(
                r#"
      <torznab:attr name="seeders" value="{s}"/>"#
            ));
        }
        if let Some(l) = r.leechers {
            attrs.push_str(&format!(
                r#"
      <torznab:attr name="peers" value="{l}"/>"#
            ));
        }
        if let Some(ref imdb) = r.imdb_id {
            let numeric = imdb.trim_start_matches("tt");
            attrs.push_str(&format!(
                r#"
      <torznab:attr name="imdb" value="{numeric}"/>"#
            ));
        }
        if let Some(ref tmdb) = r.tmdb_id {
            attrs.push_str(&format!(
                r#"
      <torznab:attr name="tmdbid" value="{tmdb}"/>"#
            ));
        }

        items.push_str(&format!(
            r#"
    <item>
      <title>{name}</title>
      <guid>{hash}</guid>
      <size>{size}</size>
      {pub_date}
      <link>{magnet}</link>
      <enclosure url="{magnet}" length="{size}" type="application/x-bittorrent;x-scheme-handler/magnet"/>
      <category>{category}</category>
      {attrs}
    </item>"#,
            name = xml_escape(&r.name),
            hash = xml_escape(&r.info_hash),
            size = size,
            pub_date = pub_date_elem,
            magnet = xml_escape(&magnet),
            category = category,
            attrs = attrs,
        ));
    }

    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>{title}</title>
    <description>Torznab feed from MediaFusion</description>
    <link>{url}</link>
    {items}
  </channel>
</rss>"#,
        title = xml_escape(title),
        url = xml_escape(host_url),
        items = items,
    )
}

// ─── Utilities ────────────────────────────────────────────────────────────────

fn category_for(media_type: &str, resolution: Option<&str>) -> u32 {
    let res = resolution.unwrap_or("").to_lowercase();
    if media_type == "movie" {
        if res.contains("2160") || res.contains("4k") {
            2045
        } else if res.contains("1080") || res.contains("720") {
            2040
        } else if res.contains("480") {
            2030
        } else {
            2000
        }
    } else {
        if res.contains("2160") || res.contains("4k") {
            5045
        } else if res.contains("1080") || res.contains("720") {
            5030
        } else if res.contains("480") {
            5020
        } else {
            5000
        }
    }
}

fn build_magnet(info_hash: &str, name: &str, trackers: &[String]) -> String {
    let encoded_name = url_encode(name);
    let mut m = format!("magnet:?xt=urn:btih:{info_hash}&dn={encoded_name}");
    for tr in trackers.iter().take(10) {
        m.push_str(&format!("&tr={}", url_encode(tr)));
    }
    m
}

fn url_encode(s: &str) -> String {
    s.bytes()
        .flat_map(|b| {
            if b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'.' || b == b'~' {
                vec![b as char]
            } else {
                format!("%{b:02X}").chars().collect::<Vec<_>>()
            }
        })
        .collect()
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn xml_response(xml: String) -> Response {
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "application/xml; charset=utf-8")],
        xml,
    )
        .into_response()
}

fn xml_error(code: u32, description: &str) -> Response {
    let xml = format!(
        r#"<?xml version="1.0" encoding="UTF-8"?><error code="{code}" description="{desc}"/>"#,
        code = code,
        desc = xml_escape(description),
    );
    xml_response(xml)
}

fn validate_apikey(
    apikey: Option<&str>,
    required_password: Option<&str>,
    is_public_instance: bool,
) -> bool {
    let require_password =
        required_password.is_some_and(|pwd| !pwd.is_empty()) && !is_public_instance;
    if !require_password {
        return true;
    }

    let Some(pwd) = required_password else {
        return true;
    };

    apikey
        .map(|k| k.split(':').next().unwrap_or(k) == pwd)
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_params(t: &str, q: Option<&str>) -> TorznabParams {
        TorznabParams {
            t: t.to_string(),
            apikey: None,
            q: q.map(str::to_string),
            imdbid: None,
            tmdbid: None,
            season: None,
            ep: None,
            limit: 50,
            offset: 0,
        }
    }

    #[test]
    fn validation_samples_include_valid_pub_dates() {
        let rss = build_rss(
            &validation_samples(50),
            "MediaFusion",
            "http://127.0.0.1:8001",
        );

        assert_eq!(rss.matches("<item>").count(), 2);
        assert_eq!(
            rss.matches("<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>")
                .count(),
            2
        );
    }

    #[test]
    fn validation_samples_respect_limit() {
        assert_eq!(validation_samples(1).len(), 1);
        assert_eq!(validation_samples(50).len(), 2);
    }

    #[test]
    fn blank_search_term_returns_validation_samples() {
        for q in [None, Some(""), Some("   ")] {
            let action = resolve_search_action(&sample_params("search", q));
            assert!(
                matches!(action, SearchAction::ValidationSamples),
                "expected validation samples for q={q:?}"
            );
        }
    }

    #[test]
    fn missing_query_on_non_search_requests_is_an_error() {
        let action = resolve_search_action(&sample_params("movie", None));
        assert!(matches!(action, SearchAction::MissingParameters));
    }

    #[test]
    fn title_search_trims_query() {
        let action = resolve_search_action(&sample_params("search", Some("  inception  ")));
        match action {
            SearchAction::Search(TorznabQuery::Title(q)) => assert_eq!(q, "inception"),
            other => panic!("expected title search, got {other:?}"),
        }
    }

    #[test]
    fn public_instance_skips_apikey_requirement() {
        assert!(validate_apikey(None, Some("secret"), true));
        assert!(!validate_apikey(None, Some("secret"), false));
        assert!(validate_apikey(Some("secret"), Some("secret"), false));
    }
}
