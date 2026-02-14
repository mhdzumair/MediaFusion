/**
 * Metadata API client for refreshing and migrating content metadata.
 * Available to all authenticated users.
 */

import { apiClient } from './client'

// ============================================
// Types
// ============================================

export type MetadataProvider = 'imdb' | 'tmdb' | 'tvdb' | 'mal' | 'kitsu'

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

export type ExternalProvider = 'imdb' | 'tmdb' | 'tvdb' | 'mal' | 'kitsu'

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

export interface SearchExternalRequest {
  title: string
  year?: number
  media_type: 'movie' | 'series'
}

export interface ExternalSearchResult {
  id: string // Primary ID (imdb_id or tmdb:xxx or tvdb:xxx)
  title: string
  year?: number
  poster?: string
  description?: string
  provider?: string // 'imdb', 'tmdb', 'tvdb', 'mal', 'kitsu'
  imdb_id?: string
  tmdb_id?: string
  tvdb_id?: string | number
  external_ids?: Record<string, string | number | null> // All external IDs
}

export interface SearchExternalResponse {
  status: string
  results: ExternalSearchResult[]
}

// ============================================
// API Functions
// ============================================

export const metadataApi = {
  /**
   * Refresh metadata from external sources (IMDB/TMDB/TVDB/MAL/Kitsu).
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
   * @param provider External provider ('imdb', 'tmdb', 'tvdb', 'mal', 'kitsu')
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
   * Search for metadata in external sources (IMDB/TMDB/TVDB/MAL/Kitsu).
   * Useful for finding the correct external ID when linking providers.
   */
  searchExternal: (title: string, mediaType: 'movie' | 'series', year?: number): Promise<SearchExternalResponse> => {
    return apiClient.post<SearchExternalResponse>('/metadata/search-external', { title, media_type: mediaType, year })
  },

  /**
   * Link multiple external provider IDs to a media item at once.
   * This is useful when a search result contains multiple IDs (IMDb, TMDB, TVDB).
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
