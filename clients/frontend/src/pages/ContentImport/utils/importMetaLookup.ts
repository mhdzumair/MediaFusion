import { metadataApi, type ImportProvider, type MediaMatchSearchResult } from '@/lib/api'
import type { ExtendedMatch } from '../components/MatchResultsGrid'
import type { CombinedSearchResult } from '@/hooks/useCombinedMetadataSearch'

export function parseImportMetaId(metaId: string): { provider: ImportProvider; externalId: string } | null {
  const trimmed = metaId.trim()
  if (!trimmed) return null

  if (trimmed.startsWith('tt')) {
    return { provider: 'imdb', externalId: trimmed }
  }
  if (trimmed.startsWith('tmdb:')) {
    return { provider: 'tmdb', externalId: trimmed.slice(5) }
  }
  if (trimmed.startsWith('tvdb:')) {
    return { provider: 'tvdb', externalId: trimmed.slice(5) }
  }
  if (trimmed.startsWith('mal:')) {
    return { provider: 'mal', externalId: trimmed.slice(4) }
  }
  if (trimmed.startsWith('kitsu:')) {
    return { provider: 'kitsu', externalId: trimmed.slice(6) }
  }
  if (/^\d+$/.test(trimmed)) {
    return { provider: 'tmdb', externalId: trimmed }
  }

  return null
}

export function formatImportExternalId(metaId: string): string {
  const parsed = parseImportMetaId(metaId)
  if (!parsed) return metaId.trim()
  if (parsed.provider === 'imdb') return parsed.externalId
  return `${parsed.provider}:${parsed.externalId}`
}

export function mapSearchResultToExtendedMatch(
  result: MediaMatchSearchResult,
  contentType: 'movie' | 'series',
): ExtendedMatch {
  return {
    id: result.imdb_id || result.id,
    media_id: result.media_id,
    title: result.title,
    year: result.year,
    poster: result.poster,
    background: result.background,
    description: result.description ?? undefined,
    release_date: result.release_date,
    type: (result.type as 'movie' | 'series' | undefined) ?? contentType,
    imdb_id: result.imdb_id,
    imdb_rating: result.imdb_rating,
    runtime: result.runtime,
  }
}

export async function fetchImportMatchByMetaId(
  metaId: string,
  contentType: 'movie' | 'series',
): Promise<ExtendedMatch> {
  const externalId = formatImportExternalId(metaId)
  if (!parseImportMetaId(metaId)) {
    throw new Error('Enter a valid external ID (e.g. tt1234567, tmdb:603, tvdb:81189)')
  }

  const response = await metadataApi.searchMatches({
    external_id: externalId,
    media_type: contentType,
    limit: 1,
    include_user_content: true,
    include_catalog: true,
    include_external: true,
    include_official: true,
  })

  const result = response.results[0]
  if (!result?.title) {
    throw new Error('Metadata not found for that ID')
  }

  return mapSearchResultToExtendedMatch(result, contentType)
}

export async function fetchCombinedMatchByProviderId(
  provider: ImportProvider,
  externalId: string,
  metaType: 'movie' | 'series' | 'all',
): Promise<CombinedSearchResult> {
  const fullExternalId = provider === 'imdb' ? externalId.trim() : `${provider}:${externalId.trim()}`
  const response = await metadataApi.searchMatches({
    external_id: fullExternalId,
    media_type: metaType,
    limit: 1,
    include_user_content: true,
    include_catalog: true,
    include_external: true,
    include_official: true,
  })

  const result = response.results[0]
  if (!result?.title) {
    throw new Error('Metadata not found for that ID')
  }

  const resolvedType: 'movie' | 'series' = metaType === 'all' ? 'movie' : metaType
  return mapSearchResultToCombined(result, provider, externalId.trim(), resolvedType)
}

function mapSearchResultToCombined(
  result: MediaMatchSearchResult,
  provider: ImportProvider,
  rawExternalId: string,
  metaType: 'movie' | 'series',
): CombinedSearchResult {
  const isDatabase = result.source === 'database'
  const externalId =
    result.imdb_id ||
    (result.tmdb_id ? `tmdb:${result.tmdb_id}` : undefined) ||
    (result.tvdb_id ? `tvdb:${result.tvdb_id}` : undefined) ||
    rawExternalId

  return {
    id: isDatabase ? `internal-${result.media_id ?? result.id}` : `manual-${provider}-${rawExternalId}`,
    title: result.title,
    year: result.year,
    poster: result.poster,
    type: result.type ?? metaType,
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
    provider,
    description: result.description ?? undefined,
  }
}

export function applyImportMatchToForm(
  match: ExtendedMatch,
  setters: {
    setMetaId: (value: string) => void
    setTitle: (value: string) => void
    setPoster: (value: string) => void
    setBackground: (value: string) => void
    setReleaseDate: (value: string) => void
  },
): void {
  setters.setMetaId(match.imdb_id || match.id)
  setters.setTitle(match.title)
  setters.setPoster(match.poster ?? '')
  setters.setBackground(match.background ?? '')
  setters.setReleaseDate(match.release_date ?? '')
}
