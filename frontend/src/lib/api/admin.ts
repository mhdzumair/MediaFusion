import { apiClient } from './client'

// ============================================
// Types - Reference Data
// ============================================

export interface ReferenceItem {
  id: number
  name: string
  usage_count: number
}

export interface ReferenceListParams {
  page?: number
  per_page?: number
  search?: string
}

export interface ReferenceListResponse {
  items: ReferenceItem[]
  total: number
  page: number
  per_page: number
  pages: number
  has_more: boolean
}

export interface ReferenceItemCreate {
  name: string
}

// ============================================
// Types - Episode Files
// ============================================

export interface EpisodeFileItem {
  id: number
  season_number: number
  episode_number: number
  file_index?: number
  filename?: string
  size?: number
}

// ============================================
// Types - Metadata
// ============================================

export interface ExternalIds {
  imdb?: string
  tmdb?: number
  tvdb?: number
  mal?: number
}

export interface MetadataItem {
  id: number // Internal media_id
  external_ids?: ExternalIds // All external IDs
  type: 'movie' | 'series' | 'tv'
  title: string
  year?: number
  poster?: string
  is_poster_working: boolean
  is_add_title_to_poster: boolean
  background?: string
  description?: string
  runtime?: string
  website?: string

  // Read-only computed fields
  total_streams: number
  created_at: string
  updated_at?: string
  last_stream_added?: string

  // Type-specific fields (Movie/Series)
  imdb_rating?: number
  tmdb_rating?: number
  parent_guide_nudity_status?: string

  // Series-specific
  end_date?: string // ISO format date string (YYYY-MM-DD)

  // TV-specific
  country?: string
  tv_language?: string
  logo?: string

  // Relationships
  genres: string[]
  catalogs: string[]
  stars: string[]
  parental_certificates: string[]
  aka_titles: string[]

  // Content moderation
  is_blocked?: boolean
  blocked_at?: string
  block_reason?: string
}

// ============================================
// Types - Content Moderation
// ============================================

export interface BlockMediaRequest {
  reason?: string
}

export interface BlockMediaResponse {
  media_id: number
  is_blocked: boolean
  blocked_at?: string
  blocked_by?: string
  block_reason?: string
  message: string
}

export interface MetadataListParams {
  page?: number
  per_page?: number
  media_type?: 'movie' | 'series' | 'tv'
  search?: string
  has_streams?: boolean
}

