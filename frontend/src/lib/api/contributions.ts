import { apiClient } from './client'

export type ContributionType = 'metadata' | 'stream' | 'torrent'
export type ContributionStatus = 'pending' | 'approved' | 'rejected'

export interface ContributionData {
  [key: string]: unknown
}

export interface Contribution {
  id: string
  user_id: string
  contribution_type: ContributionType
  target_id?: string
  data: ContributionData
  status: ContributionStatus
  reviewed_by?: string
  reviewed_at?: string
  review_notes?: string
  created_at: string
  updated_at?: string
}

export interface ContributionListParams {
  contribution_type?: ContributionType
  contribution_status?: ContributionStatus
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

export interface ContributionStats {
  total_contributions: number
  pending: number
  approved: number
  rejected: number
  by_type: {
    metadata: number
    stream: number
    torrent: number
  }
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

export const contributionsApi = {
  /**
   * List contributions (user's own or all for mods)
   */
  list: async (params: ContributionListParams = {}): Promise<ContributionListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.contribution_type) searchParams.append('contribution_type', params.contribution_type)
    if (params.contribution_status) searchParams.append('contribution_status', params.contribution_status)
    if (params.page) searchParams.append('page', params.page.toString())
    if (params.page_size) searchParams.append('page_size', params.page_size.toString())

    const query = searchParams.toString()
    return apiClient.get<ContributionListResponse>(`/contributions${query ? `?${query}` : ''}`)
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
   * Get overall contribution stats (Mod+ only)
   */
  getAllStats: async (): Promise<ContributionStats> => {
    return apiClient.get<ContributionStats>('/contributions/review/stats')
  },
}
