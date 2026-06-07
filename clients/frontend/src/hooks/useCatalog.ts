import { useQuery } from '@tanstack/react-query'
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
    streamId?: string,
  ) => [...catalogKeys.all, 'streams', type, id, season, episode, profileId, provider, streamId] as const,
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

// Browse catalog with filters (paginated, supports explicit page)
export function useCatalogList(
  catalogType: CatalogType,
  params: CatalogListParams = {},
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: catalogKeys.list(catalogType, params),
    queryFn: () => catalogApi.browseCatalog(catalogType, params),
    staleTime: 5 * 60 * 1000, // 5 minutes
    enabled: options?.enabled ?? true,
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
  options?: { enabled?: boolean; streamId?: string },
  profileUuid?: string,
) {
  const streamId = options?.streamId
  return useQuery({
    queryKey: catalogKeys.streams(catalogType, mediaId.toString(), season, episode, profileId, provider, streamId),
    queryFn: () =>
      catalogApi.getStreams(catalogType, mediaId, season, episode, profileId, provider, profileUuid, streamId),
    // Require profileId and provider to be set to avoid unnecessary API calls
    // Movie and TV channels don't need season/episode, series does (unless stream_id deep link)
    enabled:
      options?.enabled !== false &&
      !!mediaId &&
      profileId !== undefined &&
      provider !== undefined &&
      (catalogType === 'movie' ||
        catalogType === 'tv' ||
        (season !== undefined && episode !== undefined) ||
        !!streamId),
    staleTime: 2 * 60 * 1000, // 2 minutes (streams can change)
    refetchOnMount: 'always', // Always refetch when navigating to the detail page
  })
}

// Export types for convenience
export type { CatalogType, SortOption, SortDirection, CatalogListParams }
