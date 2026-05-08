import { useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Code, Wand2, Eye, RotateCcw, Copy, Check, ArrowRightLeft, Sparkles, Download, Upload } from 'lucide-react'
import { renderTemplatePreview } from '@/lib/templatePreview'
import { cn } from '@/lib/utils'
import type { ConfigSectionProps } from './types'

// Default templates using MediaFusion simplified syntax
// Stream type indicators: 🧲 Torrent, 📰 Usenet, 📱 Telegram, ▶️ YouTube, 🌐 HTTP/Direct
const DEFAULT_TITLE_TEMPLATE = `{addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{elif stream.type = telegram}📱{elif stream.type = youtube}▶️{elif stream.type = http}🌐{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}`
const DEFAULT_DESCRIPTION_TEMPLATE = `{if stream.hdr_formats}🎨 {stream.hdr_formats|join('|')} {/if}{if stream.quality}📺 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.audio_formats}🎵 {stream.audio_formats|join('|')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')}{/if}
{if stream.size > 0}📦 {stream.size|bytes}{if stream.folderSize > stream.size} / {stream.folderSize|bytes}{/if} {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}
{if stream.languages}🌐 {stream.languages|join(' + ')}{/if}
🔗 {stream.source}{if stream.uploader} | 🧑‍💻 {stream.uploader}{/if}`

// Preset templates using new MediaFusion syntax
// Stream type indicators: 🧲 Torrent, 📰 Usenet/NZB, 🔗 HTTP/Direct, 📺 TV
const PRESETS = {
  default: {
    name: 'Default',
    description: 'Standard MediaFusion format with stream type',
    title: DEFAULT_TITLE_TEMPLATE,
    desc: DEFAULT_DESCRIPTION_TEMPLATE,
  },
  torrentio: {
    name: 'Torrentio',
    description: 'Similar to Torrentio addon',
    title: `{if stream.type = torrent}[🧲{service.shortName}{if service.cached}⚡{/if}]{elif stream.type = usenet}[📰{service.shortName}]{else}[🔗]{/if} {addon.name} {if stream.resolution}{stream.resolution}{/if}`,
    desc: `{if stream.quality}{stream.quality} {/if}{if stream.codec}{stream.codec} {/if}{if stream.hdr_formats}{stream.hdr_formats|join(' ')} {/if}
{if stream.size > 0}💾 {stream.size|bytes} {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}
{if stream.language_flags}{stream.language_flags|join(' ')}{/if}
⚙️ {stream.source}`,
  },
  minimal: {
    name: 'Minimal',
    description: 'Clean and compact display',
    title: `{addon.name} {if stream.type = torrent}🧲{elif stream.type = usenet}📰{else}🔗{/if} {if stream.resolution}{stream.resolution} {/if}{if stream.type = torrent}{if service.cached}⚡️{else}⏳{/if}{/if}`,
    desc: `{if stream.quality}{stream.quality} | {/if}{if stream.codec}{stream.codec} | {/if}{if stream.size > 0}{stream.size|bytes}{/if}`,
  },
  detailed: {
    name: 'Detailed',
    description: 'Maximum information density',
    title: `{addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{elif stream.type = telegram}📱{elif stream.type = youtube}▶️{elif stream.type = http}🌐{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}`,
    desc: `📂 {stream.name}
{if stream.type = torrent}🧲 Torrent{elif stream.type = usenet}📰 Usenet/NZB{elif stream.type = http}🔗 Direct Stream{else}📺 {stream.type|title}{/if}
{if stream.quality}🎥 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.bit_depth}{stream.bit_depth}-bit {/if}
{if stream.hdr_formats}🎨 {stream.hdr_formats|join(' ')} {/if}{if stream.audio_formats}🎧 {stream.audio_formats|join(' ')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')} {/if}
{if stream.size > 0}📦 {stream.size|bytes}{if stream.folderSize > stream.size} / {stream.folderSize|bytes}{/if} {/if}{if stream.seeders > 0}👤 {stream.seeders} seeders {/if}
{if stream.languages}🌐 {stream.languages|join(' | ')}{/if}
{if stream.issue_reports > 0}⚠️ {stream.issue_reports} issue report(s)
{/if}{if stream.rating_total > 0}👍 {stream.rating_up} · 👎 {stream.rating_down} · net {stream.rating_score}
{/if}
🔗 {stream.source}{if stream.release_group} | 🏷️ {stream.release_group}{/if}{if stream.uploader} | 🧑‍💻 {stream.uploader}{/if}`,
  },
  usenetFocused: {
    name: 'Usenet Focus',
    description: 'Optimized for Usenet/NZB streams',
    title: `{addon.name} {if stream.type = usenet}📰 NZB{elif stream.type = torrent}🧲 {service.shortName}{if service.cached}⚡{/if}{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}`,
    desc: `{if stream.type = usenet}📰 Usenet • {stream.source}{elif stream.type = torrent}🧲 Torrent • {if service.cached}Cached{else}Not Cached{/if}{else}🔗 Direct{/if}
{if stream.quality}📺 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.hdr_formats}🎨 {stream.hdr_formats|join(' ')}{/if}
{if stream.audio_formats}🎵 {stream.audio_formats|join(' ')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')}{/if}
{if stream.size > 0}📦 {stream.size|bytes}{/if}{if stream.seeders > 0} • 👤 {stream.seeders}{/if}
{if stream.languages}🌐 {stream.languages|join(' + ')}{/if}`,
  },
}

