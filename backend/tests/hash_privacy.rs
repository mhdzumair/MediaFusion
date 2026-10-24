//! Requires disposable TEST_DATABASE_URL and TEST_REDIS_URL services.
mod common;
use axum::{
    Json, Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
    routing::get,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use hmac::{Hmac, KeyInit, Mac};
use mediafusion_api::{
    config::AppConfig,
    db::{
        self, MediaId, MediaType, StoreStreamOpts, StreamStoreBase, TorrentStoreInput, TorrentType,
        UserRole,
    },
    routes::{content::catalog_browse, playback},
    state::AppState,
    torrent_privacy_middleware::torrent_privacy_middleware,
};
use serde_json::{Value, json};
use sha2::Sha256;
use std::sync::Arc;
use tower::ServiceExt;

const HASH: &str = "1f1914a9d7a2163f08c540bae788806bfad2c732";
const SECRET: &str = "privacy-regression-only-secret-key";

fn token(user: i32, role: &str) -> String {
    let payload = URL_SAFE_NO_PAD.encode(json!({"sub":user.to_string(),"role":role,"type":"access","exp":chrono::Utc::now().timestamp()+3600}).to_string());
    let mut mac = Hmac::<Sha256>::new_from_slice(SECRET.as_bytes()).unwrap();
    mac.update(payload.as_bytes());
    let signature: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    format!("{payload}.{signature}")
}

fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/api/v1/stream/{catalog_type}/{video_id}", get(catalog_browse::get_resource_streams))
        .route("/api/v1/catalog/{catalog_type}/{media_id}/streams", get(catalog_browse::get_media_streams))
        .route("/secondary", get(||async { Json(json!({"info_hash":HASH,"nested":{"description":format!("file_{HASH}.mkv"),"infoHash":HASH,"torrent_file":"base64"}})) }))
        .route("/torznab",get(|| async { "magnet XML" }))
        .route("/streaming_provider/{secret_str}/playback/{provider_name}/{info_hash}",get(playback::handler_base))
        .layer(axum::middleware::from_fn_with_state(state.clone(), torrent_privacy_middleware))
        .with_state(state)
}