export interface MetadataListResponse {
  items: MetadataItem[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface MetadataUpdateRequest {
  // Base fields
  title?: string
  year?: number
  poster?: string
  is_poster_working?: boolean
  is_add_title_to_poster?: boolean
  background?: string
  description?: string
  runtime?: string
  website?: string

  // Type-specific fields (Movie/Series)
  imdb_rating?: number
  tmdb_rating?: number
  parent_guide_nudity_status?: string
  nudity_status?: string // Nudity status on Media table

  // Series-specific
  end_date?: string // ISO format date string (YYYY-MM-DD)

  // TV-specific
  country?: string
  tv_language?: string
  logo?: string

  // Relationships
  genres?: string[]
  catalogs?: string[]
  stars?: string[]
  parental_certificates?: string[]
  aka_titles?: string[]
}

export interface MetadataStatsResponse {
  total_movies: number
  total_series: number
  total_tv: number
  total_streams: number
  total_tv_streams: number
}

// Nudity status options
export const NUDITY_STATUS_OPTIONS = [
  { value: 'None', label: 'None' },
  { value: 'Mild', label: 'Mild' },
  { value: 'Moderate', label: 'Moderate' },
  { value: 'Severe', label: 'Severe' },
  { value: 'Unknown', label: 'Unknown' },
  { value: 'Disable', label: 'Disable' },
]

// ============================================
// Types - Torrent Streams
// ============================================

export interface TorrentStreamItem {
  id: string
  meta_id: string
  meta_title?: string

  // Basic info
  name: string // Stream display name (formerly torrent_name)
  size: number
  source: string
  resolution?: string
  codec?: string
  bit_depth?: string
  release_group?: string
  quality?: string
  seeders?: number
  leechers?: number
  is_blocked: boolean
  is_active: boolean

  // Normalized quality attributes (arrays)
  audio_formats: string[]
  channels: string[]
  hdr_formats: string[]

  // Release flags
  is_remastered: boolean
  is_upscaled: boolean
  is_proper: boolean
  is_repack: boolean
  is_extended: boolean
  is_complete: boolean
  is_dubbed: boolean
  is_subbed: boolean

  // Additional metadata
  torrent_type: string
  uploader?: string
  uploaded_at?: string

  // File info (for movies)
  filename?: string
  file_index?: number
  file_count: number
  total_size: number

  // Read-only
  playback_count: number
  created_at: string
  updated_at?: string

  // Relationships
  languages: string[]
  trackers: string[]
  files: StreamFileItem[]
}

export interface StreamFileItem {
  id: number
  file_index?: number
  filename: string
  file_path?: string
  size?: number
  file_type: string
}

export interface TorrentStreamListParams {
  page?: number
  per_page?: number
  meta_id?: string
  search?: string
  source?: string
  is_blocked?: boolean
  resolution?: string
}

export interface TorrentStreamListResponse {
  items: TorrentStreamItem[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface TorrentStreamUpdateRequest {
  name?: string
  source?: string
  resolution?: string
  codec?: string
  quality?: string
  bit_depth?: string
  seeders?: number
  leechers?: number
  is_blocked?: boolean

  // Normalized quality attributes (arrays)
  audio_formats?: string[]
  channels?: string[]
  hdr_formats?: string[]

  // Additional metadata
  torrent_type?: string
  uploader?: string
  release_group?: string
  uploaded_at?: string

  // Relationships
  languages?: string[]
  trackers?: string[]
}

// Torrent type options
export const TORRENT_TYPE_OPTIONS = [
  { value: 'public', label: 'Public' },
  { value: 'semi-private', label: 'Semi-Private' },
  { value: 'private', label: 'Private' },
  { value: 'web-seed', label: 'Web Seed' },
]

// ============================================
// Types - TV Streams
// ============================================

export interface TVStreamItem {
  id: number
  meta_id: string
  meta_title?: string

  // Basic info
  name: string
  url?: string
  ytId?: string
  externalUrl?: string
  source: string
  country?: string

  // Status
  is_active: boolean
  is_blocked: boolean
  test_failure_count: number // Read-only

  // DRM
  drm_key_id?: string
  drm_key?: string

  // Advanced
  behaviorHints?: Record<string, unknown>

  // Read-only
  created_at: string
  updated_at?: string

  // Relationships
  namespaces: string[]
}

export interface TVStreamListParams {
  page?: number
  per_page?: number
  meta_id?: string
  search?: string
  source?: string
  is_active?: boolean
  country?: string
}

export interface TVStreamListResponse {
  items: TVStreamItem[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface TVStreamUpdateRequest {
  name?: string
  url?: string
  ytId?: string
  externalUrl?: string
  source?: string
  country?: string
  is_active?: boolean

  // DRM
  drm_key_id?: string
  drm_key?: string

  // Advanced
  behaviorHints?: Record<string, unknown>

  // Relationships
  namespaces?: string[]
}

// ============================================
// Helper to build query params
// ============================================

function buildQueryString<T extends object>(params: T): string {
  const searchParams = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      searchParams.append(key, String(value))
    }
  }
  const query = searchParams.toString()
  return query ? `?${query}` : ''
}

// Note: The admin API is at /api/admin, not /api/v1
// We need to bypass the standard client's base URL handling
const ADMIN_BASE = '/api/v1/admin'

async function adminGet<T>(endpoint: string): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${ADMIN_BASE}${endpoint}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  return response.json()
}

async function adminPost<T>(endpoint: string, data?: unknown): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${ADMIN_BASE}${endpoint}`, {
    method: 'POST',
    headers,
    body: data ? JSON.stringify(data) : undefined,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  return response.json()
}

async function adminPatch<T>(endpoint: string, data?: unknown): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${ADMIN_BASE}${endpoint}`, {
    method: 'PATCH',
    headers,
    body: data ? JSON.stringify(data) : undefined,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  return response.json()
}

async function adminDelete<T>(endpoint: string): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${ADMIN_BASE}${endpoint}`, {
    method: 'DELETE',
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return {} as T
  }

  return response.json()
}

// ============================================
// Types - External Metadata
// ============================================

export interface ExternalMetadataPreview {
  provider: 'imdb' | 'tmdb'
  external_id: string
  title: string
  year?: number
  description?: string
  poster?: string
  background?: string
  genres: string[]
  imdb_rating?: number
  tmdb_rating?: number
  nudity_status?: string
  parental_certificates: string[]
  stars: string[]
  aka_titles: string[]
  runtime?: string
  imdb_id?: string
  tmdb_id?: string
}

export interface FetchExternalRequest {
  provider: 'imdb' | 'tmdb'
  external_id: string
}

export interface MigrateIdRequest {
  new_external_id: string
}

export interface SearchExternalRequest {
  provider: 'imdb' | 'tmdb'
  title: string
  year?: number
  media_type?: 'movie' | 'series'
}

export interface SearchExternalResponse {
  results: ExternalMetadataPreview[]
}

// ============================================
// Admin API
// ============================================

export const adminApi = {
  // ============================================
  // Stats
  // ============================================

  getStats: async (): Promise<MetadataStatsResponse> => {
    return adminGet<MetadataStatsResponse>('/stats')
  },

  // ============================================
  // Metadata
  // ============================================

  listMetadata: async (params: MetadataListParams = {}): Promise<MetadataListResponse> => {
    const query = buildQueryString(params)
    return adminGet<MetadataListResponse>(`/metadata${query}`)
  },

  getMetadata: async (mediaId: number): Promise<MetadataItem> => {
    return adminGet<MetadataItem>(`/metadata/${mediaId}`)
  },

  updateMetadata: async (mediaId: number, data: MetadataUpdateRequest): Promise<MetadataItem> => {
    return adminPatch<MetadataItem>(`/metadata/${mediaId}`, data)
  },

  deleteMetadata: async (mediaId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/metadata/${mediaId}`)
  },

