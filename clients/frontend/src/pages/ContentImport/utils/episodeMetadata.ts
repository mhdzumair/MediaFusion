export function detectEpisodeMetadata(filename: string): {
  season_number: number | null
  episode_number: number | null
  episode_end: number | null
} {
  const basename = (filename.replace(/\\/g, '/').split('/').pop() || filename).replace(/\.[^/.]+$/, '')
  const normalized = basename.replace(/[._]/g, ' ')

  const seasonEpisodeMatch = normalized.match(
    /\bS(\d{1,3})[\s-]*EP?\s*(\d{1,3})(?:\s*(?:EP?|[-~]\s*(?:EP?)?)\s*(\d{1,3}))?\b/i,
  )
  if (seasonEpisodeMatch) {
    return {
      season_number: parseInt(seasonEpisodeMatch[1], 10),
      episode_number: parseInt(seasonEpisodeMatch[2], 10),
      episode_end: seasonEpisodeMatch[3] ? parseInt(seasonEpisodeMatch[3], 10) : null,
    }
  }

  const xPatternMatch = normalized.match(/\b(\d{1,3})x(\d{1,3})(?:\s*-\s*(\d{1,3}))?\b/i)
  if (xPatternMatch) {
    return {
      season_number: parseInt(xPatternMatch[1], 10),
      episode_number: parseInt(xPatternMatch[2], 10),
      episode_end: xPatternMatch[3] ? parseInt(xPatternMatch[3], 10) : null,
    }
  }

  const seasonTextMatch = normalized.match(
    /\bseason\s*(\d{1,3})\D+episode\s*(\d{1,3})(?:\s*[-~]\s*(?:episode\s*)?(\d{1,3}))?\b/i,
  )
  if (seasonTextMatch) {
    return {
      season_number: parseInt(seasonTextMatch[1], 10),
      episode_number: parseInt(seasonTextMatch[2], 10),
      episode_end: seasonTextMatch[3] ? parseInt(seasonTextMatch[3], 10) : null,
    }
  }

  const episodeOnlyMatch = normalized.match(/\bE(?:P)?\s*(\d{1,3})(?:\s*[-~]\s*(\d{1,3}))?\b/i)
  if (episodeOnlyMatch) {
    const explicitSeason = normalized.match(/\bS(?:eason)?\s*(\d{1,3})\b/i)
    return {
      season_number: explicitSeason ? Number(explicitSeason[1]) : null,
      episode_number: parseInt(episodeOnlyMatch[1], 10),
      episode_end: episodeOnlyMatch[2] ? parseInt(episodeOnlyMatch[2], 10) : null,
    }
  }

  return {
    season_number: null,
    episode_number: null,
    episode_end: null,
  }
}
