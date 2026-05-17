import { CheckCircle2, Clock, XCircle } from 'lucide-react'

import type { Contribution, StreamSuggestion, Suggestion, SuggestionStatus } from '@/lib/api'

export type ReviewDecision = 'approve' | 'reject'
export type ModeratorTab = 'contributions' | 'annotations' | 'streams' | 'pending' | 'migration' | 'settings'

export function formatTimeAgo(dateString: string): string {
  const date = new Date(dateString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSecs = Math.floor(diffMs / 1000)
  const diffMins = Math.floor(diffSecs / 60)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffSecs < 60) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

function getMediaTypeLabel(mediaType: Suggestion['media_type']): string {
  if (mediaType === 'movie') return 'Movie'
  if (mediaType === 'series') return 'Series'
  if (mediaType === 'tv') return 'TV'
  if (mediaType === 'sports') return 'Sports'
  return 'Unknown type'
}

const SPORTS_SERIES_CATEGORIES = new Set(['formula_racing', 'motogp_racing'])

function normalizeContributionMetaType(data: Record<string, unknown>, rawMetaType: string | null): string | null {
  if (rawMetaType !== 'sports') {
    return rawMetaType
  }

  const sportsCategoryValue = typeof data.sports_category === 'string' ? data.sports_category.trim() : ''
  if (SPORTS_SERIES_CATEGORIES.has(sportsCategoryValue)) {
    return 'series'
  }

  return 'movie'
}

export function getSuggestionMediaSummary(suggestion: Suggestion): string {
  const parts = [
    getMediaTypeLabel(suggestion.media_type),
    suggestion.media_year?.toString(),
    `#${suggestion.media_id}`,
  ].filter((part): part is string => Boolean(part))
  return parts.join(' • ')
}

export function getSuggestionContentPath(suggestion: Suggestion): string | null {
  if (!suggestion.media_type) return null
  return `/dashboard/content/${suggestion.media_type}/${suggestion.media_id}`
}

export const statusConfig: Record<SuggestionStatus, { label: string; color: string; icon: typeof Clock }> = {
  pending: { label: 'Pending', color: 'bg-primary/10 text-primary border-primary/30', icon: Clock },
  approved: {
    label: 'Approved',
    color: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30',
    icon: CheckCircle2,
  },
  auto_approved: {
    label: 'Auto-Approved',
    color: 'bg-blue-500/10 text-blue-500 border-blue-500/30',
    icon: CheckCircle2,
  },
  rejected: { label: 'Rejected', color: 'bg-red-500/10 text-red-500 border-red-500/30', icon: XCircle },
}

export function parseEpisodeLinkField(
  fieldName: string | null,
): { fileId: string; field: string; displayField: string } | null {
  if (!fieldName || !fieldName.startsWith('episode_link:')) return null
  const parts = fieldName.split(':')
  if (parts.length < 3) return null

  const fieldDisplayMap: Record<string, string> = {
    season_number: 'Season',
    episode_number: 'Episode',
    episode_end: 'Episode End',
  }

  return {
    fileId: parts[1],
    field: parts[2],
    displayField: fieldDisplayMap[parts[2]] || parts[2],
  }
}

export function formatStreamFieldName(fieldName: string | null): string {
  if (!fieldName) return ''

  const episodeInfo = parseEpisodeLinkField(fieldName)
  if (episodeInfo) {
    return `Episode ${episodeInfo.displayField}`
  }

  const nameMap: Record<string, string> = {
    name: 'Name',
    resolution: 'Resolution',
    codec: 'Codec',
    quality: 'Quality',
    bit_depth: 'Bit Depth',
    audio_formats: 'Audio',
    channels: 'Channels',
    hdr_formats: 'HDR',
    source: 'Source',
    languages: 'Languages',
  }
  return nameMap[fieldName] || fieldName
}

export function formatStreamSuggestionType(type: string): string {
  const base = type.includes(':') ? type.split(':', 1)[0] : type
  const typeMap: Record<string, string> = {
    report_broken: 'Broken Report',
    field_correction: 'Field Correction',
    language_add: 'Add Language',
    language_remove: 'Remove Language',
    mark_duplicate: 'Mark Duplicate',
    relink_media: 'Relink Media',
    add_media_link: 'Add Media Link',
    other: 'Other',
  }
  if (base === 'field_correction' && type.includes(':')) {
    return `Field: ${type.split(':', 2)[1] || 'correction'}`
  }
  return typeMap[base] || type
}

export function formatTorrentData(
  data: Record<string, unknown>,
): { label: string; value: string; type: 'text' | 'link' | 'badge' | 'size' }[] {
  const fields: { label: string; value: string; type: 'text' | 'link' | 'badge' | 'size' }[] = []

  if (data.name) fields.push({ label: 'Torrent Name', value: String(data.name), type: 'text' })
  if (data.title) fields.push({ label: 'Title', value: String(data.title), type: 'text' })
  if (data.meta_type) fields.push({ label: 'Type', value: String(data.meta_type), type: 'badge' })
  if (data.meta_id) fields.push({ label: 'Media ID', value: String(data.meta_id), type: 'link' })
  if (data.info_hash) fields.push({ label: 'Info Hash', value: String(data.info_hash), type: 'text' })
  if (data.resolution) fields.push({ label: 'Resolution', value: String(data.resolution), type: 'badge' })
  if (data.quality) fields.push({ label: 'Quality', value: String(data.quality), type: 'badge' })
  if (data.codec) fields.push({ label: 'Codec', value: String(data.codec), type: 'badge' })
  if (data.total_size) fields.push({ label: 'Size', value: String(data.total_size), type: 'size' })
  if (data.file_count) fields.push({ label: 'Files', value: String(data.file_count), type: 'text' })
  if (data.languages && Array.isArray(data.languages) && data.languages.length > 0) {
    fields.push({ label: 'Languages', value: (data.languages as string[]).join(', '), type: 'text' })
  }
  if (data.catalogs && Array.isArray(data.catalogs) && data.catalogs.length > 0) {
    fields.push({ label: 'Catalogs', value: (data.catalogs as string[]).join(', '), type: 'text' })
  }

  return fields
}

export function formatBytes(bytes: number | string): string {
  const size = typeof bytes === 'string' ? parseFloat(bytes) : bytes
  if (isNaN(size) || size === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(size) / Math.log(k))
  return parseFloat((size / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

export function getContributionUploaderLabel(contribution: Contribution): string {
  const contributionData = contribution.data as Record<string, unknown>
  if (contributionData.is_anonymous === true) {
    const anonymousName =
      typeof contributionData.anonymous_display_name === 'string' ? contributionData.anonymous_display_name.trim() : ''
    return anonymousName || 'Anonymous'
  }

  if (contribution.username && contribution.username.trim().length > 0) {
    return contribution.username
  }

  if (typeof contribution.user_id === 'number') {
    return `User #${contribution.user_id}`
  }

  return 'Unknown'
}

function getNormalizedString(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  return normalized.length > 0 ? normalized : null
}

export function getContributionMediaPreview(contribution: Contribution): {
  title: string
  posterUrl: string | null
  metaType: string | null
  metaId: string | null
  year: string | null
} {
  const data = contribution.data as Record<string, unknown>
  const title =
    getNormalizedString(data.title) ??
    getNormalizedString(data.name) ??
    getNormalizedString(data.file_name) ??
    'Untitled'
  const posterUrl =
    getNormalizedString(data.poster) ??
    getNormalizedString(data.meta_poster) ??
    getNormalizedString(data.thumbnail) ??
    null
  const rawMetaType = getNormalizedString(data.meta_type)
  const metaType = normalizeContributionMetaType(data, rawMetaType)
  const metaId = getNormalizedString(data.meta_id) ?? getNormalizedString(contribution.target_id)
  const yearValue = data.year
  const year =
    typeof yearValue === 'number'
      ? String(yearValue)
      : typeof yearValue === 'string'
        ? getNormalizedString(yearValue)
        : null

  return { title, posterUrl, metaType, metaId, year }
}

export function getLibraryBrowseLink(preview: {
  title: string
  metaType: string | null
  metaId: string | null
}): string {
  const params = new URLSearchParams({ tab: 'browse' })
  if (preview.metaType === 'movie' || preview.metaType === 'series' || preview.metaType === 'tv') {
    params.set('type', preview.metaType)
  }
  const searchTerm = preview.title !== 'Untitled' ? preview.title : ''
  if (searchTerm) {
    params.set('search', searchTerm)
  }
  return `/dashboard/library?${params.toString()}`
}

export function getInternalMediaId(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    return Math.trunc(value)
  }
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  if (!normalized) return null
  if (normalized.startsWith('mf:')) {
    const parsedMfId = Number.parseInt(normalized.slice(3), 10)
    return Number.isFinite(parsedMfId) && parsedMfId > 0 ? parsedMfId : null
  }
  if (!/^\d+$/.test(normalized)) return null
  const parsedId = Number.parseInt(normalized, 10)
  return Number.isFinite(parsedId) && parsedId > 0 ? parsedId : null
}

export function getContentDetailLink(preview: { metaType: string | null }, mediaId: number | null): string | null {
  if (!mediaId) return null
  if (preview.metaType !== 'movie' && preview.metaType !== 'series' && preview.metaType !== 'tv') {
    return null
  }
  return `/dashboard/content/${preview.metaType}/${mediaId}`
}

function baseStreamSuggestionType(type: string): string {
  return type.includes(':') ? type.split(':', 1)[0] : type
}

export function isIssueStreamSuggestion(suggestion: StreamSuggestion): boolean {
  const b = baseStreamSuggestionType(suggestion.suggestion_type)
  return b === 'report_broken' || b === 'other'
}