  // ============================================
  // Content Moderation
  // ============================================

  /**
   * Block media content (Moderator/Admin only).
   * Blocked content is hidden from regular users.
   */
  blockMedia: async (mediaId: number, request: BlockMediaRequest = {}): Promise<BlockMediaResponse> => {
    return adminPost<BlockMediaResponse>(`/metadata/${mediaId}/block`, request)
  },

  /**
   * Unblock media content (Moderator/Admin only).
   * Makes content visible to regular users again.
   */
  unblockMedia: async (mediaId: number): Promise<BlockMediaResponse> => {
    return adminPost<BlockMediaResponse>(`/metadata/${mediaId}/unblock`, {})
  },

  /**
   * List all blocked media (Moderator/Admin only).
   */
  listBlockedMedia: async (params: { page?: number; per_page?: number } = {}): Promise<MetadataListResponse> => {
    const query = buildQueryString(params)
    return adminGet<MetadataListResponse>(`/blocked-media${query}`)
  },

  // ============================================
  // External Metadata (ID Migration & Data Fetch)
  // ============================================

  /**
   * Preview external metadata from IMDb or TMDB without applying changes.
   */
  fetchExternalMetadata: async (mediaId: number, request: FetchExternalRequest): Promise<ExternalMetadataPreview> => {
    return adminPost<ExternalMetadataPreview>(`/metadata/${mediaId}/fetch-external`, request)
  },

  /**
   * Fetch and apply external metadata from IMDb or TMDB.
   */
  applyExternalMetadata: async (mediaId: number, request: FetchExternalRequest): Promise<MetadataItem> => {
    return adminPost<MetadataItem>(`/metadata/${mediaId}/apply-external`, request)
  },

  /**
   * Migrate internal mf/mftmdb ID to proper external ID.
   */
  migrateMetadataId: async (mediaId: number, request: MigrateIdRequest): Promise<MetadataItem> => {
    return adminPost<MetadataItem>(`/metadata/${mediaId}/migrate-id`, request)
  },

