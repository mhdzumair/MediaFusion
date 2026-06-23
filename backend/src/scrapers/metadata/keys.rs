use crate::db::UserId;
use sqlx::PgPool;

/// Server-level metadata API keys (from env/config).
pub struct MetadataServerKeys<'a> {
    pub tmdb: Option<&'a str>,
    pub tvdb: Option<&'a str>,
    pub mdblist: Option<&'a str>,
}

/// Resolved keys preferring the user's profile config, falling back to server keys.
#[derive(Default)]
pub struct ResolvedMetadataKeys {
    pub tmdb: Option<String>,
    pub tvdb: Option<String>,
    pub mdblist: Option<String>,
}

async fn profile_provider_key(pool: &PgPool, user_id: UserId, provider: &str) -> Option<String> {
    let sql = match provider {
        "tmdb" => {
            "SELECT config->'tmdb'->>'ak' FROM user_profiles WHERE user_id = $1 AND is_default = true"
        }
        "tvdb" => {
            "SELECT config->'tvdb'->>'ak' FROM user_profiles WHERE user_id = $1 AND is_default = true"
        }
        "mdb" | "mdblist" => {
            "SELECT config->'mdb'->>'ak' FROM user_profiles WHERE user_id = $1 AND is_default = true"
        }
        _ => return None,
    };

    sqlx::query_scalar(sql)
        .bind(user_id)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
        .flatten()
        .filter(|s: &String| !s.is_empty())
}

fn coalesce_key(user: Option<String>, server: Option<&str>) -> Option<String> {
    user.filter(|s| !s.is_empty())
        .or_else(|| server.filter(|s| !s.is_empty()).map(str::to_string))
}

/// Resolve TMDB/TVDB/MDBList keys: user profile first, then server config.
pub async fn resolve_metadata_keys(
    pool: &PgPool,
    user_id: Option<UserId>,
    server: MetadataServerKeys<'_>,
) -> ResolvedMetadataKeys {
    let (user_tmdb, user_tvdb, user_mdb) = if let Some(uid) = user_id {
        (
            profile_provider_key(pool, uid, "tmdb").await,
            profile_provider_key(pool, uid, "tvdb").await,
            profile_provider_key(pool, uid, "mdb").await,
        )
    } else {
        (None, None, None)
    };

    ResolvedMetadataKeys {
        tmdb: coalesce_key(user_tmdb, server.tmdb),
        tvdb: coalesce_key(user_tvdb, server.tvdb),
        mdblist: coalesce_key(user_mdb, server.mdblist),
    }
}

impl ResolvedMetadataKeys {
    pub fn server_keys_from_config(config: &crate::config::AppConfig) -> MetadataServerKeys<'_> {
        MetadataServerKeys {
            tmdb: config.tmdb_api_key.as_deref(),
            tvdb: config.tvdb_api_key.as_deref(),
            mdblist: config.mdblist_api_key.as_deref(),
        }
    }

    pub fn fetch_ctx<'a>(
        &'a self,
        trakt_client_id: Option<&'a str>,
        trakt_client_secret: Option<&'a str>,
        cinemeta_fallback: bool,
    ) -> super::FetchCtx<'a> {
        super::FetchCtx {
            tmdb_api_key: self.tmdb.as_deref(),
            tvdb_api_key: self.tvdb.as_deref(),
            mdblist_api_key: self.mdblist.as_deref(),
            trakt_client_id,
            trakt_client_secret,
            cinemeta_fallback,
        }
    }
}
