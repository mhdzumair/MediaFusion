//! Requires SECRET_KEY plus disposable TEST_DATABASE_URL and TEST_REDIS_URL services.
//! SECRET_KEY only bootstraps AppConfig; this test overrides the signing/encryption keys.
mod common;
use axum::{
    Json, Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
    routing::{get, post},
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
        .route("/api/v1/stream-links/stream/{stream_id}/files", get(mediafusion_api::routes::content::stream_linking::get_stream_files_for_annotation))
        .route("/api/v1/admin/db/tables/{table}/export",get(mediafusion_api::routes::admin_db::import_export::export_table))
        .route("/api/v1/admin/tasks/stream",get(mediafusion_api::routes::admin_scrapers::stream_task_snapshots))
        .route("/api/v1/admin/tasks/{task_id}/stream",get(mediafusion_api::routes::admin_scrapers::stream_task_detail))
        .route("/api/v1/watchlist/providers",get(||async {Json(json!({"ok":true}))}))
        .route("/api/v1/watchlist/realdebrid",get(||async {Json(json!({"ok":true}))}))
        .route("/api/v1/watchlist/realdebrid/missing",get(||async {Json(json!({"ok":true}))}))
        .route("/api/v1/watchlist/refs",post(|Json(value):Json<Value>|async move {
            assert_eq!(value["hash"], HASH);
            Json(json!({"ok":true}))
        }))
        .route("/signed-token",get(||async {Json(json!({"access_token":token(1,"user")}))}))
        .route("/text/{kind}",get(|axum::extract::Path(kind):axum::extract::Path<String>|async move {
            use axum::http::header;
            use axum::response::IntoResponse;
            let content_type = match kind.as_str() { "html"=>"text/html", "xml"=>"application/problem+xml", "csv"=>"text/csv", _=>"text/plain" };
            ([(header::CONTENT_TYPE, content_type)],format!("prefix{HASH}suffix")).into_response()
        }))
        .route("/header",get(||async {
            axum::http::Response::builder().header("Content-Type","application/octet-stream")
                .header("Content-Disposition",format!("attachment; filename=prefix{HASH}suffix.nzb"))
                .header("X-Stream-Name",HASH.bytes().map(|b|format!("%{b:02x}")).collect::<String>())
                .body(Body::from("bytes")).unwrap()
        }))
        .route("/unsafe-redirect",get(||async {
            let encoded:String=HASH.bytes().map(|b|format!("%{b:02x}")).collect();
            axum::http::Response::builder().status(StatusCode::FOUND)
                .header("Location",format!("https://example.test/?d={}",urlencoding::encode(&encoded)))
                .body(Body::empty()).unwrap()
        }))
        .route("/resolved-provider",get(|axum::extract::State(state):axum::extract::State<Arc<AppState>>, headers:axum::http::HeaderMap|async move {
            let url=headers.get("x-test-destination").and_then(|v|v.to_str().ok()).map(str::to_owned)
                .unwrap_or_else(||format!("https://example.test/video/{HASH}.mkv"));
            playback::resolved_playback_response(&state, axum::http::Method::GET,
                url, &headers, HASH,"test-provider").await
        }))
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
        .bind(user).bind(json!({"sps":[{"sv":"realdebrid","en":true}],"mfc":{"pu":"https://mf.example.test","ap":"test","ewp":true},"st":{"title":"{stream.name|replace('', '-')}","description":"{stream.filename|replace('', '-')}"}})).fetch_one(pool).await.unwrap();
    let media: i32=sqlx::query_scalar("INSERT INTO media (type,title,adult,is_blocked,is_public,is_user_created,total_streams,nudity_status,created_at) VALUES ($1,'Privacy regression movie',false,false,true,false,0,'UNKNOWN',NOW()) RETURNING id")
        .bind(MediaType::Movie).fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO media_external_id (media_id,provider,external_id,created_at) VALUES ($1,'imdb','tt0063350',NOW()),($1,'kitsu','987654321',NOW())").bind(media).execute(pool).await.unwrap();
    let torrent = TorrentStoreInput {
        base: StreamStoreBase {
            name: format!("prefix{HASH}suffix.mkv"),
            source: "test".into(),
            ..Default::default()
        },
        info_hash: HASH.into(),
        total_size: 2000,
        seeders: None,
        torrent_type: TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
        files: vec![db::StreamFileStoreInput {
            file_index: 0,
            filename: format!("prefix{HASH}suffix.mkv"),
            size: Some(2000),
            season_number: 0,
            episode_number: 0,
        }],
    };
    let stream = db::store_torrent_stream(
        pool,
        &torrent,
        &StoreStreamOpts::scraper(MediaId(media), MediaType::Movie),
    )
    .await
    .unwrap()
    .stream_id();
    db::upsert_stream_file_row(pool, stream, &torrent.files[0])
        .await
        .unwrap();
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
    config.hide_torrent_info_hashes_relay = true;
    config.disabled_providers = vec!["p2p".into()];
    let state = AppState::build(config.clone()).await.unwrap();
    let private = router(state.clone());
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
    assert!(
        !streams.to_string().replace('-', "").contains(HASH),
        "template context must be scrubbed before replace"
    );
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
    let (status, files) = request(
        &private,
        &format!("/api/v1/stream-links/stream/{}/files", stream.0),
        Some(&user_token),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(!files.to_string().contains(HASH));
    for path in [
        "/api/v1/watchlist/providers",
        "/api/v1/watchlist/realdebrid",
        "/api/v1/watchlist/realdebrid/missing",
    ] {
        let response = private
            .clone()
            .oneshot(
                Request::builder()
                    .uri(path)
                    .header("content-type", "application/json")
                    .header("authorization", format!("Bearer {user_token}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK, "{path}");
    }
    let operation = mediafusion_api::crypto::torrent_ref::seal(
        &mediafusion_api::crypto::torrent_ref::TorrentRef {
            hash: HASH.into(),
            filename: None,
            season: None,
            episode: None,
            scope: format!("api:{user}"),
        },
        &config.secret_key,
    )
    .unwrap();
    let response = private
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/watchlist/refs")
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {user_token}"))
                .body(Body::from(json!({"hash":operation}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let (_, signed) = request(&private, "/signed-token", Some(&user_token)).await;
    let mut signed_headers = axum::http::HeaderMap::new();
    signed_headers.insert(
        "authorization",
        format!("Bearer {}", signed["access_token"].as_str().unwrap())
            .parse()
            .unwrap(),
    );
    assert_eq!(
        mediafusion_api::routes::auth_guard::decode_access_token(&signed_headers, SECRET),
        Ok((1, "user".into())),
        "hash-looking signatures must still authenticate"
    );
    for path in [
        "/text/html",
        "/text/xml",
        "/text/csv",
        "/text/plain",
        "/unsafe-redirect",
    ] {
        assert_eq!(
            request(&private, path, Some(&user_token)).await.0,
            StatusCode::FORBIDDEN,
            "{path}"
        );
    }
    let response = private
        .clone()
        .oneshot(
            Request::builder()
                .uri("/header")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.headers().get("content-disposition").is_none());
    assert!(response.headers().get("x-stream-name").is_none());
    assert_eq!(to_bytes(response.into_body(), 1024).await.unwrap(), "bytes");
    let upstream = Router::new().route(
        "/video/{name}",
        get(|| async {
            (
                [(axum::http::header::CONTENT_TYPE, "video/mp4")],
                "video-bytes",
            )
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let destination = format!("http://{}/video/{HASH}.mkv", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(listener, upstream).await.unwrap();
    });
    let relayed = private
        .clone()
        .oneshot(
            Request::builder()
                .uri("/resolved-provider")
                .header("x-test-destination", &destination)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(relayed.status(), StatusCode::OK);
    assert!(relayed.headers().get("location").is_none());
    assert_eq!(
        to_bytes(relayed.into_body(), 1024).await.unwrap(),
        "video-bytes"
    );
    server.abort();
    let mut no_relay = config.clone();
    no_relay.hide_torrent_info_hashes_relay = false;
    let redirecting = router(AppState::build(no_relay).await.unwrap());
    let response = redirecting
        .clone()
        .oneshot(
            Request::builder()
                .uri("/resolved-provider")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FOUND);
    assert_eq!(
        response.headers()["location"],
        format!("https://example.test/video/{HASH}.mkv")
    );
    assert_eq!(
        request(&redirecting, "/unsafe-redirect", None).await.0,
        StatusCode::FORBIDDEN,
        "relay opt-out must not disable unrelated response protection"
    );
    for path in [
        "/api/v1/admin/db/tables/torrent_stream/export?format=csv",
        "/api/v1/admin/tasks/stream",
        "/api/v1/admin/tasks/1/stream",
    ] {
        assert_eq!(
            request(&private, path, Some(&token(user, "admin"))).await.0,
            StatusCode::FORBIDDEN,
            "stale admin: {path}"
        );
    }
    // Role enforcement must also work independently of privacy middleware.
    let unwrapped = Router::new()
        .route(
            "/export/{table}",
            get(mediafusion_api::routes::admin_db::import_export::export_table),
        )
        .route(
            "/tasks",
            get(mediafusion_api::routes::admin_scrapers::stream_task_snapshots),
        )
        .with_state(state.clone());
    assert_eq!(
        request(
            &unwrapped,
            "/export/torrent_stream?format=csv",
            Some(&token(user, "admin"))
        )
        .await
        .0,
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        request(&unwrapped, "/tasks", Some(&token(user, "admin")))
            .await
            .0,
        StatusCode::FORBIDDEN
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
    let export = private
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/v1/admin/db/tables/torrent_stream/export?format=csv")
                .header("authorization", format!("Bearer {}", token(user, "admin")))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(export.status(), StatusCode::OK);
    let csv = to_bytes(export.into_body(), 32 * 1024 * 1024)
        .await
        .unwrap();
    assert!(String::from_utf8_lossy(&csv).contains(HASH));
    use futures::StreamExt;
    let events = private
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/v1/admin/tasks/stream?interval_ms=1000")
                .header("authorization", format!("Bearer {}", token(user, "admin")))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(events.status(), StatusCode::OK);
    let mut body = events.into_body().into_data_stream();
    assert!(body.next().await.is_some());
    sqlx::query("UPDATE users SET role=$2 WHERE id=$1")
        .bind(user)
        .bind(UserRole::User)
        .execute(pool)
        .await
        .unwrap();
    assert!(
        tokio::time::timeout(std::time::Duration::from_secs(3), body.next())
            .await
            .unwrap()
            .is_none(),
        "an already-open SSE stream must stop after demotion"
    );

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
    let upstream = upstream
        .route(
            "/error",
            get(|| async {
                (
                    StatusCode::FORBIDDEN,
                    [(header::CONTENT_TYPE, "text/html")],
                    format!("error for {HASH}"),
                )
            }),
        )
        .route(
            "/xml",
            get(|| async {
                (
                    [(header::CONTENT_TYPE, "application/xml")],
                    format!("<error>{HASH}</error>"),
                )
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
    for path in ["/error", "/xml"] {
        let error_url = url.replace("/video", path);
        let response =
            playback::private_video_response(&http, Method::GET, &error_url, &headers).await;
        assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
        assert!(
            !String::from_utf8_lossy(&to_bytes(response.into_body(), 1024).await.unwrap())
                .contains(HASH)
        );
    }
    server.abort();
}