  /**
   * Search external providers for metadata.
   */
  searchExternalMetadata: async (request: SearchExternalRequest): Promise<SearchExternalResponse> => {
    return adminPost<SearchExternalResponse>('/metadata/search-external', request)
  },

  // ============================================
  // Torrent Streams
  // ============================================

  listTorrentStreams: async (params: TorrentStreamListParams = {}): Promise<TorrentStreamListResponse> => {
    const query = buildQueryString(params)
    return adminGet<TorrentStreamListResponse>(`/torrent-streams${query}`)
  },

  getTorrentStream: async (streamId: string): Promise<TorrentStreamItem> => {
    return adminGet<TorrentStreamItem>(`/torrent-streams/${streamId}`)
  },

  updateTorrentStream: async (streamId: string, data: TorrentStreamUpdateRequest): Promise<TorrentStreamItem> => {
    return adminPatch<TorrentStreamItem>(`/torrent-streams/${streamId}`, data)
  },

  blockTorrentStream: async (streamId: number): Promise<{ message: string }> => {
    return adminPost<{ message: string }>(`/torrent-streams/${streamId}/block`)
  },

  unblockTorrentStream: async (streamId: number): Promise<{ message: string }> => {
    return adminPost<{ message: string }>(`/torrent-streams/${streamId}/unblock`)
  },

