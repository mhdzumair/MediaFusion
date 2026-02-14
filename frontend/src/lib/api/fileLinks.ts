import { apiClient } from './client'

export interface FileLink {
  file_id: number
  file_name: string
  file_index?: number | null
  size?: number | null
  season_number: number | null
  episode_number: number | null
  episode_end?: number | null
}

export interface FileLinkUpdate {
  file_id: number
  season_number: number | null
  episode_number: number | null
  episode_end?: number | null
}

export interface BulkFileLinkUpdateRequest {
  stream_id: number
  media_id: number
  updates: FileLinkUpdate[]
}

export interface FileLinkUpdateResponse {
  updated: number
  failed: number
  errors: string[]
}

export interface StreamFileLinksResponse {
  stream_id: number
  media_id: number
  files: FileLink[]
  total: number
}

// ============================================
// Annotation Request Types
// ============================================

export interface StreamNeedingAnnotation {
  stream_id: number
  stream_name: string
  source: string | null
  size: number | null
  resolution: string | null
  info_hash: string | null
  file_count: number
  unmapped_count: number
  created_at: string
  // Associated media info
  media_id: number
  media_title: string
  media_year: number | null
}

export interface StreamsNeedingAnnotationResponse {
  items: StreamNeedingAnnotation[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface StreamsNeedingAnnotationParams {
  page?: number
  per_page?: number
  search?: string
}

export const fileLinksApi = {
  /**
   * Get all file links for a stream and media combination
   */
  getStreamFileLinks: async (streamId: number, mediaId: number): Promise<StreamFileLinksResponse> => {
    return apiClient.get<StreamFileLinksResponse>(`/stream-links/files/${streamId}?media_id=${mediaId}`)
  },

  /**
   * Update file links for a stream (fix season/episode numbers)
   */
  updateFileLinks: async (request: BulkFileLinkUpdateRequest): Promise<FileLinkUpdateResponse> => {
    return apiClient.put<FileLinkUpdateResponse>('/stream-links/files', request)
  },

  /**
   * Get list of streams that need file annotation (moderator only)
   */
  getStreamsNeedingAnnotation: async (
    params: StreamsNeedingAnnotationParams = {},
  ): Promise<StreamsNeedingAnnotationResponse> => {
    const searchParams = new URLSearchParams()
    if (params.page) searchParams.set('page', params.page.toString())
    if (params.per_page) searchParams.set('per_page', params.per_page.toString())
    if (params.search) searchParams.set('search', params.search)

    const queryString = searchParams.toString()
    return apiClient.get<StreamsNeedingAnnotationResponse>(
      `/stream-links/needs-annotation${queryString ? `?${queryString}` : ''}`,
    )
  },
}
