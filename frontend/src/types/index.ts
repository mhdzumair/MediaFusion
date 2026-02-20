// User roles
export type UserRole = 'user' | 'paid_user' | 'moderator' | 'admin'

// User type
export interface User {
  id: string
  uuid: string
  email: string
  username: string | null
  role: UserRole
  is_verified: boolean
  is_active: boolean
  created_at: string
  last_login: string | null
  // Contribution stats
  contribution_points?: number
  contribution_level?: string
  // Contribution preferences
  contribute_anonymously?: boolean
}

// User profile type (alias for Profile)
export interface UserProfile {
  id: string
  user_id: string
  name: string
  config: UserConfig
  is_default: boolean
  created_at: string
}

// Profile type (same as UserProfile)
export type Profile = UserProfile

// ProfileConfig type (alias for UserConfig)
export type ProfileConfig = UserConfig

// Per-catalog configuration
export interface CatalogConfig {
  catalog_id: string
  enabled: boolean
  sort?: 'latest' | 'popular' | 'rating' | 'year' | 'title' | 'release_date' | null
  order?: 'asc' | 'desc'
}

// User configuration (matches existing UserData schema)
export interface UserConfig {
  streaming_provider?: StreamingProvider
  catalog_configs?: CatalogConfig[] // New: per-catalog configuration
  selected_catalogs: string[] // Deprecated: use catalog_configs
  selected_resolutions: string[]
  quality_filter: string[]
  max_size: number
  min_size: number
  torrent_sorting_priority: SortingOption[]
  language_sorting: string[]
  nudity_filter: string[]
  certification_filter: string[]
  enable_catalogs: boolean
  enable_imdb_metadata: boolean
  max_streams_per_resolution: number
  max_streams: number
  live_search_streams: boolean
  mediaflow_config?: MediaFlowConfig
  rpdb_config?: RPDBConfig
  mdblist_config?: MDBListConfig
  stream_template?: StreamTemplate
  // Stream display settings
  stream_type_grouping: 'mixed' | 'separate'
  stream_type_order: string[]
  // Stream name filter
  stream_name_filter_mode: 'disabled' | 'include' | 'exclude'
  stream_name_filter_patterns: string[]
  stream_name_filter_use_regex: boolean
}

export interface StreamingProvider {
  service: string
  token?: string
  email?: string
  password?: string
  url?: string
  stremthru_store_name?: string
  enable_watchlist_catalogs: boolean
  download_via_browser: boolean
  only_show_cached_streams: boolean
  qbittorrent_config?: QBittorrentConfig
}

export interface QBittorrentConfig {
  qbittorrent_url: string
  qbittorrent_username: string
  qbittorrent_password: string
  seeding_time_limit: number
  seeding_ratio_limit: number
  play_video_after: number
  category: string
  webdav_url: string
  webdav_username: string
  webdav_password: string
  webdav_downloads_path: string
}

export interface MediaFlowConfig {
  proxy_url: string
  api_password: string
  public_ip?: string
  proxy_live_streams: boolean
  proxy_debrid_streams: boolean
}

export interface RPDBConfig {
  api_key: string
}

export interface MDBListConfig {
  api_key: string
  lists: MDBList[]
}

export interface MDBList {
  id: number
  title: string
  catalog_type: 'movie' | 'series'
  media_type: string[]
  sort_by: string
  sort_order: string
  use_filters: boolean
}

export interface SortingOption {
  key: string
  direction: 'asc' | 'desc'
}

// Watch history
export interface WatchHistoryEntry {
  id: string
  user_id: string
  profile_id: string
  meta_id: string
  title: string
  media_type: 'movie' | 'series'
  season?: number
  episode?: number
  progress: number
  duration?: number
  watched_at: string
  poster?: string
}

// Download history
export interface DownloadHistoryEntry {
  id: string
  user_id: string
  profile_id: string
  meta_id: string
  title: string
  media_type: 'movie' | 'series'
  season?: number
  episode?: number
  stream_info: Record<string, unknown>
  status: 'COMPLETED' | 'FAILED' | 'CANCELLED'
  downloaded_at: string
  poster?: string
}

// Contribution
export interface Contribution {
  id: string
  user_id: string
  contribution_type: 'metadata' | 'stream' | 'torrent' | 'telegram' | 'youtube' | 'nzb' | 'http' | 'acestream'
  target_id: string | null
  data: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  updated_at?: string
  reviewed_by?: string
  reviewed_at?: string
  review_notes?: string
}

// RSS Feed
export interface RSSFeed {
  id: string
  user_id: string
  name: string
  url: string
  is_active: boolean
  parsing_config: RSSParsingConfig
  last_scraped_at: string | null
  created_at: string
  updated_at: string
}

export interface RSSParsingConfig {
  title?: string
  description?: string
  pubDate?: string
  magnet?: string
  torrent?: string
  size?: string
  seeders?: string
  category?: string
  patterns: Record<string, string>
  filters: RSSFilters
  catalog_config: RSSCatalogConfig
}

export interface RSSFilters {
  title_filter?: string
  title_exclude_filter?: string
  min_size?: number
  max_size?: number
  min_seeders?: number
  category_filter?: string[]
}