  deleteTorrentStream: async (streamId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/torrent-streams/${streamId}`)
  },

  // ============================================
  // TV Streams
  // ============================================

  listTVStreams: async (params: TVStreamListParams = {}): Promise<TVStreamListResponse> => {
    const query = buildQueryString(params)
    return adminGet<TVStreamListResponse>(`/tv-streams${query}`)
  },

  getTVStream: async (streamId: number): Promise<TVStreamItem> => {
    return adminGet<TVStreamItem>(`/tv-streams/${streamId}`)
  },

  updateTVStream: async (streamId: number, data: TVStreamUpdateRequest): Promise<TVStreamItem> => {
    return adminPatch<TVStreamItem>(`/tv-streams/${streamId}`, data)
  },

  toggleTVStreamActive: async (streamId: number): Promise<{ message: string; is_active: boolean }> => {
    return adminPost<{ message: string; is_active: boolean }>(`/tv-streams/${streamId}/toggle-active`)
  },

  deleteTVStream: async (streamId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/tv-streams/${streamId}`)
  },

  // ============================================
  // Utility / Filter Options
  // ============================================

  getTorrentSources: async (): Promise<{ sources: string[] }> => {
    return adminGet<{ sources: string[] }>('/sources/torrent')
  },

  getTVSources: async (): Promise<{ sources: string[] }> => {
    return adminGet<{ sources: string[] }>('/sources/tv')
  },

  getCountries: async (): Promise<{ countries: string[] }> => {
    return adminGet<{ countries: string[] }>('/countries')
  },

  getResolutions: async (): Promise<{ resolutions: string[] }> => {
    return adminGet<{ resolutions: string[] }>('/resolutions')
  },

  // ============================================
  // Reference Data - Genres
  // ============================================

  listGenres: async (params: ReferenceListParams = {}): Promise<ReferenceListResponse> => {
    const query = buildQueryString(params)
    return adminGet<ReferenceListResponse>(`/reference/genres${query}`)
  },

  createGenre: async (data: ReferenceItemCreate): Promise<ReferenceItem> => {
    return adminPost<ReferenceItem>('/reference/genres', data)
  },

  deleteGenre: async (genreId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/reference/genres/${genreId}`)
  },

  // ============================================
  // Reference Data - Catalogs
  // ============================================

  listCatalogs: async (params: ReferenceListParams = {}): Promise<ReferenceListResponse> => {
    const query = buildQueryString(params)
    return adminGet<ReferenceListResponse>(`/reference/catalogs${query}`)
  },

  createCatalog: async (data: ReferenceItemCreate): Promise<ReferenceItem> => {
    return adminPost<ReferenceItem>('/reference/catalogs', data)
  },

  deleteCatalog: async (catalogId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/reference/catalogs/${catalogId}`)
  },

  // ============================================
  // Reference Data - Languages
  // ============================================

  listLanguages: async (params: ReferenceListParams = {}): Promise<ReferenceListResponse> => {
    const query = buildQueryString(params)
    return adminGet<ReferenceListResponse>(`/reference/languages${query}`)
  },

  createLanguage: async (data: ReferenceItemCreate): Promise<ReferenceItem> => {
    return adminPost<ReferenceItem>('/reference/languages', data)
  },

  deleteLanguage: async (languageId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/reference/languages/${languageId}`)
  },

  // ============================================
  // Reference Data - Stars
  // ============================================

  listStars: async (params: ReferenceListParams = {}): Promise<ReferenceListResponse> => {
    const query = buildQueryString(params)
    return adminGet<ReferenceListResponse>(`/reference/stars${query}`)
  },

  createStar: async (data: ReferenceItemCreate): Promise<ReferenceItem> => {
    return adminPost<ReferenceItem>('/reference/stars', data)
  },

  deleteStar: async (starId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/reference/stars/${starId}`)
  },

  // ============================================
  // Reference Data - Parental Certificates
  // ============================================

  listParentalCertificates: async (params: ReferenceListParams = {}): Promise<ReferenceListResponse> => {
    const query = buildQueryString(params)
    return adminGet<ReferenceListResponse>(`/reference/parental-certificates${query}`)
  },

  createParentalCertificate: async (data: ReferenceItemCreate): Promise<ReferenceItem> => {
    return adminPost<ReferenceItem>('/reference/parental-certificates', data)
  },

  deleteParentalCertificate: async (certId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/reference/parental-certificates/${certId}`)
  },

  // ============================================
  // Reference Data - Namespaces
  // ============================================

  listNamespaces: async (params: ReferenceListParams = {}): Promise<ReferenceListResponse> => {
    const query = buildQueryString(params)
    return adminGet<ReferenceListResponse>(`/reference/namespaces${query}`)
  },

  createNamespace: async (data: ReferenceItemCreate): Promise<ReferenceItem> => {
    return adminPost<ReferenceItem>('/reference/namespaces', data)
  },

  deleteNamespace: async (namespaceId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/reference/namespaces/${namespaceId}`)
  },

  // ============================================
  // Reference Data - Announce URLs
  // ============================================

  listAnnounceUrls: async (params: ReferenceListParams = {}): Promise<ReferenceListResponse> => {
    const query = buildQueryString(params)
    return adminGet<ReferenceListResponse>(`/reference/announce-urls${query}`)
  },

  createAnnounceUrl: async (data: ReferenceItemCreate): Promise<ReferenceItem> => {
    return adminPost<ReferenceItem>('/reference/announce-urls', data)
  },

  deleteAnnounceUrl: async (urlId: number): Promise<{ message: string }> => {
    return adminDelete<{ message: string }>(`/reference/announce-urls/${urlId}`)
  },
}

// ============================================
// Database Admin Types
// ============================================

export interface DatabaseStats {
  version: string
  database_name: string
  size_human: string
  total_size_bytes: number
  connection_count: number
  max_connections: number
  cache_hit_ratio: number
  uptime_seconds: number
  active_queries: number
  deadlocks: number
  transactions_committed: number
  transactions_rolled_back: number
}

export interface TableInfo {
  name: string
  schema_name: string
  row_count: number
  size_human: string
  size_bytes: number
  index_size_human: string
  index_size_bytes: number
  last_vacuum: string | null
  last_analyze: string | null
  last_autovacuum: string | null
  last_autoanalyze: string | null
}

export interface TablesListResponse {
  tables: TableInfo[]
  total_count: number
  total_size_human: string
  total_size_bytes: number
}

export interface ColumnInfo {
  name: string
  data_type: string
  is_nullable: boolean
  default_value: string | null
  is_primary_key: boolean
  is_foreign_key: boolean
  foreign_key_ref: string | null
}

export interface IndexInfo {
  name: string
  columns: string[]
  is_unique: boolean
  is_primary: boolean
  index_type: string
}

