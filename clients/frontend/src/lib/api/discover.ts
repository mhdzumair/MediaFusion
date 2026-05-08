/**
 * Discover API client — browse external catalogs (TMDB, AniList, Kitsu)
 * without requiring streams to exist in MediaFusion.
 */

import { apiClient } from './client'
import type { ImportProvider } from './user-metadata'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface DiscoverItem {
  provider: ImportProvider | 'anilist'
  external_id: string
  media_type: 'movie' | 'series'
  title: string
  year?: string | null
  release_date?: string | null
  poster?: string | null
  backdrop?: string | null
  overview?: string
  popularity?: number
  vote_average?: number
  genre_ids?: number[]
  genres?: string[]
  imdb_id?: string | null
}

export interface DiscoverDbEntry {
  id: number
  imdb_id?: string | null
}

export interface DiscoverPage {
  items: DiscoverItem[]
  page: number
  total_pages: number
  total_results: number
  /** "<provider>:<external_id>" -> { id: Media.id, imdb_id? } for already-imported titles */
  db_index: Record<string, DiscoverDbEntry>
}

export interface WatchProvider {
  provider_id: number
  name: string
  logo?: string | null
}

export interface WatchProvidersResponse {
  providers: WatchProvider[]
  region: string
}

export interface TmdbKeyVerification {
  valid: boolean
  error?: string
}

// ─── API ──────────────────────────────────────────────────────────────────────

export const discoverApi = {
  /** TMDB trending (movie/tv/all, day/week) */
  trending: (params: {
    media_type?: 'movie' | 'tv' | 'all'
    window?: 'day' | 'week'
    language?: string
    page?: number
  }): Promise<DiscoverPage> => {
    const q = new URLSearchParams()
    if (params.media_type) q.set('media_type', params.media_type)
    if (params.window) q.set('window', params.window)
    if (params.language) q.set('language', params.language)
    if (params.page) q.set('page', String(params.page))
    return apiClient.get<DiscoverPage>(`/discover/trending?${q}`)
  },

  /** Named TMDB lists: popular, top_rated, now_playing, upcoming */
  list: (params: {
    kind: 'popular' | 'top_rated' | 'now_playing' | 'upcoming'
    media_type: 'movie' | 'tv'
    page?: number
    region?: string
    language?: string
  }): Promise<DiscoverPage> => {
    const q = new URLSearchParams({ kind: params.kind, media_type: params.media_type })
    if (params.page) q.set('page', String(params.page))
    if (params.region) q.set('region', params.region)
    if (params.language) q.set('language', params.language)
    return apiClient.get<DiscoverPage>(`/discover/list?${q}`)
  },

  /** Available watch providers for a region */
  watchProviders: (params: { media_type: 'movie' | 'tv'; region?: string }): Promise<WatchProvidersResponse> => {
    const q = new URLSearchParams({ media_type: params.media_type })
    if (params.region) q.set('region', params.region)
    return apiClient.get<WatchProvidersResponse>(`/discover/watch-providers?${q}`)
  },

  /** Per-OTT-provider feed (e.g. New on Netflix) */
  providerFeed: (params: {
    media_type: 'movie' | 'tv'
    provider_id: number
    region?: string
    sort_by?: string
    language?: string
    page?: number
  }): Promise<DiscoverPage> => {
    const q = new URLSearchParams({
      media_type: params.media_type,
      provider_id: String(params.provider_id),
    })
    if (params.region) q.set('region', params.region)
    if (params.sort_by) q.set('sort_by', params.sort_by)
    if (params.language) q.set('language', params.language)
    if (params.page) q.set('page', String(params.page))
    return apiClient.get<DiscoverPage>(`/discover/provider-feed?${q}`)
  },

  /** Anime trending / seasonal from AniList or Kitsu */
  anime: (params: {
    kind?: 'trending' | 'seasonal'
    season?: string
    year?: number
    source?: 'anilist' | 'kitsu'
    page?: number
  }): Promise<DiscoverPage> => {
    const q = new URLSearchParams()
    if (params.kind) q.set('kind', params.kind)
    if (params.season) q.set('season', params.season)
    if (params.year) q.set('year', String(params.year))
    if (params.source) q.set('source', params.source)
    if (params.page) q.set('page', String(params.page))
    return apiClient.get<DiscoverPage>(`/discover/anime?${q}`)
  },

  /** Search TMDB by title */
  search: (params: {
    query: string
    media_type?: 'movie' | 'tv' | 'all'
    language?: string
    page?: number
  }): Promise<DiscoverPage> => {
    const q = new URLSearchParams({ query: params.query })
    if (params.media_type) q.set('media_type', params.media_type)
    if (params.language) q.set('language', params.language)
    if (params.page) q.set('page', String(params.page))
    return apiClient.get<DiscoverPage>(`/discover/search?${q}`)
  },

  /** TVDB popular/trending series or movies */
  tvdbFilter: (params: {
    media_type?: 'movie' | 'tv'
    sort?: string
    sort_type?: string
    page?: number
  }): Promise<DiscoverPage> => {
    const q = new URLSearchParams()
    if (params.media_type) q.set('media_type', params.media_type)
    if (params.sort) q.set('sort', params.sort)
    if (params.sort_type) q.set('sort_type', params.sort_type)
    if (params.page) q.set('page', String(params.page))
    return apiClient.get<DiscoverPage>(`/discover/tvdb-filter?${q}`)
  },

  /** Items from a user's MDBList list */
  mdblist: (params: { list_id: number; catalog_type: 'movie' | 'series'; page?: number }): Promise<DiscoverPage> => {
    const q = new URLSearchParams({
      list_id: String(params.list_id),
      catalog_type: params.catalog_type,
    })
    if (params.page) q.set('page', String(params.page))
    return apiClient.get<DiscoverPage>(`/discover/mdblist?${q}`)
  },

  /** Verify a TMDB API key */
  verifyTmdbKey: (api_key: string): Promise<TmdbKeyVerification> => {
    return apiClient.get<TmdbKeyVerification>(`/discover/verify-tmdb-key?api_key=${encodeURIComponent(api_key)}`)
  },
}

/** Return the db_index lookup key for a DiscoverItem */
export function discoverDbKey(item: Pick<DiscoverItem, 'provider' | 'external_id'>): string {
  return `${item.provider}:${item.external_id}`
}
