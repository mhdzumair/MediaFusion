/**
 * MediaFusion Browser Extension Types
 * Types for torrent import API interactions
 */

export interface TorrentFile {
  filename: string
  size: number
  index: number
  season_number?: number
  episode_number?: number
}

export interface TorrentMatch {
  id?: string
  title: string
  year?: number
  poster?: string
  type: 'movie' | 'series'
  // Provider-specific IDs
  imdb_id?: string
  tmdb_id?: string
  mal_id?: string
  kitsu_id?: string
  // Ratings
  imdb_rating?: number
  tmdb_rating?: number
  mal_rating?: number
  kitsu_rating?: string
  runtime?: string
  description?: string
  genres?: string[]
  stars?: string[]
  countries?: string[]
  languages?: string[]
  aka_titles?: string[]
  background?: string
  logo?: string
  release_date?: string
  is_add_title_to_poster?: boolean
}

export interface TorrentAnalyzeResponse {
  status: 'success' | 'error' | 'needs_annotation' | 'validation_failed'
  info_hash?: string
  torrent_name?: string
  total_size?: number
  total_size_readable?: string
  file_count?: number
  files?: TorrentFile[]
  parsed_title?: string
  year?: number
  resolution?: string
  quality?: string
  codec?: string
  audio?: string[]
  hdr?: string[]
  languages?: string[]
  matches?: TorrentMatch[]
  error?: string
  errors?: Array<{
    type: string
    message: string
  }>
  torrent_data?: {
    file_data?: TorrentFile[]
  }
}

export interface ImportResponse {
  status: 'success' | 'error' | 'warning' | 'needs_annotation' | 'validation_failed' | 'processing'
  message: string
  import_id?: string
  details?: {
    job_id?: string
    total_items?: number
    background?: boolean
    stats?: Record<string, number>
    source_saved?: boolean
    source_id?: number
    [key: string]: unknown
  }
  errors?: Array<{
    type: string
    message: string
  }>
  torrent_data?: {
    file_data?: TorrentFile[]
  }
}

export interface TorrentImportRequest {
  // Source - one of these required
  magnet_link?: string
  
  // Content type
  meta_type: 'movie' | 'series' | 'sports'
  
  // Metadata
  meta_id?: string
  title?: string
  poster?: string
  background?: string
  logo?: string
  
  // Technical specs
  resolution?: string
  quality?: string
  codec?: string
  audio?: string  // Comma-separated for multi-value
  hdr?: string    // Comma-separated for multi-value
  languages?: string  // Comma-separated for multi-value
  
  // Catalogs
  catalogs?: string  // Comma-separated
  
  // Series/Sports specific
  episode_name_parser?: string
  created_at?: string  // Release date YYYY-MM-DD
  
  // Import options
  force_import?: boolean
  is_add_title_to_poster?: boolean
  is_anonymous?: boolean
  
  // File annotations for series/collections
  file_data?: string  // JSON stringified array
  
  // Sports category
  sports_category?: string
}

// File annotation for multi-content imports
export interface FileAnnotation {
  index: number
  filename: string
  size: number
  meta_id?: string
  title?: string
  season?: number
  episode?: number
  skip?: boolean
}

// Import mode for multi-content
export type ImportMode = 'single' | 'collection' | 'pack'

// Content type
export type ContentType = 'movie' | 'series' | 'sports'

// Torrent source type (for bulk upload)
export type TorrentSourceType = 'torrent' | 'magnet'

// Torrent type (public/private)
export type TorrentType = 'public' | 'semi-private' | 'private' | 'web-seed'

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

// Bulk upload torrent item
export interface BulkTorrentItem {
  id: string
  title: string
  url: string
  type: TorrentSourceType
  contentType: ContentType
  detectedContentType: ContentType  // Auto-detected type
  sportsCategory?: SportsCategory
  size?: string
  seeders?: number
  // Status tracking
  status: 'pending' | 'processing' | 'success' | 'error' | 'warning' | 'skipped'
  statusMessage?: string
  // For local files
  file?: File
}

// Auth types
export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user: {
    id: number
    email: string
    display_name: string
    role: string
  }
}

export interface User {
  id: number
  email: string
  display_name: string
  role: string
}

// Extension settings
export interface ExtensionSettings {
  instanceUrl: string
  authToken?: string
  apiKey?: string  // X-API-Key for private instances
  user?: User
  defaultContentType: ContentType
  autoAnalyze: boolean
  showNotifications: boolean
}

// Catalog data from API
export interface CatalogData {
  id: string
  name: string
  type: 'movie' | 'series' | 'tv'
  is_user_uploadable?: boolean
}

export interface CatalogsResponse {
  movie_catalogs: CatalogData[]
  series_catalogs: CatalogData[]
  languages: string[]
}

// Technical spec options
export interface TechSpecOptions {
  resolutions: string[]
  qualities: string[]
  codecs: string[]
  audio_formats: string[]
  hdr_formats: string[]
}

// Pre-filled data from content script
export interface PrefilledData {
  magnetLink?: string
  torrentUrl?: string
  torrentFileName?: string
  pageTitle?: string
  pageUrl?: string
  contentType?: ContentType
}
