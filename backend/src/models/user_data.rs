use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Mirrors Python's `SortingOption` (schema/config.py).
/// Stored in `torrent_sorting_priority` as `{"k": "resolution", "d": "desc"}`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SortingOption {
    #[serde(rename = "k", alias = "key")]
    pub key: String,
    #[serde(default = "default_sort_direction", rename = "d", alias = "direction")]
    pub direction: String,
}

fn default_sort_direction() -> String {
    "desc".to_string()
}

fn default_torrent_sorting_priority() -> Vec<Value> {
    const KEYS: &[&str] = &[
        "cached",
        "resolution",
        "quality",
        "language",
        "size",
        "seeders",
        "created_at",
    ];
    KEYS.iter()
        .map(|k| serde_json::json!({"k": k, "d": "desc"}))
        .collect()
}

fn default_language_sorting() -> Vec<Value> {
    const LANGS: &[Option<&str>] = &[
        Some("English"),
        Some("Tamil"),
        Some("Hindi"),
        Some("Malayalam"),
        Some("Kannada"),
        Some("Telugu"),
        Some("Chinese"),
        Some("Russian"),
        Some("Arabic"),
        Some("Japanese"),
        Some("Korean"),
        Some("Taiwanese"),
        Some("Latino"),
        Some("French"),
        Some("Spanish"),
        Some("Portuguese"),
        Some("Italian"),
        Some("German"),
        Some("Ukrainian"),
        Some("Polish"),
        Some("Czech"),
        Some("Thai"),
        Some("Indonesian"),
        Some("Vietnamese"),
        Some("Dutch"),
        Some("Bengali"),
        Some("Turkish"),
        Some("Greek"),
        Some("Swedish"),
        Some("Romanian"),
        Some("Hungarian"),
        Some("Finnish"),
        Some("Norwegian"),
        Some("Danish"),
        Some("Hebrew"),
        Some("Lithuanian"),
        Some("Punjabi"),
        Some("Marathi"),
        Some("Gujarati"),
        Some("Bhojpuri"),
        Some("Nepali"),
        Some("Urdu"),
        Some("Tagalog"),
        Some("Filipino"),
        Some("Malay"),
        Some("Mongolian"),
        Some("Armenian"),
        Some("Georgian"),
        None,
    ];
    LANGS
        .iter()
        .map(|l| match l {
            Some(s) => Value::String(s.to_string()),
            None => Value::Null,
        })
        .collect()
}

fn default_true() -> bool {
    true
}
fn default_max_streams() -> u32 {
    25
}
fn default_nudity_filter() -> Vec<String> {
    vec!["Severe".to_string()]
}
fn default_cert_filter() -> Vec<String> {
    vec!["Adults+".to_string()]
}
fn default_quality_filter() -> Vec<String> {
    // Mirrors Python's `list(const.QUALITY_GROUPS.keys())`
    vec![
        "BluRay/UHD".to_string(),
        "WEB/HD".to_string(),
        "DVD/TV/SAT".to_string(),
        "CAM/Screener".to_string(),
    ]
}
fn default_priority() -> i32 {
    1
}
fn default_stream_type_grouping() -> String {
    "separate".to_string()
}
fn default_stream_type_order() -> Vec<String> {
    vec![
        "torrent".to_string(),
        "usenet".to_string(),
        "telegram".to_string(),
        "http".to_string(),
        "acestream".to_string(),
        "youtube".to_string(),
    ]
}
fn default_enable_usenet_streams() -> bool {
    true
}
fn default_max_streams_per_resolution() -> u32 {
    10
}

// ─── Indexer configuration ────────────────────────────────────────────────────

#[derive(Debug, Default, Deserialize, Serialize, Clone)]
pub struct IndexerInstanceConfig {
    #[serde(default, rename = "u", alias = "url")]
    pub url: Option<String>,
    #[serde(default, rename = "ak", alias = "api_key")]
    pub api_key: Option<String>,
    #[serde(default = "default_true", rename = "ug", alias = "use_global")]
    pub use_global: bool,
    #[serde(default, rename = "en", alias = "enabled")]
    pub enabled: bool,
}

#[derive(Debug, Default, Deserialize, Serialize, Clone)]
pub struct TorznabEndpoint {
    #[serde(rename = "i", alias = "id")]
    pub id: String,
    #[serde(rename = "n", alias = "name")]
    pub name: String,
    #[serde(rename = "u", alias = "url")]
    pub url: String,
    #[serde(default, rename = "h", alias = "headers")]
    pub headers: Option<std::collections::HashMap<String, String>>,
    #[serde(default = "default_true", rename = "en", alias = "enabled")]
    pub enabled: bool,
    #[serde(default, rename = "c", alias = "categories")]
    pub categories: Vec<i64>,
    #[serde(default = "default_priority", rename = "p", alias = "priority")]
    pub priority: i32,
}

