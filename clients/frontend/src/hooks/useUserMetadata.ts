/**
 * React Query hooks for user metadata management.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  userMetadataApi,
  type UserMediaCreate,
  type UserMediaUpdate,
  type SeasonAddRequest,
  type EpisodeAddRequest,
  type EpisodeUpdateRequest,
} from '@/lib/api'

// Query keys
export const userMetadataKeys = {
  all: ['user-metadata'] as const,
  lists: () => [...userMetadataKeys.all, 'list'] as const,
  list: (params: { page?: number; per_page?: number; type?: string; search?: string }) =>
    [...userMetadataKeys.lists(), params] as const,
  details: () => [...userMetadataKeys.all, 'detail'] as const,
  detail: (id: number) => [...userMetadataKeys.details(), id] as const,
  search: (params: { query: string; type?: string }) => [...userMetadataKeys.all, 'search', params] as const,
}

// ============================================
// List & Detail Hooks
// ============================================

/**
 * Fetch list of user-created metadata
 */
export function useUserMetadataList(params?: {
  page?: number
  per_page?: number
  type?: 'movie' | 'series' | 'tv' | 'all'
  search?: string
}) {
  return useQuery({
    queryKey: userMetadataKeys.list(params || {}),
    queryFn: () => userMetadataApi.list(params),
  })
}

/**
 * Fetch single user metadata by ID
 */
export function useUserMetadata(mediaId: number | undefined) {
  return useQuery({
    queryKey: userMetadataKeys.detail(mediaId!),
    queryFn: () => userMetadataApi.get(mediaId!),
    enabled: !!mediaId,
  })
}

// ============================================
// Mutation Hooks
// ============================================

/**
 * Create new user metadata
 */
export function useCreateUserMetadata() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: UserMediaCreate) => userMetadataApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.lists() })
    },
  })
}

/**
 * Update user metadata
 */
export function useUpdateUserMetadata() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, data }: { mediaId: number; data: UserMediaUpdate }) =>
      userMetadataApi.update(mediaId, data),
    onSuccess: (_, { mediaId }) => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.detail(mediaId) })
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.lists() })
    },
  })
}

/**
 * Delete user metadata
 */
export function useDeleteUserMetadata() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, force }: { mediaId: number; force?: boolean }) => userMetadataApi.delete(mediaId, force),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.lists() })
    },
  })
}

// ============================================
// Season/Episode Hooks
// ============================================

/**
 * Add a season to a series
 */
export function useAddSeason() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, data }: { mediaId: number; data: SeasonAddRequest }) =>
      userMetadataApi.addSeason(mediaId, data),
    onSuccess: (_, { mediaId }) => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.detail(mediaId) })
    },
  })
}

/**
 * Add episodes to a season
 */
export function useAddEpisodes() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, data }: { mediaId: number; data: EpisodeAddRequest }) =>
      userMetadataApi.addEpisodes(mediaId, data),
    onSuccess: (_, { mediaId }) => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.detail(mediaId) })
    },
  })
}

/**
 * Update an episode
 */
export function useUpdateEpisode() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, episodeId, data }: { mediaId: number; episodeId: number; data: EpisodeUpdateRequest }) =>
      userMetadataApi.updateEpisode(mediaId, episodeId, data),
    onSuccess: (_, { mediaId }) => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.detail(mediaId) })
    },
  })
}

/**
 * Delete an episode
 */
export function useDeleteEpisode() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, episodeId }: { mediaId: number; episodeId: number }) =>
      userMetadataApi.deleteEpisode(mediaId, episodeId),
    onSuccess: (_, { mediaId }) => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.detail(mediaId) })
    },
  })
}

/**
 * Delete a season
 */
export function useDeleteSeason() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, seasonNumber }: { mediaId: number; seasonNumber: number }) =>
      userMetadataApi.deleteSeason(mediaId, seasonNumber),
    onSuccess: (_, { mediaId }) => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.detail(mediaId) })
    },
  })
}

// ============================================
// Moderator-only Hooks
// ============================================

/**
 * Delete an episode (moderator only)
 * Bypasses ownership check - for cleaning up orphaned episodes
 * @param deleteStreamLinks - Also delete file-media links for this episode
 */
export function useDeleteEpisodeAdmin() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      mediaId,
      episodeId,
      deleteStreamLinks = false,
    }: {
      mediaId: number
      episodeId: number
      deleteStreamLinks?: boolean
    }) => userMetadataApi.deleteEpisodeAdmin(mediaId, episodeId, deleteStreamLinks),
    onSuccess: (_, { mediaId }) => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.detail(mediaId) })
      // Also invalidate catalog queries since episode structure changed
      queryClient.invalidateQueries({ queryKey: ['catalog'] })
    },
  })
}

/**
 * Delete a season (moderator only)
 * Bypasses ownership check - for cleaning up orphaned seasons
 */
export function useDeleteSeasonAdmin() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, seasonNumber }: { mediaId: number; seasonNumber: number }) =>
      userMetadataApi.deleteSeasonAdmin(mediaId, seasonNumber),
    onSuccess: (_, { mediaId }) => {
      queryClient.invalidateQueries({ queryKey: userMetadataKeys.detail(mediaId) })
      // Also invalidate catalog queries since season structure changed
      queryClient.invalidateQueries({ queryKey: ['catalog'] })
    },
  })
}

// ============================================
// Search Hook
// ============================================

/**
 * Search all metadata (user-created and official) for linking
 */
export function useMetadataSearch(
  params: {
    query: string
    type?: 'movie' | 'series' | 'all'
    limit?: number
    include_official?: boolean
  },
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: userMetadataKeys.search({ query: params.query, type: params.type }),
    queryFn: () => userMetadataApi.searchAll(params),
    enabled: options?.enabled !== false && params.query.length >= 2,
    staleTime: 30 * 1000, // 30 seconds for search results
  })
}
