import type { CombinedSearchResult } from '@/hooks'
import type { ImportProvider, UserMediaResponse } from '@/lib/api'

interface ExternalImportTarget {
  provider: ImportProvider
  externalId: string
}

const IMPORT_PROVIDER_PRIORITY: ImportProvider[] = ['imdb', 'tmdb', 'tvdb', 'mal', 'kitsu']

function normalizeImdbId(rawId: string): string {
  const trimmed = rawId.trim()
  if (!trimmed) return ''
  return trimmed.startsWith('tt') ? trimmed : `tt${trimmed}`
}

function normalizeExternalId(provider: ImportProvider, rawId: string): string {
  const trimmed = rawId.trim()
  if (!trimmed) return ''
  return provider === 'imdb' ? normalizeImdbId(trimmed) : trimmed
}

function getExternalIdsFromResult(result: CombinedSearchResult): Partial<Record<ImportProvider, string>> {
  const ids: Partial<Record<ImportProvider, string>> = {}

  if (result.imdb_id) ids.imdb = String(result.imdb_id)
  if (result.tmdb_id) ids.tmdb = String(result.tmdb_id)
  if (result.tvdb_id) ids.tvdb = String(result.tvdb_id)
  if (result.mal_id) ids.mal = String(result.mal_id)
  if (result.kitsu_id) ids.kitsu = String(result.kitsu_id)

  const externalIds = result.external_ids || {}
  if (!ids.imdb && externalIds.imdb != null) ids.imdb = String(externalIds.imdb)
  if (!ids.tmdb && externalIds.tmdb != null) ids.tmdb = String(externalIds.tmdb)
  if (!ids.tvdb && externalIds.tvdb != null) ids.tvdb = String(externalIds.tvdb)
  if (!ids.mal && externalIds.mal != null) ids.mal = String(externalIds.mal)
  if (!ids.mal && externalIds.mal_id != null) ids.mal = String(externalIds.mal_id)
  if (!ids.kitsu && externalIds.kitsu != null) ids.kitsu = String(externalIds.kitsu)
  if (!ids.kitsu && externalIds.kitsu_id != null) ids.kitsu = String(externalIds.kitsu_id)

  if (result.external_id) {
    const externalId = String(result.external_id).trim()

    if (externalId.startsWith('tt') && !ids.imdb) {
      ids.imdb = externalId
    } else {
      const prefixedMatch = externalId.match(/^(imdb|tmdb|tvdb|mal|kitsu):(.+)$/i)
      if (prefixedMatch) {
        const provider = prefixedMatch[1].toLowerCase() as ImportProvider
        const value = prefixedMatch[2]
        if (!ids[provider]) {
          ids[provider] = value
        }
      } else if (result.provider) {
        const provider = result.provider.toLowerCase() as ImportProvider
        if (IMPORT_PROVIDER_PRIORITY.includes(provider) && !ids[provider]) {
          ids[provider] = externalId
        }
      }
    }
  }

  return ids
}

export function resolveExternalImportTarget(result: CombinedSearchResult): ExternalImportTarget | null {
  const ids = getExternalIdsFromResult(result)

  for (const provider of IMPORT_PROVIDER_PRIORITY) {
    const rawId = ids[provider]
    if (!rawId) continue
    const externalId = normalizeExternalId(provider, rawId)
    if (!externalId) continue
    return { provider, externalId }
  }

  return null
}

export function toImportedInternalResult(
  imported: UserMediaResponse,
  fallback: CombinedSearchResult,
): CombinedSearchResult {
  return {
    id: `internal-${imported.id}`,
    title: imported.title || fallback.title,
    year: imported.year ?? fallback.year,
    poster: imported.poster_url || fallback.poster,
    type: imported.type || fallback.type,
    source: 'internal',
    internal_id: imported.id,
    external_id: imported.external_ids?.imdb || fallback.external_id,
    external_ids: imported.external_ids,
    is_user_created: imported.is_user_created,
    is_own: true,
    imdb_id: imported.external_ids?.imdb || fallback.imdb_id,
    tmdb_id: imported.external_ids?.tmdb || fallback.tmdb_id,
    tvdb_id: imported.external_ids?.tvdb || fallback.tvdb_id,
    mal_id: imported.external_ids?.mal || fallback.mal_id,
    kitsu_id: imported.external_ids?.kitsu || fallback.kitsu_id,
    anilist_id: imported.external_ids?.anilist || fallback.anilist_id,
  }
}
