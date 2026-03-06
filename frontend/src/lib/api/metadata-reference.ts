import { apiClient } from './client'

export interface MetadataReferenceItem {
  id: number
  name: string
  usage_count: number
}

export interface MetadataReferenceListParams {
  page?: number
  per_page?: number
  search?: string
}

export interface MetadataReferenceListResponse {
  items: MetadataReferenceItem[]
  total: number
  page: number
  per_page: number
  pages: number
  has_more: boolean
}

function buildQueryString(params: MetadataReferenceListParams = {}): string {
  const searchParams = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.append(key, String(value))
    }
  })
  const query = searchParams.toString()
  return query ? `?${query}` : ''
}

export const metadataReferenceApi = {
  listGenres: async (params: MetadataReferenceListParams = {}): Promise<MetadataReferenceListResponse> => {
    const query = buildQueryString(params)
    return apiClient.get<MetadataReferenceListResponse>(`/metadata/reference/genres${query}`)
  },

  listCatalogs: async (params: MetadataReferenceListParams = {}): Promise<MetadataReferenceListResponse> => {
    const query = buildQueryString(params)
    return apiClient.get<MetadataReferenceListResponse>(`/metadata/reference/catalogs${query}`)
  },

  listStars: async (params: MetadataReferenceListParams = {}): Promise<MetadataReferenceListResponse> => {
    const query = buildQueryString(params)
    return apiClient.get<MetadataReferenceListResponse>(`/metadata/reference/stars${query}`)
  },

  listParentalCertificates: async (
    params: MetadataReferenceListParams = {},
  ): Promise<MetadataReferenceListResponse> => {
    const query = buildQueryString(params)
    return apiClient.get<MetadataReferenceListResponse>(`/metadata/reference/parental-certificates${query}`)
  },
}
