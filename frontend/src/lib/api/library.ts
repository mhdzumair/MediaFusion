import { apiClient } from './client'
import type { CatalogType, ExternalIds } from './catalog'

// Types
export interface LibraryItemCreate {
  media_id: number // Internal media ID
  catalog_type: CatalogType
}

export interface LibraryItemResponse {
  id: number
  media_id: number
  external_ids: ExternalIds
  catalog_type: CatalogType
  title: string
  poster?: string
  added_at: string
}

export interface LibraryListResponse {
  items: LibraryItemResponse[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface LibraryStatsResponse {
  total_items: number
  movies: number
  series: number
  tv: number
}

export interface LibraryListParams {
  catalog_type?: CatalogType
  search?: string
  sort?: 'added' | 'title'
  page?: number
  page_size?: number
}

export interface LibraryCheckResponse {
  in_library: boolean
  item_id?: string
}

// API functions
export const libraryApi = {
  // Get user's library
  getLibrary: async (params: LibraryListParams = {}): Promise<LibraryListResponse> => {
    const searchParams = new URLSearchParams()

    if (params.catalog_type) searchParams.set('catalog_type', params.catalog_type)
    if (params.search) searchParams.set('search', params.search)
    if (params.sort) searchParams.set('sort', params.sort)
    if (params.page) searchParams.set('page', params.page.toString())
    if (params.page_size) searchParams.set('page_size', params.page_size.toString())

    const queryString = searchParams.toString()
    const url = `/library${queryString ? `?${queryString}` : ''}`

    return apiClient.get<LibraryListResponse>(url)
  },

  // Get library statistics
  getStats: async (): Promise<LibraryStatsResponse> => {
    return apiClient.get<LibraryStatsResponse>('/library/stats')
  },

  // Add item to library
  addToLibrary: async (data: LibraryItemCreate): Promise<LibraryItemResponse> => {
    return apiClient.post<LibraryItemResponse>('/library', data)
  },

  // Get a specific library item
  getLibraryItem: async (itemId: number): Promise<LibraryItemResponse> => {
    return apiClient.get<LibraryItemResponse>(`/library/${itemId}`)
  },

  // Check if item is in library by media_id
  checkInLibrary: async (mediaId: number): Promise<LibraryCheckResponse> => {
    return apiClient.get<LibraryCheckResponse>(`/library/check/${mediaId}`)
  },

  // Remove item from library by ID
  removeFromLibrary: async (itemId: number): Promise<void> => {
    await apiClient.delete(`/library/${itemId}`)
  },

  // Remove item from library by media_id
  removeFromLibraryByMediaId: async (mediaId: number): Promise<void> => {
    await apiClient.delete(`/library/by-media-id/${mediaId}`)
  },
}
