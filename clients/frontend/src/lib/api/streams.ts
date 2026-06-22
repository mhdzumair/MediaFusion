import { apiClient } from './client'
import type { StreamInfo } from './catalog'

export interface MyStreamItem extends StreamInfo {
  is_blocked?: boolean
  is_active?: boolean
  is_public?: boolean
  media_id?: number
  media_title?: string
  media_type?: string
  media_poster_url?: string | null
  media_imdb_id?: string | null
  file_count?: number
  created_at?: string
}

export interface MyStreamsListParams {
  page?: number
  page_size?: number
  status?: 'active' | 'blocked' | 'inactive' | 'keyword_blocked'
  search?: string
  stream_type?: string
}

export interface MyStreamsListResponse {
  items: MyStreamItem[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface UpdateMyStreamRequest {
  name?: string
  resolution?: string
  quality?: string
  codec?: string
  bit_depth?: string
  source?: string
  languages?: string[]
  audio_formats?: string[]
  hdr_formats?: string[]
}

export interface BlockMyStreamResponse {
  stream_id: number
  is_blocked: boolean
  message: string
}

export const streamsApi = {
  listMyStreams: async (params?: MyStreamsListParams): Promise<MyStreamsListResponse> => {
    const searchParams = new URLSearchParams()
    if (params?.page) searchParams.set('page', String(params.page))
    if (params?.page_size) searchParams.set('page_size', String(params.page_size))
    if (params?.status) searchParams.set('status', params.status)
    if (params?.search) searchParams.set('search', params.search)
    if (params?.stream_type) searchParams.set('stream_type', params.stream_type)
    const query = searchParams.toString()
    return apiClient.get<MyStreamsListResponse>(`/streams/mine${query ? `?${query}` : ''}`)
  },

  updateMyStream: async (
    streamId: number,
    body: UpdateMyStreamRequest,
  ): Promise<{ stream_id: number; message: string }> => {
    return apiClient.patch<{ stream_id: number; message: string }>(`/streams/${streamId}`, body)
  },

  blockMyStream: async (streamId: number): Promise<BlockMyStreamResponse> => {
    return apiClient.post<BlockMyStreamResponse>(`/streams/${streamId}/block`)
  },

  deleteStream: async (streamId: number): Promise<{ message: string }> => {
    return apiClient.delete<{ message: string }>(`/streams/${streamId}`)
  },
}
