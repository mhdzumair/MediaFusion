//! Shared types for IMDb dataset import.

pub const STATUS_REDIS_KEY: &str = "imdb_import:status";

/// IMDb dataset definition: staging table + TSV file name + column list for COPY.
pub struct DatasetDef {
    pub key: &'static str,
    pub file_name: &'static str,
    pub staging_table: &'static str,
    pub copy_columns: &'static str,
}

pub const ALL_DATASETS: &[DatasetDef] = &[
    DatasetDef {
        key: "basics",
        file_name: "title.basics.tsv.gz",
        staging_table: "imdb_stage_basics",
        copy_columns: "tconst, title_type, primary_title, original_title, is_adult, start_year, end_year, runtime_minutes, genres",
    },
    DatasetDef {
        key: "names",
        file_name: "name.basics.tsv.gz",
        staging_table: "imdb_stage_names",
        copy_columns: "nconst, primary_name, birth_year, death_year, primary_profession, known_for_titles",
    },
    DatasetDef {
        key: "ratings",
        file_name: "title.ratings.tsv.gz",
        staging_table: "imdb_stage_ratings",
        copy_columns: "tconst, average_rating, num_votes",
    },
    DatasetDef {
        key: "akas",
        file_name: "title.akas.tsv.gz",
        staging_table: "imdb_stage_akas",
        copy_columns: "title_id, ordering, title, region, language, types, attributes, is_original_title",
    },
    DatasetDef {
        key: "episode",
        file_name: "title.episode.tsv.gz",
        staging_table: "imdb_stage_episode",
        copy_columns: "tconst, parent_tconst, season_number, episode_number",
    },
    DatasetDef {
        key: "crew",
        file_name: "title.crew.tsv.gz",
        staging_table: "imdb_stage_crew",
        copy_columns: "tconst, directors, writers",
    },
    DatasetDef {
        key: "principals",
        file_name: "title.principals.tsv.gz",
        staging_table: "imdb_stage_principals",
        copy_columns: "tconst, ordering, nconst, category, job, characters",
    },
];

/// Default processing order (respects FK dependencies).
pub const DEFAULT_ORDER: &[&str] = &[
    "basics",
    "names",
    "ratings",
    "akas",
    "episode",
    "crew",
    "principals",
];

pub fn dataset_by_key(key: &str) -> Option<&'static DatasetDef> {
    ALL_DATASETS.iter().find(|d| d.key == key)
}

pub fn resolve_datasets(
    requested: Option<&[String]>,
    config_allowlist: &[String],
) -> Vec<&'static DatasetDef> {
    let keys: Vec<&str> = if let Some(req) = requested {
        req.iter().map(|s| s.as_str()).collect()
    } else if !config_allowlist.is_empty() {
        config_allowlist.iter().map(|s| s.as_str()).collect()
    } else {
        DEFAULT_ORDER.to_vec()
    };

    let mut ordered: Vec<&'static DatasetDef> = DEFAULT_ORDER
        .iter()
        .filter_map(|k| {
            if keys.iter().any(|r| r == k) {
                dataset_by_key(k)
            } else {
                None
            }
        })
        .collect();

    // Include any extra keys not in DEFAULT_ORDER (shouldn't happen, but be safe).
    for key in keys {
        if !ordered.iter().any(|d| d.key == key)
            && let Some(d) = dataset_by_key(key)
        {
            ordered.push(d);
        }
    }

    ordered
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ImportStatus {
    pub phase: String,
    pub dataset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub merge_step: Option<String>,
    pub rows_loaded: Option<i64>,
    pub rows_merged: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rows_processed: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rows_total: Option<i64>,
    pub started_at: String,
    pub message: Option<String>,
}
