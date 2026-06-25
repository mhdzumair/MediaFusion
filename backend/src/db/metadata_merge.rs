use std::collections::{HashMap, HashSet};

use crate::db::{
    NormalizedAkaTitle, NormalizedCastMember, NormalizedCrewMember, NormalizedEpisode,
    NormalizedMetadata, NormalizedRating, NormalizedSeason, NormalizedTrailer, NudityStatus,
};

const PROVIDER_PRIORITY: &[&str] = &["imdb", "tmdb", "tvdb", "mal", "kitsu", "anilist"];

/// Field-specific provider order (Python `providers.py` FIELD_PRIORITY parity).
const FIELD_PRIORITY: &[(&str, &[&str])] = &[
    ("title", &["imdb", "tmdb", "tvdb", "mal"]),
    ("description", &["tmdb", "imdb", "tvdb", "mal"]),
    ("runtime", &["imdb", "tmdb", "tvdb"]),
    ("release_date", &["tmdb", "imdb", "tvdb"]),
    ("poster", &["fanart", "tmdb", "tvdb", "imdb"]),
    ("background", &["fanart", "tmdb", "tvdb"]),
];

fn field_providers(field: &str) -> &'static [&'static str] {
    FIELD_PRIORITY
        .iter()
        .find(|(name, _)| *name == field)
        .map(|(_, providers)| *providers)
        .unwrap_or(PROVIDER_PRIORITY)
}

fn first_scalar_with_providers<T: Clone>(
    metas: &[NormalizedMetadata],
    providers: &[&str],
    pick: impl Fn(&NormalizedMetadata) -> Option<T>,
) -> Option<T> {
    for provider in providers {
        for meta in metas {
            if meta_has_provider(meta, provider)
                && let Some(v) = pick(meta) {
                    return Some(v);
                }
        }
    }
    for meta in metas {
        if let Some(v) = pick(meta) {
            return Some(v);
        }
    }
    None
}

fn first_scalar_for_field<T: Clone>(
    field: &str,
    metas: &[NormalizedMetadata],
    pick: impl Fn(&NormalizedMetadata) -> Option<T>,
) -> Option<T> {
    first_scalar_with_providers(metas, field_providers(field), pick)
}

fn first_scalar<T: Clone>(
    metas: &[NormalizedMetadata],
    pick: impl Fn(&NormalizedMetadata) -> Option<T>,
) -> Option<T> {
    first_scalar_with_providers(metas, PROVIDER_PRIORITY, pick)
}

fn is_empty_str(opt: &Option<String>) -> bool {
    opt.as_ref().is_none_or(|s| s.is_empty())
}

fn meta_has_provider(meta: &NormalizedMetadata, provider: &str) -> bool {
    meta.external_ids.iter().any(|(p, _)| p == provider)
}

fn merge_external_ids(metas: &[NormalizedMetadata]) -> Vec<(String, String)> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for meta in metas {
        for (provider, id) in &meta.external_ids {
            if id.is_empty() {
                continue;
            }
            let key = format!("{provider}:{id}");
            if seen.insert(key) {
                out.push((provider.clone(), id.clone()));
            }
        }
    }
    out
}

fn merge_string_list<F>(metas: &[NormalizedMetadata], pick: F) -> Vec<String>
where
    F: Fn(&NormalizedMetadata) -> &[String],
{
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for meta in metas {
        for item in pick(meta) {
            let key = item.to_ascii_lowercase();
            if !item.is_empty() && seen.insert(key) {
                out.push(item.clone());
            }
        }
    }
    out
}

fn merge_cast(metas: &[NormalizedMetadata]) -> Vec<NormalizedCastMember> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for meta in metas {
        for member in &meta.cast {
            let key = member
                .tmdb_id
                .map(|id| format!("tmdb:{id}"))
                .or_else(|| member.imdb_id.as_ref().map(|id| format!("imdb:{id}")))
                .unwrap_or_else(|| format!("name:{}", member.name.to_ascii_lowercase()));
            if seen.insert(key) {
                out.push(member.clone());
            }
        }
    }
    out
}

fn merge_crew(metas: &[NormalizedMetadata]) -> Vec<NormalizedCrewMember> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for meta in metas {
        for member in &meta.crew {
            let key = format!(
                "{}:{}:{}",
                member.name.to_ascii_lowercase(),
                member.job.as_deref().unwrap_or(""),
                member.department.as_deref().unwrap_or("")
            );
            if seen.insert(key) {
                out.push(member.clone());
            }
        }
    }
    out
}

fn merge_trailers(metas: &[NormalizedMetadata]) -> Vec<NormalizedTrailer> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for meta in metas {
        for trailer in &meta.trailers {
            let key = format!("{}:{}", trailer.site, trailer.video_key);
            if seen.insert(key) {
                out.push(trailer.clone());
            }
        }
    }
    if !out.is_empty() && !out.iter().any(|t| t.is_primary) {
        out[0].is_primary = true;
    }
    out
}

