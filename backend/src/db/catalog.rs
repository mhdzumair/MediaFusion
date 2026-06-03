use sqlx::PgPool;
use tracing::warn;

use super::types::{nudity_statuses_from_filter, MediaId, MediaType, UserId};

const LIMIT: i64 = 100;
const WATCHLIST_LIMIT: i64 = 25;

#[derive(sqlx::FromRow, Debug)]
pub struct CatalogRow {
    pub media_id: MediaId,
    pub media_type: MediaType,
    pub title: String,
    pub year: Option<i32>,
    pub end_year: Option<i32>,
    pub description: Option<String>,
    pub imdb_id: Option<String>,
    pub poster_url: Option<String>,
}

fn order_clause(sort: &str, sort_dir: &str) -> String {
    let sort_dir = if sort_dir == "asc" { "ASC" } else { "DESC" };
    let nulls = if sort_dir == "ASC" {
        "NULLS FIRST"
    } else {
        "NULLS LAST"
    };
    match sort {
        "popular" => format!(
            "m.popularity {sort_dir} {nulls}, m.total_streams {sort_dir} {nulls}, m.last_stream_added {sort_dir} {nulls}, m.id ASC"
        ),
        "rating" => format!(
            "(SELECT mr2.rating FROM media_rating mr2 JOIN rating_provider rp2 ON rp2.id = mr2.rating_provider_id WHERE mr2.media_id = m.id AND rp2.name = 'imdb' LIMIT 1) {sort_dir} {nulls}, m.total_streams {sort_dir} {nulls}, m.id ASC"
        ),
        "year" => format!("m.year {sort_dir} {nulls}, m.id ASC"),
        "title" => format!("m.title {sort_dir}, m.id ASC"),
        "release_date" => format!(
            "COALESCE(m.release_date, make_date(m.year, 12, 31)) {sort_dir} {nulls}, m.id ASC"
        ),
        _ => format!("m.last_stream_added {sort_dir} {nulls}, m.id ASC"),
    }
}

pub struct CatalogQuery<'a> {
    pub catalog_id: &'a str,
    pub media_type: &'a str,
    pub skip: i64,
    pub genre: Option<&'a str>,
    pub nudity_excludes: &'a [String],
    /// Parental certificate names to exclude (e.g. ["Adults+"]). Empty = no filter.
    pub cert_excludes: &'a [String],
    pub sort: &'a str,
    pub sort_dir: &'a str,
    /// Set for my_library_* catalogs; filters by user_library_item.user_id.
    pub user_id: Option<UserId>,
}

pub async fn get_catalog_items(pool: &PgPool, q: CatalogQuery<'_>) -> Vec<CatalogRow> {
    let CatalogQuery {
        catalog_id,
        media_type,
        skip,
        genre,
        nudity_excludes,
        cert_excludes,
        sort,
        sort_dir,
        user_id,
    } = q;

    let Some(mt) = MediaType::from_wire(media_type) else {
        return vec![];
    };

    // my_library_* catalogs join through user_library_item instead of catalog/media_catalog_link.
    if catalog_id.starts_with("my_library_") {
        return get_library_items(pool, mt, skip, nudity_excludes, cert_excludes, user_id).await;
    }

    let nudity_exclude_enums = nudity_statuses_from_filter(nudity_excludes);
    let ord = order_clause(sort, sort_dir);
    let sql = format!(
        r#"
        SELECT
            m.id AS media_id,
            m.type AS media_type,
            m.title,
            m.year,
            EXTRACT(YEAR FROM m.end_date)::int AS end_year,
            m.description,
            mei.external_id AS imdb_id,
            mi.url AS poster_url
        FROM media m
        JOIN media_catalog_link mcl ON mcl.media_id = m.id
        JOIN catalog c ON c.id = mcl.catalog_id AND c.name = $1
        LEFT JOIN media_external_id mei ON mei.media_id = m.id AND mei.provider = 'imdb'
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi ON true
        WHERE m.type = $2
          AND m.total_streams > 0
          AND NOT m.is_blocked
          AND ($4::text IS NULL OR EXISTS (
              SELECT 1 FROM media_genre_link mgl
              JOIN genre g ON g.id = mgl.genre_id
              WHERE mgl.media_id = m.id AND g.name = $4
          ))
          AND (cardinality($5::nuditystatus[]) = 0 OR m.nudity_status <> ALL($5))
          AND (cardinality($6::text[]) IS NULL OR NOT EXISTS (
              SELECT 1 FROM media_parental_certificate_link mpcl
              JOIN parental_certificate pc ON pc.id = mpcl.certificate_id
              WHERE mpcl.media_id = m.id AND pc.name = ANY($6)
          ))
        ORDER BY {ord}
        LIMIT {LIMIT} OFFSET $3
        "#
    );

    sqlx::query_as::<_, CatalogRow>(&sql)
        .bind(catalog_id) // $1
        .bind(mt) // $2
        .bind(skip) // $3
        .bind(genre) // $4 - Option<&str> → NULL when None
        .bind(&nudity_exclude_enums) // $5 - nuditystatus[]
        .bind(cert_excludes) // $6 - &[String] → text[]
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!("catalog query [{catalog_id}]: {e}");
            vec![]
        })
}

