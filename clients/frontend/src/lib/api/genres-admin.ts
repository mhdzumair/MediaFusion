import { apiClient } from './client'

export type MediaTypeWire = 'movie' | 'series' | 'tv' | 'events'

export interface GenreTypeEntry {
  genre_id: number
  media_type: MediaTypeWire
  is_hidden: boolean
  created_at: string
}

export interface GenreDetail {
  id: number
  name: string
  usage_count: number
  types: GenreTypeEntry[]
}

export interface GenreListResponse {
  items: GenreDetail[]
  total: number
  page: number
  page_size: number
}

export interface CreateGenreRequest {
  name: string
  media_types: MediaTypeWire[]
}

export interface TypeUpdate {
  media_type: MediaTypeWire
  is_hidden: boolean
}

export interface UpdateGenreRequest {
  name?: string
  types?: TypeUpdate[]
}

export const genreAdminApi = {
  list: async (params?: { page?: number; page_size?: number; search?: string }): Promise<GenreListResponse> => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.page_size) sp.set('page_size', String(params.page_size))
    if (params?.search) sp.set('search', params.search)
    const q = sp.toString()
    return apiClient.get<GenreListResponse>(`/admin/genres${q ? `?${q}` : ''}`)
  },

  create: async (req: CreateGenreRequest): Promise<GenreDetail> => {
    return apiClient.post<GenreDetail>('/admin/genres', req)
  },

  update: async (id: number, req: UpdateGenreRequest): Promise<GenreDetail> => {
    return apiClient.patch<GenreDetail>(`/admin/genres/${id}`, req)
  },

  delete: async (id: number): Promise<void> => {
    return apiClient.delete(`/admin/genres/${id}`)
  },

  deleteType: async (id: number, mediaType: string): Promise<void> => {
    return apiClient.delete(`/admin/genres/${id}/types/${mediaType}`)
  },

  reloadCache: async (): Promise<{ detail: string }> => {
    return apiClient.post<{ detail: string }>('/admin/genres/reload')
  },
}