const PREVIEW_CONTEXTS = {
  movie: {
    label: 'Movie (Torrent)',
    context: {
      addon: { name: 'MediaFusion' },
      service: { name: 'Real-Debrid', shortName: 'RD', cached: true },
      stream: {
        name: 'Dune.Part.Two.2024.2160p.BluRay.DV.HDR.x265.Atmos',
        filename: 'Dune.Part.Two.2024.2160p.BluRay.mkv',
        folderName: 'Dune.Part.Two.2024.2160p.BluRay',
        type: 'torrent',
        provider_type: 'debrid',
        season: null,
        episode: null,
        resolution: '2160p',
        quality: 'BluRay',
        codec: 'x265',
        bit_depth: 10,
        hdr_formats: ['DV', 'HDR10+'],
        audio_formats: ['Dolby Atmos', 'TrueHD'],
        channels: ['7.1'],
        languages: ['English'],
        language_flags: ['🇬🇧'],
        languageCodes: ['EN'],
        smallLanguageCodes: ['en'],
        size: 26560123456,
        folderSize: 26560123456,
        seeders: 512,
        source: 'TGx Movies',
        release_group: 'FraMeSToR',
        uploader: 'UploaderMovie',
        issue_reports: 1,
        rating_up: 42,
        rating_down: 5,
        rating_score: 37,
        rating_total: 47,
        vote_score: 37,
      },
    },
  },
  series: {
    label: 'Series (Torrent)',
    context: {
      addon: { name: 'MediaFusion' },
      service: { name: 'Real-Debrid', shortName: 'RD', cached: true },
      stream: {
        name: 'Silo.S01E02.2160p.WEB-DL.DV.HDR.x265.Atmos',
        filename: 'Silo.S01E02.2160p.WEB-DL.mkv',
        folderName: 'Silo.S01E02.2160p.WEB-DL',
        type: 'torrent',
        provider_type: 'debrid',
        season: 1,
        episode: 2,
        resolution: '2160p',
        quality: 'WEB-DL',
        codec: 'x265',
        bit_depth: 10,
        hdr_formats: ['DV', 'HDR10'],
        audio_formats: ['Dolby Atmos', 'DDP5.1'],
        channels: ['5.1'],
        languages: ['English'],
        language_flags: ['🇬🇧'],
        languageCodes: ['EN'],
        smallLanguageCodes: ['en'],
        size: 14560123456,
        folderSize: 97601234567,
        seeders: 342,
        source: 'TGx Series',
        release_group: 'NTb',
        uploader: 'UploaderOne',
        issue_reports: 0,
        rating_up: 12,
        rating_down: 2,
        rating_score: 10,
        rating_total: 14,
        vote_score: 10,
      },
    },
  },
  usenet: {
    label: 'Usenet',
    context: {
      addon: { name: 'MediaFusion' },
      service: { name: 'NzbDAV', shortName: 'NZB', cached: false },
      stream: {
        name: 'The.Last.of.Us.S01E01.1080p.BluRay.x264',
        filename: 'The.Last.of.Us.S01E01.1080p.mkv',
        folderName: 'The.Last.of.Us.S01E01.1080p',
        type: 'usenet',
        provider_type: 'debrid',
        season: 1,
        episode: 1,
        resolution: '1080p',
        quality: 'BluRay',
        codec: 'x264',
        bit_depth: 8,
        hdr_formats: [],
        audio_formats: ['AAC'],
        channels: ['2.0'],
        languages: ['English', 'Spanish'],
        language_flags: ['🇬🇧', '🇪🇸'],
        languageCodes: ['EN', 'ES'],
        smallLanguageCodes: ['en', 'es'],
        size: 4123456789,
        folderSize: 4123456789,
        seeders: 0,
        source: 'Prowlarr Series',
        release_group: 'NOGRP',
        uploader: 'ScenePoster',
        issue_reports: 0,
        rating_up: 3,
        rating_down: 0,
        rating_score: 3,
        rating_total: 3,
        vote_score: 3,
      },
    },
  },
  http: {
    label: 'Direct / HTTP',
    context: {
      addon: { name: 'MediaFusion' },
      service: { name: 'Direct', shortName: 'WEB', cached: false },
      stream: {
        name: 'Live Sports Channel HD',
        filename: 'live-sports.m3u8',
        folderName: 'live-sports',
        type: 'http',
        provider_type: 'direct',
        season: null,
        episode: null,
        resolution: '720p',
        quality: 'HDTV',
        codec: 'H.264',
        bit_depth: 8,
        hdr_formats: [],
        audio_formats: ['AAC'],
        channels: ['2.0'],
        languages: ['English'],
        language_flags: ['🇬🇧'],
        languageCodes: ['EN'],
        smallLanguageCodes: ['en'],
        size: 0,
        folderSize: 0,
        seeders: 0,
        source: 'Live TV',
        release_group: '',
        uploader: '',
        issue_reports: 0,
        rating_up: 0,
        rating_down: 0,
        rating_score: 0,
        rating_total: 0,
        vote_score: 0,
      },
    },
  },
} as const

