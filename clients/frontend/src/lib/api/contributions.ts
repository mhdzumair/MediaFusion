import { apiClient } from './client'

export type ContributionType = 'metadata' | 'stream' | 'torrent' | 'telegram' | 'youtube' | 'nzb' | 'http' | 'acestream'
export type ContributionStatus = 'pending' | 'approved' | 'rejected'

export interface ContributionData {
  [key: string]: unknown
}

export interface Contribution {
  id: string
  user_id: number | null
  username?: string | null
  contribution_type: ContributionType
  target_id?: string
  media_id?: number | null
  mediafusion_id?: string | null
  data: ContributionData
  status: ContributionStatus
  reviewed_by?: string
  reviewer_name?: string | null
  reviewed_at?: string
  review_notes?: string
  admin_review_requested?: boolean
  admin_review_requested_by?: string | null
  admin_review_requested_at?: string | null
  admin_review_reason?: string | null
  created_at: string
  updated_at?: string
}

export interface ContributionListParams {
  contribution_type?: ContributionType
  contribution_status?: ContributionStatus
  contributor?: string
  uploader_query?: string
  reviewer_query?: string
  me_only?: boolean
  page?: number
  page_size?: number
}

export interface ContributionListResponse {
  items: Contribution[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface ContributionContributor {
  key: string
  label: string
  user_id?: number | null
  anonymous_display_name?: string | null
  total: number
  pending: number
  approved: number
  rejected: number
}

export interface ContributionContributorListParams {
  contribution_type?: ContributionType
  contribution_status?: ContributionStatus
  query?: string
  limit?: number
}

export interface ContributionContributorListResponse {
  items: ContributionContributor[]
}

export interface ContributionStats {
  total_contributions: number
  pending: number
  approved: number
  rejected: number
  by_type: Record<string, number>
}

export interface ContributionCreateRequest {
  contribution_type: ContributionType
  target_id?: string
  data: ContributionData
}

export interface ContributionReviewRequest {
  status: 'approved' | 'rejected'
  review_notes?: string
}

export interface ContributionBulkReviewRequest {
  action: 'approve' | 'reject'
  contribution_type?: ContributionType
  contribution_ids?: string[]
  review_notes?: string
}

export interface ContributionBulkReviewResponse {
  approved: number
  rejected: number
  skipped: number
}

export interface ContributionAdminFlagRequest {
  reason?: string
}

export interface ContributionAdminRejectRequest {
  review_notes?: string
}

export const contributionsApi = {
  /**
   * List contributions (user's own or all for mods)
   */
  list: async (params: ContributionListParams = {}): Promise<ContributionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.contribution_type) searchParams.append('contribution_type', params.contribution_type)
    if (params.contribution_status) searchParams.append('contribution_status', params.contribution_status)
    if (params.contributor) searchParams.append('contributor', params.contributor)
    if (params.uploader_query) searchParams.append('uploader_query', params.uploader_query)
    if (params.reviewer_query) searchParams.append('reviewer_query', params.reviewer_query)
    if (params.me_only) searchParams.append('me_only', 'true')
    if (params.page) searchParams.append('page', params.page.toString())
    if (params.page_size) searchParams.append('page_size', params.page_size.toString())

    const query = searchParams.toString()
    return apiClient.get<ContributionListResponse>(`/contributions${query ? `?${query}` : ''}`)
  },

  /**
   * List contributor options for moderator filtering
   */
  listContributors: async (
    params: ContributionContributorListParams = {},
  ): Promise<ContributionContributorListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.contribution_type) searchParams.append('contribution_type', params.contribution_type)
    if (params.contribution_status) searchParams.append('contribution_status', params.contribution_status)
    if (params.query) searchParams.append('query', params.query)
    if (params.limit) searchParams.append('limit', params.limit.toString())

    const query = searchParams.toString()
    return apiClient.get<ContributionContributorListResponse>(`/contributions/contributors${query ? `?${query}` : ''}`)
  },

  /**
   * Get contribution statistics for current user
   */
  getStats: async (): Promise<ContributionStats> => {
    return apiClient.get<ContributionStats>('/contributions/stats')
  },

  /**
   * Get a specific contribution
   */
  get: async (contributionId: string): Promise<Contribution> => {
    return apiClient.get<Contribution>(`/contributions/${contributionId}`)
  },

  /**
   * Submit a new contribution
   */
  create: async (data: ContributionCreateRequest): Promise<Contribution> => {
    return apiClient.post<Contribution>('/contributions', data)
  },

  /**
   * Delete a contribution (only pending, owner only)
   */
  delete: async (contributionId: string): Promise<void> => {
    await apiClient.delete(`/contributions/${contributionId}`)
  },

  // Moderator endpoints

  /**
   * List pending contributions for review (Mod+ only)
   */
  listPending: async (
    params: { contribution_type?: ContributionType; page?: number; page_size?: number } = {},
  ): Promise<ContributionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.contribution_type) searchParams.append('contribution_type', params.contribution_type)
    if (params.page) searchParams.append('page', params.page.toString())
    if (params.page_size) searchParams.append('page_size', params.page_size.toString())

    const query = searchParams.toString()
    return apiClient.get<ContributionListResponse>(`/contributions/review/pending${query ? `?${query}` : ''}`)
  },

  /**
   * Review a contribution (Mod+ only)
   */
  review: async (contributionId: string, data: ContributionReviewRequest): Promise<Contribution> => {
    return apiClient.patch<Contribution>(`/contributions/${contributionId}/review`, data)
  },

  /**
   * Flag approved contribution for admin review (Mod+)
   */
  flagForAdminReview: async (contributionId: string, data: ContributionAdminFlagRequest): Promise<Contribution> => {
    return apiClient.patch<Contribution>(`/contributions/${contributionId}/flag-admin-review`, data)
  },

  /**
   * Reject approved contribution with rollback (Mod+)
   */
  rejectApproved: async (contributionId: string, data: ContributionAdminRejectRequest): Promise<Contribution> => {
    return apiClient.patch<Contribution>(`/contributions/${contributionId}/reject-approved`, data)
  },

  /**
   * Bulk review pending contributions (Mod+ only)
   */
  bulkReview: async (data: ContributionBulkReviewRequest): Promise<ContributionBulkReviewResponse> => {
    return apiClient.post<ContributionBulkReviewResponse>('/contributions/review/bulk', data)
  },

  /**
   * Get overall contribution stats (Mod+ only)
   */
  getAllStats: async (): Promise<ContributionStats> => {
    return apiClient.get<ContributionStats>('/contributions/review/stats')
  },
}
