use crate::models::{stremio::Metas, user_data::UserData};

/// Overlay RPDB poster URLs for IMDb-backed meta items (Python `update_rpdb_posters`).
pub fn apply_rpdb_posters(metas: &mut Metas, user_data: &UserData, catalog_type: &str) {
    if !matches!(catalog_type, "movie" | "series") {
        return;
    }
    let Some(api_key) = user_data.rpdb_api_key() else {
        return;
    };
    let base = format!(
        "https://api.ratingposterdb.com/{api_key}/imdb/poster-default/{{}}.jpg?fallback=true"
    );
    for meta in &mut metas.metas {
        if meta.id.starts_with("tt") {
            meta.poster = Some(base.replace("{}", &meta.id));
        }
    }
}

pub fn needs_rpdb_poster_mutation(user_data: &UserData, catalog_type: &str) -> bool {
    matches!(catalog_type, "movie" | "series") && user_data.rpdb_api_key().is_some()
}