#[derive(Debug, Default, Deserialize, Serialize, Clone)]
pub struct NewznabIndexer {
    #[serde(rename = "i", alias = "id")]
    pub id: String,
    #[serde(rename = "n", alias = "name")]
    pub name: String,
    #[serde(rename = "u", alias = "url")]
    pub url: String,
    #[serde(default, rename = "ak", alias = "api_key")]
    pub api_key: Option<String>,
    #[serde(default = "default_true", rename = "en", alias = "enabled")]
    pub enabled: bool,
    #[serde(default = "default_priority", rename = "p", alias = "priority")]
    pub priority: i32,
    #[serde(default, rename = "mc", alias = "movie_categories")]
    pub movie_categories: Vec<i64>,
    #[serde(default, rename = "tc", alias = "tv_categories")]
    pub tv_categories: Vec<i64>,
    // unused fields — kept so deserialization doesn't lose data
    #[serde(default, rename = "uz", alias = "use_zyclops", skip_serializing)]
    pub use_zyclops: bool,
    #[serde(default, rename = "zb", alias = "zyclops_backbones", skip_serializing)]
    pub zyclops_backbones: Vec<String>,
}

#[derive(Debug, Default, Deserialize, Serialize, Clone)]
pub struct IndexerConfig {
    #[serde(default, rename = "pr", alias = "prowlarr")]
    pub prowlarr: Option<IndexerInstanceConfig>,
    #[serde(default, rename = "jk", alias = "jackett")]
    pub jackett: Option<IndexerInstanceConfig>,
    #[serde(default, rename = "tz", alias = "torznab_endpoints")]
    pub torznab_endpoints: Vec<TorznabEndpoint>,
    #[serde(default, rename = "nz", alias = "newznab_indexers")]
    pub newznab_indexers: Vec<NewznabIndexer>,
}

// ─── MediaFlow Proxy configuration ───────────────────────────────────────────

#[derive(Debug, Default, Deserialize, Serialize, Clone)]
pub struct MediaFlowConfig {
    #[serde(default, rename = "pu", alias = "proxy_url")]
    pub proxy_url: Option<String>,
    #[serde(default, rename = "ap", alias = "api_password")]
    pub api_password: Option<String>,
    #[serde(default, rename = "pls", alias = "proxy_live_streams")]
    pub proxy_live_streams: bool,
    #[serde(default, rename = "ewp", alias = "enable_web_playback")]
    pub enable_web_playback: bool,
}

// ─── Streaming provider ───────────────────────────────────────────────────────

#[derive(Debug, Default, Deserialize, Serialize, Clone)]
pub struct StreamingProvider {
    #[serde(default, rename = "n", alias = "name")]
    pub name: String,
    #[serde(rename = "sv", alias = "service")]
    pub service: String,
    #[serde(
        default = "default_true",
        rename = "ewc",
        alias = "enable_watchlist_catalogs"
    )]
    pub enable_watchlist_catalogs: bool,
    #[serde(default, rename = "tk", alias = "token")]
    pub token: Option<String>,
    #[serde(default, rename = "em", alias = "email")]
    pub email: Option<String>,
    #[serde(default, rename = "pw", alias = "password")]
    pub password: Option<String>,
    #[serde(default = "default_true", rename = "en", alias = "enabled")]
    pub enabled: bool,
    #[serde(default, rename = "pr", alias = "priority")]
    pub priority: i32,
    #[serde(default = "default_true", rename = "umf", alias = "use_mediaflow")]
    pub use_mediaflow: bool,
    #[serde(default, rename = "oscs", alias = "only_show_cached_streams")]
    pub only_show_cached_streams: bool,
    // Complex nested configs — kept as raw JSON so we don't lose data but don't need to parse
    #[serde(
        default,
        rename = "qbc",
        alias = "qbittorrent_config",
        skip_serializing
    )]
    pub qbittorrent_config: Option<Value>,
    #[serde(default, rename = "sbc", alias = "sabnzbd_config", skip_serializing)]
    pub sabnzbd_config: Option<Value>,
    #[serde(default, rename = "ngc", alias = "nzbget_config", skip_serializing)]
    pub nzbget_config: Option<Value>,
    #[serde(default, rename = "ndc", alias = "nzbdav_config", skip_serializing)]
    pub nzbdav_config: Option<Value>,
    #[serde(default, rename = "enc", alias = "easynews_config", skip_serializing)]
    pub easynews_config: Option<Value>,
    #[serde(default, rename = "u", alias = "url", skip_serializing)]
    pub url: Option<String>,
    #[serde(
        default,
        rename = "stsn",
        alias = "stremthru_store_name",
        skip_serializing
    )]
    pub stremthru_store_name: Option<String>,
}

