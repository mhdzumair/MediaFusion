import { useQuery, useInfiniteQuery } from '@tanstack/react-query'
import { catalogApi, type CatalogType, type CatalogListParams, type SortOption, type SortDirection } from '@/lib/api'

// Query keys
export const catalogKeys = {
  all: ['catalog'] as const,
  availableCatalogs: () => [...catalogKeys.all, 'available'] as const,
  genres: (type: CatalogType) => [...catalogKeys.all, 'genres', type] as const,
  list: (type: CatalogType, params: CatalogListParams) => [...catalogKeys.all, 'list', type, params] as const,
  item: (type: CatalogType, id: string | number) => [...catalogKeys.all, 'item', type, id] as const,
  streams: (
    type: CatalogType,
    id: string | number,
    season?: number,
    episode?: number,
    profileId?: number,
    provider?: string,
  ) => [...catalogKeys.all, 'streams', type, id, season, episode, profileId, provider] as const,
}

// Get available catalogs
export function useAvailableCatalogs() {
  return useQuery({
    queryKey: catalogKeys.availableCatalogs(),
    queryFn: () => catalogApi.getAvailableCatalogs(),
    staleTime: 10 * 60 * 1000, // 10 minutes
  })
}

// Get genres for a catalog type
export function useGenres(catalogType: CatalogType) {
  return useQuery({
    queryKey: catalogKeys.genres(catalogType),
    queryFn: () => catalogApi.getGenres(catalogType),
    staleTime: 10 * 60 * 1000, // 10 minutes
  })
}

// Browse catalog with filters (paginated)
export function useCatalogList(catalogType: CatalogType, params: Omit<CatalogListParams, 'page'> = {}) {
  return useQuery({
    queryKey: catalogKeys.list(catalogType, params),
    queryFn: () => catalogApi.browseCatalog(catalogType, params),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

// Browse catalog with infinite loading
export function useInfiniteCatalog(catalogType: CatalogType, params: Omit<CatalogListParams, 'page'> = {}) {
  return useInfiniteQuery({
    queryKey: [...catalogKeys.list(catalogType, params), 'infinite'],
    queryFn: ({ pageParam = 1 }) => catalogApi.browseCatalog(catalogType, { ...params, page: pageParam }),
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.page + 1 : undefined),
    initialPageParam: 1,
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

// Get catalog item details
export function useCatalogItem(catalogType: CatalogType, mediaId: number, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: catalogKeys.item(catalogType, mediaId.toString()),
    queryFn: () => catalogApi.getCatalogItem(catalogType, mediaId),
    enabled: (options?.enabled ?? true) && !!mediaId,
    staleTime: 10 * 60 * 1000, // 10 minutes
  })
}

// Get streams for a catalog item
export function useCatalogStreams(
  catalogType: 'movie' | 'series' | 'tv',
  mediaId: number,
  season?: number,
  episode?: number,
  profileId?: number,
  provider?: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: catalogKeys.streams(catalogType, mediaId.toString(), season, episode, profileId, provider),
    queryFn: () => catalogApi.getStreams(catalogType, mediaId, season, episode, profileId, provider),
    // Require profileId and provider to be set to avoid unnecessary API calls
    // Movie and TV channels don't need season/episode, series does
    enabled:
      options?.enabled !== false &&
      !!mediaId &&
      profileId !== undefined &&
      provider !== undefined &&
      (catalogType === 'movie' || catalogType === 'tv' || (season !== undefined && episode !== undefined)),
    staleTime: 2 * 60 * 1000, // 2 minutes (streams can change)
    refetchOnMount: 'always', // Always refetch when navigating to the detail page
  })
}

// Export types for convenience
export type { CatalogType, SortOption, SortDirection, CatalogListParams }
