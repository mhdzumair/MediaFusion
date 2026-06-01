use std::collections::{HashMap, HashSet};

use sqlx::PgPool;
use tokio::try_join;
use tracing::warn;

use super::types::MediaType;

#[derive(sqlx::FromRow)]
struct MediaTypeRow {
    id: i32,
    media_type: MediaType,
}

#[derive(sqlx::FromRow)]
struct GenreNameRow {
    id: i32,
    name: String,
}

#[derive(sqlx::FromRow)]
struct GenreLinkRow {
    genre_id: i32,
    media_id: i32,
}

fn is_manifest_media_type(media_type: MediaType) -> bool {
    matches!(
        media_type,
        MediaType::Movie | MediaType::Series | MediaType::Tv
    )
}

pub async fn get_all_genres_by_type(pool: &PgPool) -> HashMap<String, Vec<String>> {
    let media_future =
        sqlx::query_as::<_, MediaTypeRow>("SELECT id, type AS media_type FROM media")
            .fetch_all(pool);
    let genre_future =
        sqlx::query_as::<_, GenreNameRow>("SELECT id, name FROM genre").fetch_all(pool);
    let links_future =
        sqlx::query_as::<_, GenreLinkRow>("SELECT genre_id, media_id FROM media_genre_link")
            .fetch_all(pool);

    let (media_rows, genre_rows, link_rows) =
        match try_join!(media_future, genre_future, links_future) {
            Ok(rows) => rows,
            Err(e) => {
                warn!("genres query: {e}");
                return HashMap::new();
            }
        };

    let media_types: HashMap<i32, MediaType> = media_rows
        .into_iter()
        .map(|row| (row.id, row.media_type))
        .collect();
    let genre_names: HashMap<i32, String> = genre_rows
        .into_iter()
        .map(|row| (row.id, row.name))
        .collect();

    let mut by_type: HashMap<String, HashSet<String>> = HashMap::new();
    for link in link_rows {
        let Some(media_type) = media_types.get(&link.media_id) else {
            continue;
        };
        if !is_manifest_media_type(*media_type) {
            continue;
        }
        let Some(genre_name) = genre_names.get(&link.genre_id) else {
            continue;
        };
        by_type
            .entry(media_type.as_wire().to_string())
            .or_default()
            .insert(genre_name.clone());
    }

    by_type
        .into_iter()
        .map(|(media_type, genres)| {
            let mut list: Vec<String> = genres.into_iter().collect();
            list.sort_unstable();
            (media_type, list)
        })
        .collect()
}
