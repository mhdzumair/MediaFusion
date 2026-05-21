import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { discoverApi, type DiscoverPage } from '@/lib/api/discover'

export const discoverKeys = {
  all: ['discover'] as const,
  trending: (mediaType: string, window: string, language?: string, page?: number) =>
    [...discoverKeys.all, 'trending', mediaType, window, language ?? '', page ?? 1] as const,
  list: (kind: string, mediaType: string, region?: string, language?: string, page?: number) =>
    [...discoverKeys.all, 'list', kind, mediaType, region ?? 'US', language ?? '', page ?? 1] as const,
  watchProviders: (mediaType: string, region: string) =>
    [...discoverKeys.all, 'watchProviders', mediaType, region] as const,
  providerFeed: (mediaType: string, providerId: number, region: string, language?: string, page?: number) =>
    [...discoverKeys.all, 'providerFeed', mediaType, providerId, region, language ?? '', page ?? 1] as const,
  anime: (kind: string, season?: string, year?: number, source?: string, page?: number) =>
    [...discoverKeys.all, 'anime', kind, season ?? '', year ?? 0, source ?? 'anilist', page ?? 1] as const,
  search: (query: string, mediaType: string, language?: string, page?: number) =>
    [...discoverKeys.all, 'search', query, mediaType, language ?? '', page ?? 1] as const,
  tvdb: (mediaType: string, page?: number) => [...discoverKeys.all, 'tvdb', mediaType, page ?? 1] as const,
  mdblist: (listId: number, catalogType: string, page?: number) =>
    [...discoverKeys.all, 'mdblist', listId, catalogType, page ?? 1] as const,
}

export type DiscoverSource =
  | {
      kind: 'trending'
      mediaType: 'movie' | 'tv' | 'all'
      window: 'day' | 'week'
      language?: string
      enabled?: boolean
    }
  | {
      kind: 'list'
      listKind: 'popular' | 'top_rated' | 'now_playing' | 'upcoming'
      mediaType: 'movie' | 'tv'
      region?: string
      language?: string
      enabled?: boolean
    }
  | {
      kind: 'providerFeed'
      mediaType: 'movie' | 'tv'
      providerId: number
      region?: string
      language?: string
      enabled?: boolean
    }
  | {
      kind: 'anime'
      animeKind: 'trending' | 'seasonal'
      season?: string
      year?: number
      source?: 'anilist' | 'kitsu'
      enabled?: boolean
    }
  | { kind: 'tvdb'; mediaType: 'movie' | 'tv'; enabled?: boolean }
  | { kind: 'mdblist'; listId: number; catalogType: 'movie' | 'series'; enabled?: boolean }
  | {
      kind: 'search'
      query: string
      mediaType: 'movie' | 'tv' | 'all'
      language?: string
      enabled?: boolean
    }

function isDiscoverSourceEnabled(source: DiscoverSource): boolean {
  if (source.enabled === false) return false
  if (source.kind === 'search') return source.query.trim().length > 0
  if (source.kind === 'providerFeed') return !!source.providerId
  return true
}

async function fetchDiscoverPage(source: DiscoverSource, page: number): Promise<DiscoverPage> {
  switch (source.kind) {
    case 'trending':
      return discoverApi.trending({
        media_type: source.mediaType,
        window: source.window,
        language: source.language,
        page,
      })
    case 'list':
      return discoverApi.list({
        kind: source.listKind,
        media_type: source.mediaType,
        region: source.region,
        language: source.language,
        page,
      })
    case 'providerFeed':
      return discoverApi.providerFeed({
        media_type: source.mediaType,
        provider_id: source.providerId,
        region: source.region ?? 'US',
        sort_by: 'primary_release_date.desc',
        language: source.language,
        page,
      })
    case 'anime':
      return discoverApi.anime({
        kind: source.animeKind,
        season: source.season,
        year: source.year,
        source: source.source ?? 'anilist',
        page,
      })
    case 'tvdb':
      return discoverApi.tvdbFilter({
        media_type: source.mediaType,
        sort: 'score',
        sort_type: 'desc',
        page,
      })
    case 'mdblist':
      return discoverApi.mdblist({
        list_id: source.listId,
        catalog_type: source.catalogType,
        page,
      })
    case 'search':
      return discoverApi.search({
        query: source.query,
        media_type: source.mediaType,
        language: source.language,
        page,
      })
  }
}

export function useDiscoverSource(source: DiscoverSource, page: number) {
  return useQuery<DiscoverPage>({
    queryKey: [...discoverKeys.all, 'source', source, page],
    queryFn: () => fetchDiscoverPage(source, page),
    enabled: isDiscoverSourceEnabled(source),
    staleTime: 10 * 60 * 1000,
    placeholderData: keepPreviousData,
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

// Paginated discover hooks (for direct use outside DiscoverTab rows)
export function useDiscoverTrending(
  mediaType: 'movie' | 'tv' | 'all' = 'all',
  window: 'day' | 'week' = 'week',
  enabled = true,
  language?: string,
  page = 1,
) {
  return useDiscoverSource({ kind: 'trending', mediaType, window, language, enabled }, page)
}

export function useDiscoverList(
  kind: 'popular' | 'top_rated' | 'now_playing' | 'upcoming',
  mediaType: 'movie' | 'tv',
  region?: string,
  enabled = true,
  language?: string,
  page = 1,
) {
  return useDiscoverSource({ kind: 'list', listKind: kind, mediaType, region, language, enabled }, page)
}

export function useDiscoverProviderFeed(
  mediaType: 'movie' | 'tv',
  providerId: number | null,
  region = 'US',
  enabled = true,
  language?: string,
  page = 1,
) {
  return useDiscoverSource(
    {
      kind: 'providerFeed',
      mediaType,
      providerId: providerId!,
      region,
      language,
      enabled: enabled && !!providerId,
    },
    page,
  )
}

export function useDiscoverAnime(
  kind: 'trending' | 'seasonal' = 'trending',
  season?: string,
  year?: number,
  source: 'anilist' | 'kitsu' = 'anilist',
  enabled = true,
  page = 1,
) {
  return useDiscoverSource({ kind: 'anime', animeKind: kind, season, year, source, enabled }, page)
}

export function useDiscoverSearch(
  query: string,
  mediaType: 'movie' | 'tv' | 'all' = 'all',
  enabled = true,
  language?: string,
  page = 1,
) {
  return useDiscoverSource(
    { kind: 'search', query, mediaType, language, enabled: enabled && query.trim().length > 0 },
    page,
  )
}

export function useDiscoverTvdb(mediaType: 'movie' | 'tv' = 'tv', enabled = true, page = 1) {
  return useDiscoverSource({ kind: 'tvdb', mediaType, enabled }, page)
}

export function useDiscoverMdblist(listId: number, catalogType: 'movie' | 'series', enabled = true, page = 1) {
  return useDiscoverSource({ kind: 'mdblist', listId, catalogType, enabled }, page)
}
