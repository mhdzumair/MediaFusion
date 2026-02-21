import { apiClient } from './client'

// Types
export type CatalogType = 'movie' | 'series' | 'tv'
export type SortOption = 'latest' | 'popular' | 'rating' | 'year' | 'release_date' | 'title'
export type SortDirection = 'asc' | 'desc'

// External IDs structure - all external IDs for a media item
export interface ExternalIds {
  imdb?: string // IMDb ID (tt...)
  tmdb?: string // TMDB ID (now string for consistency)
  tvdb?: string // TVDB ID
  mal?: string // MyAnimeList ID
  kitsu?: string // Kitsu ID
}

// Helper to get canonical external ID for Stremio/display (prefers IMDb)
export function getCanonicalExternalId(externalIds: ExternalIds | undefined, mediaId: number): string {
  if (!externalIds) return `mf:${mediaId}`
  if (externalIds.imdb) return externalIds.imdb
  if (externalIds.tmdb) return `tmdb:${externalIds.tmdb}`
  if (externalIds.tvdb) return `tvdb:${externalIds.tvdb}`
  if (externalIds.mal) return `mal:${externalIds.mal}`
  return `mf:${mediaId}`
}

export interface GenreResponse {
  id: number
  name: string
}

// Rating types
export interface ProviderRating {
  provider: string // imdb, tmdb, trakt, rottentomatoes, metacritic, letterboxd
  provider_display_name: string // IMDb, TMDB, etc.
  rating: number // Normalized 0-10 scale
  rating_raw?: number // Original value (e.g., 87% for RT)
  max_rating: number // Original scale max (10, 100, 5)
  is_percentage: boolean // True for RT, Metacritic
  vote_count?: number
  rating_type?: string // audience, critic, fresh
  certification?: string // fresh, rotten, certified_fresh
}

export interface CommunityRating {
  average_rating: number // 1-10 scale
  total_votes: number
  upvotes: number
  downvotes: number
  user_vote?: number // Current user's vote if authenticated
}

export interface AllRatings {
  external_ratings: ProviderRating[] // IMDb, TMDB, RT, etc.
  community_rating?: CommunityRating // MediaFusion votes
  // Convenience fields for quick display
  imdb_rating?: number // IMDb rating for backward compatibility
  tmdb_rating?: number
}

export interface CatalogItemBase {
  id: number // Internal database ID (media_id)
  external_ids: ExternalIds // All external IDs (imdb, tmdb, tvdb, mal)
  title: string
  type: CatalogType
  year?: number
  poster?: string
  background?: string
  description?: string
  runtime?: string
  genres: string[]
  // All ratings
  ratings?: AllRatings
  // Convenience - kept for backward compatibility
  imdb_rating?: number
  last_stream_added?: string
  likes_count?: number
  // Content guidance
  certification?: string // All Ages, Children, Parental Guidance, Teens, Adults, Adults+
  nudity?: string // None, Mild, Moderate, Severe
  // Content moderation (visible to admins/moderators)
  is_blocked?: boolean
  block_reason?: string
}

export interface SeasonInfo {
  season_number: number
  episodes: EpisodeInfo[]
}

export interface EpisodeInfo {
  id?: number // Episode database ID (for moderator actions like deletion)
  episode_number: number
  title?: string
  released?: string
  overview?: string
  thumbnail?: string
  is_user_created?: boolean
  is_user_addition?: boolean
}

export interface TrailerInfo {
  key: string // YouTube video ID
  site: string
  name?: string
  type: string // trailer, teaser, clip, etc.
  is_official: boolean
}

export interface CatalogItemDetail extends CatalogItemBase {
  catalogs: string[]
  aka_titles: string[]
  seasons?: SeasonInfo[]
  country?: string
  tv_language?: string
  // Credits
  cast?: string[] // Cast members
  directors?: string[] // Directors
  writers?: string[] // Writers
  stars?: string[] // Legacy - same as cast
  // Trailers/Videos
  trailers?: TrailerInfo[]
  // Metadata tracking
  last_refreshed_at?: string // ISO timestamp
  last_scraped_at?: string // ISO timestamp - when streams were last scraped
}

