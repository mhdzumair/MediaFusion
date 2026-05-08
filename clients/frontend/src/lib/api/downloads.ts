import { apiClient } from './client'
import type { ExternalIds } from './catalog'

export type DownloadStatus = 'COMPLETED' | 'FAILED' | 'CANCELLED'

export interface DownloadStreamInfo {
  info_hash?: string
  source?: string
  resolution?: string
  quality?: string
  size?: number
  file_name?: string
}

export interface DownloadHistoryItem {
  id: number
  user_id: number
  profile_id: number
  media_id: number // Internal media_id
  external_ids: ExternalIds // All external IDs
  title: string
  media_type: 'movie' | 'series'
  season?: number
  episode?: number
  stream_info: DownloadStreamInfo
  status: DownloadStatus
  downloaded_at: string
  poster?: string
}

export interface DownloadListParams {
  profile_id?: number
  media_type?: 'movie' | 'series'
  download_status?: DownloadStatus
  page?: number
  page_size?: number
}

export interface DownloadListResponse {
  items: DownloadHistoryItem[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface DownloadStats {
  total_downloads: number
  completed: number
  failed: number
  cancelled: number
  movies_downloaded: number
  series_downloaded: number
  this_month: number
}

export interface DownloadCreateRequest {
  profile_id: number
  media_id: number // Internal media_id
  title: string
  media_type: 'movie' | 'series'
  season?: number
  episode?: number
  stream_info?: DownloadStreamInfo
  status?: DownloadStatus
}

export const downloadsApi = {
  /**
   * List downloads with pagination
   */
  list: async (params: DownloadListParams = {}): Promise<DownloadListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.profile_id) searchParams.append('profile_id', params.profile_id.toString())
    if (params.media_type) searchParams.append('media_type', params.media_type)
    if (params.download_status) searchParams.append('download_status', params.download_status)
    if (params.page) searchParams.append('page', params.page.toString())
    if (params.page_size) searchParams.append('page_size', params.page_size.toString())

    const query = searchParams.toString()
    return apiClient.get<DownloadListResponse>(`/downloads${query ? `?${query}` : ''}`)
  },

  /**
   * Get download statistics
   */
  getStats: async (profileId?: number): Promise<DownloadStats> => {
    const query = profileId ? `?profile_id=${profileId}` : ''
    return apiClient.get<DownloadStats>(`/downloads/stats${query}`)
  },

  /**
   * Log a download
   */
  create: async (data: DownloadCreateRequest): Promise<DownloadHistoryItem> => {
    return apiClient.post<DownloadHistoryItem>('/downloads', data)
  },

  /**
   * Delete a download entry
   */
  delete: async (downloadId: number): Promise<void> => {
    await apiClient.delete(`/downloads/${downloadId}`)
  },

  /**
   * Clear all downloads
   */
  clear: async (profileId?: number): Promise<void> => {
    const query = profileId ? `?profile_id=${profileId}` : ''
    await apiClient.delete(`/downloads${query}`)
  },
}