// Available fields organized by category
const FIELD_GROUPS = {
  addon: {
    label: '🏷️ Addon',
    fields: [{ field: 'addon.name', description: 'Addon name (MediaFusion)' }],
  },
  service: {
    label: '☁️ Debrid Service',
    fields: [
      { field: 'service.name', description: 'Full debrid service name' },
      { field: 'service.shortName', description: 'Short name (RD, AD, TB, etc.)' },
      { field: 'service.cached', description: 'Is stream cached (true/false)' },
    ],
  },
  stream: {
    label: '🎬 Stream Info',
    fields: [
      { field: 'stream.name', description: 'Full torrent/stream name' },
      { field: 'stream.filename', description: 'Video filename being played' },
      { field: 'stream.type', description: 'Stream type (torrent, http, usenet, etc.)' },
      { field: 'stream.resolution', description: 'Resolution (4K, 1080p, 720p)' },
      { field: 'stream.quality', description: 'Quality (WEB-DL, BluRay, HDRip)' },
      { field: 'stream.codec', description: 'Video codec (x265, x264, AV1)' },
      { field: 'stream.bit_depth', description: 'Bit depth (8, 10, 12)' },
      { field: 'stream.size', description: 'File size in bytes (use |bytes)' },
      { field: 'stream.seeders', description: 'Number of seeders (torrent only)' },
      { field: 'stream.cached', description: 'Is cached on debrid' },
    ],
  },
  arrays: {
    label: '📋 Arrays (use |join)',
    fields: [
      { field: 'stream.audio_formats', description: 'Audio formats (DTS-HD, Atmos)' },
      { field: 'stream.channels', description: 'Audio channels (5.1, 7.1)' },
      { field: 'stream.hdr_formats', description: 'HDR formats (HDR10, DV)' },
      { field: 'stream.languages', description: 'Language names (English, Hindi)' },
      { field: 'stream.language_flags', description: 'Country flag emojis (🇬🇧, 🇮🇳)' },
    ],
  },
  metadata: {
    label: '📝 Metadata',
    fields: [
      { field: 'stream.source', description: 'Source/catalog name' },
      { field: 'stream.release_group', description: 'Release group name' },
      { field: 'stream.uploader', description: 'Uploader name' },
    ],
  },
  community: {
    label: '👥 Community (catalog / Stremio)',
    fields: [
      { field: 'stream.issue_reports', description: 'Open issue report count (broken, etc.)' },
      { field: 'stream.rating_up', description: 'Thumb-up count' },
      { field: 'stream.rating_down', description: 'Thumb-down count' },
      { field: 'stream.rating_score', description: 'Net score (up minus down)' },
      { field: 'stream.rating_total', description: 'Total thumb votes' },
      { field: 'stream.vote_score', description: 'Same as rating_score (alias)' },
    ],
  },
  aioCompat: {
    label: '🔁 AIO Compatibility',
    fields: [
      { field: 'stream.provider_type', description: 'Provider kind (debrid, p2p, direct)' },
      { field: 'stream.folderName', description: 'Filename without extension' },
      { field: 'stream.folderSize', description: 'Container/folder size in bytes' },
      { field: 'stream.languageCodes', description: 'Language codes (EN, IT, ES)' },
      { field: 'stream.smallLanguageCodes', description: 'Lowercase language codes (en, it)' },
      { field: 'stream.infoHash', description: 'Torrent info hash when available' },
      { field: 'stream.age', description: 'Age shorthand (10d, 8h)' },
      { field: 'stream.ageHours', description: 'Age in hours' },
    ],
  },
}

// Syntax reference for new MediaFusion format
const SYNTAX_EXAMPLES = [
  {
    category: 'Variables',
    examples: [
      { code: '{stream.resolution}', desc: 'Simple variable' },
      { code: '{stream.size|bytes}', desc: 'With modifier' },
      { code: '{stream.name|upper|truncate(30)}', desc: 'Chained modifiers' },
    ],
  },
  {
    category: 'Conditionals',
    examples: [
      { code: '{if service.cached}⚡️{/if}', desc: 'Simple if' },
      { code: '{if service.cached}⚡️{else}⏳{/if}', desc: 'If/else' },
      { code: '{if stream.type = torrent}...{elif stream.type = http}...{else}...{/if}', desc: 'If/elif/else' },
    ],
  },
  {
    category: 'Comparisons',
    examples: [
      { code: '{if stream.size > 0}...{/if}', desc: 'Greater than' },
      { code: '{if stream.type = torrent}...{/if}', desc: 'Equality' },
      { code: '{if stream.name ~ 720}...{/if}', desc: 'Contains' },
    ],
  },
  {
    category: 'Logical',
    examples: [
      { code: '{if cached and stream.type = torrent}...{/if}', desc: 'AND' },
      { code: '{if cached or stream.library}...{/if}', desc: 'OR' },
      { code: '{if not stream.cached}...{/if}', desc: 'NOT' },
    ],
  },
]

// Modifiers reference
const MODIFIERS = [
  { modifier: '|bytes', description: 'Format bytes (1.5 GB)' },
  { modifier: '|time', description: 'Format duration (HH:MM:SS)' },
  { modifier: "|join(', ')", description: 'Join array with separator' },
  { modifier: '|upper', description: 'Uppercase' },
  { modifier: '|lower', description: 'Lowercase' },
  { modifier: '|title', description: 'Title case' },
  { modifier: '|first', description: 'First array element' },
  { modifier: '|last', description: 'Last array element' },
  { modifier: '|truncate(50)', description: 'Truncate to N chars' },
  { modifier: '|escape', description: 'HTML escape' },
]

/**
 * Convert AIOStreams syntax to MediaFusion syntax
 * This is a client-side implementation for the converter dialog
 */
