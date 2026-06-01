use crate::db::{MediaType, NudityStatus};

/// Provider-agnostic metadata contract consumed by [`super::metadata_store::store_media`].
#[derive(Debug, Clone)]
pub struct NormalizedMetadata {
    pub media_type: MediaType,
    pub title: String,
    pub original_title: Option<String>,
    pub year: Option<i32>,
    pub description: Option<String>,
    pub tagline: Option<String>,
    /// Calendar date as `YYYY-MM-DD`.
    pub release_date: Option<String>,
    pub runtime_minutes: Option<i32>,
    pub original_language: Option<String>,
    pub status: Option<String>,
    pub poster_url: Option<String>,
    pub backdrop_url: Option<String>,
    pub logo_url: Option<String>,
    pub genres: Vec<String>,
    /// `(provider, external_id)` pairs: tmdb, imdb, tvdb, mal, kitsu, anilist, …
    pub external_ids: Vec<(String, String)>,
    pub catalogs: Vec<String>,
    pub seasons: Vec<NormalizedSeason>,
    pub cast: Vec<NormalizedCastMember>,
    pub crew: Vec<NormalizedCrewMember>,
    pub trailers: Vec<NormalizedTrailer>,
    pub website: Option<String>,
    /// Series end air date (`YYYY-MM-DD`) → stored on `media.end_date`.
    pub end_date: Option<String>,
    pub country: Option<String>,
    /// Primary network name for series → `series_metadata.network`.
    pub network: Option<String>,
    pub aka_titles: Vec<NormalizedAkaTitle>,
    pub keywords: Vec<String>,
    pub ratings: Vec<NormalizedRating>,
    pub certificates: Vec<String>,
    pub popularity: Option<f64>,
    pub adult: bool,
    pub nudity_status: NudityStatus,
    /// Movie-only TMDB fields → `movie_metadata`.
    pub budget: Option<i64>,
    pub revenue: Option<i64>,
}

#[derive(Debug, Clone, Default)]
pub struct NormalizedCastMember {
    pub name: String,
    pub character: Option<String>,
    pub order: i32,
    pub tmdb_id: Option<i32>,
    pub imdb_id: Option<String>,
    pub profile_url: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct NormalizedCrewMember {
    pub name: String,
    pub department: Option<String>,
    pub job: Option<String>,
    pub tmdb_id: Option<i32>,
    pub imdb_id: Option<String>,
    pub profile_url: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct NormalizedTrailer {
    pub video_key: String,
    pub site: String,
    pub name: Option<String>,
    pub trailer_type: String,
    pub is_official: bool,
    pub is_primary: bool,
    pub size: Option<i32>,
}

#[derive(Debug, Clone, Default)]
pub struct NormalizedAkaTitle {
    pub title: String,
    pub language_code: Option<String>,
}

#[derive(Debug, Clone)]
pub struct NormalizedRating {
    /// Rating provider name: imdb, tmdb, mal, kitsu, …
    pub provider: String,
    pub rating: f64,
    pub vote_count: Option<i32>,
    /// Usually `user` (matches Python / IMDb dataset import).
    pub rating_type: String,
}

impl Default for NormalizedMetadata {
    fn default() -> Self {
        Self {
            media_type: MediaType::Movie,
            title: String::new(),
            original_title: None,
            year: None,
            description: None,
            tagline: None,
            release_date: None,
            runtime_minutes: None,
            original_language: None,
            status: None,
            poster_url: None,
            backdrop_url: None,
            logo_url: None,
            genres: vec![],
            external_ids: vec![],
            catalogs: vec![],
            seasons: vec![],
            cast: vec![],
            crew: vec![],
            trailers: vec![],
            website: None,
            end_date: None,
            country: None,
            network: None,
            aka_titles: vec![],
            keywords: vec![],
            ratings: vec![],
            certificates: vec![],
            popularity: None,
            adult: false,
            nudity_status: NudityStatus::Unknown,
            budget: None,
            revenue: None,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct NormalizedSeason {
    pub season_number: i32,
    pub name: Option<String>,
    pub overview: Option<String>,
    pub air_date: Option<String>,
    pub episodes: Vec<NormalizedEpisode>,
}

#[derive(Debug, Clone, Default)]
pub struct NormalizedEpisode {
    pub episode_number: i32,
    pub title: String,
    pub overview: Option<String>,
    pub air_date: Option<String>,
    pub runtime_minutes: Option<i32>,
    pub still_url: Option<String>,
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<i32>,
    pub tvdb_id: Option<i32>,
}

/// Flags for media creation paths (user import, sports stub, refresh update, …).
#[derive(Debug, Clone)]
pub struct StoreMediaOpts {
    pub is_user_created: bool,
    pub created_by_user_id: Option<i32>,
    pub is_add_title_to_poster: bool,
    pub is_public: bool,
    /// When set, upsert this row instead of resolving by external id / title match.
    pub existing_media_id: Option<crate::db::MediaId>,
}

impl Default for StoreMediaOpts {
    fn default() -> Self {
        Self {
            is_user_created: false,
            created_by_user_id: None,
            is_add_title_to_poster: false,
            is_public: true,
            existing_media_id: None,
        }
    }
}

impl StoreMediaOpts {
    pub fn user_created(user_id: i32, is_public: bool) -> Self {
        Self {
            is_user_created: true,
            created_by_user_id: Some(user_id),
            is_public,
            ..Self::default()
        }
    }

    pub fn sports_stub() -> Self {
        Self {
            is_add_title_to_poster: true,
            ..Self::default()
        }
    }

    pub fn refresh(media_id: crate::db::MediaId) -> Self {
        Self {
            existing_media_id: Some(media_id),
            ..Self::default()
        }
    }
}

impl NormalizedMetadata {
    pub fn external_id(&self, provider: &str) -> Option<&str> {
        self.external_ids
            .iter()
            .find(|(p, _)| p == provider)
            .map(|(_, id)| id.as_str())
    }

    pub fn apply_overrides(
        &mut self,
        title: Option<&str>,
        year: Option<i32>,
        poster: Option<&str>,
        background: Option<&str>,
        release_date: Option<&str>,
    ) {
        if let Some(t) = title.filter(|s| !s.is_empty()) {
            self.title = t.to_string();
        }
        if let Some(y) = year {
            self.year = Some(y);
        }
        if let Some(p) = poster.filter(|s| !s.is_empty()) {
            self.poster_url = Some(p.to_string());
        }
        if let Some(b) = background.filter(|s| !s.is_empty()) {
            self.backdrop_url = Some(b.to_string());
        }
        if let Some(d) = release_date.filter(|s| !s.is_empty()) {
            self.release_date = Some(d.to_string());
        }
    }
}