fn merge_aka_titles(metas: &[NormalizedMetadata], primary_title: &str) -> Vec<NormalizedAkaTitle> {
    let primary = primary_title.to_ascii_lowercase();
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for meta in metas {
        for aka in &meta.aka_titles {
            let key = aka.title.to_ascii_lowercase();
            if aka.title.is_empty() || key == primary || !seen.insert(key) {
                continue;
            }
            out.push(aka.clone());
        }
    }
    out
}

fn merge_ratings(metas: &[NormalizedMetadata]) -> Vec<NormalizedRating> {
    let mut by_provider: HashMap<String, NormalizedRating> = HashMap::new();
    for meta in metas {
        for rating in &meta.ratings {
            if rating.rating <= 0.0 {
                continue;
            }
            by_provider
                .entry(rating.provider.clone())
                .or_insert_with(|| rating.clone());
        }
    }
    by_provider.into_values().collect()
}

fn merge_seasons(metas: &[NormalizedMetadata]) -> Vec<NormalizedSeason> {
    let mut seasons: HashMap<i32, NormalizedSeason> = HashMap::new();
    for meta in metas {
        for season in &meta.seasons {
            let entry = seasons
                .entry(season.season_number)
                .or_insert_with(|| NormalizedSeason {
                    season_number: season.season_number,
                    ..Default::default()
                });
            if is_empty_str(&entry.name) {
                entry.name = season.name.clone();
            }
            if is_empty_str(&entry.overview) {
                entry.overview = season.overview.clone();
            }
            if is_empty_str(&entry.air_date) {
                entry.air_date = season.air_date.clone();
            }

            let mut ep_map: HashMap<i32, NormalizedEpisode> = HashMap::new();
            for ep in entry.episodes.drain(..) {
                ep_map.insert(ep.episode_number, ep);
            }
            for ep in &season.episodes {
                ep_map
                    .entry(ep.episode_number)
                    .and_modify(|existing| merge_episode(existing, ep))
                    .or_insert_with(|| ep.clone());
            }
            let mut episodes: Vec<_> = ep_map.into_values().collect();
            episodes.sort_by_key(|e| e.episode_number);
            entry.episodes = episodes;
        }
    }
    let mut out: Vec<_> = seasons.into_values().collect();
    out.sort_by_key(|s| s.season_number);
    out
}

fn merge_episode(existing: &mut NormalizedEpisode, incoming: &NormalizedEpisode) {
    if (existing.title.is_empty() || existing.title == "Episode") && !incoming.title.is_empty() {
        existing.title = incoming.title.clone();
    }
    if existing.overview.is_none() {
        existing.overview = incoming.overview.clone();
    }
    if existing.air_date.is_none() {
        existing.air_date = incoming.air_date.clone();
    }
    if existing.runtime_minutes.is_none() {
        existing.runtime_minutes = incoming.runtime_minutes;
    }
    if existing.still_url.is_none() {
        existing.still_url = incoming.still_url.clone();
    }
    if existing.imdb_id.is_none() {
        existing.imdb_id = incoming.imdb_id.clone();
    }
    if existing.tmdb_id.is_none() {
        existing.tmdb_id = incoming.tmdb_id;
    }
    if existing.tvdb_id.is_none() {
        existing.tvdb_id = incoming.tvdb_id;
    }
}

fn merge_nudity(metas: &[NormalizedMetadata]) -> NudityStatus {
    for meta in metas {
        if meta.nudity_status == NudityStatus::Severe {
            return NudityStatus::Severe;
        }
    }
    for meta in metas {
        if meta.nudity_status != NudityStatus::Unknown {
            return meta.nudity_status;
        }
    }
    NudityStatus::Unknown
}

