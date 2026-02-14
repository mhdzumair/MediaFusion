import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { contributionSettingsApi, type ContributionSettingsUpdate } from '@/lib/api'

export const contributionSettingsKeys = {
  all: ['contribution-settings'] as const,
  settings: () => [...contributionSettingsKeys.all, 'settings'] as const,
  levels: () => [...contributionSettingsKeys.all, 'levels'] as const,
}

/**
 * Get contribution settings (admin only)
 */
export function useContributionSettings() {
  return useQuery({
    queryKey: contributionSettingsKeys.settings(),
    queryFn: () => contributionSettingsApi.get(),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

/**
 * Get contribution levels info (admin only)
 */
export function useContributionLevels() {
  return useQuery({
    queryKey: contributionSettingsKeys.levels(),
    queryFn: () => contributionSettingsApi.getLevels(),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

/**
 * Update contribution settings (admin only)
 */
export function useUpdateContributionSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ContributionSettingsUpdate) => contributionSettingsApi.update(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: contributionSettingsKeys.all })
    },
  })
}

/**
 * Reset contribution settings to defaults (admin only)
 */
export function useResetContributionSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => contributionSettingsApi.reset(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: contributionSettingsKeys.all })
    },
  })
}