export interface ForeignKeyInfo {
  name: string
  columns: string[]
  referenced_table: string
  referenced_columns: string[]
}

export interface TableSchema {
  name: string
  schema_name: string
  columns: ColumnInfo[]
  indexes: IndexInfo[]
  foreign_keys: ForeignKeyInfo[]
  row_count: number
  size_human: string
}

export type FilterOperator =
  | 'equals'
  | 'not_equals'
  | 'contains'
  | 'starts_with'
  | 'ends_with'
  | 'is_null'
  | 'is_not_null'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  | 'array_contains'
  | 'array_not_contains'
  | 'array_empty'
  | 'array_not_empty'
  | 'array_length_eq'
  | 'array_length_gt'
  | 'json_is_null'
  | 'json_is_not_null'

export interface FilterCondition {
  column: string
  operator: FilterOperator
  value?: string
}

export interface TableDataParams {
  page?: number
  per_page?: number
  order_by?: string
  order_dir?: 'asc' | 'desc'
  search?: string
  /** Multiple filters as array - preferred method */
  filters?: FilterCondition[]
  /** @deprecated Use filters instead */
  filter_column?: string
  /** @deprecated Use filters instead */
  filter_operator?: FilterOperator
  /** @deprecated Use filters instead */
  filter_value?: string
}

export interface TableDataResponse {
  table: string
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface OrphanRecord {
  table: string
  id: string
  reason: string
  created_at: string | null
}

export interface OrphansResponse {
  orphans: OrphanRecord[]
  total_count: number
  by_type: Record<string, number>
}

export interface OrphanCleanupRequest {
  tables?: string[]
  dry_run?: boolean
}

export interface OrphanCleanupResponse {
  dry_run: boolean
  deleted: Record<string, number>
  would_delete: Record<string, number>
}

export interface MaintenanceRequest {
  tables?: string[]
  operation: 'vacuum' | 'analyze' | 'vacuum_analyze' | 'reindex'
  full?: boolean
}

export interface MaintenanceResult {
  success: boolean
  operation: string
  tables_processed: string[]
  execution_time_ms: number
  message: string
}

export interface BulkDeleteRequest {
  table: string
  ids: string[]
  id_column?: string
  cascade?: boolean // If true, delete related records in child tables first
}

export interface BulkUpdateRequest {
  table: string
  ids: string[]
  id_column?: string
  updates: Record<string, unknown>
}

export interface BulkOperationResult {
  success: boolean
  rows_affected: number
  execution_time_ms: number
  errors: string[]
}

export interface ImportPreviewResponse {
  total_rows: number
  sample_rows: Record<string, unknown>[]
  detected_columns: string[]
  table_columns: string[]
  column_mapping: Record<string, string>
  validation_errors: string[]
  warnings: string[]
}

export interface ImportResult {
  success: boolean
  rows_imported: number
  rows_updated: number
  rows_skipped: number
  errors: string[]
  execution_time_ms: number
}

// ============================================
// Database Admin API
// ============================================

const DB_BASE = '/api/v1/admin/db'

async function dbGet<T>(endpoint: string): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${DB_BASE}${endpoint}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  return response.json()
}

async function dbPost<T>(endpoint: string, data?: unknown): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${DB_BASE}${endpoint}`, {
    method: 'POST',
    headers,
    body: data ? JSON.stringify(data) : undefined,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  return response.json()
}

async function dbPostFormData<T>(endpoint: string, formData: FormData): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${DB_BASE}${endpoint}`, {
    method: 'POST',
    headers,
    body: formData,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  return response.json()
}