// ─── Catalog configuration ────────────────────────────────────────────────────

#[derive(Debug, Default, Deserialize, Serialize, Clone)]
pub struct CatalogConfig {
    #[serde(rename = "ci", alias = "catalog_id")]
    pub catalog_id: String,
    #[serde(default = "default_true", rename = "en", alias = "enabled")]
    pub enabled: bool,
    #[serde(default, rename = "s", alias = "sort")]
    pub sort: Option<String>,
    #[serde(default, rename = "o", alias = "order")]
    pub order: Option<String>,
}

// ─── UserData ─────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct UserData {
    // User identification
    #[serde(default, rename = "uid", alias = "user_id")]
    pub user_id: Option<i64>,
    #[serde(default, rename = "pid", alias = "profile_id")]
    pub profile_id: Option<i64>,
    #[serde(default, rename = "uuuid", alias = "user_uuid", skip_serializing)]
    pub user_uuid: Option<String>,
    #[serde(default, rename = "puuid", alias = "profile_uuid", skip_serializing)]
    pub profile_uuid: Option<String>,

    // Auth
    #[serde(default, rename = "ap", alias = "api_password")]
    pub api_password: Option<String>,

    // Streaming providers
    #[serde(default, rename = "sps", alias = "streaming_providers")]
    pub streaming_providers: Vec<StreamingProvider>,
    // Legacy single provider — merged into streaming_providers on access
    #[serde(default, rename = "sp", alias = "streaming_provider")]
    pub streaming_provider: Option<StreamingProvider>,

    // Catalog settings
    #[serde(default = "default_true", rename = "ec", alias = "enable_catalogs")]
    pub enable_catalogs: bool,
    #[serde(default, rename = "eim", alias = "enable_imdb_metadata")]
    pub enable_imdb_metadata: bool,
    #[serde(default, rename = "cc", alias = "catalog_configs")]
    pub catalog_configs: Vec<CatalogConfig>,
    #[serde(default, rename = "sc", alias = "selected_catalogs")]
    pub selected_catalogs: Vec<String>,

    // Stream filters
    #[serde(default, rename = "nf", alias = "nudity_filter")]
    pub nudity_filter: Vec<String>,
    #[serde(default, rename = "cf", alias = "certification_filter")]
    pub certification_filter: Vec<String>,
    #[serde(default, rename = "sr", alias = "selected_resolutions")]
    pub selected_resolutions: Vec<Option<String>>,
    #[serde(default, rename = "hf", alias = "hdr_filter", skip_serializing)]
    pub hdr_filter: Vec<String>,
    #[serde(
        default = "default_quality_filter",
        rename = "qf",
        alias = "quality_filter",
        skip_serializing
    )]
    pub quality_filter: Vec<String>,

    // Stream display / combining
    #[serde(default = "default_max_streams", rename = "mxs", alias = "max_streams")]
    pub max_streams: u32,
    #[serde(
        default = "default_max_streams_per_resolution",
        rename = "mspr",
        alias = "max_streams_per_resolution"
    )]
    pub max_streams_per_resolution: u32,
    #[serde(
        default = "default_stream_type_grouping",
        rename = "stg",
        alias = "stream_type_grouping"
    )]
    pub stream_type_grouping: String,
    #[serde(
        default = "default_stream_type_order",
        rename = "sto",
        alias = "stream_type_order"
    )]
    pub stream_type_order: Vec<String>,
    #[serde(default, rename = "pg", alias = "provider_grouping", skip_serializing)]
    pub provider_grouping: Option<String>,

    // Stream type toggles
    #[serde(
        default = "default_enable_usenet_streams",
        rename = "eus",
        alias = "enable_usenet_streams"
    )]
    pub enable_usenet_streams: bool,
    #[serde(default, rename = "puot", alias = "prefer_usenet_over_torrent")]
    pub prefer_usenet_over_torrent: bool,
    #[serde(default, rename = "ets", alias = "enable_telegram_streams")]
    pub enable_telegram_streams: bool,
    #[serde(default, rename = "eas", alias = "enable_acestream_streams")]
    pub enable_acestream_streams: bool,

    // Live search
    #[serde(default, rename = "lss", alias = "live_search_streams")]
    pub live_search_streams: bool,

    // MediaFlow
    #[serde(default, rename = "mfc", alias = "mediaflow_config")]
    pub mediaflow_config: Option<MediaFlowConfig>,

    // Indexers
    #[serde(default, rename = "ic", alias = "indexer_config")]
    pub indexer_config: Option<IndexerConfig>,

    // External integrations — kept as raw JSON, not parsed by Rust
    #[serde(default, rename = "mdb", alias = "mdblist_config", skip_serializing)]
    pub mdblist_config: Option<Value>,
    #[serde(default, rename = "rpc", alias = "rpdb_config", skip_serializing)]
    pub rpdb_config: Option<Value>,
    #[serde(default, rename = "tmdb", alias = "tmdb_config", skip_serializing)]
    pub tmdb_config: Option<Value>,
    #[serde(default, rename = "tvdb", alias = "tvdb_config", skip_serializing)]
    pub tvdb_config: Option<Value>,
    #[serde(default, rename = "tgc", alias = "telegram_config", skip_serializing)]
    pub telegram_config: Option<Value>,
    #[serde(default, rename = "st", alias = "stream_template", skip_serializing)]
    pub stream_template: Option<Value>,

    // Stream name filter — not yet used in Rust but must survive round-trip
    #[serde(
        default,
        rename = "snfm",
        alias = "stream_name_filter_mode",
        skip_serializing
    )]
    pub stream_name_filter_mode: Option<String>,
    #[serde(
        default,
        rename = "snfp",
        alias = "stream_name_filter_patterns",
        skip_serializing
    )]
    pub stream_name_filter_patterns: Vec<String>,
    #[serde(
        default,
        rename = "snfr",
        alias = "stream_name_filter_use_regex",
        skip_serializing
    )]
    pub stream_name_filter_use_regex: bool,

    // Misc unused fields — round-trip safe
    #[serde(default, rename = "ia", alias = "include_anime", skip_serializing)]
    pub include_anime: bool,
    #[serde(default, rename = "ed", alias = "enable_discover", skip_serializing)]
    pub enable_discover: bool,
    #[serde(
        default = "default_torrent_sorting_priority",
        rename = "tsp",
        alias = "torrent_sorting_priority",
        skip_serializing
    )]
    pub torrent_sorting_priority: Vec<Value>,
    #[serde(
        default = "default_language_sorting",
        rename = "ls",
        alias = "language_sorting",
        skip_serializing
    )]
    pub language_sorting: Vec<Value>,
}

