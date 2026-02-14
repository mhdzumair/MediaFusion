import { apiClient } from './client'
import type { ExternalIds } from './catalog'

export type WatchAction = 'WATCHED' | 'DOWNLOADED' | 'QUEUED'
export type HistorySource = 'mediafusion' | 'trakt' | 'simkl' | 'manual'

export interface StreamInfo {
  resolution?: string
  size?: number
  source?: string
  codec?: string
  quality?: string
}

export interface WatchHistoryItem {
  id: number
  user_id: number
  profile_id: number
  media_id: number
  external_ids: ExternalIds
  title: string
  media_type: 'movie' | 'series' | 'tv'
  season?: number
  episode?: number
  duration?: number
  progress: number
  watched_at: string
  poster?: string
  episode_poster?: string  // Episode still/thumbnail if available (for series)
  action: WatchAction
  source: HistorySource  // Where this history entry came from
  stream_info?: StreamInfo
}

export interface WatchHistoryListParams {
  profile_id?: number
  media_type?: 'movie' | 'series' | 'tv'
  action?: WatchAction
  page?: number
  page_size?: number
}

export interface WatchHistoryListResponse {
  items: WatchHistoryItem[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface ContinueWatchingItem {
  id: number
  media_id: number
  external_ids: ExternalIds
  title: string
  media_type: 'movie' | 'series' | 'tv'
  season?: number
  episode?: number
  progress: number
  duration?: number
  progress_percent: number
  watched_at: string
  poster?: string
}

export interface WatchHistoryCreateRequest {
  profile_id: number
  media_id: number  // Internal media ID
  title: string
  media_type: 'movie' | 'series' | 'tv'
  season?: number
  episode?: number
  duration?: number
  progress: number
}

export interface WatchHistoryUpdateRequest {
  progress: number
  duration?: number
}

export type StreamAction = 'download' | 'queue' | 'watch'

export interface StreamActionTrackRequest {
  media_id: number  // Internal media ID
  title: string
  catalog_type: 'movie' | 'series' | 'tv'
  season?: number
  episode?: number
  action: StreamAction
  stream_info?: Record<string, unknown>
}

export const watchHistoryApi = {
  /**
   * List watch history with pagination
   */
  list: async (params: WatchHistoryListParams = {}): Promise<WatchHistoryListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.profile_id !== undefined) searchParams.append('profile_id', params.profile_id.toString())
    if (params.media_type) searchParams.append('media_type', params.media_type)
    if (params.action) searchParams.append('action', params.action)
    if (params.page) searchParams.append('page', params.page.toString())
    if (params.page_size) searchParams.append('page_size', params.page_size.toString())
    
    const query = searchParams.toString()
    return apiClient.get<WatchHistoryListResponse>(`/watch-history${query ? `?${query}` : ''}`)
  },

  /**
   * Get continue watching items
   */
  getContinueWatching: async (profileId?: number, limit: number = 10): Promise<ContinueWatchingItem[]> => {
    const searchParams = new URLSearchParams()
    if (profileId !== undefined) searchParams.append('profile_id', profileId.toString())
    searchParams.append('limit', limit.toString())
    
    return apiClient.get<ContinueWatchingItem[]>(`/watch-history/continue-watching?${searchParams.toString()}`)
  },

  /**
   * Create or update a watch history entry
   */
  create: async (data: WatchHistoryCreateRequest): Promise<WatchHistoryItem> => {
    return apiClient.post<WatchHistoryItem>('/watch-history', data)
  },

  /**
   * Update watch progress
   */
  updateProgress: async (historyId: number, data: WatchHistoryUpdateRequest): Promise<WatchHistoryItem> => {
    return apiClient.patch<WatchHistoryItem>(`/watch-history/${historyId}`, data)
  },

  /**
   * Delete a watch history entry
   */
  delete: async (historyId: number): Promise<void> => {
    await apiClient.delete(`/watch-history/${historyId}`)
  },

  /**
   * Clear all watch history
   */
  clear: async (profileId?: number): Promise<void> => {
    const query = profileId ? `?profile_id=${profileId}` : ''
    await apiClient.delete(`/watch-history${query}`)
  },

  /**
   * Track a stream action (download, queue, copy, watch)
   * Auto-creates/updates watch history entry
   */
  trackAction: async (data: StreamActionTrackRequest): Promise<WatchHistoryItem> => {
    return apiClient.post<WatchHistoryItem>('/watch-history/track', data)
  },
}
