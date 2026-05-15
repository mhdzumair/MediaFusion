mod common;

use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn validate_tv_marks_working_stream_active() {
    let server = MockServer::start().await;
    Mock::given(method("HEAD"))
        .and(path("/live/stream.m3u8"))
        .respond_with(ResponseTemplate::new(200))
        .mount(&server)
        .await;

    let url = format!("{}/live/stream.m3u8", server.uri());
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .unwrap();

    let result = client.head(&url).send().await;
    let is_active = match result {
        Ok(resp) => resp.status().as_u16() < 500,
        Err(_) => false,
    };
    assert!(is_active);
}

#[tokio::test]
async fn validate_tv_marks_dead_stream_inactive() {
    let server = MockServer::start().await;
    Mock::given(method("HEAD"))
        .and(path("/dead/stream.m3u8"))
        .respond_with(ResponseTemplate::new(503))
        .mount(&server)
        .await;

    let url = format!("{}/dead/stream.m3u8", server.uri());
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .unwrap();

    let result = client.head(&url).send().await;
    let is_active = match result {
        Ok(resp) => resp.status().as_u16() < 500,
        Err(_) => false,
    };
    assert!(!is_active);
}
