use std::collections::HashMap;
use std::sync::LazyLock;

use sqlx::PgPool;
use tracing::warn;

use super::metadata_store::link_genre;
use super::types::MediaType;

static CERTIFICATION_MAPPING: LazyLock<HashMap<String, Vec<String>>> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../resources/json/certification_mapping.json"
    ))
    .unwrap_or_default()
});

/// Expand a UI parental-certificate level (e.g. "PG-13") into concrete certificate names.
pub fn expand_parental_certificate_selection(value: &str) -> Vec<String> {
    let normalized = value.trim();
    if normalized.is_empty() {
        return Vec::new();
    }

    if let Some(mapped) = CERTIFICATION_MAPPING.get(normalized) {
        let mut expanded = Vec::new();
        for certificate in mapped {
            let name = certificate.trim();
            if !name.is_empty() && !expanded.iter().any(|existing| existing == name) {
                expanded.push(name.to_string());
            }
        }
        return expanded;
    }

    // Backward compatibility for older suggestions that stored raw CSV values.
    if normalized.contains(',') {
        return parse_comma_list(normalized);
    }
    vec![normalized.to_string()]
}

fn parse_comma_list(value: &str) -> Vec<String> {
    value
        .split(',')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .map(str::to_string)
        .collect()
}

async fn ensure_metadata_provider(pool: &PgPool, name: &str) -> Option<i32> {
    sqlx::query_scalar(
        r#"
        INSERT INTO metadata_provider (name, display_name, is_external, is_active, priority, default_priority, created_at)
        VALUES ($1, $1, false, true, 100, 100, NOW())
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        "#,
    )
    .bind(name)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
}

async fn media_type_for(pool: &PgPool, media_id: i32) -> Option<MediaType> {
    sqlx::query_scalar("SELECT type FROM media WHERE id = $1")
        .bind(media_id)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
}

async fn get_or_create_person_by_name(pool: &PgPool, name: &str) -> Option<i32> {
    if let Ok(Some(id)) = sqlx::query_scalar("SELECT id FROM person WHERE name = $1 LIMIT 1")
        .bind(name)
        .fetch_optional(pool)
        .await
    {
        return Some(id);
    }

    if let Ok(Some(id)) = sqlx::query_scalar(
        "SELECT id FROM person WHERE lower(name) = lower($1) ORDER BY id LIMIT 1",
    )
    .bind(name)
    .fetch_optional(pool)
    .await
    {
        return Some(id);
    }

    sqlx::query_scalar(
        r#"
        INSERT INTO person (name, created_at, updated_at)
        VALUES ($1, NOW(), NOW())
        RETURNING id
        "#,
    )
    .bind(name)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
}

/// Replace the primary poster or background image for a media row (user-submitted URL).
pub async fn replace_primary_image_for_media(
    pool: &PgPool,
    media_id: i32,
    image_type: &str,
    url: &str,
) {
    let normalized = url.trim();
    let delete_types: &[&str] = if image_type == "background" {
        &["background", "backdrop"]
    } else {
        &[image_type]
    };

    if let Err(e) =
        sqlx::query("DELETE FROM media_image WHERE media_id = $1 AND image_type = ANY($2::text[])")
            .bind(media_id)
            .bind(delete_types)
            .execute(pool)
            .await
    {
        warn!("replace_primary_image_for_media delete media_id={media_id}: {e}");
        return;
    }

    if normalized.is_empty() {
        return;
    }

    let Some(provider_id) = ensure_metadata_provider(pool, "user").await else {
        warn!("replace_primary_image_for_media: could not resolve user metadata provider");
        return;
    };

    if let Err(e) = sqlx::query(
        "INSERT INTO media_image \
         (media_id, provider_id, image_type, url, is_primary, display_order) \
         VALUES ($1, $2, $3, $4, true, 0) \
         ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING",
    )
    .bind(media_id)
    .bind(provider_id)
    .bind(image_type)
    .bind(normalized)
    .execute(pool)
    .await
    {
        warn!("replace_primary_image_for_media insert media_id={media_id}: {e}");
    }
}

