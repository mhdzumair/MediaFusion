/**
 * Combined Metadata Search Hook
 *
 * Uses the unified POST /metadata/search/matches endpoint which searches
 * user-accessible DB content, global catalog, and external providers.
 */

import { useQuery } from '@tanstack/react-query'
import { metadataApi, type MediaMatchSearchResult } from '@/lib/api'

export interface CombinedSearchResult {
  id: string
  title: string
  year?: number
  poster?: string
  type: string
  source: 'internal' | 'external'

  internal_id?: number
  external_id?: string
  is_user_created?: boolean
  is_own?: boolean

  imdb_id?: string
  tmdb_id?: string
  tvdb_id?: string | number
  mal_id?: string | number
  kitsu_id?: string | number
  anilist_id?: string | number
  external_ids?: Record<string, string | number | null>
  provider?: string
  description?: string
}

export interface UseCombinedSearchOptions {
  query: string
  type?: 'movie' | 'series' | 'all'
  sources?: ('internal' | 'external')[]
  limit?: number
  year?: number
  includeAnime?: boolean
  animeSources?: ('kitsu' | 'anilist')[]
}

function matchToCombined(result: MediaMatchSearchResult, fallbackType?: string): CombinedSearchResult {
  const isDatabase = result.source === 'database'
  const externalId =
    result.imdb_id ||
    (result.tmdb_id ? `tmdb:${result.tmdb_id}` : undefined) ||
    (result.tvdb_id ? `tvdb:${result.tvdb_id}` : undefined) ||
    (result.mal_id ? `mal:${result.mal_id}` : undefined) ||
    (result.kitsu_id ? `kitsu:${result.kitsu_id}` : undefined) ||
    result.id

  return {
    id: isDatabase ? `internal-${result.media_id ?? result.id}` : `external-${externalId}`,
    title: result.title,
    year: result.year,
    poster: result.poster,
    type: result.type ?? fallbackType ?? 'movie',
    source: isDatabase ? 'internal' : 'external',
    internal_id: result.media_id,
    external_id: externalId,
    is_user_created: result.is_user_created,
    is_own: result.is_own,
    imdb_id: result.imdb_id,
    tmdb_id: result.tmdb_id,
    tvdb_id: result.tvdb_id,
    mal_id: result.mal_id,
    kitsu_id: result.kitsu_id,
    anilist_id: result.anilist_id,
    provider: result.provider,
    description: result.description ?? undefined,
  }
}

export const combinedSearchKeys = {
  all: ['combined-metadata-search'] as const,
  search: (query: string, type?: string, year?: number, limit?: number, sources?: ('internal' | 'external')[]) =>
    [...combinedSearchKeys.all, { query, type, year, limit, sources }] as const,
}

export function useCombinedMetadataSearch(params: UseCombinedSearchOptions, options?: { enabled?: boolean }) {
  const { query, type = 'all', sources = ['internal', 'external'], limit = 15, year } = params
  const searchInternal = sources.includes('internal')
  const searchExternal = sources.includes('external')
  const isEnabled = options?.enabled !== false && query.length >= 2

  const searchQuery = useQuery<CombinedSearchResult[]>({
    queryKey: combinedSearchKeys.search(query, type, year, limit, sources),
    queryFn: async (): Promise<CombinedSearchResult[]> => {
      const response = await metadataApi.searchMatches({
        title: query,
        year,
        media_type: type === 'all' ? 'all' : type,
        limit,
        include_user_content: searchInternal,
        include_official: true,
        include_catalog: searchInternal || searchExternal,
        include_external: searchExternal,
      })

      return (response.results || []).map((result) => matchToCombined(result, type === 'all' ? undefined : type))
    },
    enabled: isEnabled && (searchInternal || searchExternal),
    staleTime: 30 * 1000,
  })

  const results = searchQuery.data ?? []

  return {
    data: results,
    isLoading: searchQuery.isLoading && results.length === 0,
    isFetching: searchQuery.isFetching,
    hasPartialResults: results.length > 0 && searchQuery.isFetching,
    isError: searchQuery.isError,
    internalStatus: searchInternal ? searchQuery.status : 'idle',
    externalMovieStatus: searchExternal ? searchQuery.status : 'idle',
    externalSeriesStatus: searchExternal ? searchQuery.status : 'idle',
  }
}

export function getBestExternalId(result: CombinedSearchResult): string {
  return (
    result.imdb_id ||
    result.external_id ||
    (result.tmdb_id ? `tmdb:${result.tmdb_id}` : '') ||
    (result.tvdb_id ? `tvdb:${result.tvdb_id}` : '') ||
    (result.mal_id ? `mal:${result.mal_id}` : '') ||
    (result.kitsu_id ? `kitsu:${result.kitsu_id}` : '') ||
    (result.anilist_id ? `anilist:${result.anilist_id}` : '') ||
    result.id
  )
}