async fn request(router: &Router, path: &str, token: Option<&str>) -> (StatusCode, Value) {
    let mut request = Request::builder().uri(path);
    if let Some(token) = token {
        request = request.header("authorization", format!("Bearer {token}"));
    }
    let response = router
        .clone()
        .oneshot(request.body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 32 * 1024 * 1024)
        .await
        .unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

#[tokio::test]
async fn stream_resource_and_privacy_contract() {
    std::env::var("TEST_DATABASE_URL").expect("use a disposable database");
    let pool = common::test_pool().await;
    mediafusion_api::migrate::run(pool).await.unwrap();
    let user: i32=sqlx::query_scalar("INSERT INTO users (uuid,email,username,role,is_verified,is_active,contribution_points,metadata_edits_approved,stream_edits_approved,contribution_level,contribute_anonymously,uploads_restricted,created_at) VALUES ('privacy-test','privacy-test@example.test','privacy-test',$1,true,true,0,0,0,'new',false,false,NOW()) RETURNING id")
        .bind(UserRole::User).fetch_one(pool).await.unwrap();
    let profile: i32=sqlx::query_scalar("INSERT INTO user_profiles (uuid,user_id,name,config,is_default,created_at) VALUES ('privacy-profile',$1,'privacy',$2,true,NOW()) RETURNING id")
        .bind(user).bind(json!({"sps":[{"sv":"realdebrid","en":true}],"mfc":{"pu":"https://mf.example.test","ap":"test","ewp":true}})).fetch_one(pool).await.unwrap();
    let media: i32=sqlx::query_scalar("INSERT INTO media (type,title,adult,is_blocked,is_public,is_user_created,total_streams,nudity_status,created_at) VALUES ($1,'Privacy regression movie',false,false,true,false,0,'UNKNOWN',NOW()) RETURNING id")
        .bind(MediaType::Movie).fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO media_external_id (media_id,provider,external_id,created_at) VALUES ($1,'imdb','tt0063350',NOW()),($1,'kitsu','987654321',NOW())").bind(media).execute(pool).await.unwrap();
    let torrent = TorrentStoreInput {
        base: StreamStoreBase {
            name: format!("Movie_{HASH}.mkv"),
            source: "test".into(),
            ..Default::default()
        },
        info_hash: HASH.into(),
        total_size: 2000,
        seeders: None,
        torrent_type: TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
        files: vec![],
    };
    let stream = db::store_torrent_stream(
        pool,
        &torrent,
        &StoreStreamOpts::scraper(MediaId(media), MediaType::Movie),
    )
    .await
    .unwrap()
    .stream_id();
    let mut config = AppConfig::from_env();
    config.postgres_uri = std::env::var("TEST_DATABASE_URL").unwrap();
    config.postgres_ro_uri = None;
    config.redis_url = std::env::var("TEST_REDIS_URL").expect("use a disposable Redis");
    config.secret_key_raw = SECRET.into();
    config.secret_key = [9; 32];
    config.db_pool_size = 3;
    config.db_pool_min = 1;
    config.poster_nsfw_enabled = false;
    config.requests_proxy_url = None;
    config.hide_torrent_info_hashes = true;
    config.disabled_providers = vec!["p2p".into()];
    let state = AppState::build(config.clone()).await.unwrap();
    let private = router(state);
    let user_token = token(user, "user");
    let query = format!("?profile_id={profile}&profile_uuid=privacy-profile&provider=realdebrid");
    assert_eq!(
        request(
            &private,
            &format!("/api/v1/stream/movie/tt0063350.json{query}"),
            None
        )
        .await
        .0,
        StatusCode::UNAUTHORIZED
    );
    let (status, streams) = request(
        &private,
        &format!("/api/v1/stream/movie/tt0063350.json{query}"),
        Some(&user_token),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(streams["streams"].as_array().unwrap().len(), 1);
    assert!(!streams.to_string().contains(HASH));
    assert!(streams["streams"][0].get("info_hash").is_none());
    assert!(streams["streams"][0].get("browser_url").is_none());
    assert_eq!(
        streams["web_playback"]["proxy_url"],
        "https://mf.example.test"
    );
    let url = url::Url::parse(streams["streams"][0]["url"].as_str().unwrap()).unwrap();
    let reference = mediafusion_api::crypto::torrent_ref::open(
        url.path_segments().unwrap().next_back().unwrap(),
        &[9; 32],
    )
    .unwrap();
    assert_eq!(reference.hash, HASH);
    // Valid references reach ordinary provider resolution; cross-profile reuse does not.
    assert_eq!(
        request(&private, url.path(), None).await.0,
        StatusCode::FOUND
    );
    assert_eq!(
        request(
            &private,
            &url.path().replace("U-privacy-profile", "U-another-profile"),
            None
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    let mut disabled_config = config.clone();
    disabled_config.disabled_providers.push("realdebrid".into());
    let disabled = router(AppState::build(disabled_config).await.unwrap());
    let (_, disabled_provider) = request(
        &disabled,
        &format!("/api/v1/stream/movie/tt0063350.json{query}"),
        Some(&user_token),
    )
    .await;
    assert_eq!(disabled_provider["streams"], json!([]));
    assert_eq!(disabled_provider["streaming_providers"], json!([]));
    for path in [
        format!("/api/v1/catalog/movie/{media}/streams{query}"),
        format!("/api/v1/stream/movie/mf:{media}.json{query}"),
        format!("/api/v1/stream/movie/kitsu:987654321.json{query}"),
    ] {
        let (status, value) = request(&private, &path, Some(&user_token)).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(value["streams"][0]["id"], stream.0);
        assert!(!value.to_string().contains(HASH));
    }
    // A stale admin claim cannot bypass the database role check.
    let (_, secondary) = request(&private, "/secondary", Some(&token(user, "admin"))).await;
    assert!(!secondary.to_string().contains(HASH));
    assert_eq!(
        request(&private, "/torznab", Some(&user_token)).await.0,
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        request(
            &private,
            "/streaming_provider/U-x/playback/realdebrid/T-invalid",
            None
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    sqlx::query("UPDATE user_profiles SET config=$2 WHERE id=$1")
        .bind(profile)
        .bind(json!({}))
        .execute(pool)
        .await
        .unwrap();
    let (_, no_provider) = request(
        &private,
        &format!("/api/v1/stream/movie/tt0063350.json{query}"),
        Some(&user_token),
    )
    .await;
    assert_eq!(no_provider["streams"], json!([]));
    sqlx::query("UPDATE users SET role=$2 WHERE id=$1")
        .bind(user)
        .bind(UserRole::Admin)
        .execute(pool)
        .await
        .unwrap();
    let (_, admin) = request(&private, "/secondary", Some(&token(user, "admin"))).await;
    assert_eq!(admin["info_hash"], HASH);
    // Provider restrictions apply even with the privacy flag disabled.
    config.hide_torrent_info_hashes = false;
    let public = router(AppState::build(config).await.unwrap());
    let (_, original) = request(&public, "/secondary", None).await;
    assert_eq!(
        original["info_hash"], HASH,
        "flag off preserves existing response"
    );
    let (_, disabled_p2p) = request(
        &public,
        &format!("/api/v1/stream/movie/tt0063350.json{query}"),
        Some(&user_token),
    )
    .await;
    assert_eq!(disabled_p2p["streams"], json!([]));
    sqlx::query("DELETE FROM stream WHERE id=$1")
        .bind(stream)
        .execute(pool)
        .await
        .unwrap();
    sqlx::query("DELETE FROM media WHERE id=$1")
        .bind(media)
        .execute(pool)
        .await
        .unwrap();
    sqlx::query("DELETE FROM users WHERE id=$1")
        .bind(user)
        .execute(pool)
        .await
        .unwrap();
}

#[tokio::test]
async fn hash_bearing_video_destinations_preserve_ranges_without_exposing_redirects() {
    use axum::{
        http::{HeaderMap, Method, header},
        response::{IntoResponse, Response},
    };
    let upstream = Router::new().route(
        "/video",
        get(|method: Method, headers: HeaderMap| async move {
            assert_eq!(headers.get(header::RANGE).unwrap(), "bytes=1-3");
            Response::builder()
                .status(StatusCode::PARTIAL_CONTENT)
                .header(header::CONTENT_RANGE, "bytes 1-3/5")
                .header(header::CONTENT_LENGTH, 3)
                .header(header::CONTENT_TYPE, "video/mp4")
                .header(
                    header::CONTENT_DISPOSITION,
                    format!("inline; filename={HASH}.mp4"),
                )
                .body(if method == Method::HEAD {
                    Body::empty()
                } else {
                    Body::from("bcd")
                })
                .unwrap()
                .into_response()
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/video", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(listener, upstream).await.unwrap();
    });
    let http = reqwest::Client::new();
    let mut headers = HeaderMap::new();
    headers.insert(header::RANGE, "bytes=1-3".parse().unwrap());
    for method in [Method::GET, Method::HEAD] {
        let response =
            playback::private_video_response(&http, method.clone(), &url, &headers).await;
        assert_eq!(response.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(
            response.headers().get(header::CONTENT_RANGE).unwrap(),
            "bytes 1-3/5"
        );
        assert!(response.headers().get(header::LOCATION).is_none());
        assert!(
            response
                .headers()
                .get(header::CONTENT_DISPOSITION)
                .is_none()
        );
        let bytes = to_bytes(response.into_body(), 1024).await.unwrap();
        assert_eq!(
            &bytes[..],
            if method == Method::HEAD {
                b"".as_slice()
            } else {
                b"bcd".as_slice()
            }
        );
    }
    server.abort();
}