/// Replace all genre links for a media row.
pub async fn replace_genres_for_media(pool: &PgPool, media_id: i32, genre_names: &[String]) {
    let Some(media_type) = media_type_for(pool, media_id).await else {
        warn!("replace_genres_for_media: media {media_id} not found");
        return;
    };

    if let Err(e) = sqlx::query("DELETE FROM media_genre_link WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await
    {
        warn!("replace_genres_for_media delete media_id={media_id}: {e}");
        return;
    }

    for name in genre_names {
        if !name.is_empty() {
            link_genre(pool, media_id, name, media_type).await;
        }
    }
}

/// Replace all AKA titles for a media row.
pub async fn replace_aka_titles_for_media(pool: &PgPool, media_id: i32, titles: &[String]) {
    if let Err(e) = sqlx::query("DELETE FROM aka_title WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await
    {
        warn!("replace_aka_titles_for_media delete media_id={media_id}: {e}");
        return;
    }

    let mut seen = std::collections::HashSet::new();
    for title in titles {
        if title.is_empty() || !seen.insert(title.clone()) {
            continue;
        }
        if let Err(e) = sqlx::query(
            "INSERT INTO aka_title (media_id, title) VALUES ($1, $2) \
             ON CONFLICT (media_id, title) DO NOTHING",
        )
        .bind(media_id)
        .bind(title)
        .execute(pool)
        .await
        {
            warn!("replace_aka_titles_for_media insert media_id={media_id}: {e}");
        }
    }
}

/// Replace all cast members for a media row (names only, in display order).
pub async fn replace_cast_for_media(pool: &PgPool, media_id: i32, names: &[String]) {
    if let Err(e) = sqlx::query("DELETE FROM media_cast WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await
    {
        warn!("replace_cast_for_media delete media_id={media_id}: {e}");
        return;
    }

    for (index, name) in names.iter().enumerate() {
        if name.is_empty() {
            continue;
        }
        let Some(person_id) = get_or_create_person_by_name(pool, name).await else {
            continue;
        };
        if let Err(e) = sqlx::query(
            "INSERT INTO media_cast (media_id, person_id, display_order) VALUES ($1, $2, $3)",
        )
        .bind(media_id)
        .bind(person_id)
        .bind(index as i32)
        .execute(pool)
        .await
        {
            warn!("replace_cast_for_media insert media_id={media_id}: {e}");
        }
    }
}

/// Replace director crew entries for a media row.
pub async fn replace_directors_for_media(pool: &PgPool, media_id: i32, names: &[String]) {
    if let Err(e) =
        sqlx::query("DELETE FROM media_crew WHERE media_id = $1 AND lower(job) = 'director'")
            .bind(media_id)
            .execute(pool)
            .await
    {
        warn!("replace_directors_for_media delete media_id={media_id}: {e}");
        return;
    }

    for name in names {
        if name.is_empty() {
            continue;
        }
        let Some(person_id) = get_or_create_person_by_name(pool, name).await else {
            continue;
        };
        if let Err(e) = sqlx::query(
            "INSERT INTO media_crew (media_id, person_id, department, job) \
             VALUES ($1, $2, 'Directing', 'Director')",
        )
        .bind(media_id)
        .bind(person_id)
        .execute(pool)
        .await
        {
            warn!("replace_directors_for_media insert media_id={media_id}: {e}");
        }
    }
}

/// Replace writer crew entries for a media row.
pub async fn replace_writers_for_media(pool: &PgPool, media_id: i32, names: &[String]) {
    if let Err(e) = sqlx::query(
        "DELETE FROM media_crew WHERE media_id = $1 \
         AND lower(job) IN ('writer', 'screenplay', 'story')",
    )
    .bind(media_id)
    .execute(pool)
    .await
    {
        warn!("replace_writers_for_media delete media_id={media_id}: {e}");
        return;
    }

    for name in names {
        if name.is_empty() {
            continue;
        }
        let Some(person_id) = get_or_create_person_by_name(pool, name).await else {
            continue;
        };
        if let Err(e) = sqlx::query(
            "INSERT INTO media_crew (media_id, person_id, department, job) \
             VALUES ($1, $2, 'Writing', 'Writer')",
        )
        .bind(media_id)
        .bind(person_id)
        .execute(pool)
        .await
        {
            warn!("replace_writers_for_media insert media_id={media_id}: {e}");
        }
    }
}

/// Replace all parental certificate links for a media row.
pub async fn replace_parental_certificates_for_media(
    pool: &PgPool,
    media_id: i32,
    certificate_names: &[String],
) {
    if let Err(e) = sqlx::query("DELETE FROM media_parental_certificate_link WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await
    {
        warn!("replace_parental_certificates_for_media delete media_id={media_id}: {e}");
        return;
    }

    for cert in certificate_names {
        if cert.is_empty() {
            continue;
        }
        let cert_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO parental_certificate (name) VALUES ($1) \
             ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name \
             RETURNING id",
        )
        .bind(cert)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();

        if let Some(cid) = cert_id
            && let Err(e) = sqlx::query(
                "INSERT INTO media_parental_certificate_link (media_id, certificate_id) \
                 VALUES ($1, $2) ON CONFLICT DO NOTHING",
            )
            .bind(media_id)
            .bind(cid)
            .execute(pool)
            .await
        {
            warn!("replace_parental_certificates_for_media insert media_id={media_id}: {e}");
        }
    }
}

/// Update TV country metadata (TV media type only).
pub async fn update_tv_country_for_media(pool: &PgPool, media_id: i32, country: &str) {
    let Some(media_type) = media_type_for(pool, media_id).await else {
        warn!("update_tv_country_for_media: media {media_id} not found");
        return;
    };
    if media_type != MediaType::Tv {
        warn!("update_tv_country_for_media: media {media_id} is not TV type");
        return;
    }

    let country_val = country.trim();
    let country_opt = if country_val.is_empty() {
        None
    } else {
        Some(country_val)
    };

    if let Err(e) = sqlx::query(
        r#"
        INSERT INTO tv_metadata (media_id, country, created_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (media_id) DO UPDATE SET
            country = EXCLUDED.country,
            updated_at = NOW()
        "#,
    )
    .bind(media_id)
    .bind(country_opt)
    .execute(pool)
    .await
    {
        warn!("update_tv_country_for_media media_id={media_id}: {e}");
    }
}

/// Update TV language metadata (TV media type only).
pub async fn update_tv_language_for_media(pool: &PgPool, media_id: i32, language: &str) {
    let Some(media_type) = media_type_for(pool, media_id).await else {
        warn!("update_tv_language_for_media: media {media_id} not found");
        return;
    };
    if media_type != MediaType::Tv {
        warn!("update_tv_language_for_media: media {media_id} is not TV type");
        return;
    }

    let language_val = language.trim();
    let language_opt = if language_val.is_empty() {
        None
    } else {
        Some(language_val)
    };

    if let Err(e) = sqlx::query(
        r#"
        INSERT INTO tv_metadata (media_id, tv_language, created_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (media_id) DO UPDATE SET
            tv_language = EXCLUDED.tv_language,
            updated_at = NOW()
        "#,
    )
    .bind(media_id)
    .bind(language_opt)
    .execute(pool)
    .await
    {
        warn!("update_tv_language_for_media media_id={media_id}: {e}");
    }
}

#[cfg(test)]
mod tests {
    use super::expand_parental_certificate_selection;

    #[test]
    fn expand_parental_certificate_maps_ui_level() {
        let expanded = expand_parental_certificate_selection("Teens");
        assert!(expanded.contains(&"PG-13".to_string()));
        assert!(expanded.len() > 1);
    }

    #[test]
    fn expand_parental_certificate_passthrough_unknown() {
        assert_eq!(
            expand_parental_certificate_selection("Custom Cert"),
            vec!["Custom Cert"]
        );
    }

    #[test]
    fn expand_parental_certificate_csv_fallback() {
        assert_eq!(
            expand_parental_certificate_selection("R, NC-17"),
            vec!["R", "NC-17"]
        );
    }

    #[test]
    fn expand_parental_certificate_empty() {
        assert!(expand_parental_certificate_selection("").is_empty());
        assert!(expand_parental_certificate_selection("   ").is_empty());
    }
}