export interface CatalogListResponse {
  items: CatalogItemBase[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface StreamVoteSummary {
  upvotes: number
  downvotes: number
  score: number // upvotes - downvotes
  user_vote?: number // Current user's vote: 1, -1, or null
}

// Episode link info for series streams
export interface EpisodeLinkInfo {
  file_id: number
  file_name: string
  season_number?: number
  episode_number?: number
  episode_end?: number
}

export interface StreamInfo {
  // Core identifiers
  id?: number // Stream database ID
  torrent_stream_id?: number // TorrentStream ID for torrent admin actions
  info_hash?: string // For torrent streams

  // Stremio-compatible fields
  name: string // Formatted name with provider, resolution, status
  description?: string // Formatted description
  url?: string // Playback URL (for debrid users)
  behavior_hints?: Record<string, unknown>

  // Rich metadata for frontend UI (v5 schema)
  stream_name?: string // Raw torrent title/stream name
  stream_type?: string // Stream type: torrent, http, youtube, usenet, telegram, external_link
  resolution?: string
  quality?: string
  codec?: string
  bit_depth?: string

  // Normalized quality attributes (formatted as joined strings for display)
  audio_formats?: string // Formatted audio string (e.g., "Atmos|DTS")
  channels?: string // Formatted channels string (e.g., "5.1|7.1")
  hdr_formats?: string // Formatted HDR string (e.g., "DV|HDR10")

  source?: string
  languages?: string[] // Array of language names
  size?: string // Formatted size (e.g., "2.5 GB")
  size_bytes?: number // Raw size in bytes
  seeders?: number
  uploader?: string
  release_group?: string
  cached?: boolean // Whether stream is cached in debrid

  // Episode links for series (for fixing season/episode detection)
  episode_links?: EpisodeLinkInfo[]

  // Release flags
  is_remastered?: boolean
  is_upscaled?: boolean
  is_proper?: boolean
  is_repack?: boolean
  is_extended?: boolean
  is_complete?: boolean
  is_dubbed?: boolean
  is_subbed?: boolean

  // Voting data
  votes?: StreamVoteSummary
}

// Streaming provider info for multi-provider support
export interface StreamingProviderInfo {
  service: string // Provider service name (realdebrid, alldebrid, etc.)
  name?: string | null // User-defined display name
  enabled: boolean
}

export interface StreamListResponse {
  streams: StreamInfo[]
  season?: number
  episode?: number
  // Web playback requires MediaFlow - this indicates if it's available
  web_playback_enabled?: boolean // Whether web browser playback is enabled (requires MediaFlow)
  // Multi-provider support
  streaming_providers?: StreamingProviderInfo[] // All configured providers in selected profile
  selected_provider?: string | null // Currently selected provider service name
  profile_id?: number | null // Currently selected profile ID
}

export interface CatalogInfo {
  name: string // Internal name (used for filtering)
  display_name: string // Human-readable name
}

export interface AvailableCatalogsResponse {
  movies: CatalogInfo[]
  series: CatalogInfo[]
  tv: CatalogInfo[]
}

export interface CatalogListParams {
  catalog?: string
  genre?: string
  search?: string
  sort?: SortOption
  sort_dir?: SortDirection // Sort direction: 'asc' or 'desc'
  page?: number
  page_size?: number
  include_upcoming?: boolean // Include unreleased/upcoming content
  has_streams?: boolean // Only show media with available streams (default: true)
  // TV-specific filters
  working_only?: boolean // [TV only] Only show channels with working/active streams
  my_channels?: boolean // [TV only] Only show channels imported by the user
}

// API functions
export const catalogApi = {
  // Get available catalogs grouped by type
  getAvailableCatalogs: async (): Promise<AvailableCatalogsResponse> => {
    return apiClient.get<AvailableCatalogsResponse>('/catalog/available')
  },

  // Get genres for a catalog type
  getGenres: async (catalogType: CatalogType): Promise<GenreResponse[]> => {
    return apiClient.get<GenreResponse[]>(`/catalog/genres?catalog_type=${catalogType}`)
  },

  // Browse catalog with filters
  browseCatalog: async (catalogType: CatalogType, params: CatalogListParams = {}): Promise<CatalogListResponse> => {
    const searchParams = new URLSearchParams()

    if (params.catalog) searchParams.set('catalog', params.catalog)
    if (params.genre) searchParams.set('genre', params.genre)
    if (params.search) searchParams.set('search', params.search)
    if (params.sort) searchParams.set('sort', params.sort)
    if (params.sort_dir) searchParams.set('sort_dir', params.sort_dir)
    if (params.page) searchParams.set('page', params.page.toString())
    if (params.page_size) searchParams.set('page_size', params.page_size.toString())
    if (params.include_upcoming !== undefined) searchParams.set('include_upcoming', params.include_upcoming.toString())
    if (params.has_streams !== undefined) searchParams.set('has_streams', params.has_streams.toString())
    // TV-specific filters
    if (params.working_only !== undefined) searchParams.set('working_only', params.working_only.toString())
    if (params.my_channels !== undefined) searchParams.set('my_channels', params.my_channels.toString())

    const queryString = searchParams.toString()
    const url = `/catalog/${catalogType}${queryString ? `?${queryString}` : ''}`

    return apiClient.get<CatalogListResponse>(url)
  },

  // Get catalog item details
  getCatalogItem: async (catalogType: CatalogType, mediaId: number): Promise<CatalogItemDetail> => {
    return apiClient.get<CatalogItemDetail>(`/catalog/${catalogType}/${mediaId}`)
  },

  // Get streams for a catalog item (requires auth)
  getStreams: async (
    catalogType: 'movie' | 'series' | 'tv',
    mediaId: number,
    season?: number,
    episode?: number,
    profileId?: number,
    provider?: string,
  ): Promise<StreamListResponse> => {
    const searchParams = new URLSearchParams()

    if (season !== undefined) searchParams.set('season', season.toString())
    if (episode !== undefined) searchParams.set('episode', episode.toString())
    if (profileId !== undefined) searchParams.set('profile_id', profileId.toString())
    if (provider !== undefined) searchParams.set('provider', provider)

    const queryString = searchParams.toString()
    const url = `/catalog/${catalogType}/${mediaId}/streams${queryString ? `?${queryString}` : ''}`

    return apiClient.get<StreamListResponse>(url)
  },

  // Get files for a specific stream (for file annotation)
  getStreamFiles: async (streamId: number): Promise<StreamFileInfo[]> => {
    return apiClient.get<StreamFileInfo[]>(`/stream-links/stream/${streamId}/files`)
  },
}

// Additional types for stream files
export interface StreamFileInfo {
  file_id: number
  file_name: string
  size: number | null // File size in bytes
  season_number: number | null
  episode_number: number | null
  episode_end: number | null
}