impl Default for UserData {
    fn default() -> Self {
        UserData {
            user_id: None,
            profile_id: None,
            user_uuid: None,
            profile_uuid: None,
            api_password: None,
            streaming_providers: Vec::new(),
            streaming_provider: None,
            enable_catalogs: true,
            enable_imdb_metadata: false,
            catalog_configs: Vec::new(),
            selected_catalogs: Vec::new(),
            nudity_filter: default_nudity_filter(),
            certification_filter: default_cert_filter(),
            selected_resolutions: Vec::new(),
            hdr_filter: Vec::new(),
            quality_filter: default_quality_filter(),
            max_streams: 25,
            max_streams_per_resolution: 10,
            stream_type_grouping: default_stream_type_grouping(),
            stream_type_order: default_stream_type_order(),
            provider_grouping: None,
            enable_usenet_streams: true,
            prefer_usenet_over_torrent: false,
            enable_telegram_streams: false,
            enable_acestream_streams: false,
            live_search_streams: false,
            mediaflow_config: None,
            indexer_config: None,
            mdblist_config: None,
            rpdb_config: None,
            tmdb_config: None,
            tvdb_config: None,
            telegram_config: None,
            stream_template: None,
            stream_name_filter_mode: None,
            stream_name_filter_patterns: Vec::new(),
            stream_name_filter_use_regex: false,
            include_anime: true,
            enable_discover: false,
            torrent_sorting_priority: default_torrent_sorting_priority(),
            language_sorting: default_language_sorting(),
        }
    }
}

// ─── Provider short names ─────────────────────────────────────────────────────

const PROVIDER_SHORT_NAMES: &[(&str, &str)] = &[
    ("realdebrid", "RD"),
    ("premiumize", "PM"),
    ("alldebrid", "AD"),
    ("debridlink", "DL"),
    ("offcloud", "OC"),
    ("pikpak", "PKP"),
    ("torbox", "TRB"),
    ("seedr", "SEEDR"),
    ("stremthru", "ST"),
    ("qbittorrent", "QB"),
    ("easydebrid", "ED"),
    ("debrider", "DBR"),
];

