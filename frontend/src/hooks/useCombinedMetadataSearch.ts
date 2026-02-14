/**
 * Combined Metadata Search Hook
 *
 * Searches both internal database (user-created + official media) AND
 * external providers (TMDB, IMDB, TVDB, MAL, Kitsu) in parallel.
 * Results are merged and deduplicated, with internal results prioritized.
 *
 * Shows results progressively as each source completes - no waiting for all sources.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { userMetadataApi, metadataApi } from '@/lib/api'
import type { MetadataSearchResult, ExternalSearchResult } from '@/lib/api'

// Unified search result type that works for both internal and external results
export interface CombinedSearchResult {
  id: string // Unique identifier for React keys
  title: string
  year?: number
  poster?: string
  type: string // 'movie' | 'series'
  source: 'internal' | 'external'

  // Internal-specific fields
  internal_id?: number // Database ID for internal results
  external_id?: string // External ID (e.g., tt1234567) for internal results
  is_user_created?: boolean
  is_own?: boolean

  // External-specific fields
  imdb_id?: string
  tmdb_id?: string
  tvdb_id?: string | number
  provider?: string // 'imdb', 'tmdb', 'tvdb', 'mal', 'kitsu'
  description?: string
}

export interface UseCombinedSearchOptions {
  query: string
  type?: 'movie' | 'series' | 'all'
  sources?: ('internal' | 'external')[] // Default: both
  limit?: number
}

// Convert internal search result to combined format
function internalToCombined(result: MetadataSearchResult): CombinedSearchResult {
  return {
    id: `internal-${result.id}`,
    title: result.title,
    year: result.year,
    poster: result.poster,
    type: result.type,
    source: 'internal',
    internal_id: result.id,
    external_id: result.external_id,
    is_user_created: result.is_user_created,
    is_own: result.is_own,
    // Extract IMDB ID if it looks like one
    imdb_id: result.external_id?.startsWith('tt') ? result.external_id : undefined,
  }
}

// Convert external search result to combined format
function externalToCombined(result: ExternalSearchResult, metaType?: 'movie' | 'series'): CombinedSearchResult {
  return {
    id: `external-${result.imdb_id || result.tmdb_id || result.id}`,
    title: result.title,
    year: result.year,
    poster: result.poster,
    type: metaType || (result.provider === 'tvdb' ? 'series' : 'movie'),
    source: 'external',
    imdb_id: result.imdb_id,
    tmdb_id: result.tmdb_id,
    tvdb_id: result.tvdb_id,
    provider: result.provider,
    description: result.description,
    // Use imdb_id as external_id if available, otherwise construct from tmdb
    external_id: result.imdb_id || (result.tmdb_id ? `tmdb:${result.tmdb_id}` : result.id),
  }
}

// Deduplicate results, prioritizing internal over external
function deduplicateResults(
  internalResults: CombinedSearchResult[],
  externalResults: CombinedSearchResult[],
): CombinedSearchResult[] {
  const seen = new Map<string, CombinedSearchResult>()

  // Add internal results first (they take priority)
  for (const result of internalResults) {
    // Key by IMDB ID if available, otherwise by normalized title+year
    const key =
      result.imdb_id || result.external_id || `${result.title.toLowerCase().trim()}-${result.year || 'unknown'}`
    seen.set(key, result)
  }

  // Add external results only if not already present
  for (const result of externalResults) {
    const key =
      result.imdb_id || result.external_id || `${result.title.toLowerCase().trim()}-${result.year || 'unknown'}`

    if (!seen.has(key)) {
      seen.set(key, result)
    }
  }

  return Array.from(seen.values())
}

// Sort results: internal first, then by year (newest first)
function sortResults(results: CombinedSearchResult[]): CombinedSearchResult[] {
  return [...results].sort((a, b) => {
    // Internal results first
    if (a.source !== b.source) {
      return a.source === 'internal' ? -1 : 1
    }
    // Then by year (newest first)
    return (b.year || 0) - (a.year || 0)
  })
}

// Query keys for caching
export const combinedSearchKeys = {
  all: ['combined-metadata-search'] as const,
  internal: (query: string, type?: string) => [...combinedSearchKeys.all, 'internal', { query, type }] as const,
  external: (query: string, type?: string) => [...combinedSearchKeys.all, 'external', { query, type }] as const,
}

/**
 * Combined metadata search hook that searches both internal and external sources
 * Results appear progressively as each source completes
 */
