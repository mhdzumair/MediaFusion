import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { profilesApi, type ProfileCreateRequest, type ProfileUpdateRequest } from '@/lib/api'

// Query keys
export const profileKeys = {
  all: ['profiles'] as const,
  list: () => [...profileKeys.all, 'list'] as const,
  detail: (id: number) => [...profileKeys.all, 'detail', id] as const,
  manifestUrl: (id: number) => [...profileKeys.all, 'manifest', id] as const,
  rpdbKey: () => [...profileKeys.all, 'rpdb-key'] as const,
}

// List profiles
export function useProfiles() {
  return useQuery({
    queryKey: profileKeys.list(),
    queryFn: () => profilesApi.list(),
  })
}

// Get single profile
export function useProfile(profileId: number | undefined) {
  return useQuery({
    queryKey: profileKeys.detail(profileId!),
    queryFn: () => profilesApi.get(profileId!),
    enabled: !!profileId,
  })
}

// Get default profile
export function useDefaultProfile() {
  const { data: profiles } = useProfiles()
  return profiles?.find((p) => p.is_default) || profiles?.[0]
}

// Create profile
export function useCreateProfile() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ProfileCreateRequest) => profilesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: profileKeys.list() })
    },
  })
}

// Update profile
export function useUpdateProfile() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ profileId, data }: { profileId: number; data: ProfileUpdateRequest }) =>
      profilesApi.update(profileId, data),
    onSuccess: (updatedProfile) => {
      // Invalidate all profile-related queries to force refetch
      queryClient.invalidateQueries({ queryKey: profileKeys.all })
      // Also set the updated data directly for immediate UI update
      queryClient.setQueryData(profileKeys.detail(updatedProfile.id), updatedProfile)
    },
  })
}

// Delete profile
export function useDeleteProfile() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (profileId: number) => profilesApi.delete(profileId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: profileKeys.list() })
    },
  })
}

// Set default profile
export function useSetDefaultProfile() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (profileId: number) => profilesApi.setDefault(profileId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: profileKeys.list() })
    },
  })
}

// Get manifest URL
export function useManifestUrl(profileId: number | undefined) {
  return useQuery({
    queryKey: profileKeys.manifestUrl(profileId!),
    queryFn: () => profilesApi.getManifestUrl(profileId!),
    enabled: !!profileId,
  })
}

// Get RPDB API key from default profile (for poster display)
export function useRpdbApiKey(enabled: boolean = true) {
  return useQuery({
    queryKey: profileKeys.rpdbKey(),
    queryFn: () => profilesApi.getRpdbApiKey(),
    enabled,
    staleTime: 5 * 60 * 1000, // 5 minutes - RPDB key doesn't change often
  })
}