fn short_name(service: &str) -> Option<&'static str> {
    PROVIDER_SHORT_NAMES
        .iter()
        .find(|(s, _)| *s == service)
        .map(|(_, n)| *n)
}

// ─── UserData methods ─────────────────────────────────────────────────────────

impl UserData {
    /// True when MediaFlow is configured with both proxy_url and api_password.
    pub fn has_mediaflow_config(&self) -> bool {
        self.mediaflow_config
            .as_ref()
            .map(|m| {
                m.proxy_url
                    .as_deref()
                    .map(|s| !s.is_empty())
                    .unwrap_or(false)
            })
            .unwrap_or(false)
    }

    /// All enabled streaming providers (merges legacy `sp` into `sps`).
    pub fn all_providers(&self) -> Vec<&StreamingProvider> {
        let mut providers: Vec<&StreamingProvider> = self
            .streaming_providers
            .iter()
            .filter(|p| p.enabled)
            .collect();
        if providers.is_empty() {
            if let Some(ref sp) = self.streaming_provider {
                if sp.enabled {
                    providers.push(sp);
                }
            }
        }
        providers
    }

    /// Streaming providers that want watchlist catalogs (service, short_name pairs).
    pub fn watchlist_providers(&self) -> Vec<(&str, &str)> {
        self.all_providers()
            .into_iter()
            .filter(|sp| sp.enable_watchlist_catalogs)
            .filter_map(|sp| short_name(&sp.service).map(|n| (sp.service.as_str(), n)))
            .collect()
    }

    /// Suffix to append to addon name, e.g. " RD+TRB".
    pub fn addon_name_suffix(&self) -> String {
        let parts: Vec<&str> = self
            .all_providers()
            .into_iter()
            .filter_map(|sp| short_name(&sp.service))
            .collect();
        if parts.is_empty() {
            String::new()
        } else {
            format!(" {}", parts.join("+"))
        }
    }

    /// Sort configuration for a specific catalog (sort, order).
    pub fn catalog_sort(&self, catalog_id: &str) -> (String, String) {
        self.catalog_configs
            .iter()
            .find(|c| c.catalog_id == catalog_id)
            .map(|c| {
                (
                    c.sort.clone().unwrap_or_else(|| "latest".into()),
                    c.order.clone().unwrap_or_else(|| "desc".into()),
                )
            })
            .unwrap_or_else(|| ("latest".into(), "desc".into()))
    }

    /// Whether any streaming providers are configured.
    pub fn has_providers(&self) -> bool {
        !self.all_providers().is_empty()
    }

    /// Find an enabled provider by its service name.
    pub fn get_provider_by_name(&self, name: &str) -> Option<&StreamingProvider> {
        self.all_providers()
            .into_iter()
            .find(|sp| sp.service == name)
    }

    /// First enabled streaming provider (primary).
    pub fn get_primary_provider(&self) -> Option<&StreamingProvider> {
        self.all_providers().into_iter().next()
    }

    /// Combine stream groups by type using user's `stg`/`sto`/`mxs` preferences.
    ///
    /// Mirrors Python's `_combine_streams_by_type`:
    /// - "mixed": round-robin interleave from each type in `sto` order
    /// - "separate" (default): concatenate each type in `sto` order
    /// - Apply `max_streams` cap at the end
    pub fn combine_streams_by_type<T: Clone>(
        &self,
        stream_groups: &std::collections::HashMap<&str, Vec<T>>,
    ) -> Vec<T> {
        if self.stream_type_grouping == "mixed" {
            // "mixed" mode is handled by the unified-sort path in stream.rs before reaching here.
            // This fallback concatenates in type order and applies the total cap.
            let mut combined: Vec<T> = Vec::new();
            for stream_type in &self.stream_type_order {
                if let Some(lst) = stream_groups.get(stream_type.as_str()) {
                    combined.extend_from_slice(lst);
                }
            }
            combined.truncate(self.max_streams as usize);
            combined
        } else {
            // "separate": concatenate in stream_type_order.
            // Each type was already capped at max_streams before this call — no re-cap.
            let mut combined: Vec<T> = Vec::new();
            for stream_type in &self.stream_type_order {
                if let Some(lst) = stream_groups.get(stream_type.as_str()) {
                    combined.extend_from_slice(lst);
                }
            }
            combined
        }
    }
}
