/**
 * User Metadata API client for creating and managing user-created content.
 * Supports movies, series, seasons, and episodes.
 */

import { apiClient } from './client'

// ============================================
// Types
// ============================================

export interface UserEpisodeCreate {
  episode_number: number
  title: string
  overview?: string
  air_date?: string // YYYY-MM-DD format
  runtime_minutes?: number
}

export interface UserSeasonCreate {
  season_number: number
  name?: string
  overview?: string
  air_date?: string
  episodes?: UserEpisodeCreate[]
}

export interface UserMediaCreate {
  type: 'movie' | 'series' | 'tv'
  title: string
  year?: number
  description?: string
  poster_url?: string
  background_url?: string
  logo_url?: string
  genres?: string[]
  catalogs?: string[]
  external_ids?: Record<string, string>
  is_public?: boolean
  runtime_minutes?: number
  seasons?: UserSeasonCreate[]
}

export interface UserMediaUpdate {
  title?: string
  original_title?: string
  year?: number
  description?: string
  tagline?: string
  poster_url?: string
  background_url?: string
  logo_url?: string
  genres?: string[]
  catalogs?: string[]
  is_public?: boolean
  runtime_minutes?: number
  release_date?: string // YYYY-MM-DD format
  status?: string
  website?: string
  original_language?: string
  nudity_status?: string
  aka_titles?: string[]
  cast?: string[]
  directors?: string[]
  writers?: string[]
  parental_certificate?: string
  external_ids?: Record<string, string>
}

export interface EpisodeResponse {
  id: number
  episode_number: number
  title: string
  overview?: string
  air_date?: string
  runtime_minutes?: number
  is_user_created: boolean
  is_user_addition: boolean
}

export interface SeasonResponse {
  id: number
  season_number: number
  name?: string
  overview?: string
  air_date?: string
  episode_count: number
  episodes: EpisodeResponse[]
}

export interface UserMediaResponse {
  id: number
  type: string
  title: string
  original_title?: string
  year?: number
  description?: string
  tagline?: string
  poster_url?: string
  background_url?: string
  logo_url?: string
  genres: string[]
  catalogs: string[]
  external_ids: Record<string, string>
  is_public: boolean
  is_user_created: boolean
  created_by_user_id?: number
  total_streams: number
  created_at: string
  updated_at?: string
  runtime_minutes?: number
  release_date?: string
  status?: string
  website?: string
  original_language?: string
  nudity_status?: string
  aka_titles: string[]
  cast: string[]
  directors: string[]
  writers: string[]
  parental_certificate?: string
  total_seasons?: number
  total_episodes?: number
  seasons?: SeasonResponse[]
}

