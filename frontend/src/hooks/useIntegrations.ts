/**
 * React hooks for external platform integrations
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/AuthContext'
import { useDefaultProfile } from '@/hooks/useProfiles'
import { integrationsApi } from '@/lib/api/integrations'
import type {
  IntegrationConfigUpdate,
  IntegrationListResponse,
  IntegrationType,
  SyncDirection,
  SyncStatusResponse,
} from '@/lib/api/integrations'

// Query keys
const INTEGRATIONS_KEY = 'integrations'

/**
 * Hook to fetch all integrations for the current profile
 */
export function useIntegrations() {
  const { user } = useAuth()
  const defaultProfile = useDefaultProfile()
  const profileId = defaultProfile?.id

  return useQuery<IntegrationListResponse>({
    queryKey: [INTEGRATIONS_KEY, 'list', profileId],
    queryFn: () => integrationsApi.list(profileId!),
    enabled: !!user && !!profileId,
  })
}

/**
 * Hook to get sync status for a specific platform
 */
export function useSyncStatus(platform: IntegrationType) {
  const { user } = useAuth()
  const defaultProfile = useDefaultProfile()
  const profileId = defaultProfile?.id

  return useQuery<SyncStatusResponse>({
    queryKey: [INTEGRATIONS_KEY, 'status', platform, profileId],
    queryFn: () => integrationsApi.getSyncStatus(profileId!, platform),
    enabled: !!user && !!profileId,
  })
}

/**
 * Hook to get OAuth URL for a platform
 */
export function useOAuthUrl() {
  return useMutation({
    mutationFn: ({ platform, clientId }: { platform: IntegrationType; clientId?: string }) =>
      integrationsApi.getOAuthUrl(platform, clientId),
  })
}

/**
 * Hook to connect Trakt
 */
export function useConnectTrakt() {
  const defaultProfile = useDefaultProfile()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ code, clientId, clientSecret }: { code: string; clientId?: string; clientSecret?: string }) =>
      integrationsApi.connectTrakt(defaultProfile!.id, code, clientId, clientSecret),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [INTEGRATIONS_KEY] })
    },
  })
}

/**
 * Hook to connect Simkl
 */
export function useConnectSimkl() {
  const defaultProfile = useDefaultProfile()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ code, clientId, clientSecret }: { code: string; clientId?: string; clientSecret?: string }) =>
      integrationsApi.connectSimkl(defaultProfile!.id, code, clientId, clientSecret),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [INTEGRATIONS_KEY] })
    },
  })
}

/**
 * Hook to disconnect an integration
 */
export function useDisconnectIntegration() {
  const defaultProfile = useDefaultProfile()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (platform: IntegrationType) =>
      integrationsApi.disconnect(defaultProfile!.id, platform),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [INTEGRATIONS_KEY] })
    },
  })
}

/**
 * Hook to update integration settings
 */
export function useUpdateIntegrationSettings() {
  const defaultProfile = useDefaultProfile()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      platform,
      settings,
    }: {
      platform: IntegrationType
      settings: IntegrationConfigUpdate
    }) => integrationsApi.updateSettings(defaultProfile!.id, platform, settings),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [INTEGRATIONS_KEY] })
    },
  })
}

/**
 * Hook to trigger sync for a platform
 */
export function useTriggerSync() {
  const defaultProfile = useDefaultProfile()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      platform,
      direction,
      fullSync,
    }: {
      platform: IntegrationType
      direction?: SyncDirection
      fullSync?: boolean
    }) => integrationsApi.triggerSync(defaultProfile!.id, platform, direction, fullSync),
    onSuccess: () => {
      // Invalidate sync status after triggering
      queryClient.invalidateQueries({ queryKey: [INTEGRATIONS_KEY, 'status'] })
    },
  })
}

/**
 * Hook to trigger sync for all platforms
 */
export function useTriggerSyncAll() {
  const defaultProfile = useDefaultProfile()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => integrationsApi.triggerSyncAll(defaultProfile!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [INTEGRATIONS_KEY, 'status'] })
    },
  })
}