async fn get_library_items(
    pool: &PgPool,
    media_type: MediaType,
    skip: i64,
    nudity_excludes: &[String],
    cert_excludes: &[String],
    user_id: Option<UserId>,
) -> Vec<CatalogRow> {
    let Some(uid) = user_id else {
        return vec![];
    };
    let nudity_exclude_enums = nudity_statuses_from_filter(nudity_excludes);
    sqlx::query_as::<_, CatalogRow>(
        r#"
        SELECT
            m.id AS media_id,
            m.type AS media_type,
            m.title,
            m.year,
            EXTRACT(YEAR FROM m.end_date)::int AS end_year,
            m.description,
            mei.external_id AS imdb_id,
            mi.url AS poster_url
        FROM user_library_item uli
        JOIN media m ON m.id = uli.media_id
        LEFT JOIN media_external_id mei ON mei.media_id = m.id AND mei.provider = 'imdb'
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi ON true
        WHERE uli.user_id = $1
          AND m.type = $2
          AND NOT m.is_blocked
          AND (cardinality($3::nuditystatus[]) = 0 OR m.nudity_status <> ALL($3))
          AND (cardinality($5::text[]) IS NULL OR NOT EXISTS (
              SELECT 1 FROM media_parental_certificate_link mpcl
              JOIN parental_certificate pc ON pc.id = mpcl.certificate_id
              WHERE mpcl.media_id = m.id AND pc.name = ANY($5)
          ))
        ORDER BY uli.added_at DESC
        LIMIT 100 OFFSET $4
        "#,
    )
    .bind(uid) // $1
    .bind(media_type) // $2
    .bind(&nudity_exclude_enums) // $3
    .bind(skip) // $4
    .bind(cert_excludes) // $5
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!(
            "library query [uid={uid} type={}]: {e}",
            media_type.as_wire()
        );
        vec![]
    })
}

/// Watchlist catalog rows: media linked to any of the given torrent info_hashes.
pub async fn get_watchlist_items(
    pool: &PgPool,
    media_type: &str,
    info_hashes: &[String],
    skip: i64,
    nudity_excludes: &[String],
    cert_excludes: &[String],
    sort: &str,
    sort_dir: &str,
) -> Vec<CatalogRow> {
    if info_hashes.is_empty() {
        return vec![];
    }

    let Some(mt) = MediaType::from_wire(media_type) else {
        return vec![];
    };

    let info_hashes_lower: Vec<String> = info_hashes.iter().map(|h| h.to_lowercase()).collect();
    let nudity_exclude_enums = nudity_statuses_from_filter(nudity_excludes);
    let ord = order_clause(sort, sort_dir);
    let sql = format!(
        r#"
        SELECT
            m.id AS media_id,
            m.type AS media_type,
            m.title,
            m.year,
            EXTRACT(YEAR FROM m.end_date)::int AS end_year,
            m.description,
            mei.external_id AS imdb_id,
            mi.url AS poster_url
        FROM media m
        JOIN stream_media_link sml ON sml.media_id = m.id
        JOIN stream s ON s.id = sml.stream_id
        JOIN torrent_stream ts ON ts.stream_id = s.id
        LEFT JOIN media_external_id mei ON mei.media_id = m.id AND mei.provider = 'imdb'
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi ON true
        WHERE m.type = $1
          AND m.total_streams > 0
          AND NOT m.is_blocked
          AND lower(ts.info_hash) = ANY($2)
          AND (cardinality($3::nuditystatus[]) = 0 OR m.nudity_status <> ALL($3))
          AND (cardinality($4::text[]) IS NULL OR NOT EXISTS (
              SELECT 1 FROM media_parental_certificate_link mpcl
              JOIN parental_certificate pc ON pc.id = mpcl.certificate_id
              WHERE mpcl.media_id = m.id AND pc.name = ANY($4)
          ))
        GROUP BY m.id, m.type, m.title, m.year, m.end_date, m.description, mei.external_id, mi.url
        ORDER BY {ord}
        LIMIT {WATCHLIST_LIMIT} OFFSET $5
        "#
    );

    sqlx::query_as::<_, CatalogRow>(&sql)
        .bind(mt) // $1
        .bind(&info_hashes_lower) // $2
        .bind(&nudity_exclude_enums) // $3
        .bind(cert_excludes) // $4
        .bind(skip) // $5
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!("watchlist catalog query [type={media_type}]: {e}");
            vec![]
        })
}

