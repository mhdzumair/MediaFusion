import type { CatalogStreamInfo } from '@/lib/api'
import { LANGUAGES } from '@/pages/Configure/components/constants'

export const DEFAULT_LANGUAGE_SORT_ORDER = LANGUAGES.filter((lang): lang is string => lang !== null)

export type StreamSortKey =
  | 'cached'
  | 'resolution'
  | 'quality'
  | 'language'
  | 'size'
  | 'seeders'
  | 'created_at'
  | 'source'
  | 'watched_count'

export type StreamSortDirection = 'asc' | 'desc'

export interface StreamSortOption {
  k: StreamSortKey
  d: StreamSortDirection
}

export const STREAM_SORT_OPTIONS: {
  key: StreamSortKey
  label: string
  desc: string
  asc: string
}[] = [
  { key: 'cached', label: 'Cached', desc: 'Cached first', asc: 'Uncached first' },
  { key: 'resolution', label: 'Resolution', desc: 'Highest first', asc: 'Lowest first' },
  { key: 'quality', label: 'Quality', desc: 'Best first', asc: 'Lower first' },
  { key: 'language', label: 'Language', desc: 'Preferred first', asc: 'Least preferred first' },
  { key: 'size', label: 'Size', desc: 'Largest first', asc: 'Smallest first' },
  { key: 'seeders', label: 'Seeders', desc: 'Most first', asc: 'Fewest first' },
  { key: 'created_at', label: 'Added', desc: 'Newest first', asc: 'Oldest first' },
  { key: 'source', label: 'Source', desc: 'A → Z', asc: 'Z → A' },
  { key: 'watched_count', label: 'Watched', desc: 'Most watched first', asc: 'Least watched first' },
]

export const DEFAULT_STREAM_SORT_PRIORITY: StreamSortOption[] = [
  { k: 'cached', d: 'desc' },
  { k: 'resolution', d: 'desc' },
  { k: 'quality', d: 'desc' },
  { k: 'seeders', d: 'desc' },
]

function getResolutionRank(resolution?: string): number {
  const res = (resolution || '').toLowerCase()
  if (res.includes('4k') || res.includes('2160')) return 4
  if (res.includes('1080')) return 3
  if (res.includes('720')) return 2
  if (res.includes('480')) return 1
  return 0
}

const QUALITY_TIERS = ['bluray', 'web-dl', 'webrip', 'hdrip', 'hdtv', 'dvdrip', 'cam']

function getQualityRank(quality?: string): number {
  const q = (quality || '').toLowerCase()
  for (let i = 0; i < QUALITY_TIERS.length; i++) {
    if (q.includes(QUALITY_TIERS[i])) return QUALITY_TIERS.length - i
  }
  return 0
}

function parseSizeBytes(stream: CatalogStreamInfo): number {
  if (stream.size_bytes && stream.size_bytes > 0) return stream.size_bytes
  if (!stream.size) return 0
  const match = stream.size.match(/([\d.]+)\s*(GB|MB|KB|TB)/i)
  if (!match) return 0
  const [, num, unit] = match
  const multipliers: Record<string, number> = { KB: 1, MB: 1024, GB: 1024 * 1024, TB: 1024 * 1024 * 1024 }
  return parseFloat(num) * (multipliers[unit.toUpperCase()] || 1)
}

function parseCreatedAtTs(stream: CatalogStreamInfo): number {
  const raw = (stream as CatalogStreamInfo & { created_at?: string }).created_at
  if (!raw) return 0
  const ts = Date.parse(raw)
  return Number.isFinite(ts) ? ts : 0
}

function getLanguageRank(stream: CatalogStreamInfo, languageSorting: string[]): number {
  if (languageSorting.length === 0 || !stream.languages?.length) return languageSorting.length
  let best = languageSorting.length
  for (const lang of stream.languages) {
    const idx = languageSorting.findIndex((pref) => pref.toLowerCase() === lang.toLowerCase())
    if (idx >= 0 && idx < best) best = idx
  }
  return best
}

function compareSortValue(a: number, b: number, direction: StreamSortDirection): number {
  if (a === b) return 0
  return direction === 'asc' ? a - b : b - a
}

export function compareStreamsByPriority(
  a: CatalogStreamInfo,
  b: CatalogStreamInfo,
  priority: StreamSortOption[],
  languageSorting: string[] = [],
): number {
  for (const opt of priority) {
    let cmp = 0
    switch (opt.k) {
      case 'cached':
        cmp = compareSortValue(a.cached ? 1 : 0, b.cached ? 1 : 0, opt.d)
        break
      case 'resolution':
        cmp = compareSortValue(getResolutionRank(a.resolution), getResolutionRank(b.resolution), opt.d)
        break
      case 'quality':
        cmp = compareSortValue(getQualityRank(a.quality), getQualityRank(b.quality), opt.d)
        break
      case 'language':
        cmp = compareSortValue(
          getLanguageRank(a, languageSorting),
          getLanguageRank(b, languageSorting),
          opt.d === 'desc' ? 'asc' : 'desc',
        )
        break
      case 'size':
        cmp = compareSortValue(parseSizeBytes(a), parseSizeBytes(b), opt.d)
        break
      case 'seeders':
        cmp = compareSortValue(a.seeders || 0, b.seeders || 0, opt.d)
        break
      case 'created_at':
        cmp = compareSortValue(parseCreatedAtTs(a), parseCreatedAtTs(b), opt.d)
        break
      case 'source':
        cmp = (a.source || '').localeCompare(b.source || '', undefined, { sensitivity: 'base' })
        if (cmp !== 0 && opt.d === 'desc') cmp = -cmp
        break
      case 'watched_count':
        cmp = compareSortValue(a.watched_count || 0, b.watched_count || 0, opt.d)
        break
    }
    if (cmp !== 0) return cmp
  }
  return 0
}

export function sortStreams(
  streams: CatalogStreamInfo[],
  priority: StreamSortOption[],
  languageSorting: string[] = DEFAULT_LANGUAGE_SORT_ORDER,
): CatalogStreamInfo[] {
  if (priority.length === 0) return streams
  return [...streams].sort((a, b) => compareStreamsByPriority(a, b, priority, languageSorting))
}
