import { apiClient } from './client'

// Types
export type StreamSuggestionType =
  | 'report_broken'
  | 'field_correction'
  | 'language_add'
  | 'language_remove'
  | 'mark_duplicate'
  | 'relink_media' // Re-link stream to different media (replaces current link)
  | 'add_media_link' // Add additional media link (for collections/multi-content)
  | 'other'

export type StreamSuggestionStatus = 'pending' | 'approved' | 'rejected' | 'auto_approved'

export type StreamFieldName =
  | 'name'
  | 'resolution'
  | 'codec'
  | 'quality'
  | 'bit_depth'
  | 'audio_formats'
  | 'channels'
  | 'hdr_formats'
  | 'source'
  | 'languages'

// Dynamic field name for episode link corrections
// Format: episode_link:{file_id}:{field} where field is season_number, episode_number, or episode_end
export type EpisodeLinkFieldName = `episode_link:${number}:${'season_number' | 'episode_number' | 'episode_end'}`

export interface StreamSuggestionCreateRequest {
  suggestion_type: StreamSuggestionType
  field_name?: StreamFieldName | EpisodeLinkFieldName
  current_value?: string
  suggested_value?: string
  reason?: string
  related_stream_id?: string
  // Fields for stream re-linking suggestions
  target_media_id?: number // Target media ID to link stream to (for relink_media/add_media_link)
  file_index?: number // Specific file index within torrent (for multi-file torrents)
}

export interface StreamSuggestion {
  id: string
  user_id: number
  username: string | null
  stream_id: string
  stream_name: string | null
  media_id: number | null
  suggestion_type: string
  field_name: string | null
  current_value: string | null
  suggested_value: string | null
  reason: string | null
  status: StreamSuggestionStatus
  was_auto_approved: boolean
  created_at: string
  reviewed_by: string | null // User ID as string
  reviewer_name: string | null // Reviewer's username for display
  reviewed_at: string | null
  review_notes: string | null
  // User contribution info
  user_contribution_level: string | null
  user_contribution_points: number | null
}

export interface StreamSuggestionListResponse {
  suggestions: StreamSuggestion[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface StreamSuggestionReviewRequest {
  action: 'approve' | 'reject'
  review_notes?: string
}

export interface StreamSuggestionStats {
  total: number
  pending: number
  approved: number
  auto_approved: number
  rejected: number
  // Today's stats (for moderators)
  approved_today: number
  rejected_today: number
  // User-specific stats
  user_pending: number
  user_approved: number
  user_auto_approved: number
  user_rejected: number
}

// Stream field info for editing
export interface StreamFieldInfo {
  field_name: StreamFieldName
  display_name: string
  current_value: string | null
  field_type: 'text' | 'select' | 'multi_select'
  options?: string[]
}

export interface StreamEditableFields {
  stream_id: string
  stream_name: string
  fields: StreamFieldInfo[]
}

export interface StreamSuggestionListParams {
  status?: StreamSuggestionStatus
  page?: number
  page_size?: number
}

export interface BrokenReportStatus {
  stream_id: number
  is_blocked: boolean
  report_count: number
  threshold: number
  user_has_reported: boolean
  reports_needed: number
}

// API functions
export const streamSuggestionsApi = {
  // Create a stream suggestion
  createSuggestion: async (streamId: number, data: StreamSuggestionCreateRequest): Promise<StreamSuggestion> => {
    return apiClient.post<StreamSuggestion>(`/streams/${streamId}/suggest`, data)
  },

  // Get editable fields for a stream
  getEditableFields: async (streamId: number): Promise<StreamEditableFields> => {
    return apiClient.get<StreamEditableFields>(`/streams/${streamId}/editable-fields`)
  },

  // Get suggestions for a stream
  getStreamSuggestions: async (
    streamId: number,
    params: StreamSuggestionListParams = {},
  ): Promise<StreamSuggestionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.status) searchParams.set('status', params.status)
    if (params.page) searchParams.set('page', params.page.toString())
    if (params.page_size) searchParams.set('page_size', params.page_size.toString())

    const queryString = searchParams.toString()
    return apiClient.get<StreamSuggestionListResponse>(
      `/streams/${streamId}/suggestions${queryString ? `?${queryString}` : ''}`,
    )
  },

  // Get user's own stream suggestions
  getMySuggestions: async (params: StreamSuggestionListParams = {}): Promise<StreamSuggestionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.status) searchParams.set('status', params.status)
    if (params.page) searchParams.set('page', params.page.toString())
    if (params.page_size) searchParams.set('page_size', params.page_size.toString())

    const queryString = searchParams.toString()
    return apiClient.get<StreamSuggestionListResponse>(`/stream-suggestions/my${queryString ? `?${queryString}` : ''}`)
  },

  // Get pending suggestions (moderator only)
  getPendingSuggestions: async (
    params: Omit<StreamSuggestionListParams, 'status'> & { suggestion_type?: string } = {},
  ): Promise<StreamSuggestionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.page) searchParams.set('page', params.page.toString())
    if (params.page_size) searchParams.set('page_size', params.page_size.toString())
    if (params.suggestion_type) searchParams.set('suggestion_type', params.suggestion_type)

    const queryString = searchParams.toString()
    return apiClient.get<StreamSuggestionListResponse>(
      `/stream-suggestions/pending${queryString ? `?${queryString}` : ''}`,
    )
  },

  // Review a suggestion (moderator only)
  reviewSuggestion: async (suggestionId: string, data: StreamSuggestionReviewRequest): Promise<StreamSuggestion> => {
    return apiClient.put<StreamSuggestion>(`/stream-suggestions/${suggestionId}/review`, data)
  },

  // Bulk review suggestions (moderator only)
  bulkReview: async (
    suggestionIds: string[],
    action: 'approve' | 'reject',
    reviewNotes?: string,
  ): Promise<{ approved: number; rejected: number; skipped: number }> => {
    return apiClient.post('/stream-suggestions/bulk-review', {
      suggestion_ids: suggestionIds,
      action,
      review_notes: reviewNotes,
    })
  },

  // Delete a pending suggestion
  deleteSuggestion: async (suggestionId: string): Promise<void> => {
    await apiClient.delete(`/stream-suggestions/${suggestionId}`)
  },

  // Get stats
  getStats: async (): Promise<StreamSuggestionStats> => {
    return apiClient.get<StreamSuggestionStats>('/stream-suggestions/stats')
  },

  // Get broken report status for a stream
  getBrokenStatus: async (streamId: number): Promise<BrokenReportStatus> => {
    return apiClient.get<BrokenReportStatus>(`/streams/${streamId}/broken-status`)
  },
}