pub async fn search_metadata(
    pool: &PgPool,
    media_type: &str,
    query: &str,
    skip: i64,
    nudity_excludes: &[String],
    cert_excludes: &[String],
) -> Vec<CatalogRow> {
    let Some(mt) = MediaType::from_wire(media_type) else {
        return vec![];
    };

    let nudity_exclude_enums = nudity_statuses_from_filter(nudity_excludes);
    sqlx::query_as::<_, CatalogRow>(
        r#"
        SELECT
            m.id AS media_id,
            m.type AS media_type,
            m.title,
            m.year,
            EXTRACT(YEAR FROM m.end_date)::int AS end_year,
            m.description,
            mei.external_id AS imdb_id,
            mi.url AS poster_url
        FROM media m
        LEFT JOIN media_external_id mei ON mei.media_id = m.id AND mei.provider = 'imdb'
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi ON true
        WHERE m.type = $1
          AND m.total_streams > 0
          AND NOT m.is_blocked
          AND (cardinality($3::nuditystatus[]) = 0 OR m.nudity_status <> ALL($3))
          AND (cardinality($5::text[]) IS NULL OR NOT EXISTS (
              SELECT 1 FROM media_parental_certificate_link mpcl
              JOIN parental_certificate pc ON pc.id = mpcl.certificate_id
              WHERE mpcl.media_id = m.id AND pc.name = ANY($5)
          ))
          AND m.id IN (
              -- Phase 1: FTS on main title (uses GIN index)
              SELECT m2.id FROM media m2
              WHERE m2.title_tsv @@ plainto_tsquery('simple', $2)
                AND m2.type = $1
              UNION
              -- Phase 2: FTS on aka_title
              SELECT at2.media_id FROM aka_title at2
              WHERE at2.title_tsv @@ plainto_tsquery('simple', $2)
              UNION
              -- Phase 3: trigram similarity (uses GIN gin_trgm_ops index)
              SELECT m3.id FROM media m3
              WHERE m3.title % $2
                AND m3.type = $1
          )
        ORDER BY
            ts_rank_cd(m.title_tsv, plainto_tsquery('simple', $2)) DESC,
            m.total_streams DESC
        LIMIT 100 OFFSET $4
        "#,
    )
    .bind(mt) // $1
    .bind(query) // $2
    .bind(&nudity_exclude_enums) // $3
    .bind(skip) // $4
    .bind(cert_excludes) // $5
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("search query [type={media_type} q={query}]: {e}");
        vec![]
    })
}

/// Filter MDBList IMDb ids through PostgreSQL with parental-guide filters and stream availability.
pub async fn get_mdblist_filtered_items(
    pool: &PgPool,
    media_type: MediaType,
    imdb_ids: &[String],
    skip: i64,
    limit: i64,
    nudity_excludes: &[String],
    cert_excludes: &[String],
) -> Vec<CatalogRow> {
    if imdb_ids.is_empty() {
        return vec![];
    }

    let nudity_exclude_enums = nudity_statuses_from_filter(nudity_excludes);
    let type_join = match media_type {
        MediaType::Movie => "JOIN movie_metadata mm ON mm.media_id = m.id",
        MediaType::Series => "JOIN series_metadata sm ON sm.media_id = m.id",
        _ => return vec![],
    };

    let sql = format!(
        r#"
        SELECT
            m.id AS media_id,
            m.type AS media_type,
            m.title,
            m.year,
            EXTRACT(YEAR FROM m.end_date)::int AS end_year,
            m.description,
            mei.external_id AS imdb_id,
            mi.url AS poster_url
        FROM media m
        JOIN media_external_id mei ON mei.media_id = m.id AND mei.provider = 'imdb'
        {type_join}
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi ON true
        WHERE mei.external_id = ANY($1)
          AND m.type = $2
          AND m.total_streams > 0
          AND (cardinality($3::nuditystatus[]) = 0 OR m.nudity_status <> ALL($3))
          AND (cardinality($4::text[]) IS NULL OR NOT EXISTS (
              SELECT 1 FROM media_parental_certificate_link mpcl
              JOIN parental_certificate pc ON pc.id = mpcl.certificate_id
              WHERE mpcl.media_id = m.id AND pc.name = ANY($4)
          ))
        ORDER BY m.last_stream_added DESC NULLS LAST, m.id ASC
        LIMIT $5 OFFSET $6
        "#
    );

    sqlx::query_as::<_, CatalogRow>(&sql)
        .bind(imdb_ids)
        .bind(media_type)
        .bind(&nudity_exclude_enums)
        .bind(cert_excludes)
        .bind(limit)
        .bind(skip)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!(
                "mdblist filtered query [type={}]: {e}",
                media_type.as_wire()
            );
            vec![]
        })
}