export interface RSSCatalogConfig {
  auto_detect: boolean
  source_name?: string
  torrent_type: 'public' | 'private' | 'webseed'
  catalog_patterns: CatalogPattern[]
}

export interface CatalogPattern {
  name: string
  regex: string
  catalogs: string[]
  case_sensitive: boolean
  enabled: boolean
}

// Auth types
export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  email: string
  username?: string
  password: string
  newsletter_opt_in?: boolean
}

export interface AuthResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: User
}

export interface RegisterResponse {
  message: string
  email: string
  requires_verification: boolean
}

// API response types
export interface ApiError {
  detail: string
  status?: string
  status_code?: number
  error?: boolean
  errors?: Array<{ type: string; loc: (string | number)[]; msg: string }>
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  per_page: number
  pages: number
}

// Permission constants
export const Permission = {
  // User permissions
  VIEW_DASHBOARD: 'view_dashboard',
  MANAGE_PROFILES: 'manage_profiles',
  VIEW_WATCH_HISTORY: 'view_watch_history',
  VIEW_DOWNLOADS: 'view_downloads',
  SUBMIT_CONTRIBUTION: 'submit_contribution',
  IMPORT_CONTENT: 'import_content',
  MANAGE_OWN_RSS: 'manage_own_rss',

  // Moderator permissions
  VIEW_METRICS: 'view_metrics',
  BLOCK_TORRENT: 'block_torrent',
  DELETE_TORRENT: 'delete_torrent',
  REVIEW_CONTRIBUTIONS: 'review_contributions',
  RUN_SCRAPERS: 'run_scrapers',
  MANAGE_METADATA: 'manage_metadata',

  // Admin permissions
  MANAGE_USERS: 'manage_users',
  ASSIGN_ROLES: 'assign_roles',
  VIEW_ALL_RSS: 'view_all_rss',
  MANAGE_ALL_RSS: 'manage_all_rss',
  SYSTEM_CONFIG: 'system_config',
} as const

export type Permission = (typeof Permission)[keyof typeof Permission]

// =============================================================================
// Stream Types (v5 Schema)
// =============================================================================

export type FileType = 'video' | 'audio' | 'subtitle' | 'archive' | 'sample' | 'trailer' | 'nfo' | 'other'
export type LinkSource =
  | 'user'
  | 'ptt_parser'
  | 'torrent_metadata'
  | 'debrid_realdebrid'
  | 'debrid_alldebrid'
  | 'debrid_premiumize'
  | 'debrid_torbox'
  | 'debrid_debridlink'
  | 'manual'
  | 'filename'

export interface StreamFile {
  id: number
  stream_id: number
  file_index?: number
  filename: string
  file_path?: string
  size?: number
  file_type: FileType
  is_archive: boolean
  archive_contents?: Record<string, unknown>
}

export interface FileMediaLink {
  id: number
  file_id: number
  media_id: number
  season_number?: number
  episode_number?: number
  episode_end?: number
  is_primary: boolean
  confidence: number
  link_source: LinkSource
  debrid_service?: string
}

export interface TorrentStream {
  id: number
  stream_id: number
  info_hash: string

  // From Stream base
  name: string
  source: string
  resolution?: string
  codec?: string
  quality?: string
  bit_depth?: string
  uploader?: string
  release_group?: string
  is_blocked: boolean
  is_active: boolean
  playback_count: number

  // Normalized quality attributes (arrays)
  audio_formats: string[]
  channels: string[]
  hdr_formats: string[]
  languages: string[]

  // Release flags
  is_remastered: boolean
  is_upscaled: boolean
  is_proper: boolean
  is_repack: boolean
  is_extended: boolean
  is_complete: boolean
  is_dubbed: boolean
  is_subbed: boolean

  // TorrentStream-specific
  total_size: number
  seeders?: number
  leechers?: number
  torrent_type: 'public' | 'private' | 'webseed'
  uploaded_at?: string
  file_count: number

  // Timestamps
  created_at: string
  updated_at?: string

  // Relationships
  trackers: string[]
  files: StreamFile[]
}

export interface StreamInfo {
  // Core identifiers
  id: string
  info_hash: string

  // Stremio-compatible fields
  stremio_name?: string
  description?: string
  url?: string
  behavior_hints?: Record<string, unknown>

  // Rich metadata for frontend
  name: string // Stream display name
  resolution?: string
  quality?: string
  codec?: string
  bit_depth?: string

  // Normalized quality attributes (displayed as joined strings)
  audio_formats?: string
  channels?: string
  hdr_formats?: string
  languages?: string

  // Size and peers
  size?: string
  size_bytes?: number
  seeders?: number

  // Uploader info
  uploader?: string
  release_group?: string

  // Status
  cached?: boolean

  // Release flags
  is_remastered?: boolean
  is_upscaled?: boolean
  is_proper?: boolean
  is_repack?: boolean
  is_extended?: boolean
  is_complete?: boolean
  is_dubbed?: boolean
  is_subbed?: boolean
}

// Stream Template for user customization
export interface StreamTemplate {
  name_template: string
  description_template: string
}
