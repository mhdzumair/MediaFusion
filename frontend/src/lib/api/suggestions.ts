import { apiClient } from './client'

// Types
export type SuggestionStatus = 'pending' | 'approved' | 'rejected' | 'auto_approved'
export type EditableField = 
  | 'title' 
  | 'description' 
  | 'year' 
  | 'poster' 
  | 'background' 
  | 'runtime' 
  | 'genres' 
  | 'country' 
  | 'language'
  | 'aka_titles'
  | 'cast'
  | 'directors'
  | 'writers'
  | 'imdb_id'
  | 'tmdb_id'

export interface SuggestionCreateRequest {
  field_name: EditableField
  current_value?: string
  suggested_value: string
  reason?: string
}

export interface SuggestionReviewRequest {
  action: 'approve' | 'reject'
  review_notes?: string
}

export interface Suggestion {
  id: string  // UUID
  user_id: number
  username: string | null
  media_id: number  // Internal media ID
  media_title: string | null
  field_name: string
  current_value: string | null
  suggested_value: string
  reason: string | null
  status: SuggestionStatus
  was_auto_approved: boolean
  reviewed_by: string | null  // UUID
  reviewed_at: string | null
  review_notes: string | null
  created_at: string
  updated_at: string | null
  // User contribution info
  user_contribution_level: string | null
  user_contribution_points: number | null
}

export interface SuggestionListResponse {
  suggestions: Suggestion[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface SuggestionStats {
  total: number
  pending: number
  approved: number
  auto_approved: number
  rejected: number
  // Today's stats (for moderators)
  approved_today: number
  rejected_today: number
  // User stats
  user_pending: number
  user_approved: number
  user_auto_approved: number
  user_rejected: number
  user_contribution_points: number
  user_contribution_level: string
}

export interface SuggestionListParams {
  status?: SuggestionStatus
  page?: number
  page_size?: number
}

export interface PendingSuggestionParams {
  field_name?: string
  page?: number
  page_size?: number
}

// API functions
export const suggestionsApi = {
  // Create a suggestion
  create: async (
    mediaId: number,
    data: SuggestionCreateRequest
  ): Promise<Suggestion> => {
    return apiClient.post<Suggestion>(`/metadata/${mediaId}/suggest`, data)
  },

  // List user's suggestions
  list: async (params?: SuggestionListParams): Promise<SuggestionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params?.status) searchParams.set('status', params.status)
    if (params?.page) searchParams.set('page', params.page.toString())
    if (params?.page_size) searchParams.set('page_size', params.page_size.toString())

    const query = searchParams.toString()
    return apiClient.get<SuggestionListResponse>(`/suggestions${query ? `?${query}` : ''}`)
  },

  // Get single suggestion
  get: async (suggestionId: string): Promise<Suggestion> => {
    return apiClient.get<Suggestion>(`/suggestions/${suggestionId}`)
  },

  // Delete suggestion (pending only)
  delete: async (suggestionId: string): Promise<void> => {
    await apiClient.delete(`/suggestions/${suggestionId}`)
  },

  // List pending suggestions (moderator)
  listPending: async (params?: PendingSuggestionParams): Promise<SuggestionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params?.field_name) searchParams.set('field_name', params.field_name)
    if (params?.page) searchParams.set('page', params.page.toString())
    if (params?.page_size) searchParams.set('page_size', params.page_size.toString())

    const query = searchParams.toString()
    return apiClient.get<SuggestionListResponse>(`/suggestions/pending${query ? `?${query}` : ''}`)
  },

  // Review suggestion (moderator)
  review: async (
    suggestionId: string,
    data: SuggestionReviewRequest
  ): Promise<Suggestion> => {
    return apiClient.put<Suggestion>(`/suggestions/${suggestionId}/review`, data)
  },

  // Get stats
  getStats: async (): Promise<SuggestionStats> => {
    return apiClient.get<SuggestionStats>('/suggestions/stats')
  },
}

