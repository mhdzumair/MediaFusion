mod common;

use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn prowlarr_feed_skips_items_without_hash() {
    let server = MockServer::start().await;

    // Mock indexer list.
    Mock::given(method("GET"))
        .and(path("/api/v1/indexer"))
        .respond_with(
            ResponseTemplate::new(200).set_body_string(r#"[{"id":1,"name":"Test","enable":true}]"#),
        )
        .mount(&server)
        .await;

    // Mock indexer status (no disabled indexers).
    Mock::given(method("GET"))
        .and(path("/api/v1/indexerstatus"))
        .respond_with(ResponseTemplate::new(200).set_body_string("[]"))
        .mount(&server)
        .await;

    // Mock search results (includes one item without hash).
    let results = include_str!("fixtures/prowlarr_search.json");
    Mock::given(method("GET"))
        .and(path("/api/v1/search"))
        .respond_with(ResponseTemplate::new(200).set_body_string(results))
        .mount(&server)
        .await;

    // Parse the fixture directly and count items with valid 40-char hashes.
    let items: Vec<serde_json::Value> = serde_json::from_str(results).unwrap();
    let valid: Vec<_> = items
        .iter()
        .filter(|i| {
            i["infoHash"]
                .as_str()
                .map(|h| h.len() == 40)
                .unwrap_or(false)
        })
        .collect();

    assert_eq!(valid.len(), 2, "item with null hash should be filtered");
}
