//! Shared Chrome browser fingerprint for all outbound HTTP.
//!
//! MediaFusion must not advertise its own bot User-Agent — sites like overtakefans
//! reject it with HTTP 415. Every shared `reqwest` client gets these defaults.

use reqwest::RequestBuilder;
use reqwest::header::{ACCEPT, ACCEPT_LANGUAGE, HeaderMap, HeaderName, HeaderValue, USER_AGENT};

/// Chrome major version baked into the static fingerprint.
pub const CHROME_MAJOR: &str = "131";

pub const CHROME_USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

pub const ACCEPT_HTML: &str = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7";

pub const ACCEPT_RSS: &str = "application/rss+xml, application/xml, text/xml, */*";

pub const ACCEPT_JSON: &str = "application/json";

const ACCEPT_LANG: &str = "en-US,en;q=0.9";

fn sec_ch_ua_value() -> HeaderValue {
    HeaderValue::from_str(&format!(
        r#""Google Chrome";v="{CHROME_MAJOR}", "Chromium";v="{CHROME_MAJOR}", "Not_A Brand";v="24""#
    ))
    .expect("sec-ch-ua header value")
}

/// Default headers applied to every shared HTTP client at build time.
pub fn default_client_headers() -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(USER_AGENT, HeaderValue::from_static(CHROME_USER_AGENT));
    // This client also serves APIs and binary downloads. HTML negotiation
    // belongs on document requests, otherwise APIs such as MDBList serve docs.
    headers.insert(ACCEPT, HeaderValue::from_static("*/*"));
    headers.insert(ACCEPT_LANGUAGE, HeaderValue::from_static(ACCEPT_LANG));
    headers.insert(
        HeaderName::from_static("upgrade-insecure-requests"),
        HeaderValue::from_static("1"),
    );
    headers.insert(HeaderName::from_static("sec-ch-ua"), sec_ch_ua_value());
    headers.insert(
        HeaderName::from_static("sec-ch-ua-mobile"),
        HeaderValue::from_static("?0"),
    );
    headers.insert(
        HeaderName::from_static("sec-ch-ua-platform"),
        HeaderValue::from_static(r#""Windows""#),
    );
    headers
}

/// Full top-level navigation headers (Reddit, strict bot checks).
pub fn document_navigation_headers() -> HeaderMap {
    let mut headers = default_client_headers();
    headers.insert(ACCEPT, HeaderValue::from_static(ACCEPT_HTML));
    headers.insert(
        HeaderName::from_static("cache-control"),
        HeaderValue::from_static("max-age=0"),
    );
    headers.insert(
        HeaderName::from_static("sec-fetch-dest"),
        HeaderValue::from_static("document"),
    );
    headers.insert(
        HeaderName::from_static("sec-fetch-mode"),
        HeaderValue::from_static("navigate"),
    );
    headers.insert(
        HeaderName::from_static("sec-fetch-site"),
        HeaderValue::from_static("none"),
    );
    headers.insert(
        HeaderName::from_static("sec-fetch-user"),
        HeaderValue::from_static("?1"),
    );
    headers
}

/// Apply RSS/Atom Accept headers on top of the client defaults.
pub fn apply_rss_request(req: RequestBuilder) -> RequestBuilder {
    req.header(ACCEPT, ACCEPT_RSS)
}

/// Explicit JSON negotiation, including when the caller supplies a custom client.
pub fn apply_json_request(req: RequestBuilder) -> RequestBuilder {
    req.header(ACCEPT, ACCEPT_JSON)
}

pub fn api_client_headers() -> HeaderMap {
    let mut headers = default_client_headers();
    headers.insert(ACCEPT, HeaderValue::from_static(ACCEPT_JSON));
    headers
}

/// Apply full document-navigation headers (overrides client defaults for one request).
pub fn apply_document_request(req: RequestBuilder) -> RequestBuilder {
    req.headers(document_navigation_headers())
}

#[cfg(test)]
mod tests {
    use super::*;
    use wiremock::{Mock, MockServer, ResponseTemplate, matchers::method};

    #[tokio::test]
    async fn content_negotiation_matches_the_request_type() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({"ok":true})))
            .expect(5)
            .mount(&server)
            .await;
        let http = crate::util::http::build(None, 30);
        let debrid = crate::util::http::build_debrid(None, 30);
        for request in [
            http.get(format!("{}/default", server.uri())),
            apply_json_request(http.get(format!("{}/api", server.uri()))),
            debrid.get(format!("{}/debrid", server.uri())),
            apply_document_request(http.get(format!("{}/document", server.uri()))),
            apply_rss_request(http.get(format!("{}/rss", server.uri()))),
        ] {
            let body: serde_json::Value = request.send().await.unwrap().json().await.unwrap();
            assert_eq!(body["ok"], true);
        }
        let requests = server.received_requests().await.unwrap();
        for (request, expected) in
            requests
                .iter()
                .zip(["*/*", ACCEPT_JSON, ACCEPT_JSON, ACCEPT_HTML, ACCEPT_RSS])
        {
            assert_eq!(
                request.headers.get(ACCEPT).unwrap().to_str().unwrap(),
                expected
            );
        }
    }
}
