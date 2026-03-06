import { apiClient } from './client'
import type {
  ExternalMetadataPreview,
  FetchExternalRequest,
  MetadataItem,
  MetadataListParams,
  MetadataListResponse,
  MigrateIdRequest,
  SearchExternalRequest,
  SearchExternalResponse,
} from './admin'

const MODERATOR_METADATA_BASE = '/moderator/metadata'

function buildQueryString(params: MetadataListParams): string {
  const searchParams = new URLSearchParams()

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.append(key, String(value))
    }
  })

  const query = searchParams.toString()
  return query ? `?${query}` : ''
}

export const moderatorApi = {
  listMetadata: async (params: MetadataListParams = {}): Promise<MetadataListResponse> => {
    const query = buildQueryString(params)
    return apiClient.get<MetadataListResponse>(`${MODERATOR_METADATA_BASE}${query}`)
  },

  getMetadata: async (mediaId: number): Promise<MetadataItem> => {
    return apiClient.get<MetadataItem>(`${MODERATOR_METADATA_BASE}/${mediaId}`)
  },

  fetchExternalMetadata: async (mediaId: number, request: FetchExternalRequest): Promise<ExternalMetadataPreview> => {
    return apiClient.post<ExternalMetadataPreview>(`${MODERATOR_METADATA_BASE}/${mediaId}/fetch-external`, request)
  },

  applyExternalMetadata: async (mediaId: number, request: FetchExternalRequest): Promise<MetadataItem> => {
    return apiClient.post<MetadataItem>(`${MODERATOR_METADATA_BASE}/${mediaId}/apply-external`, request)
  },

  migrateMetadataId: async (mediaId: number, request: MigrateIdRequest): Promise<MetadataItem> => {
    return apiClient.post<MetadataItem>(`${MODERATOR_METADATA_BASE}/${mediaId}/migrate-id`, request)
  },

  searchExternalMetadata: async (request: SearchExternalRequest): Promise<SearchExternalResponse> => {
    return apiClient.post<SearchExternalResponse>(`${MODERATOR_METADATA_BASE}/search-external`, request)
  },
}