/// Merge multiple provider payloads into one normalized record (Python `apply_multi_provider_metadata` parity).
pub fn merge_normalized(metas: Vec<NormalizedMetadata>) -> Option<NormalizedMetadata> {
    if metas.is_empty() {
        return None;
    }
    if metas.len() == 1 {
        return metas.into_iter().next();
    }

    let title = first_scalar_for_field("title", &metas, |m| {
        if m.title.is_empty() {
            None
        } else {
            Some(m.title.clone())
        }
    })?;
    let media_type = metas
        .iter()
        .find(|m| !m.title.is_empty())
        .map(|m| m.media_type)
        .unwrap_or(metas[0].media_type);

    Some(NormalizedMetadata {
        media_type,
        title: title.clone(),
        original_title: first_scalar(&metas, |m| m.original_title.clone()),
        year: first_scalar(&metas, |m| m.year),
        description: first_scalar_for_field("description", &metas, |m| m.description.clone()),
        tagline: first_scalar(&metas, |m| m.tagline.clone()),
        release_date: first_scalar_for_field("release_date", &metas, |m| m.release_date.clone()),
        runtime_minutes: first_scalar_for_field("runtime", &metas, |m| m.runtime_minutes),
        original_language: first_scalar(&metas, |m| m.original_language.clone()),
        status: first_scalar(&metas, |m| m.status.clone()),
        poster_url: first_scalar_for_field("poster", &metas, |m| m.poster_url.clone()),
        backdrop_url: first_scalar_for_field("background", &metas, |m| m.backdrop_url.clone()),
        logo_url: first_scalar(&metas, |m| m.logo_url.clone()),
        website: first_scalar(&metas, |m| m.website.clone()),
        end_date: first_scalar(&metas, |m| m.end_date.clone()),
        country: first_scalar(&metas, |m| m.country.clone()),
        network: first_scalar(&metas, |m| m.network.clone()),
        popularity: first_scalar(&metas, |m| m.popularity),
        budget: first_scalar(&metas, |m| m.budget),
        revenue: first_scalar(&metas, |m| m.revenue),
        adult: metas.iter().any(|m| m.adult),
        nudity_status: merge_nudity(&metas),
        genres: merge_string_list(&metas, |m| &m.genres),
        catalogs: merge_string_list(&metas, |m| &m.catalogs),
        keywords: merge_string_list(&metas, |m| &m.keywords),
        certificates: merge_string_list(&metas, |m| &m.certificates),
        external_ids: merge_external_ids(&metas),
        cast: merge_cast(&metas),
        crew: merge_crew(&metas),
        trailers: merge_trailers(&metas),
        aka_titles: merge_aka_titles(&metas, &title),
        ratings: merge_ratings(&metas),
        seasons: merge_seasons(&metas),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::MediaType;

    #[test]
    fn merge_fills_scalar_gaps_from_second_provider() {
        let a = NormalizedMetadata {
            title: "Show A".into(),
            media_type: MediaType::Series,
            description: Some("From A".into()),
            ..Default::default()
        };
        let b = NormalizedMetadata {
            title: "Show A".into(),
            media_type: MediaType::Series,
            network: Some("Netflix".into()),
            external_ids: vec![("tmdb".into(), "1".into())],
            ..Default::default()
        };
        let merged = merge_normalized(vec![a, b]).unwrap();
        assert_eq!(merged.description.as_deref(), Some("From A"));
        assert_eq!(merged.network.as_deref(), Some("Netflix"));
        assert!(merged.external_id("tmdb").is_some());
    }

    #[test]
    fn merge_episodes_by_season_and_number() {
        let a = NormalizedMetadata {
            title: "Anime".into(),
            media_type: MediaType::Series,
            seasons: vec![NormalizedSeason {
                season_number: 1,
                episodes: vec![NormalizedEpisode {
                    episode_number: 1,
                    title: "Ep 1".into(),
                    ..Default::default()
                }],
                ..Default::default()
            }],
            ..Default::default()
        };
        let b = NormalizedMetadata {
            title: "Anime".into(),
            media_type: MediaType::Series,
            seasons: vec![NormalizedSeason {
                season_number: 1,
                episodes: vec![NormalizedEpisode {
                    episode_number: 1,
                    overview: Some("Overview".into()),
                    ..Default::default()
                }],
                ..Default::default()
            }],
            ..Default::default()
        };
        let merged = merge_normalized(vec![a, b]).unwrap();
        assert_eq!(
            merged.seasons[0].episodes[0].overview.as_deref(),
            Some("Overview")
        );
    }

    #[test]
    fn field_priority_prefers_tmdb_description_over_imdb() {
        let imdb = NormalizedMetadata {
            title: "Film".into(),
            media_type: MediaType::Movie,
            description: Some("IMDb summary".into()),
            external_ids: vec![("imdb".into(), "tt1".into())],
            ..Default::default()
        };
        let tmdb = NormalizedMetadata {
            title: "Film".into(),
            media_type: MediaType::Movie,
            description: Some("TMDB summary".into()),
            external_ids: vec![("tmdb".into(), "1".into())],
            ..Default::default()
        };
        let merged = merge_normalized(vec![imdb, tmdb]).unwrap();
        assert_eq!(merged.description.as_deref(), Some("TMDB summary"));
    }

    #[test]
    fn field_priority_prefers_imdb_title_over_tmdb() {
        let tmdb = NormalizedMetadata {
            title: "TMDB Title".into(),
            media_type: MediaType::Movie,
            external_ids: vec![("tmdb".into(), "1".into())],
            ..Default::default()
        };
        let imdb = NormalizedMetadata {
            title: "IMDb Title".into(),
            media_type: MediaType::Movie,
            external_ids: vec![("imdb".into(), "tt1".into())],
            ..Default::default()
        };
        let merged = merge_normalized(vec![tmdb, imdb]).unwrap();
        assert_eq!(merged.title, "IMDb Title");
    }
}
