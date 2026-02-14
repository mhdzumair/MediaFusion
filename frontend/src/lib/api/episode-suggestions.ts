import { apiClient } from './client'

// Types
export type EpisodeSuggestionStatus = 'pending' | 'approved' | 'rejected' | 'auto_approved'
export type EpisodeEditableField = 'title' | 'overview' | 'air_date' | 'runtime_minutes'

export interface EpisodeSuggestionCreateRequest {
  field_name: EpisodeEditableField
  current_value?: string
  suggested_value: string
  reason?: string
}

export interface EpisodeSuggestionReviewRequest {
  action: 'approve' | 'reject'
  review_notes?: string
}

export interface EpisodeSuggestion {
  id: string  // UUID
  user_id: number
  username: string | null
  episode_id: number
  episode_title: string | null
  season_number: number | null
  episode_number: number | null
  series_title: string | null
  field_name: string
  current_value: string | null
  suggested_value: string
  reason: string | null
  status: EpisodeSuggestionStatus
  was_auto_approved: boolean
  reviewed_by: string | null
  reviewed_at: string | null
  review_notes: string | null
  created_at: string
  updated_at: string | null
  // User contribution info
  user_contribution_level: string | null
  user_contribution_points: number | null
}

export interface EpisodeSuggestionListResponse {
  suggestions: EpisodeSuggestion[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface EpisodeSuggestionStats {
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

export interface EpisodeSuggestionListParams {
  status?: EpisodeSuggestionStatus
  page?: number
  page_size?: number
}

export interface PendingEpisodeSuggestionParams {
  field_name?: string
  page?: number
  page_size?: number
}

export interface BulkReviewResult {
  approved: number
  rejected: number
  skipped: number
}

// API functions
export const episodeSuggestionsApi = {
  // Create an episode suggestion
  create: async (
    episodeId: number,
    data: EpisodeSuggestionCreateRequest
  ): Promise<EpisodeSuggestion> => {
    return apiClient.post<EpisodeSuggestion>(`/episode/${episodeId}/suggest`, data)
  },

  // List user's episode suggestions
  list: async (params?: EpisodeSuggestionListParams): Promise<EpisodeSuggestionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params?.status) searchParams.set('status', params.status)
    if (params?.page) searchParams.set('page', params.page.toString())
    if (params?.page_size) searchParams.set('page_size', params.page_size.toString())

    const query = searchParams.toString()
    return apiClient.get<EpisodeSuggestionListResponse>(`/episode-suggestions${query ? `?${query}` : ''}`)
  },

  // Get single episode suggestion
  get: async (suggestionId: string): Promise<EpisodeSuggestion> => {
    return apiClient.get<EpisodeSuggestion>(`/episode-suggestions/${suggestionId}`)
  },

  // Delete episode suggestion (pending only)
  delete: async (suggestionId: string): Promise<void> => {
    await apiClient.delete(`/episode-suggestions/${suggestionId}`)
  },

  // List pending episode suggestions (moderator)
  listPending: async (params?: PendingEpisodeSuggestionParams): Promise<EpisodeSuggestionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params?.field_name) searchParams.set('field_name', params.field_name)
    if (params?.page) searchParams.set('page', params.page.toString())
    if (params?.page_size) searchParams.set('page_size', params.page_size.toString())

    const query = searchParams.toString()
    return apiClient.get<EpisodeSuggestionListResponse>(`/episode-suggestions/pending${query ? `?${query}` : ''}`)
  },

  // Review episode suggestion (moderator)
  review: async (
    suggestionId: string,
    data: EpisodeSuggestionReviewRequest
  ): Promise<EpisodeSuggestion> => {
    return apiClient.put<EpisodeSuggestion>(`/episode-suggestions/${suggestionId}/review`, data)
  },

  // Bulk review episode suggestions (moderator)
  bulkReview: async (
    suggestionIds: string[],
    action: 'approve' | 'reject',
    reviewNotes?: string
  ): Promise<BulkReviewResult> => {
    const params = new URLSearchParams()
    params.set('action', action)
    if (reviewNotes) params.set('review_notes', reviewNotes)
    
    return apiClient.post<BulkReviewResult>(
      `/episode-suggestions/bulk-review?${params.toString()}`,
      suggestionIds
    )
  },

  // Get stats
  getStats: async (): Promise<EpisodeSuggestionStats> => {
    return apiClient.get<EpisodeSuggestionStats>('/episode-suggestions/stats')
  },
}
