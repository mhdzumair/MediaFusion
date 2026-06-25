//! Uploaded metadata image storage (local disk or S3/R2).

use std::path::PathBuf;

use aws_sdk_s3::primitives::ByteStream;
use chrono::Utc;
use tracing::warn;
use uuid::Uuid;

use crate::config::AppConfig;

pub fn generate_image_storage_key(extension: &str) -> String {
    let normalized = extension.trim().trim_start_matches('.').to_lowercase();
    let ext = if normalized.is_empty() {
        "bin"
    } else {
        normalized.as_str()
    };
    format!(
        "images/{}/{}/{}.{}",
        Utc::now().format("%Y"),
        Utc::now().format("%m"),
        Uuid::new_v4().as_simple(),
        ext
    )
}

pub fn normalize_image_storage_key(raw_key: &str) -> Result<String, String> {
    let key = raw_key.trim().trim_start_matches('/');
    if key.is_empty() || key.contains("..") || key.contains('\\') || !key.starts_with("images/") {
        return Err("Invalid image key".into());
    }
    Ok(key.to_string())
}

pub async fn store_image(
    config: &AppConfig,
    key: &str,
    content: &[u8],
    content_type: &str,
) -> Result<String, String> {
    if config.effective_image_storage_backend() == "s3" {
        if let (Some(client), Some(bucket)) = (
            crate::util::s3_client::build_s3_client(config).await,
            crate::util::s3_client::bucket_name(config),
        ) {
            match client
                .put_object()
                .bucket(bucket)
                .key(key)
                .body(ByteStream::from(content.to_vec()))
                .content_type(content_type)
                .cache_control("public, max-age=31536000, immutable")
                .send()
                .await
            {
                Ok(_) => return Ok(key.to_string()),
                Err(e) => {
                    warn!("image_storage: S3 store failed key={key}: {e}");
                    return Err("Failed to store image in S3".into());
                }
            }
        }
        return Err("S3 image storage is not configured".into());
    }

    let dir = PathBuf::from(&config.images_dir);
    tokio::fs::create_dir_all(&dir)
        .await
        .map_err(|e| format!("Failed to create image directory: {e}"))?;
    let file_name = key.rsplit('/').next().unwrap_or(key);
    let path = dir.join(file_name);
    tokio::fs::write(&path, content)
        .await
        .map_err(|e| format!("Failed to save image: {e}"))?;
    Ok(key.to_string())
}

pub async fn retrieve_image(config: &AppConfig, key: &str) -> Option<(Vec<u8>, String)> {
    let normalized = normalize_image_storage_key(key).ok()?;

    if config.effective_image_storage_backend() == "s3"
        && let (Some(client), Some(bucket)) = (
            crate::util::s3_client::build_s3_client(config).await,
            crate::util::s3_client::bucket_name(config),
        ) {
            match client
                .get_object()
                .bucket(bucket)
                .key(&normalized)
                .send()
                .await
            {
                Ok(resp) => {
                    let content_type = resp
                        .content_type()
                        .unwrap_or("application/octet-stream")
                        .to_string();
                    if let Ok(data) = resp.body.collect().await {
                        return Some((data.into_bytes().to_vec(), content_type));
                    }
                }
                Err(e) => warn!("image_storage: S3 retrieve failed key={normalized}: {e}"),
            }
            return None;
        }

    let file_name = normalized.rsplit('/').next()?;
    let path = PathBuf::from(&config.images_dir).join(file_name);
    let bytes = tokio::fs::read(&path).await.ok()?;
    let ext = file_name.rsplit('.').next().unwrap_or("");
    let content_type = match ext {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        _ => "application/octet-stream",
    };
    Some((bytes, content_type.to_string()))
}