export interface UserMediaListResponse {
  items: UserMediaResponse[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface SeasonAddRequest {
  season_number: number
  name?: string
  overview?: string
  air_date?: string
  episodes?: UserEpisodeCreate[]
}

export interface EpisodeAddRequest {
  season_number: number
  episodes: UserEpisodeCreate[]
}

export interface EpisodeUpdateRequest {
  title?: string
  overview?: string
  air_date?: string
  runtime_minutes?: number
}

export interface MetadataSearchResult {
  id: number
  external_id: string
  title: string
  year?: number
  type: string
  poster?: string
  is_user_created: boolean
  is_own: boolean
}

export interface MetadataSearchResponse {
  results: MetadataSearchResult[]
  total: number
}

export type ImportProvider = 'imdb' | 'tmdb' | 'tvdb' | 'mal' | 'kitsu'

export interface ImportFromExternalRequest {
  provider: ImportProvider
  external_id: string
  media_type: 'movie' | 'series' | 'tv'
  is_public?: boolean
}

export interface ImportPreviewResponse {
  provider: string
  external_id: string
  title: string
  year?: number
  description?: string
  poster?: string
  background?: string
  genres: string[]
  runtime?: string
  imdb_id?: string
  tmdb_id?: string
  tvdb_id?: string | number
  mal_id?: string | number
  kitsu_id?: string | number
}

// ============================================
// API Functions
// ============================================

export const userMetadataApi = {
  /**
   * Create user-generated metadata (movie or series).
   */
  create: (data: UserMediaCreate): Promise<UserMediaResponse> => {
    return apiClient.post<UserMediaResponse>('/metadata/user', data)
  },

  /**
   * List user-created metadata for the current user.
   */
  list: (params?: {
    page?: number
    per_page?: number
    type?: 'movie' | 'series' | 'tv' | 'all'
    search?: string
  }): Promise<UserMediaListResponse> => {
    const searchParams = new URLSearchParams()
    if (params?.page) searchParams.set('page', params.page.toString())
    if (params?.per_page) searchParams.set('per_page', params.per_page.toString())
    if (params?.type) searchParams.set('type', params.type)
    if (params?.search) searchParams.set('search', params.search)

    const query = searchParams.toString()
    return apiClient.get<UserMediaListResponse>(`/metadata/user${query ? `?${query}` : ''}`)
  },

  /**
   * Get details of user-created metadata.
   */
  get: (mediaId: number): Promise<UserMediaResponse> => {
    return apiClient.get<UserMediaResponse>(`/metadata/user/${mediaId}`)
  },

  /**
   * Update user-created metadata.
   */
  update: (mediaId: number, data: UserMediaUpdate): Promise<UserMediaResponse> => {
    return apiClient.put<UserMediaResponse>(`/metadata/user/${mediaId}`, data)
  },

  /**
   * Delete user-created metadata.
   */
  delete: (mediaId: number, force?: boolean): Promise<void> => {
    const query = force ? '?force=true' : ''
    return apiClient.delete(`/metadata/user/${mediaId}${query}`)
  },

  /**
   * Add a season to a series.
   */
  addSeason: (mediaId: number, data: SeasonAddRequest): Promise<SeasonResponse> => {
    return apiClient.post<SeasonResponse>(`/metadata/user/${mediaId}/seasons`, data)
  },

  /**
   * Add episodes to an existing season.
   */
  addEpisodes: (mediaId: number, data: EpisodeAddRequest): Promise<EpisodeResponse[]> => {
    return apiClient.post<EpisodeResponse[]>(`/metadata/user/${mediaId}/episodes`, data)
  },

  /**
   * Update an episode.
   */
  updateEpisode: (mediaId: number, episodeId: number, data: EpisodeUpdateRequest): Promise<EpisodeResponse> => {
    return apiClient.put<EpisodeResponse>(`/metadata/user/${mediaId}/episodes/${episodeId}`, data)
  },

  /**
   * Delete an episode.
   */
  deleteEpisode: (mediaId: number, episodeId: number): Promise<void> => {
    return apiClient.delete(`/metadata/user/${mediaId}/episodes/${episodeId}`)
  },

  /**
   * Delete a season and all its episodes.
   */
  deleteSeason: (mediaId: number, seasonNumber: number): Promise<void> => {
    return apiClient.delete(`/metadata/user/${mediaId}/seasons/${seasonNumber}`)
  },

  /**
   * Delete an episode (moderator only).
   * Allows moderators to delete any episode regardless of ownership.
   * @param deleteStreamLinks - Also delete file-media links for this episode (removes streams from this episode)
   */
  deleteEpisodeAdmin: (mediaId: number, episodeId: number, deleteStreamLinks: boolean = false): Promise<void> => {
    const query = deleteStreamLinks ? '?delete_stream_links=true' : ''
    return apiClient.delete(`/metadata/user/${mediaId}/episodes/${episodeId}/admin${query}`)
  },

  /**
   * Delete a season and all its episodes (moderator only).
   * Allows moderators to delete any season regardless of ownership.
   */
  deleteSeasonAdmin: (mediaId: number, seasonNumber: number): Promise<void> => {
    return apiClient.delete(`/metadata/user/${mediaId}/seasons/${seasonNumber}/admin`)
  },

  /**
   * Search for metadata (both user-created and official) for linking purposes.
   */
  searchAll: (params: {
    query: string
    type?: 'movie' | 'series' | 'tv' | 'all'
    limit?: number
    include_official?: boolean
  }): Promise<MetadataSearchResponse> => {
    const searchParams = new URLSearchParams()
    searchParams.set('query', params.query)
    if (params.type) searchParams.set('type', params.type)
    if (params.limit) searchParams.set('limit', params.limit.toString())
    if (params.include_official !== undefined) {
      searchParams.set('include_official', params.include_official.toString())
    }

    return apiClient.get<MetadataSearchResponse>(`/metadata/user/search/all?${searchParams}`)
  },

  /**
   * Preview metadata from an external provider before importing.
   */
  previewImport: (data: ImportFromExternalRequest): Promise<ImportPreviewResponse> => {
    return apiClient.post<ImportPreviewResponse>('/metadata/user/import/preview', data)
  },

  /**
   * Import metadata from an external provider and create user-owned metadata.
   */
  importFromExternal: (data: ImportFromExternalRequest): Promise<UserMediaResponse> => {
    return apiClient.post<UserMediaResponse>('/metadata/user/import', data)
  },
}
