import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { watchlistApi, type WatchlistParams, type AdvancedTorrentImport } from '@/lib/api/watchlist'

// Query keys
export const watchlistKeys = {
  all: ['watchlist'] as const,
  providers: (profileId?: number) => [...watchlistKeys.all, 'providers', profileId] as const,
  list: (provider: string, params: WatchlistParams) => [...watchlistKeys.all, 'list', provider, params] as const,
  missing: (provider: string, profileId?: number) => [...watchlistKeys.all, 'missing', provider, profileId] as const,
}

/**
 * Hook to get available watchlist providers for a profile
 */
export function useWatchlistProviders(profileId?: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: watchlistKeys.providers(profileId),
    queryFn: () => watchlistApi.getProviders(profileId),
    enabled: options?.enabled !== false,
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

/**
 * Hook to get watchlist items from a specific provider
 */
export function useWatchlist(
  provider: string | undefined,
  params: WatchlistParams = {},
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: watchlistKeys.list(provider || '', params),
    queryFn: () => watchlistApi.getWatchlist(provider!, params),
    enabled: options?.enabled !== false && !!provider,
    staleTime: 2 * 60 * 1000, // 2 minutes (watchlist can change)
  })
}

/**
 * Hook to get watchlist items with infinite scrolling
 */
export function useInfiniteWatchlist(
  provider: string | undefined,
  params: Omit<WatchlistParams, 'page'> = {},
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery({
    queryKey: watchlistKeys.list(provider || '', { ...params, page: 'infinite' as unknown as number }),
    queryFn: ({ pageParam = 1 }) => watchlistApi.getWatchlist(provider!, { ...params, page: pageParam }),
    getNextPageParam: (lastPage) => {
      if (lastPage.has_more) {
        return lastPage.page + 1
      }
      return undefined
    },
    initialPageParam: 1,
    enabled: options?.enabled !== false && !!provider,
    staleTime: 2 * 60 * 1000,
  })
}

/**
 * Hook to get missing torrents from a provider
 */
export function useMissingTorrents(provider: string | undefined, profileId?: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: watchlistKeys.missing(provider || '', profileId),
    queryFn: () => watchlistApi.getMissing(provider!, profileId),
    enabled: options?.enabled !== false && !!provider,
    staleTime: 30 * 1000, // 30 seconds - missing list can change frequently
  })
}

/**
 * Hook to import torrents from debrid account
 */
export function useImportTorrents() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      provider,
      infoHashes,
      profileId,
      overrides,
      isAnonymous,
      anonymousDisplayName,
    }: {
      provider: string
      infoHashes: string[]
      profileId?: number
      overrides?: Record<string, { title?: string; year?: number; type?: 'movie' | 'series' }>
      isAnonymous?: boolean
      anonymousDisplayName?: string
    }) => watchlistApi.importTorrents(provider, infoHashes, profileId, overrides, isAnonymous, anonymousDisplayName),
    onSuccess: (_data, variables) => {
      // Invalidate both missing and list queries after import
      queryClient.invalidateQueries({ queryKey: watchlistKeys.missing(variables.provider, variables.profileId) })
      queryClient.invalidateQueries({ queryKey: [...watchlistKeys.all, 'list', variables.provider] })
    },
  })
}

/**
 * Hook to remove a torrent from debrid account
 */
export function useRemoveTorrent() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ provider, infoHash, profileId }: { provider: string; infoHash: string; profileId?: number }) =>
      watchlistApi.removeTorrent(provider, infoHash, profileId),
    onSuccess: (_data, variables) => {
      // Invalidate watchlist queries after removal
      queryClient.invalidateQueries({ queryKey: [...watchlistKeys.all, 'list', variables.provider] })
      queryClient.invalidateQueries({ queryKey: watchlistKeys.missing(variables.provider, variables.profileId) })
    },
  })
}

/**
 * Hook to clear all torrents from debrid account
 */
export function useClearAllTorrents() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ provider, profileId }: { provider: string; profileId?: number }) =>
      watchlistApi.clearAll(provider, profileId),
    onSuccess: (_data, variables) => {
      // Invalidate all watchlist queries after clearing
      queryClient.invalidateQueries({ queryKey: [...watchlistKeys.all, 'list', variables.provider] })
      queryClient.invalidateQueries({ queryKey: watchlistKeys.missing(variables.provider, variables.profileId) })
    },
  })
}

/**
 * Hook for advanced import with file annotations (multi-content support)
 */
export function useAdvancedImport() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      provider,
      imports,
      profileId,
      isAnonymous,
      anonymousDisplayName,
    }: {
      provider: string
      imports: AdvancedTorrentImport[]
      profileId?: number
      isAnonymous?: boolean
      anonymousDisplayName?: string
    }) => watchlistApi.advancedImport(provider, imports, profileId, isAnonymous, anonymousDisplayName),
    onSuccess: (_data, variables) => {
      // Invalidate both missing and list queries after import
      queryClient.invalidateQueries({ queryKey: watchlistKeys.missing(variables.provider, variables.profileId) })
      queryClient.invalidateQueries({ queryKey: [...watchlistKeys.all, 'list', variables.provider] })
    },
  })
}
