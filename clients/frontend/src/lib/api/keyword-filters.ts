import { apiClient } from './client'

export interface KeywordFilter {
  id: number
  keyword: string
  is_active: boolean
  scope: string
  created_at: string
}

export interface WhitelistPhrase {
  id: number
  phrase: string
  reason: string | null
  created_at: string
}

export interface KeywordFilterListResponse {
  items: KeywordFilter[]
  total: number
  page: number
  page_size: number
}

export interface WhitelistListResponse {
  items: WhitelistPhrase[]
  total: number
  page: number
  page_size: number
}

export interface KeywordCacheStats {
  keywords_count: number
  stream_keywords_count: number
  whitelist_count: number
  sync_status?: KeywordSyncStatus
}

export interface FileSyncStatus {
  embedded_hash: string
  stored_hash: string | null
  synced_at: string | null
  in_sync: boolean
  embedded_keyword_count: number
  db_file_keyword_count: number
  embedded_whitelist_count: number
  db_file_whitelist_count: number
}

export interface RuntimeStreamKeywordsStatus {
  embedded_hash: string
  embedded_keyword_count: number
  cache_keyword_count: number
  admin_override_count: number
  runtime_only: boolean
}

export interface RecomputeJobStatus {
  target_version: string
  recorded_version: string | null
  up_to_date: boolean
  in_progress: boolean
  lease_owner: string | null
  lease_synced_at: string | null
}

export interface KeywordSyncStatus {
  file_sync: {
    media: FileSyncStatus
    stream: RuntimeStreamKeywordsStatus
  }
  recompute: RecomputeJobStatus
  cache: {
    media_keywords: number
    stream_keywords: number
    whitelist: number
  }
  admin_overrides: {
    keywords: number
    whitelist: number
  }
}

export const keywordFiltersApi = {
  listKeywords: async (params?: {
    page?: number
    page_size?: number
    search?: string
    scope?: string
  }): Promise<KeywordFilterListResponse> => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.page_size) sp.set('page_size', String(params.page_size))
    if (params?.search) sp.set('search', params.search)
    if (params?.scope) sp.set('scope', params.scope)
    const q = sp.toString()
    return apiClient.get<KeywordFilterListResponse>(`/admin/keyword-filters${q ? `?${q}` : ''}`)
  },

  addKeyword: async (keyword: string, scope = 'all'): Promise<KeywordFilter> => {
    return apiClient.post<KeywordFilter>('/admin/keyword-filters', { keyword, scope })
  },

  toggleKeyword: async (id: number, is_active: boolean): Promise<KeywordFilter> => {
    return apiClient.patch<KeywordFilter>(`/admin/keyword-filters/${id}`, { is_active })
  },

  updateKeywordScope: async (id: number, scope: string): Promise<KeywordFilter> => {
    return apiClient.patch<KeywordFilter>(`/admin/keyword-filters/${id}`, { scope })
  },

  deleteKeyword: async (id: number): Promise<void> => {
    return apiClient.delete(`/admin/keyword-filters/${id}`)
  },

  reloadCache: async (): Promise<KeywordCacheStats> => {
    return apiClient.post<KeywordCacheStats>('/admin/keyword-filters/reload')
  },

  getSyncStatus: async (): Promise<KeywordSyncStatus> => {
    return apiClient.get<KeywordSyncStatus>('/admin/keyword-filters/sync-status')
  },

  resetToDefaults: async (): Promise<KeywordSyncStatus> => {
    return apiClient.post<KeywordSyncStatus>('/admin/keyword-filters/reset')
  },

  listWhitelist: async (params?: { page?: number; page_size?: number }): Promise<WhitelistListResponse> => {
    const sp = new URLSearchParams()
    if (params?.page) sp.set('page', String(params.page))
    if (params?.page_size) sp.set('page_size', String(params.page_size))
    const q = sp.toString()
    return apiClient.get<WhitelistListResponse>(`/admin/keyword-whitelist${q ? `?${q}` : ''}`)
  },

  addWhitelistPhrase: async (phrase: string, reason?: string): Promise<WhitelistPhrase> => {
    return apiClient.post<WhitelistPhrase>('/admin/keyword-whitelist', { phrase, reason })
  },

  deleteWhitelistPhrase: async (id: number): Promise<void> => {
    return apiClient.delete(`/admin/keyword-whitelist/${id}`)
  },
}
