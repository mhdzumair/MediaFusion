/**
 * Metadata API client for refreshing and migrating content metadata.
 * Available to all authenticated users.
 */

import { apiClient } from './client'

// ============================================
// Types
// ============================================

export type MetadataProvider = 'imdb' | 'tmdb' | 'tvdb' | 'mal' | 'kitsu' | 'anilist'

export interface RefreshMetadataRequest {
  media_type: 'movie' | 'series'
  providers?: MetadataProvider[] // Specific providers to refresh from, or undefined for all
}

export interface RefreshMetadataResponse {
  status: string
  message: string
  media_id: number // Internal media_id
  title?: string
  refreshed_providers?: MetadataProvider[]
}

export type ExternalProvider = 'imdb' | 'tmdb' | 'tvdb' | 'mal' | 'kitsu' | 'anilist'

export interface LinkExternalIdRequest {
  provider: ExternalProvider
  external_id: string
  media_type: 'movie' | 'series'
  fetch_metadata?: boolean
}

export interface LinkExternalIdResponse {
  status: string
  message: string
  media_id: number
  provider: string
  external_id: string
  title?: string
  metadata_updated: boolean
}

export interface LinkMultipleExternalIdsRequest {
  imdb_id?: string
  tmdb_id?: string | number
  tvdb_id?: string | number
  mal_id?: string | number
  kitsu_id?: string | number
  anilist_id?: string | number
  media_type: 'movie' | 'series'
  fetch_metadata?: boolean
}

export interface LinkMultipleExternalIdsResponse {
  status: string
  message: string
  media_id: number
  linked_providers: string[]
  failed_providers: string[]
  metadata_updated: boolean
}

// Backward compatibility aliases
export type MigrateIdRequest = LinkExternalIdRequest
export type MigrateIdResponse = LinkExternalIdResponse

export interface SearchMatchesRequest {
  title?: string
  year?: number
  external_id?: string
  media_type: 'movie' | 'series' | 'all' | 'sports' | 'tv'
  limit?: number
  include_user_content?: boolean
  include_official?: boolean
  include_catalog?: boolean
  include_external?: boolean
}

export interface MediaMatchSearchResult extends ExternalSearchResult {
  source?: 'database' | 'external'
  is_user_created?: boolean
  is_own?: boolean
}

export interface SearchMatchesResponse {
  results: MediaMatchSearchResult[]
}

// Legacy alias kept for callers migrating from search-external
export type SearchExternalRequest = {
  title?: string
  year?: number
  media_type: 'movie' | 'series' | 'all'
  limit?: number
  include_user_content?: boolean
  include_official?: boolean
  include_catalog?: boolean
  include_external?: boolean
}

export type SearchExternalResponse = SearchMatchesResponse

export interface ExternalSearchResult {
  id: string // Primary ID (imdb_id or tmdb/tvdb/mal/kitsu/anilist prefixed ID)
  title: string
  year?: number
  end_year?: number
  poster?: string
  background?: string
  logo?: string
  description?: string | null
  provider?: string // 'imdb', 'tmdb', 'tvdb', 'mal', 'kitsu', 'anilist'
  imdb_id?: string
  imdb_rating?: number
  runtime?: string
  release_date?: string
  media_id?: number
  tmdb_id?: string
  tvdb_id?: string | number
  mal_id?: string | number
  kitsu_id?: string | number
  anilist_id?: string | number
  type?: 'movie' | 'series'
  external_ids?: Record<string, string | number | null> // All external IDs
}

// ============================================
// API Functions
// ============================================

export const metadataApi = {
  /**
   * Refresh metadata from external sources (IMDB/TMDB/TVDB/MAL/Kitsu/AniList).
   * Fetches fresh data from all configured providers.
   * @param mediaId Internal media_id
   * @param mediaType Type of media
   * @param providers Optional list of specific providers to refresh from
   */
  refreshMetadata: (
    mediaId: number,
    mediaType: 'movie' | 'series',
    providers?: MetadataProvider[],
  ): Promise<RefreshMetadataResponse> => {
    const payload: RefreshMetadataRequest = { media_type: mediaType }
    if (providers && providers.length > 0) {
      payload.providers = providers
    }
    return apiClient.post<RefreshMetadataResponse>(`/metadata/${mediaId}/refresh`, payload)
  },

  /**
   * Link an external provider ID to existing media.
   * @param mediaId Internal media_id
   * @param provider External provider ('imdb', 'tmdb', 'tvdb', 'mal', 'kitsu', 'anilist')
   * @param externalId The external ID to link
   * @param mediaType Type of media
   * @param fetchMetadata Whether to fetch and update metadata from the provider
   */
  linkExternalId: (
    mediaId: number,
    provider: ExternalProvider,
    externalId: string,
    mediaType: 'movie' | 'series',
    fetchMetadata: boolean = true,
  ): Promise<LinkExternalIdResponse> => {
    return apiClient.post<LinkExternalIdResponse>(`/metadata/${mediaId}/link-external`, {
      provider,
      external_id: externalId,
      media_type: mediaType,
      fetch_metadata: fetchMetadata,
    })
  },

  /**
   * @deprecated Use linkExternalId instead
   */
  migrateId: (
    metaId: string,
    newExternalId: string,
    mediaType: 'movie' | 'series',
  ): Promise<LinkExternalIdResponse> => {
    return apiClient.post<LinkExternalIdResponse>(`/metadata/${metaId}/migrate`, {
      provider: 'imdb',
      external_id: newExternalId,
      media_type: mediaType,
    })
  },

  /**
   * Unified media match search — DB first (user + catalog), then external providers.
   * Supports title (+ optional year) or external_id (tt..., tmdb:123, etc.).
   */
  searchMatches: (request: SearchMatchesRequest): Promise<SearchMatchesResponse> => {
    return apiClient.post<SearchMatchesResponse>('/metadata/search/matches', request)
  },

  /**
   * Link multiple external provider IDs to a media item at once.
   * This is useful when a search result contains multiple IDs (IMDb, TMDB, TVDB, MAL, Kitsu, AniList).
   * @param mediaId Internal media_id
   * @param ids Object containing the external IDs to link
   * @param mediaType Type of media
   * @param fetchMetadata Whether to fetch and update metadata from providers
   */
  linkMultipleExternalIds: (
    mediaId: number,
    ids: {
      imdb_id?: string
      tmdb_id?: string | number
      tvdb_id?: string | number
      mal_id?: string | number
      kitsu_id?: string | number
      anilist_id?: string | number
    },
    mediaType: 'movie' | 'series',
    fetchMetadata: boolean = true,
  ): Promise<LinkMultipleExternalIdsResponse> => {
    return apiClient.post<LinkMultipleExternalIdsResponse>(`/metadata/${mediaId}/link-multiple`, {
      ...ids,
      media_type: mediaType,
      fetch_metadata: fetchMetadata,
    })
  },
}
