import type { TorrentAnalyzeResponse, TorrentMatch } from '@/lib/api'
import type { ContentType, SportsCategory } from '@/lib/constants'

export interface ImportResult {
  success: boolean
  message: string
}

export interface TorrentBatchAnalysisItem {
  file: File
  analysis: TorrentAnalyzeResponse
}

export interface TorrentDialogQueueItem {
  index: number
  label: string
}

export interface TorrentImportSubmitOptions {
  selectedQueueIndices?: number[]
}

export interface TorrentImportState {
  analysis: TorrentAnalyzeResponse | null
  dialogOpen: boolean
  isAnalyzing: boolean
  isImporting: boolean
}

// Extended import form data
export interface TorrentImportFormData {
  // Content type
  contentType: ContentType
  sportsCategory?: SportsCategory

  // Metadata
  metaId?: string
  title?: string
  poster?: string
  background?: string
  logo?: string

  // Technical specs
  resolution?: string
  quality?: string
  codec?: string
  audio?: string[]
  hdr?: string[]
  languages?: string[]

  // Catalogs
  catalogs?: string[]

  // Series/Sports specific
  episodeNameParser?: string
  releaseDate?: string

  // Import options
  forceImport?: boolean
  isAnonymous?: boolean // Whether to contribute anonymously
  anonymousDisplayName?: string

  // File annotations for series
  fileData?: FileAnnotation[]
}

export interface FileAnnotation {
  file_id?: number
  filename: string
  size?: number
  index: number
  season_number: number | null
  episode_number: number | null
  episode_end?: number | null
  included: boolean
  // Sports episode metadata
  title?: string
  overview?: string
  thumbnail?: string
  release_date?: string
  // Per-file metadata linking (for multi-content torrents)
  meta_id?: string // External ID (e.g., tt1234567) or internal ID
  meta_title?: string // Display title for the linked metadata
  meta_poster?: string // Poster URL for display
  meta_type?: 'movie' | 'series' // Type of the linked metadata
}

// NZB import form data (simplified version of TorrentImportFormData)
export interface NZBImportFormData {
  contentType: ContentType

  // Metadata
  metaId?: string
  title?: string
  poster?: string
  background?: string

  // Technical specs
  resolution?: string
  quality?: string
  codec?: string
  audio?: string[]
  languages?: string[]

  // Catalogs
  catalogs?: string[]

  // Import options
  forceImport?: boolean
  isAnonymous?: boolean
  anonymousDisplayName?: string
}

// Selected match from analysis
export interface SelectedMatch extends TorrentMatch {
  imdb_id?: string
  poster?: string
  background?: string
  logo?: string
  genres?: string[]
  runtime?: string
  imdb_rating?: number
  description?: string
  stars?: string[]
  countries?: string[]
  languages?: string[]
  aka_titles?: string[]
  is_add_title_to_poster?: boolean
}