const AIO_FIELD_ALIASES: Record<string, string> = {
  'config.addonName': 'addon.name',
  'stream.encode': 'stream.codec',
  'stream.visualTags': 'stream.hdr_formats',
  'stream.audioTags': 'stream.audio_formats',
  'stream.audioChannels': 'stream.channels',
  'stream.releaseGroup': 'stream.release_group',
  'stream.bitDepth': 'stream.bit_depth',
  'stream.fileName': 'stream.filename',
  'stream.file_name': 'stream.filename',
  'stream.folderName': 'stream.folderName',
  'stream.folderSize': 'stream.folderSize',
  'stream.languageCodes': 'stream.languageCodes',
  'stream.uLanguageCodes': 'stream.uLanguageCodes',
  'stream.title': 'stream.name',
  'stream.languageEmojis': 'stream.language_flags',
  'stream.language_emojis': 'stream.language_flags',
  'stream.smallLanguageCodes': 'stream.smallLanguageCodes',
  'stream.uSmallLanguageCodes': 'stream.uSmallLanguageCodes',
  'stream.infoHash': 'stream.infoHash',
  'stream.providerType': 'stream.provider_type',
  'stream.typeName': 'stream.type',
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function stripOuterQuotes(value: string): string {
  const trimmed = value.trim()
  if (trimmed.length < 2) return trimmed
  const first = trimmed[0]
  const last = trimmed[trimmed.length - 1]
  if ((first === '"' || first === "'") && first === last) {
    return trimmed.slice(1, -1)
  }
  return trimmed
}

function findConditionalSeparator(content: string): number {
  let depth = 0
  let inSingle = false
  let inDouble = false

  for (let i = 0; i < content.length - 1; i += 1) {
    const char = content[i]
    const escaped = i > 0 && content[i - 1] === '\\'

    if (char === "'" && !inDouble && !escaped) {
      inSingle = !inSingle
    } else if (char === '"' && !inSingle && !escaped) {
      inDouble = !inDouble
    } else if (!inSingle && !inDouble) {
      if (char === '[') depth += 1
      if (char === ']') depth = Math.max(0, depth - 1)
      if (char === '|' && content[i + 1] === '|' && depth === 0) {
        return i
      }
    }
  }

  return -1
}

function splitQuotedBranches(content: string): [string, string] | null {
  const parts: string[] = []
  let i = 0

  while (i < content.length) {
    while (i < content.length && /\s/.test(content[i])) i += 1
    if (i >= content.length) break

    const quote = content[i]
    if (quote !== '"' && quote !== "'") return null

    let end = i + 1
    while (end < content.length) {
      if (content[end] === quote && content[end - 1] !== '\\') break
      end += 1
    }
    if (end >= content.length) return null

    parts.push(content.slice(i, end + 1))
    i = end + 1
  }

  if (parts.length >= 2) return [parts[0], parts[1]]
  if (parts.length === 1) return [parts[0], '""']
  return null
}

function splitConditionalBranches(content: string): [string, string] | null {
  const separator = findConditionalSeparator(content)
  if (separator !== -1) {
    const trueBranch = content.slice(0, separator).trim()
    const falseBranch = content.slice(separator + 2).trim()
    return [trueBranch, falseBranch]
  }
  return splitQuotedBranches(content)
}

function mapConditionalCheck(variablePath: string, check: string): string {
  const normalized = check.trim()
  if (!normalized) return variablePath
  if (normalized === 'istrue' || normalized === 'exists') return variablePath
  if (normalized === 'isfalse') return `not ${variablePath}`

  const operators = ['>=', '<=', '!=', '>', '<', '=', '~', '$', '^']
  for (const op of operators) {
    if (normalized.startsWith(op)) {
      const value = normalized.slice(op.length).trim()
      if (!value) return variablePath
      return `${variablePath} ${op} ${value}`
    }
  }

  return variablePath
}

function findConditionalEnd(template: string, start: number): number {
  let depth = 0
  let inSingle = false
  let inDouble = false

  for (let i = start; i < template.length; i += 1) {
    const char = template[i]
    const escaped = i > 0 && template[i - 1] === '\\'

    if (char === "'" && !inDouble && !escaped) {
      inSingle = !inSingle
    } else if (char === '"' && !inSingle && !escaped) {
      inDouble = !inDouble
    } else if (!inSingle && !inDouble) {
      if (char === '[') {
        depth += 1
      } else if (char === ']') {
        depth = Math.max(0, depth - 1)
        if (depth === 0 && i + 1 < template.length && template[i + 1] === '}') {
          return i + 2
        }
      }
    }
  }

  return -1
}

function applyMediaFusionAliases(template: string): string {
  let normalized = template

  for (const [from, to] of Object.entries(AIO_FIELD_ALIASES)) {
    normalized = normalized.replace(new RegExp(`\\b${escapeRegex(from)}\\b`, 'g'), to)
  }

  // AIOStreams uses rbytes; MediaFusion supports bytes.
  normalized = normalized.replace(/\|rbytes\b/g, '|bytes')
  return normalized
}

function normalizeLegacyAIOLogicTokens(template: string): string {
  const normalized = template
    .replace(/::or::/gi, ' or ')
    .replace(/::and::/gi, ' and ')
    .replace(/::not::/gi, ' not ')
    .replace(/::(>=|<=|!=|=|>|<|~|\$|\^)/g, ' $1 ')

  // AIOStreams used `stream.type` as provider kind (debrid/p2p).
  return normalized.replace(
    /\bstream\.type\b(?=\s*(?:=|!=|~|\^|\$)\s*(?:['"])?(?:debrid|p2p)(?:['"])?\b)/gi,
    'stream.provider_type',
  )
}

function convertAIOStreamsToMediaFusion(template: string): string {
  if (!template) return template

  let result = normalizeLegacyAIOLogicTokens(template)

  // Convert conditional blocks first: {var::check["true"||"false"]}
  let cursor = 0
  while (cursor < result.length) {
    if (result[cursor] !== '{') {
      cursor += 1
      continue
    }

    const conditionalEnd = findConditionalEnd(result, cursor)
    if (conditionalEnd === -1) {
      cursor += 1
      continue
    }

    const segment = result.slice(cursor, conditionalEnd)
    const bracketStart = segment.indexOf('[')
    if (bracketStart === -1) {
      cursor += 1
      continue
    }

    const varCheck = segment.slice(1, bracketStart).trim()
    const separatorIndex = varCheck.indexOf('::')
    if (separatorIndex === -1) {
      cursor += 1
      continue
    }

    const variablePath = varCheck.slice(0, separatorIndex).trim()
    const check = varCheck.slice(separatorIndex + 2).trim()
    if (!variablePath || !check) {
      cursor += 1
      continue
    }

    const branches = splitConditionalBranches(segment.slice(bracketStart + 1, -2))
    if (!branches) {
      cursor += 1
      continue
    }

    const [rawTrueBranch, rawFalseBranch] = branches
    const trueBranch = convertAIOStreamsToMediaFusion(stripOuterQuotes(rawTrueBranch))
    const falseBranch = convertAIOStreamsToMediaFusion(stripOuterQuotes(rawFalseBranch))
    const condition = mapConditionalCheck(variablePath, check)

    const convertedSegment = falseBranch.trim()
      ? `{if ${condition}}${trueBranch}{else}${falseBranch}{/if}`
      : `{if ${condition}}${trueBranch}{/if}`

    result = `${result.slice(0, cursor)}${convertedSegment}${result.slice(conditionalEnd)}`
    cursor += convertedSegment.length
  }

  // Convert simple modifiers: {var::mod::mod2(arg)} -> {var|mod|mod2(arg)}
  result = result.replace(
    /\{([a-zA-Z_][a-zA-Z0-9_.]*)((?:::[a-zA-Z_][a-zA-Z0-9_]*(?:\([^)]*\))?)+)\}/g,
    (_match, path: string, modifiers: string) => `{${path}${modifiers.replaceAll('::', '|')}}`,
  )

  return applyMediaFusionAliases(result)
}

type ConvertedAIOImport = {
  preview: string
  title?: string
  description?: string
}

function convertAIOImportInput(input: string): ConvertedAIOImport {
  const trimmed = input.trim()
  if (!trimmed) return { preview: '' }

  try {
    const parsed = JSON.parse(trimmed) as {
      name?: unknown
      title?: unknown
      description?: unknown
      desc?: unknown
    }

    if (parsed && typeof parsed === 'object') {
      const rawTitle =
        typeof parsed.name === 'string' ? parsed.name : typeof parsed.title === 'string' ? parsed.title : undefined
      const rawDescription =
        typeof parsed.description === 'string'
          ? parsed.description
          : typeof parsed.desc === 'string'
            ? parsed.desc
            : undefined

      if (rawTitle !== undefined || rawDescription !== undefined) {
        const convertedTitle = rawTitle !== undefined ? convertAIOStreamsToMediaFusion(rawTitle) : undefined
        const convertedDescription =
          rawDescription !== undefined ? convertAIOStreamsToMediaFusion(rawDescription) : undefined

        const previewObject: Record<string, string> = {}
        if (convertedTitle !== undefined) previewObject.name = convertedTitle
        if (convertedDescription !== undefined) previewObject.description = convertedDescription

        return {
          preview: JSON.stringify(previewObject, null, 2),
          title: convertedTitle,
          description: convertedDescription,
        }
      }
    }
  } catch {
    // Non-JSON input is expected for single-template imports.
  }

  return { preview: convertAIOStreamsToMediaFusion(input) }
}

function buildAiFormatterGuidePrompt(): string {
  const fieldGroups = Object.values(FIELD_GROUPS)
    .map((group) => `${group.label}: ${group.fields.map((field) => field.field).join(', ')}`)
    .join('\n')

  const modifiers = MODIFIERS.map((modifier) => modifier.modifier).join(', ')

  return [
    'You are helping me design MediaFusion stream formatter templates.',
    '',
    'Return only a valid JSON object with this exact shape:',
    '{',
    '  "title": "<template string>",',
    '  "description": "<template string>"',
    '}',
    '',
    'Important syntax rules:',
    '- Variables: {stream.resolution}',
    '- Conditionals: {if condition}...{elif condition}...{else}...{/if}',
    '- Supported operators: =, !=, >, <, >=, <=, ~, and, or, not',
    '- Modifiers: ' + modifiers,
    '- Array fields should usually use |join(...) for readability.',
    '',
    'Available fields:',
    fieldGroups,
    '',
    'Stream types are usually one of: torrent, usenet, telegram, http, youtube.',
    '',
    'Please optimize for readability in Stremio:',
    '- Title should be compact and high-signal.',
    '- Description should be informative but not noisy.',
    '- Avoid unsupported fields and avoid legacy AIOStreams syntax (::or::, etc).',
  ].join('\n')
}

type FormatterImportPayload = {
  title?: string
  description?: string
}

function parseFormatterImportPayload(input: string): FormatterImportPayload {
  const parsed = JSON.parse(input) as unknown
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Import must be a JSON object.')
  }

  const data = parsed as Record<string, unknown>
  const hasTitle = typeof data.title === 'string' || typeof data.t === 'string'
  const hasDescription = typeof data.description === 'string' || typeof data.d === 'string'

  if (!hasTitle && !hasDescription) {
    throw new Error('JSON must include at least one template: "title"/"t" or "description"/"d".')
  }

  return {
    title: typeof data.title === 'string' ? data.title : typeof data.t === 'string' ? data.t : undefined,
    description:
      typeof data.description === 'string' ? data.description : typeof data.d === 'string' ? data.d : undefined,
  }
}

function buildFormatterExportPayload(title: string, description: string): string {
  return JSON.stringify({ title, description }, null, 2)
}

export function StreamFormatterConfig({ config, onChange }: ConfigSectionProps) {
  const [copied, setCopied] = useState<string | null>(null)
  const [aiGuideCopied, setAiGuideCopied] = useState(false)
  const [formatterExported, setFormatterExported] = useState(false)
  const [previewPreset, setPreviewPreset] = useState<keyof typeof PREVIEW_CONTEXTS>('movie')
  const [converterOpen, setConverterOpen] = useState(false)
  const [aioInput, setAioInput] = useState('')
  const [convertedOutput, setConvertedOutput] = useState('')
  const [convertedTemplates, setConvertedTemplates] = useState<{
    title?: string
    description?: string
  } | null>(null)
  const [formatterImportOpen, setFormatterImportOpen] = useState(false)
  const [formatterImportInput, setFormatterImportInput] = useState('')
  const [formatterImportError, setFormatterImportError] = useState<string | null>(null)

  const currentTitle = config.st?.t ?? DEFAULT_TITLE_TEMPLATE
  const currentDescription = config.st?.d ?? DEFAULT_DESCRIPTION_TEMPLATE
  const selectedPreview = PREVIEW_CONTEXTS[previewPreset]

  const updateTemplate = (field: 't' | 'd', value: string) => {
    onChange({
      ...config,
      st: {
        ...config.st,
        [field]: value,
      },
    })
  }

  const applyPreset = (presetKey: string) => {
    const preset = PRESETS[presetKey as keyof typeof PRESETS]
    if (preset) {
      onChange({
        ...config,
        st: {
          t: preset.title,
          d: preset.desc,
        },
      })
    }
  }

  const copyAiGuide = async () => {
    await navigator.clipboard.writeText(buildAiFormatterGuidePrompt())
    setAiGuideCopied(true)
    setTimeout(() => setAiGuideCopied(false), 2000)
  }

  const exportFormatter = () => {
    const payload = buildFormatterExportPayload(currentTitle, currentDescription)
    const blob = new Blob([payload], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'mediafusion-formatter.json'
    anchor.click()
    URL.revokeObjectURL(url)
    setFormatterExported(true)
    setTimeout(() => setFormatterExported(false), 2000)
  }

  const applyFormatterImport = () => {
    try {
      const imported = parseFormatterImportPayload(formatterImportInput)
      onChange({
        ...config,
        st: {
          ...config.st,
          ...(imported.title !== undefined ? { t: imported.title } : {}),
          ...(imported.description !== undefined ? { d: imported.description } : {}),
        },
      })
      setFormatterImportError(null)
      setFormatterImportInput('')
      setFormatterImportOpen(false)
    } catch (error) {
      setFormatterImportError(error instanceof Error ? error.message : 'Invalid formatter import JSON.')
    }
  }

  const resetToDefault = () => {
    onChange({
      ...config,
      // Use null so backend deep-merge removes custom stream template config.
      st: null,
    })
  }

  const copyField = (field: string) => {
    navigator.clipboard.writeText(`{${field}}`)
    setCopied(field)
    setTimeout(() => setCopied(null), 2000)
  }

  const handleConvert = () => {
    const converted = convertAIOImportInput(aioInput)
    setConvertedOutput(converted.preview)
    if (converted.title !== undefined || converted.description !== undefined) {
      setConvertedTemplates({ title: converted.title, description: converted.description })
    } else {
      setConvertedTemplates(null)
    }
  }

  const applyConvertedTitle = () => {
    const template = convertedTemplates?.title ?? convertedOutput
    if (template) {
      updateTemplate('t', template)
      setConverterOpen(false)
      setAioInput('')
      setConvertedOutput('')
      setConvertedTemplates(null)
    }
  }

  const applyConvertedDescription = () => {
    const template = convertedTemplates?.description ?? convertedOutput
    if (template) {
      updateTemplate('d', template)
      setConverterOpen(false)
      setAioInput('')
      setConvertedOutput('')
      setConvertedTemplates(null)
    }
  }

  const applyConvertedBoth = () => {
    if (!convertedTemplates) return

    onChange({
      ...config,
      st: {
        ...config.st,
        ...(convertedTemplates.title !== undefined ? { t: convertedTemplates.title } : {}),
        ...(convertedTemplates.description !== undefined ? { d: convertedTemplates.description } : {}),
      },
    })

    setConverterOpen(false)
    setAioInput('')
    setConvertedOutput('')
    setConvertedTemplates(null)
  }

  const closeConverter = () => {
    setConverterOpen(false)
    setConvertedOutput('')
    setConvertedTemplates(null)
  }

  const canApplyBoth = Boolean(convertedTemplates?.title !== undefined && convertedTemplates?.description !== undefined)

  const outputLabel = convertedTemplates ? '✅ Converted MediaFusion Templates' : '✅ Converted MediaFusion Template'

  const converterPlaceholder = `Paste AIOStreams template here...

Single template example:
{stream.type::=torrent["⚡ {service.shortName}"||""]}

JSON example:
{"name":"{stream.resolution::=2160p[\\"💎 4K\\"||\\"\\"]}","description":"{stream.size::>0[\\"📦 {stream.size::rbytes}\\"||\\"\\"]}"}
`

  const previewData = useMemo(() => {
    try {
      return {
        title: renderTemplatePreview(currentTitle, selectedPreview.context),
        description: renderTemplatePreview(currentDescription, selectedPreview.context),
        error: null as string | null,
      }
    } catch (error) {
      return {
        title: '',
        description: '',
        error: error instanceof Error ? error.message : 'Unable to render preview with the current template.',
      }
    }
  }, [currentTitle, currentDescription, selectedPreview.context])

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Code className="h-5 w-5 text-primary" />
          Stream Formatter
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Customize how stream titles and descriptions appear in Stremio. Use{' '}
          <span className="font-medium text-foreground">Available Fields</span> below to copy template variables.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Preset Selection */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Quick Presets</Label>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {Object.entries(PRESETS).map(([key, preset]) => (
              <Button
                key={key}
                variant="outline"
                size="sm"
                className="h-auto py-2 px-3 flex flex-col items-start gap-0.5"
                onClick={() => applyPreset(key)}
              >
                <span className="font-medium text-xs">{preset.name}</span>
                <span className="text-[10px] text-muted-foreground truncate max-w-full">{preset.description}</span>
              </Button>
            ))}
          </div>
        </div>

        <Separator />

        {/* Title Template */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="title-template" className="text-sm font-medium">
              Title Template
            </Label>
            <Badge variant="secondary" className="text-xs">
              Shows as stream title
            </Badge>
          </div>
          <Textarea
            id="title-template"
            value={currentTitle}
            onChange={(e) => updateTemplate('t', e.target.value)}
            placeholder="Enter title template..."
            className="font-mono text-sm h-20 resize-none"
          />
        </div>

        {/* Description Template */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="desc-template" className="text-sm font-medium">
              Description Template
            </Label>
            <Badge variant="secondary" className="text-xs">
              Shows as stream details
            </Badge>
          </div>
          <Textarea
            id="desc-template"
            value={currentDescription}
            onChange={(e) => updateTemplate('d', e.target.value)}
            placeholder="Enter description template..."
            className="font-mono text-sm h-40 resize-none"
          />
        </div>

        <Separator />

        {/* Live Preview */}
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <Label className="text-sm font-medium">Live Preview</Label>
            <Badge variant="outline" className="text-xs">
              {selectedPreview.label}
            </Badge>
          </div>

          <div className="flex flex-wrap gap-2">
            {Object.entries(PREVIEW_CONTEXTS).map(([key, preview]) => (
              <Button
                key={key}
                type="button"
                size="sm"
                variant={previewPreset === key ? 'default' : 'outline'}
                onClick={() => setPreviewPreset(key as keyof typeof PREVIEW_CONTEXTS)}
              >
                {preview.label}
              </Button>
            ))}
          </div>

          <div className="rounded-lg border bg-muted/20 p-3 space-y-3">
            {previewData.error ? (
              <p className="text-sm text-destructive">{previewData.error}</p>
            ) : (
              <>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Title</p>
                  <p className="text-sm font-medium break-words">{previewData.title || '(empty output)'}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Description</p>
                  <p className="text-sm whitespace-pre-wrap break-words">
                    {previewData.description || '(empty output)'}
                  </p>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex justify-between items-center">
          <div className="flex flex-wrap items-center gap-2">
            <Dialog
              open={formatterImportOpen}
              onOpenChange={(open) => {
                setFormatterImportOpen(open)
                if (!open) {
                  setFormatterImportError(null)
                }
              }}
            >
              <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2">
                  <Upload className="h-4 w-4" />
                  Import Formatter
                </Button>
              </DialogTrigger>
              <DialogContent className="sm:max-w-[560px]">
                <DialogHeader>
                  <DialogTitle>Import Formatter Templates</DialogTitle>
                  <DialogDescription>
                    Paste formatter JSON with `title` and `description` keys (or `t` and `d`).
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-2 py-2">
                  <Textarea
                    value={formatterImportInput}
                    onChange={(e) => {
                      setFormatterImportInput(e.target.value)
                      setFormatterImportError(null)
                    }}
                    placeholder={`{\n  "title": "{addon.name} {stream.resolution}",\n  "description": "{if stream.size > 0}{stream.size|bytes}{/if}"\n}`}
                    className="font-mono text-xs h-44 resize-none"
                  />
                  {formatterImportError && <p className="text-xs text-destructive">{formatterImportError}</p>}
                </div>
                <DialogFooter className="gap-2">
                  <Button variant="outline" onClick={() => setFormatterImportOpen(false)}>
                    Cancel
                  </Button>
                  <Button onClick={applyFormatterImport}>Apply Import</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>

            {/* AIOStreams Converter */}
            <Dialog open={converterOpen} onOpenChange={setConverterOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2">
                  <ArrowRightLeft className="h-4 w-4" />
                  Import from AIOStreams
                </Button>
              </DialogTrigger>
              <DialogContent className="sm:max-w-[600px]">
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-2">
                    <Sparkles className="h-5 w-5 text-amber-500" />
                    Convert AIOStreams Template
                  </DialogTitle>
                  <DialogDescription>
                    Paste your AIOStreams template below to convert it to MediaFusion format
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-4">
                  <div className="space-y-2">
                    <Label htmlFor="aio-input" className="text-sm font-medium">
                      AIOStreams Template
                    </Label>
                    <Textarea
                      id="aio-input"
                      value={aioInput}
                      onChange={(e) => setAioInput(e.target.value)}
                      placeholder={converterPlaceholder}
                      className="font-mono text-xs h-32 resize-none"
                    />
                  </div>

                  <Button onClick={handleConvert} className="w-full gap-2">
                    <ArrowRightLeft className="h-4 w-4" />
                    Convert to MediaFusion
                  </Button>

                  {convertedOutput && (
                    <div className="space-y-2">
                      <Label className="text-sm font-medium text-emerald-600">{outputLabel}</Label>
                      <Textarea
                        value={convertedOutput}
                        readOnly
                        className="font-mono text-xs h-32 resize-none bg-emerald-50 dark:bg-emerald-950/20 border-emerald-200 dark:border-emerald-800"
                      />
                    </div>
                  )}
                </div>
                <DialogFooter className="gap-2">
                  <Button variant="outline" onClick={closeConverter}>
                    Cancel
                  </Button>
                  {convertedOutput && (
                    <>
                      {canApplyBoth && (
                        <Button variant="secondary" onClick={applyConvertedBoth}>
                          Apply Both
                        </Button>
                      )}
                      <Button variant="secondary" onClick={applyConvertedTitle}>
                        Apply as Title
                      </Button>
                      <Button onClick={applyConvertedDescription}>Apply as Description</Button>
                    </>
                  )}
                </DialogFooter>
              </DialogContent>
            </Dialog>

            <Button variant="outline" size="sm" onClick={exportFormatter} className="gap-2">
              {formatterExported ? <Check className="h-4 w-4 text-emerald-500" /> : <Download className="h-4 w-4" />}
              {formatterExported ? 'Exported' : 'Export Formatter'}
            </Button>

            <Button variant="outline" size="sm" onClick={copyAiGuide} className="gap-2">
              {aiGuideCopied ? <Check className="h-4 w-4 text-emerald-500" /> : <Sparkles className="h-4 w-4" />}
              {aiGuideCopied ? 'AI Guide Copied' : 'Copy AI Formatter Guide'}
            </Button>
          </div>

          <Button variant="outline" size="sm" onClick={resetToDefault} className="gap-2">
            <RotateCcw className="h-4 w-4" />
            Reset to Default
          </Button>
        </div>

        <Separator />

        {/* Reference Documentation */}
        <Accordion type="single" collapsible className="w-full">
          <AccordionItem value="fields">
            <AccordionTrigger className="text-sm font-medium">
              <div className="flex items-center gap-2">
                <Eye className="h-4 w-4 text-blue-500" />
                Available Fields (Click to Copy)
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4">
              {Object.entries(FIELD_GROUPS).map(([key, group]) => (
                <div key={key}>
                  <h4 className="text-xs font-medium text-muted-foreground mb-2">{group.label}</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                    {group.fields.map((item) => (
                      <button
                        key={item.field}
                        onClick={() => copyField(item.field)}
                        className={cn(
                          'flex items-center justify-between gap-2 p-2 rounded-lg text-left transition-colors',
                          'hover:bg-muted/80 bg-muted/40',
                          copied === item.field && 'bg-emerald-500/20',
                        )}
                      >
                        <div className="min-w-0">
                          <code className="text-xs font-medium truncate block">{'{' + item.field + '}'}</code>
                          <span className="text-[10px] text-muted-foreground truncate block">{item.description}</span>
                        </div>
                        {copied === item.field ? (
                          <Check className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                        ) : (
                          <Copy className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </AccordionContent>
          </AccordionItem>

          <AccordionItem value="syntax">
            <AccordionTrigger className="text-sm font-medium">
              <div className="flex items-center gap-2">
                <Wand2 className="h-4 w-4 text-primary" />
                Template Syntax & Modifiers
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4">
              {/* Syntax Examples */}
              <div className="space-y-3">
                {SYNTAX_EXAMPLES.map((section) => (
                  <div key={section.category} className="p-3 rounded-lg bg-muted/50">
                    <h4 className="font-medium mb-2 text-sm">{section.category}</h4>
                    <div className="space-y-1.5">
                      {section.examples.map((ex, i) => (
                        <div key={i} className="flex items-start gap-2 text-xs">
                          <code className="bg-background px-1.5 py-0.5 rounded shrink-0 text-[11px]">{ex.code}</code>
                          <span className="text-muted-foreground">{ex.desc}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              {/* Modifiers */}
              <div>
                <h4 className="text-sm font-medium mb-2">Available Modifiers</h4>
                <div className="grid grid-cols-2 gap-1.5">
                  {MODIFIERS.map((mod) => (
                    <div key={mod.modifier} className="flex items-start gap-2 text-xs p-1.5 rounded bg-muted/30">
                      <code className="bg-background px-1 py-0.5 rounded shrink-0 text-[10px]">{mod.modifier}</code>
                      <span className="text-muted-foreground text-[10px]">{mod.description}</span>
                    </div>
                  ))}
                </div>
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </CardContent>
    </Card>
  )
}
