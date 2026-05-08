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

export interface StreamMediaLinkInfo {
  link_id: number
  media_id: number
  external_id: string
  title: string
  year: number | null
  type: string
  file_index: number | null
  season: number | null
  episode: number | null
}

export interface StreamMediaLinksResponse {
  stream_id: number
  media_entries: StreamMediaLinkInfo[]
}

export interface StreamMediaLinkCreateRequest {
  stream_id: number
  media_id: number
  file_index?: number | null
  season?: number | null
  episode?: number | null
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
  file_count: number | null
  unmapped_count: number | null
  created_at: string
  // Associated media info
  media_id: number
  media_title: string
  media_year: number | null
  media_type: string
  media_external_id: string | null
  media_poster: string | null
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

export interface AnnotationDismissRequest {
  reason?: string
}

export interface AnnotationDismissResponse {
  status: string
  stream_id: number
  media_id: number
  dismissed_at: string
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
   * Get all media links for a stream
   */
  getMediaForStream: async (streamId: number): Promise<StreamMediaLinksResponse> => {
    return apiClient.get<StreamMediaLinksResponse>(`/stream-links/stream/${streamId}`)
  },

  /**
   * Create a stream-media link (optionally file-specific via file_index)
   */
  createStreamLink: async (request: StreamMediaLinkCreateRequest) => {
    return apiClient.post('/stream-links', request)
  },

  /**
   * Delete a stream-media link by link ID
   */
  deleteStreamLink: async (linkId: number): Promise<void> => {
    return apiClient.delete(`/stream-links/${linkId}`)
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

  /**
   * Dismiss an annotation queue entry
   */
  dismissAnnotationRequest: async (
    streamId: number,
    mediaId: number,
    request: AnnotationDismissRequest = {},
  ): Promise<AnnotationDismissResponse> => {
    return apiClient.post<AnnotationDismissResponse>(
      `/stream-links/needs-annotation/${streamId}/media/${mediaId}/dismiss`,
      request,
    )
  },
}
