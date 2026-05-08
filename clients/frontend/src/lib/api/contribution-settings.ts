import { apiClient } from './client'

export interface ContributionSettings {
  id: string
  auto_approval_threshold: number
  points_per_metadata_edit: number
  points_per_stream_edit: number
  points_for_rejection_penalty: number
  contributor_threshold: number
  trusted_threshold: number
  expert_threshold: number
  allow_auto_approval: boolean
  require_reason_for_edits: boolean
  max_pending_suggestions_per_user: number
}

export interface ContributionSettingsUpdate {
  auto_approval_threshold?: number
  points_per_metadata_edit?: number
  points_per_stream_edit?: number
  points_for_rejection_penalty?: number
  contributor_threshold?: number
  trusted_threshold?: number
  expert_threshold?: number
  allow_auto_approval?: boolean
  require_reason_for_edits?: boolean
  max_pending_suggestions_per_user?: number
}

export interface ContributionLevel {
  name: string
  display_name: string
  description: string
  min_points: number
  max_points: number | null
  can_auto_approve: boolean
}

export interface ContributionLevelsInfo {
  levels: ContributionLevel[]
  current_settings: ContributionSettings
}

export const contributionSettingsApi = {
  /**
   * Get current contribution settings (admin only)
   */
  get: async (): Promise<ContributionSettings> => {
    return apiClient.get<ContributionSettings>('/admin/contribution-settings')
  },

  /**
   * Update contribution settings (admin only)
   */
  update: async (data: ContributionSettingsUpdate): Promise<ContributionSettings> => {
    return apiClient.put<ContributionSettings>('/admin/contribution-settings', data)
  },

  /**
   * Get contribution levels info (admin only)
   */
  getLevels: async (): Promise<ContributionLevelsInfo> => {
    return apiClient.get<ContributionLevelsInfo>('/admin/contribution-levels')
  },

  /**
   * Reset settings to defaults (admin only)
   */
  reset: async (): Promise<ContributionSettings> => {
    return apiClient.post<ContributionSettings>('/admin/contribution-settings/reset')
  },
}
