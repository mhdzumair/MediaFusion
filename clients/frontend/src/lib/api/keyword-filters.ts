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
  whitelist_count: number
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
