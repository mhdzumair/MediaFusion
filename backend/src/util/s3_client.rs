//! Shared S3/R2 client builder for object storage backends.

use aws_config::BehaviorVersion;
use aws_credential_types::Credentials;
use aws_sdk_s3::Client;

use crate::config::AppConfig;

pub async fn build_s3_client(config: &AppConfig) -> Option<Client> {
    let endpoint = config.s3_endpoint_url.as_ref()?;
    let access_key = config.s3_access_key_id.as_ref()?;
    let secret_key = config.s3_secret_access_key.as_ref()?;
    if endpoint.is_empty() || access_key.is_empty() || secret_key.is_empty() {
        return None;
    }

    let credentials = Credentials::new(access_key, secret_key, None, None, "mediafusion");
    let shared = aws_config::defaults(BehaviorVersion::latest())
        .credentials_provider(credentials)
        .region(aws_sdk_s3::config::Region::new(config.s3_region.clone()))
        .load()
        .await;

    let mut builder = aws_sdk_s3::config::Builder::from(&shared);
    builder = builder.endpoint_url(endpoint).force_path_style(true);

    Some(Client::from_conf(builder.build()))
}

pub fn bucket_name(config: &AppConfig) -> Option<&str> {
    config.s3_bucket_name.as_deref().filter(|s| !s.is_empty())
}
