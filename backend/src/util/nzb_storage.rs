//! NZB file storage with gzip compression and signed download URLs.

use std::path::PathBuf;

use aws_sdk_s3::primitives::ByteStream;
use flate2::Compression;
use flate2::read::GzDecoder;
use flate2::write::GzEncoder;
use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;
use std::io::{Read, Write};
use tracing::{info, warn};

use crate::config::AppConfig;

const LOCAL_NZB_DIR: &str = "data/nzb";

pub fn generate_signed_nzb_url(config: &AppConfig, guid: &str) -> String {
    let expires = chrono::Utc::now().timestamp() + config.nzb_download_url_expiry;
    let sig = compute_signature(&config.secret_key_raw, guid, expires);
    format!(
        "{}/api/v1/import/nzb/{guid}/download?expires={expires}&sig={sig}",
        config.host_url.trim_end_matches('/')
    )
}

pub fn verify_nzb_signature(config: &AppConfig, guid: &str, expires: i64, sig: &str) -> bool {
    if chrono::Utc::now().timestamp() > expires {
        return false;
    }
    let expected = compute_signature(&config.secret_key_raw, guid, expires);
    expected == sig
}

fn compute_signature(secret: &str, guid: &str, expires: i64) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("HMAC key");
    mac.update(format!("{guid}:{expires}").as_bytes());
    mac.finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn gzip_compress(content: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let mut encoder = GzEncoder::new(Vec::new(), Compression::new(6));
    encoder.write_all(content)?;
    encoder.finish()
}

fn gzip_decompress(content: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let mut decoder = GzDecoder::new(content);
    let mut out = Vec::new();
    decoder.read_to_end(&mut out)?;
    Ok(out)
}

fn local_dir(config: &AppConfig) -> PathBuf {
    if config.nzb_dir.is_empty() {
        PathBuf::from(LOCAL_NZB_DIR)
    } else {
        PathBuf::from(&config.nzb_dir)
    }
}

fn s3_key(guid: &str) -> String {
    format!("nzb/{guid}.nzb.gz")
}

pub async fn store_nzb(config: &AppConfig, guid: &str, content: &[u8]) {
    let compressed = match gzip_compress(content) {
        Ok(c) => c,
        Err(e) => {
            warn!("nzb_storage: gzip compress failed for {guid}: {e}");
            return;
        }
    };

    if config.effective_nzb_storage_backend() == "s3"
        && let (Some(client), Some(bucket)) = (
            crate::util::s3_client::build_s3_client(config).await,
            crate::util::s3_client::bucket_name(config),
        )
    {
        match client
            .put_object()
            .bucket(bucket)
            .key(s3_key(guid))
            .body(ByteStream::from(compressed.clone()))
            .content_type("application/gzip")
            .send()
            .await
        {
            Ok(_) => {
                info!(
                    "Stored NZB {guid} to S3 ({} -> {} bytes gzipped)",
                    content.len(),
                    compressed.len()
                );
                return;
            }
            Err(e) => warn!("nzb_storage: S3 store failed for {guid}: {e}"),
        }
    }

    let dir = local_dir(config);
    if let Err(e) = tokio::fs::create_dir_all(&dir).await {
        warn!("nzb_storage: create_dir_all {}: {e}", dir.display());
        return;
    }
    let path = dir.join(format!("{guid}.nzb.gz"));
    match tokio::fs::write(&path, &compressed).await {
        Ok(_) => info!(
            "Stored NZB {guid} locally ({} -> {} bytes gzipped)",
            content.len(),
            compressed.len()
        ),
        Err(e) => warn!("nzb_storage: local write {}: {e}", path.display()),
    }
}

pub async fn retrieve_nzb(config: &AppConfig, guid: &str) -> Option<Vec<u8>> {
    if config.effective_nzb_storage_backend() == "s3"
        && let (Some(client), Some(bucket)) = (
            crate::util::s3_client::build_s3_client(config).await,
            crate::util::s3_client::bucket_name(config),
        )
    {
        match client
            .get_object()
            .bucket(bucket)
            .key(s3_key(guid))
            .send()
            .await
        {
            Ok(resp) => {
                if let Ok(data) = resp.body.collect().await {
                    let bytes = data.into_bytes();
                    return gzip_decompress(&bytes).ok();
                }
            }
            Err(e) => tracing::debug!("nzb_storage: S3 retrieve miss for {guid}: {e}"),
        }
    }

    let dir = local_dir(config);
    let gz_path = dir.join(format!("{guid}.nzb.gz"));
    if let Ok(compressed) = tokio::fs::read(&gz_path).await
        && let Ok(raw) = gzip_decompress(&compressed)
    {
        return Some(raw);
    }
    // Legacy uncompressed fallback
    let raw_path = dir.join(format!("{guid}.nzb"));
    tokio::fs::read(raw_path).await.ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signature_is_deterministic() {
        let sig1 = compute_signature("secret", "guid", 123);
        let sig2 = compute_signature("secret", "guid", 123);
        assert_eq!(sig1, sig2);
        assert_ne!(sig1, compute_signature("secret", "guid", 124));
    }
}
