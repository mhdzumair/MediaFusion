/**
 * Content Type and Sports Category Detection
 * Ported from popup.js with comprehensive patterns for auto-detection
 */

import type { ContentType } from './types'

// Sports category type
export type SportsCategory = 
  | 'formula_racing'
  | 'american_football'
  | 'basketball'
  | 'football'
  | 'baseball'
  | 'hockey'
  | 'fighting'
  | 'rugby'
  | 'motogp_racing'
  | 'other_sports'

// Sports categories with display names
export const SPORTS_CATEGORIES: { value: SportsCategory | ''; label: string }[] = [
  { value: '', label: 'Select Sports Category' },
  { value: 'american_football', label: 'American Football' },
  { value: 'baseball', label: 'Baseball' },
  { value: 'basketball', label: 'Basketball' },
  { value: 'football', label: 'Football (Soccer)' },
  { value: 'formula_racing', label: 'Formula Racing' },
  { value: 'hockey', label: 'Hockey' },
  { value: 'motogp_racing', label: 'MotoGP Racing' },
  { value: 'rugby', label: 'Rugby/AFL' },
  { value: 'fighting', label: 'Fighting (WWE, UFC)' },
  { value: 'other_sports', label: 'Other Sports' },
]

// Sports detection patterns
const SPORTS_PATTERNS: RegExp[] = [
  /\b(nfl|nba|nhl|mlb|mls|ufc|wwe|aew|f1|formula\s*1)\b/i,
  /\b(premier\s*league|champions\s*league|europa\s*league)\b/i,
  /\b(world\s*cup|euro\s*\d+|olympics|olympic)\b/i,
  /\b(boxing|wrestling|mma|mixed\s*martial\s*arts)\b/i,
  /\b(football|soccer|basketball|baseball|hockey|tennis|golf)\b/i,
  /\b(cricket|rugby|volleyball|badminton|swimming|athletics)\b/i,
  /\b(racing|motogp|nascar|indycar|rally)\b/i,
  /\bfight\s*night\b/i,
  /\bpay\s*per\s*view\b/i,
  /\bppv\b/i,
  /\bgrand\s*prix\b/i,
]

// Series detection patterns
const SERIES_PATTERNS: RegExp[] = [
  /\bs\d+e\d+\b/i,           // S04E15, s1e1
  /\bseason\s*\d+\b/i,       // Season 4
  /\bs\s*\d+\b/i,            // s04, s1
  /\bepisode\s*\d+\b/i,      // Episode 15
  /\b\d{1,2}x\d{1,2}\b/,     // 4x15, 1x01
  /complete\s+series/i,       // Complete Series
  /season\s+complete/i,       // Season Complete
  /all\s+episodes/i,          // All Episodes
  /\bcomplete\s+season\b/i,  // Complete Season
  /\bfull\s+series\b/i,      // Full Series
]

// Sports category patterns for auto-detection
const SPORTS_CATEGORY_PATTERNS: { category: SportsCategory; patterns: RegExp[] }[] = [
  {
    category: 'formula_racing',
    patterns: [
      /\bf1\b/i,
      /formula\s*1/i,
      /formula\s*one/i,
      /grand\s*prix/i,
      /formula\s*racing/i,
    ],
  },
  {
    category: 'american_football',
    patterns: [
      /\bnfl\b/i,
      /american\s*football/i,
      /super\s*bowl/i,
    ],
  },
  {
    category: 'basketball',
    patterns: [
      /\bnba\b/i,
      /basketball/i,
      /\bncaa\b.*basketball/i,
    ],
  },
  {
    category: 'football',
    patterns: [
      /premier\s*league/i,
      /champions\s*league/i,
      /europa\s*league/i,
      /world\s*cup/i,
      /euro\s*\d+/i,
      /\bfifa\b/i,
      /\buefa\b/i,
      /\bla\s*liga\b/i,
      /\bbundesliga\b/i,
      /\bserie\s*a\b/i,
      /\bligue\s*1\b/i,
    ],
  },
  {
    category: 'baseball',
    patterns: [
      /\bmlb\b/i,
      /baseball/i,
      /world\s*series/i,
    ],
  },
  {
    category: 'hockey',
    patterns: [
      /\bnhl\b/i,
      /hockey/i,
      /stanley\s*cup/i,
    ],
  },
  {
    category: 'fighting',
    patterns: [
      /\bufc\b/i,
      /\bwwe\b/i,
      /\baew\b/i,
      /\bmma\b/i,
      /mixed\s*martial\s*arts/i,
      /wrestling/i,
      /boxing/i,
      /fight\s*night/i,
      /pay\s*per\s*view/i,
      /\bppv\b/i,
    ],
  },
  {
    category: 'rugby',
    patterns: [
      /rugby/i,
      /\bafl\b/i,
      /australian\s*football/i,
    ],
  },
  {
    category: 'motogp_racing',
    patterns: [
      /motogp/i,
      /moto\s*gp/i,
      /motorcycle\s*racing/i,
    ],
  },
]

/**
 * Detect content type from torrent title/filename
 * Priority: Sports > Series > Movie (default)
 */
export function guessContentTypeFromTitle(title: string): ContentType {
  if (!title) return 'movie'

  const titleLower = title.toLowerCase()

  // 1. Check for SPORTS first (most specific)
  for (const pattern of SPORTS_PATTERNS) {
    if (pattern.test(titleLower)) {
      return 'sports'
    }
  }

  // 2. Check for SERIES
  for (const pattern of SERIES_PATTERNS) {
    if (pattern.test(titleLower)) {
      return 'series'
    }
  }

  // 3. Default to MOVIE
  return 'movie'
}

/**
 * Detect sports category from torrent title
 * Returns null if no specific category detected
 */
export function guessSportsCategoryFromTitle(title: string): SportsCategory | null {
  if (!title) return null

  const titleLower = title.toLowerCase()

  for (const { category, patterns } of SPORTS_CATEGORY_PATTERNS) {
    for (const pattern of patterns) {
      if (pattern.test(titleLower)) {
        return category
      }
    }
  }

  return null
}

/**
 * Extract title from magnet link dn= parameter
 */
export function extractTitleFromMagnet(magnetLink: string): string | null {
  if (!magnetLink?.startsWith('magnet:')) return null

  try {
    const dnMatch = magnetLink.match(/[&?]dn=([^&]*)/i)
    if (dnMatch && dnMatch[1]) {
      return decodeURIComponent(dnMatch[1])
    }
  } catch {
    // Ignore decoding errors
  }

  return null
}

/**
 * Get content type from magnet link or filename
 */
export function detectContentType(source: string): ContentType {
  // Check if it's a magnet link
  if (source.startsWith('magnet:')) {
    const title = extractTitleFromMagnet(source)
    if (title) {
      return guessContentTypeFromTitle(title)
    }
  }

  // Otherwise treat as filename/title
  return guessContentTypeFromTitle(source)
}

/**
 * Get sports category from magnet link or filename
 */
export function detectSportsCategory(source: string): SportsCategory | null {
  // Check if it's a magnet link
  if (source.startsWith('magnet:')) {
    const title = extractTitleFromMagnet(source)
    if (title) {
      return guessSportsCategoryFromTitle(title)
    }
  }

  // Otherwise treat as filename/title
  return guessSportsCategoryFromTitle(source)
}
