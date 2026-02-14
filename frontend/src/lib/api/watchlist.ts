import { apiClient } from './client'
import type { ExternalIds } from './catalog'

// Types
export interface WatchlistItem {
  id: number
  title: string
  type: 'movie' | 'series'
  year?: number
  poster?: string
  external_ids: ExternalIds
  info_hashes: string[]  // Info hashes from debrid for this media
}

export interface WatchlistProviderInfo {
  service: string
  name?: string
  supports_watchlist: boolean
}

export interface WatchlistResponse {
  items: WatchlistItem[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  provider: string
  provider_name?: string
}

export interface WatchlistProvidersResponse {
  providers: WatchlistProviderInfo[]
  profile_id: number
}

export interface WatchlistParams {
  profileId?: number
  mediaType?: 'movie' | 'series'
  page?: number
  pageSize?: number
}

// Missing torrents types
export interface MissingTorrentFile {
  path: string
  size: number
}

export interface MissingTorrentItem {
  info_hash: string
  name: string
  size: number
  files: MissingTorrentFile[]
  parsed_title?: string
  parsed_year?: number
  parsed_type?: 'movie' | 'series'
}

export interface MissingTorrentsResponse {
  items: MissingTorrentItem[]
  total: number
  provider: string
  provider_name?: string
}

// Import types
export interface TorrentOverride {
  title?: string
  year?: number
  type?: 'movie' | 'series'
}

export interface ImportRequest {
  info_hashes: string[]
  overrides?: Record<string, TorrentOverride>
}

export interface ImportResultItem {
  info_hash: string
  status: 'success' | 'failed' | 'skipped'
  message?: string
  media_id?: number
  media_title?: string
}

export interface ImportResponse {
  imported: number
  failed: number
  skipped: number
  details: ImportResultItem[]
}

// Remove types
export interface RemoveRequest {
  info_hash: string
}

export interface RemoveResponse {
  success: boolean
  message: string
}

// Advanced import types for multi-content support
export interface FileAnnotationData {
  filename: string
  size?: number
  index: number
  season_number?: number | null
  episode_number?: number | null
  episode_end?: number | null
  included?: boolean
  // Multi-content: link this file to a different media
  meta_id?: string
  meta_title?: string
  meta_type?: 'movie' | 'series'
}

export interface AdvancedTorrentImport {
  info_hash: string
  meta_type: 'movie' | 'series'
  meta_id: string  // Primary media external ID (e.g., tt1234567)
  title?: string
  file_data?: FileAnnotationData[]
}

export interface AdvancedImportRequest {
  advanced_imports: AdvancedTorrentImport[]
}

// API client
export const watchlistApi = {
  /**
   * Get list of debrid providers that support watchlist
   */
  getProviders: async (profileId?: number): Promise<WatchlistProvidersResponse> => {
    const params = new URLSearchParams()
    if (profileId) params.append('profile_id', profileId.toString())
    const query = params.toString()
    return apiClient.get<WatchlistProvidersResponse>(`/watchlist/providers${query ? `?${query}` : ''}`)
  },

  /**
   * Get watchlist items from a specific debrid provider
   */
  getWatchlist: async (provider: string, params: WatchlistParams = {}): Promise<WatchlistResponse> => {
    const searchParams = new URLSearchParams()
    if (params.profileId) searchParams.append('profile_id', params.profileId.toString())
    if (params.mediaType) searchParams.append('media_type', params.mediaType)
    if (params.page) searchParams.append('page', params.page.toString())
    if (params.pageSize) searchParams.append('page_size', params.pageSize.toString())
    
    const query = searchParams.toString()
    return apiClient.get<WatchlistResponse>(`/watchlist/${provider}${query ? `?${query}` : ''}`)
  },

  /**
   * Get torrents from debrid account that are NOT in our database
   */
  getMissing: async (provider: string, profileId?: number): Promise<MissingTorrentsResponse> => {
    const params = new URLSearchParams()
    if (profileId) params.append('profile_id', profileId.toString())
    const query = params.toString()
    return apiClient.get<MissingTorrentsResponse>(`/watchlist/${provider}/missing${query ? `?${query}` : ''}`)
  },

  /**
   * Import selected torrents from debrid account into our database
   */
  importTorrents: async (
    provider: string,
    infoHashes: string[],
    profileId?: number,
    overrides?: Record<string, TorrentOverride>
  ): Promise<ImportResponse> => {
    const params = new URLSearchParams()
    if (profileId) params.append('profile_id', profileId.toString())
    const query = params.toString()
    return apiClient.post<ImportResponse>(`/watchlist/${provider}/import${query ? `?${query}` : ''}`, {
      info_hashes: infoHashes,
      overrides: overrides && Object.keys(overrides).length > 0 ? overrides : undefined,
    })
  },

  /**
   * Remove a torrent from debrid account
   */
  removeTorrent: async (provider: string, infoHash: string, profileId?: number): Promise<RemoveResponse> => {
    const params = new URLSearchParams()
    if (profileId) params.append('profile_id', profileId.toString())
    const query = params.toString()
    // Using POST with body since DELETE doesn't support body in our client
    return apiClient.post<RemoveResponse>(`/watchlist/${provider}/remove${query ? `?${query}` : ''}`, {
      info_hash: infoHash,
    })
  },

  /**
   * Clear all torrents from debrid account
   */
  clearAll: async (provider: string, profileId?: number): Promise<RemoveResponse> => {
    const params = new URLSearchParams()
    if (profileId) params.append('profile_id', profileId.toString())
    const query = params.toString()
    return apiClient.post<RemoveResponse>(`/watchlist/${provider}/clear-all${query ? `?${query}` : ''}`)
  },

  /**
   * Advanced import with file annotations (for multi-content torrents)
   */
  advancedImport: async (
    provider: string,
    imports: AdvancedTorrentImport[],
    profileId?: number
  ): Promise<ImportResponse> => {
    const params = new URLSearchParams()
    if (profileId) params.append('profile_id', profileId.toString())
    const query = params.toString()
    return apiClient.post<ImportResponse>(`/watchlist/${provider}/import/advanced${query ? `?${query}` : ''}`, {
      advanced_imports: imports,
    })
  },
}

// Display names for debrid services
export const DEBRID_SERVICE_DISPLAY_NAMES: Record<string, string> = {
  realdebrid: 'Real-Debrid',
  alldebrid: 'AllDebrid',
  debridlink: 'Debrid-Link',
  offcloud: 'Offcloud',
  pikpak: 'PikPak',
  seedr: 'Seedr',
  torbox: 'TorBox',
  premiumize: 'Premiumize',
  qbittorrent: 'qBittorrent',
  stremthru: 'StremThru',
}

/**
 * Get display name for a debrid provider
 */
export function getProviderDisplayName(provider: WatchlistProviderInfo): string {
  const serviceName = DEBRID_SERVICE_DISPLAY_NAMES[provider.service] || provider.service
  if (provider.name && provider.name !== serviceName) {
    return `${provider.name} (${serviceName})`
  }
  return serviceName
}
