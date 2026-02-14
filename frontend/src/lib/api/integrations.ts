/**
 * External Platform Integrations API
 */

import { apiClient } from './client'

// ============================================
// Types
// ============================================

export type IntegrationType = 'trakt' | 'simkl' | 'myanimelist' | 'anilist' | 'letterboxd' | 'tvtime'

export type SyncDirection = 'import' | 'export' | 'bidirectional'

export interface IntegrationStatus {
  platform: IntegrationType
  connected: boolean
  sync_enabled: boolean
  sync_direction: string
  last_sync_at: string | null
  last_sync_status: string | null
  last_sync_error: string | null
}

export interface IntegrationListResponse {
  profile_id: number
  integrations: IntegrationStatus[]
}

export interface OAuthUrlResponse {
  auth_url: string
  platform: IntegrationType
}

export interface SyncStatusResponse {
  platform: IntegrationType
  last_sync_at: string | null
  last_sync_status: string | null
  last_sync_error: string | null
  last_sync_stats: Record<string, number> | null
}

export interface SyncTriggerResponse {
  message: string
  sync_started: boolean
}

export interface IntegrationConfigUpdate {
  sync_enabled?: boolean
  sync_direction?: SyncDirection
  scrobble_enabled?: boolean
  min_watch_percent?: number
}

// ============================================
// Helper to build query string
// ============================================

function buildQueryString(params: Record<string, unknown>): string {
  const searchParams = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      searchParams.append(key, String(value))
    }
  }
  const qs = searchParams.toString()
  return qs ? `?${qs}` : ''
}

// ============================================
// API Functions
// ============================================

export const integrationsApi = {
  /**
   * List all integrations for a profile
   */
  list: async (profileId: number): Promise<IntegrationListResponse> => {
    return apiClient.get<IntegrationListResponse>(`/integrations${buildQueryString({ profile_id: profileId })}`)
  },

  /**
   * Get OAuth URL for a platform
   */
  getOAuthUrl: async (platform: IntegrationType, clientId?: string): Promise<OAuthUrlResponse> => {
    const qs = buildQueryString({ client_id: clientId })
    return apiClient.get<OAuthUrlResponse>(`/integrations/oauth/${platform}/url${qs}`)
  },

  /**
   * Connect Trakt with authorization code
   */
  connectTrakt: async (profileId: number, code: string, clientId?: string, clientSecret?: string): Promise<void> => {
    const qs = buildQueryString({ profile_id: profileId })
    return apiClient.post(`/integrations/trakt/connect${qs}`, {
      code,
      client_id: clientId,
      client_secret: clientSecret,
    })
  },

  /**
   * Connect Simkl with authorization code
   */
  connectSimkl: async (profileId: number, code: string, clientId?: string, clientSecret?: string): Promise<void> => {
    const qs = buildQueryString({ profile_id: profileId })
    return apiClient.post(`/integrations/simkl/connect${qs}`, {
      code,
      client_id: clientId,
      client_secret: clientSecret,
    })
  },

  /**
   * Disconnect an integration
   */
  disconnect: async (profileId: number, platform: IntegrationType): Promise<void> => {
    const qs = buildQueryString({ profile_id: profileId })
    return apiClient.delete(`/integrations/${platform}/disconnect${qs}`)
  },

  /**
   * Update integration settings
   */
  updateSettings: async (
    profileId: number,
    platform: IntegrationType,
    settings: IntegrationConfigUpdate,
  ): Promise<void> => {
    const qs = buildQueryString({ profile_id: profileId })
    return apiClient.patch(`/integrations/${platform}/settings${qs}`, settings)
  },

  /**
   * Get sync status for a platform
   */
  getSyncStatus: async (profileId: number, platform: IntegrationType): Promise<SyncStatusResponse> => {
    const qs = buildQueryString({ profile_id: profileId })
    return apiClient.get<SyncStatusResponse>(`/integrations/${platform}/status${qs}`)
  },

  /**
   * Trigger sync for a platform
   */
  triggerSync: async (
    profileId: number,
    platform: IntegrationType,
    direction?: SyncDirection,
    fullSync?: boolean,
  ): Promise<SyncTriggerResponse> => {
    const qs = buildQueryString({ profile_id: profileId, direction, full_sync: fullSync })
    return apiClient.post<SyncTriggerResponse>(`/integrations/${platform}/sync${qs}`)
  },

  /**
   * Trigger sync for all enabled platforms
   */
  triggerSyncAll: async (profileId: number): Promise<SyncTriggerResponse> => {
    const qs = buildQueryString({ profile_id: profileId })
    return apiClient.post<SyncTriggerResponse>(`/integrations/sync-all${qs}`)
  },
}