export function useCombinedMetadataSearch(params: UseCombinedSearchOptions, options?: { enabled?: boolean }) {
  const { query, type = 'all', sources = ['internal', 'external'], limit = 15 } = params
  const searchInternal = sources.includes('internal')
  const searchExternal = sources.includes('external')
  const isEnabled = options?.enabled !== false && query.length >= 2

  // Internal search query
  const internalQuery = useQuery({
    queryKey: combinedSearchKeys.internal(query, type),
    queryFn: async (): Promise<CombinedSearchResult[]> => {
      try {
        const response = await userMetadataApi.searchAll({
          query,
          type: type === 'all' ? undefined : type,
          limit,
          include_official: true,
        })
        return (response.results || []).map(internalToCombined)
      } catch {
        return []
      }
    },
    enabled: isEnabled && searchInternal,
    staleTime: 30 * 1000,
  })

  // External search query (movie)
  const externalMovieQuery = useQuery({
    queryKey: combinedSearchKeys.external(query, 'movie'),
    queryFn: async (): Promise<CombinedSearchResult[]> => {
      try {
        const response = await metadataApi.searchExternal(query, 'movie')
        return (response.results || []).map((r) => externalToCombined(r, 'movie'))
      } catch {
        return []
      }
    },
    enabled: isEnabled && searchExternal && (type === 'all' || type === 'movie'),
    staleTime: 30 * 1000,
  })

  // External search query (series)
  const externalSeriesQuery = useQuery({
    queryKey: combinedSearchKeys.external(query, 'series'),
    queryFn: async (): Promise<CombinedSearchResult[]> => {
      try {
        const response = await metadataApi.searchExternal(query, 'series')
        return (response.results || []).map((r) => externalToCombined(r, 'series'))
      } catch {
        return []
      }
    },
    enabled: isEnabled && searchExternal && (type === 'all' || type === 'series'),
    staleTime: 30 * 1000,
  })

  // Combine results from all sources
  const data = useMemo(() => {
    const internalResults = internalQuery.data || []
    const externalMovieResults = externalMovieQuery.data || []
    const externalSeriesResults = externalSeriesQuery.data || []

    // Combine external results
    const externalResults = [...externalMovieResults, ...externalSeriesResults]

    // Deduplicate and merge
    const combined = deduplicateResults(internalResults, externalResults)

    // Sort and limit
    return sortResults(combined).slice(0, limit)
  }, [internalQuery.data, externalMovieQuery.data, externalSeriesQuery.data, limit])

  // Determine loading states
  const isLoading =
    (searchInternal && internalQuery.isLoading) ||
    (searchExternal && (type === 'all' || type === 'movie') && externalMovieQuery.isLoading) ||
    (searchExternal && (type === 'all' || type === 'series') && externalSeriesQuery.isLoading)

  // Still loading if any query is fetching but we have some results
  const isFetching = internalQuery.isFetching || externalMovieQuery.isFetching || externalSeriesQuery.isFetching

  // Has partial results (some queries complete, some still loading)
  const hasPartialResults = data.length > 0 && isFetching

  // Error if all enabled queries failed
  const isError =
    (!searchInternal || internalQuery.isError) &&
    (!searchExternal || (type !== 'movie' && type !== 'all') || externalMovieQuery.isError) &&
    (!searchExternal || (type !== 'series' && type !== 'all') || externalSeriesQuery.isError)

  return {
    data,
    isLoading: isLoading && data.length === 0, // Only show loading if no results yet
    isFetching, // True if any query is still fetching
    hasPartialResults, // True if we have some results but more are loading
    isError,
    // Individual query states for debugging
    internalStatus: searchInternal ? internalQuery.status : 'idle',
    externalMovieStatus: searchExternal && (type === 'all' || type === 'movie') ? externalMovieQuery.status : 'idle',
    externalSeriesStatus: searchExternal && (type === 'all' || type === 'series') ? externalSeriesQuery.status : 'idle',
  }
}

/**
 * Get the best external ID from a combined search result
 * Useful for import operations
 */
export function getBestExternalId(result: CombinedSearchResult): string {
  return (
    result.imdb_id ||
    result.external_id ||
    (result.tmdb_id ? `tmdb:${result.tmdb_id}` : '') ||
    (result.tvdb_id ? `tvdb:${result.tvdb_id}` : '') ||
    result.id
  )
}
