use serde::{Deserialize, Serialize};
use serde_json::Value;

// ─── Manifest ─────────────────────────────────────────────────────────────────

#[derive(Serialize)]
pub struct Manifest {
    pub id: String,
    pub version: String,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none", rename = "contactEmail")]
    pub contact_email: Option<String>,
    pub description: String,
    pub logo: String,
    #[serde(rename = "behaviorHints")]
    pub behavior_hints: ManifestBehaviorHints,
    /// Mixed array: "catalog" (plain string) + typed resource objects.
    pub resources: Vec<Value>,
    pub types: Vec<String>,
    pub catalogs: Vec<CatalogDef>,
}

#[derive(Serialize)]
pub struct ManifestBehaviorHints {
    pub configurable: bool,
    #[serde(rename = "configurationRequired")]
    pub configuration_required: bool,
}

#[derive(Serialize)]
pub struct CatalogDef {
    pub id: String,
    #[serde(rename = "type")]
    pub catalog_type: String,
    pub name: String,
    pub extra: Vec<ExtraField>,
}

#[derive(Serialize)]
pub struct ExtraField {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none", rename = "isRequired")]
    pub is_required: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub options: Option<Vec<String>>,
}

// ─── Catalog/Metas ────────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize)]
pub struct Metas {
    pub metas: Vec<MetaPreview>,
}

#[derive(Serialize, Deserialize)]
pub struct MetaPreview {
    pub id: String,
    #[serde(rename = "type")]
    pub media_type: String,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none", rename = "releaseInfo")]
    pub release_info: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub poster: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub background: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

// ─── Meta ─────────────────────────────────────────────────────────────────────

#[derive(Serialize)]
pub struct MetaItem {
    pub meta: Meta,
}

#[derive(Serialize)]
pub struct Meta {
    pub id: String,
    #[serde(rename = "type")]
    pub media_type: String,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none", rename = "releaseInfo")]
    pub release_info: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub poster: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub background: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub logo: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub website: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub country: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub genres: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub cast: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "imdbRating")]
    pub imdb_rating: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub videos: Vec<Video>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub links: Option<Vec<Value>>,
}

#[derive(Serialize)]
pub struct Video {
    pub id: String,
    pub title: String,
    pub released: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub overview: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thumbnail: Option<String>,
    pub season: i32,
    pub episode: i32,
}
