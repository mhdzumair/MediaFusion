import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { discoverApi, type DiscoverPage } from '@/lib/api/discover'

export const discoverKeys = {
  all: ['discover'] as const,
  trending: (mediaType: string, window: string, language?: string) =>
    [...discoverKeys.all, 'trending', mediaType, window, language ?? ''] as const,
  list: (kind: string, mediaType: string, region?: string, language?: string) =>
    [...discoverKeys.all, 'list', kind, mediaType, region ?? 'US', language ?? ''] as const,
  watchProviders: (mediaType: string, region: string) =>
    [...discoverKeys.all, 'watchProviders', mediaType, region] as const,
  providerFeed: (mediaType: string, providerId: number, region: string, language?: string) =>
    [...discoverKeys.all, 'providerFeed', mediaType, providerId, region, language ?? ''] as const,
  anime: (kind: string, season?: string, year?: number, source?: string) =>
    [...discoverKeys.all, 'anime', kind, season ?? '', year ?? 0, source ?? 'anilist'] as const,
  search: (query: string, mediaType: string, language?: string) =>
    [...discoverKeys.all, 'search', query, mediaType, language ?? ''] as const,
  tvdb: (mediaType: string) => [...discoverKeys.all, 'tvdb', mediaType] as const,
  mdblist: (listId: number, catalogType: string) => [...discoverKeys.all, 'mdblist', listId, catalogType] as const,
}

function getNextPage(last: DiscoverPage): number | undefined {
  return last.page < last.total_pages ? last.page + 1 : undefined
}

export function useDiscoverTrending(
  mediaType: 'movie' | 'tv' | 'all' = 'all',
  window: 'day' | 'week' = 'week',
  enabled = true,
  language?: string,
) {
  return useInfiniteQuery<DiscoverPage>({
    queryKey: discoverKeys.trending(mediaType, window, language),
    queryFn: ({ pageParam }) =>
      discoverApi.trending({ media_type: mediaType, window, language, page: pageParam as number }),
    getNextPageParam: getNextPage,
    initialPageParam: 1,
    enabled,
    staleTime: 10 * 60 * 1000,
  })
}

export function useDiscoverList(
  kind: 'popular' | 'top_rated' | 'now_playing' | 'upcoming',
  mediaType: 'movie' | 'tv',
  region?: string,
  enabled = true,
  language?: string,
) {
  return useInfiniteQuery<DiscoverPage>({
    queryKey: discoverKeys.list(kind, mediaType, region, language),
    queryFn: ({ pageParam }) =>
      discoverApi.list({ kind, media_type: mediaType, region, language, page: pageParam as number }),
    getNextPageParam: getNextPage,
    initialPageParam: 1,
    enabled,
    staleTime: 10 * 60 * 1000,
  })
}

export function useWatchProviders(mediaType: 'movie' | 'tv', region = 'US', enabled = true) {
  return useQuery({
    queryKey: discoverKeys.watchProviders(mediaType, region),
    queryFn: () => discoverApi.watchProviders({ media_type: mediaType, region }),
    enabled,
    staleTime: 60 * 60 * 1000,
  })
}

export function useDiscoverProviderFeed(
  mediaType: 'movie' | 'tv',
  providerId: number | null,
  region = 'US',
  enabled = true,
  language?: string,
) {
  return useInfiniteQuery<DiscoverPage>({
    queryKey: discoverKeys.providerFeed(mediaType, providerId ?? 0, region, language),
    queryFn: ({ pageParam }) =>
      discoverApi.providerFeed({
        media_type: mediaType,
        provider_id: providerId!,
        region,
        sort_by: 'primary_release_date.desc',
        language,
        page: pageParam as number,
      }),
    getNextPageParam: getNextPage,
    initialPageParam: 1,
    enabled: enabled && !!providerId,
    staleTime: 15 * 60 * 1000,
  })
}

export function useDiscoverAnime(
  kind: 'trending' | 'seasonal' = 'trending',
  season?: string,
  year?: number,
  source: 'anilist' | 'kitsu' = 'anilist',
  enabled = true,
) {
  return useInfiniteQuery<DiscoverPage>({
    queryKey: discoverKeys.anime(kind, season, year, source),
    queryFn: ({ pageParam }) => discoverApi.anime({ kind, season, year, source, page: pageParam as number }),
    getNextPageParam: getNextPage,
    initialPageParam: 1,
    enabled,
    staleTime: 15 * 60 * 1000,
  })
}

export function useDiscoverSearch(
  query: string,
  mediaType: 'movie' | 'tv' | 'all' = 'all',
  enabled = true,
  language?: string,
) {
  return useInfiniteQuery<DiscoverPage>({
    queryKey: discoverKeys.search(query, mediaType, language),
    queryFn: ({ pageParam }) =>
      discoverApi.search({ query, media_type: mediaType, language, page: pageParam as number }),
    getNextPageParam: getNextPage,
    initialPageParam: 1,
    enabled: enabled && query.trim().length > 0,
    staleTime: 5 * 60 * 1000,
  })
}

export function useDiscoverTvdb(mediaType: 'movie' | 'tv' = 'tv', enabled = true) {
  return useInfiniteQuery<DiscoverPage>({
    queryKey: discoverKeys.tvdb(mediaType),
    queryFn: ({ pageParam }) =>
      discoverApi.tvdbFilter({ media_type: mediaType, sort: 'score', sort_type: 'desc', page: pageParam as number }),
    getNextPageParam: getNextPage,
    initialPageParam: 1,
    enabled,
    staleTime: 30 * 60 * 1000,
  })
}

export function useDiscoverMdblist(listId: number, catalogType: 'movie' | 'series', enabled = true) {
  return useInfiniteQuery<DiscoverPage>({
    queryKey: discoverKeys.mdblist(listId, catalogType),
    queryFn: ({ pageParam }) =>
      discoverApi.mdblist({ list_id: listId, catalog_type: catalogType, page: pageParam as number }),
    getNextPageParam: getNextPage,
    initialPageParam: 1,
    enabled,
    staleTime: 15 * 60 * 1000,
  })
}