export const databaseApi = {
  // ============================================
  // Stats
  // ============================================

  getStats: async (): Promise<DatabaseStats> => {
    return dbGet<DatabaseStats>('/stats')
  },

  // ============================================
  // Tables
  // ============================================

  listTables: async (): Promise<TablesListResponse> => {
    return dbGet<TablesListResponse>('/tables')
  },

  getTableSchema: async (tableName: string): Promise<TableSchema> => {
    return dbGet<TableSchema>(`/tables/${tableName}/schema`)
  },

  getTableData: async (tableName: string, params: TableDataParams = {}): Promise<TableDataResponse> => {
    // Handle filters array specially - serialize to JSON
    const { filters, ...restParams } = params
    const queryParams: Record<string, string | number | undefined> = { ...restParams }
    if (filters && filters.length > 0) {
      queryParams.filters = JSON.stringify(filters)
    }
    const query = buildQueryString(queryParams)
    return dbGet<TableDataResponse>(`/tables/${tableName}/data${query}`)
  },

  // ============================================
  // Export
  // ============================================

  exportTable: async (
    tableName: string,
    format: 'csv' | 'json' | 'sql' = 'csv',
    options: { include_schema?: boolean; include_data?: boolean; limit?: number } = {},
  ): Promise<Blob> => {
    const token = apiClient.getAccessToken()
    const apiKey = apiClient.getApiKey()
    const headers: HeadersInit = {}
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    if (apiKey) {
      headers['X-API-Key'] = apiKey
    }

    const params = new URLSearchParams({ format })
    if (options.include_schema !== undefined) params.append('include_schema', String(options.include_schema))
    if (options.include_data !== undefined) params.append('include_data', String(options.include_data))
    if (options.limit !== undefined) params.append('limit', String(options.limit))

    const response = await fetch(`${DB_BASE}/tables/${tableName}/export?${params}`, {
      method: 'GET',
      headers,
    })

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
      throw new Error(error.detail || 'Export failed')
    }

    return response.blob()
  },

  // ============================================
  // Import
  // ============================================

  previewImport: async (
    file: File,
    table: string,
    format: 'csv' | 'json' | 'sql' = 'csv',
  ): Promise<ImportPreviewResponse> => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('table', table)
    formData.append('format', format)
    return dbPostFormData<ImportPreviewResponse>('/import/preview', formData)
  },

  executeImport: async (
    file: File,
    table: string,
    format: 'csv' | 'json' | 'sql',
    mode: 'insert' | 'upsert' | 'replace',
    columnMapping?: Record<string, string>,
    skipErrors?: boolean,
  ): Promise<ImportResult> => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('table', table)
    formData.append('format', format)
    formData.append('mode', mode)
    if (columnMapping) {
      formData.append('column_mapping', JSON.stringify(columnMapping))
    }
    if (skipErrors !== undefined) {
      formData.append('skip_errors', String(skipErrors))
    }
    return dbPostFormData<ImportResult>('/import/execute', formData)
  },

  // ============================================
  // Maintenance
  // ============================================

  vacuum: async (request: MaintenanceRequest): Promise<MaintenanceResult> => {
    return dbPost<MaintenanceResult>('/maintenance/vacuum', request)
  },

  analyze: async (request: MaintenanceRequest): Promise<MaintenanceResult> => {
    return dbPost<MaintenanceResult>('/maintenance/analyze', request)
  },

  reindex: async (request: MaintenanceRequest): Promise<MaintenanceResult> => {
    return dbPost<MaintenanceResult>('/maintenance/reindex', request)
  },

  // ============================================
  // Orphans
  // ============================================

  findOrphans: async (): Promise<OrphansResponse> => {
    return dbGet<OrphansResponse>('/orphans')
  },

  cleanupOrphans: async (request: OrphanCleanupRequest = {}): Promise<OrphanCleanupResponse> => {
    const params = new URLSearchParams()
    if (request.dry_run !== undefined) params.append('dry_run', String(request.dry_run))
    if (request.tables) {
      request.tables.forEach((t) => params.append('tables', t))
    }
    return dbPost<OrphanCleanupResponse>(`/orphans/cleanup?${params}`)
  },

  // ============================================
  // Bulk Operations
  // ============================================

  bulkDelete: async (request: BulkDeleteRequest): Promise<BulkOperationResult> => {
    return dbPost<BulkOperationResult>('/bulk/delete', request)
  },

  bulkUpdate: async (request: BulkUpdateRequest): Promise<BulkOperationResult> => {
    return dbPost<BulkOperationResult>('/bulk/update', request)
  },
}
