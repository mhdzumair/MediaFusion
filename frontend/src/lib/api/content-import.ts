import { apiClient } from './client'

export interface TorrentFile {
  filename: string
  size: number
  index: number
  season_number?: number
  episode_number?: number
}

export interface TorrentMatch {
  id: string
  title: string
  year?: number
  poster?: string
  type: 'movie' | 'series'
  // Extended match info from analysis
  imdb_id?: string
  imdb_rating?: number
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
  // Validation error details
  errors?: Array<{
    type: string
    message: string
  }>
  // Data needed for annotation
  torrent_data?: {
    file_data?: TorrentFile[]
  }
}

// Content types supported for torrent/magnet imports (no 'tv')
export type TorrentMetaType = 'movie' | 'series' | 'sports'

export interface MagnetAnalyzeRequest {
  magnet_link: string
  meta_type: TorrentMetaType
  meta_id?: string
  title?: string
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
  // Validation error details
  errors?: Array<{
    type: string
    message: string
  }>
  // Data needed for annotation
  torrent_data?: {
    file_data?: TorrentFile[]
  }
}

export interface ImportJobStatus {
  job_id: string
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'not_found'
  progress: number
  total: number
  stats: Record<string, number>
  error?: string
  source_type: string
  created_at: string
  updated_at: string
  source_id?: number
  message?: string
}

// Extended import request for torrent/magnet
export interface TorrentImportRequest {
  // Source - one of these required
  magnet_link?: string
  torrent_file?: File

  // Content type
  meta_type: TorrentMetaType

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
  audio?: string // Comma-separated for multi-value
  hdr?: string // Comma-separated for multi-value
  languages?: string // Comma-separated for multi-value

  // Catalogs
  catalogs?: string // Comma-separated

  // Series/Sports specific
  episode_name_parser?: string
  created_at?: string // Release date YYYY-MM-DD

  // Import options
  force_import?: boolean
  is_add_title_to_poster?: boolean
  is_anonymous?: boolean // Whether to show as anonymous contribution

  // File annotations for series
  file_data?: string // JSON stringified array

  // Sports category
  sports_category?: string
}

// ============================================
// M3U Import Types
// ============================================

export type M3UContentType = 'tv' | 'movie' | 'series' | 'unknown'

export interface M3UMatchedMedia {
  id: string
  title: string
  year?: number
  poster?: string
  type: 'movie' | 'series'
}

export interface M3UChannelPreview {
  index: number
  name: string
  url: string
  logo?: string
  genres: string[]
  country?: string
  detected_type: M3UContentType
  matched_media?: M3UMatchedMedia
  season?: number
  episode?: number
  parsed_title?: string
  parsed_year?: number
}

export interface M3UAnalyzeResponse {
  status: 'success' | 'error'
  redis_key: string
  total_count: number
  channels: M3UChannelPreview[]
  summary: Record<string, number>
  error?: string
}

export interface M3UImportOverride {
  index: number
  type: M3UContentType
  media_id?: string
}

// ============================================
// Xtream Codes Types
// ============================================

export interface XtreamCredentials {
  server_url: string
  username: string
  password: string
}

export interface XtreamCategory {
  id: string
  name: string
  count: number
}

export interface XtreamAnalyzeResponse {
  status: 'success' | 'error'
  account_info?: {
    status?: string
    exp_date?: string
    max_connections?: number
    active_cons?: number
    is_trial?: boolean
  }
  summary: Record<string, number>
  live_categories: XtreamCategory[]
  vod_categories: XtreamCategory[]
  series_categories: XtreamCategory[]
  redis_key: string
  error?: string
}

export interface XtreamImportRequest {
  redis_key: string
  source_name: string
  save_source?: boolean
  is_public?: boolean
  import_live?: boolean
  import_vod?: boolean
  import_series?: boolean
  live_category_ids?: string[]
  vod_category_ids?: string[]
  series_category_ids?: string[]
}

// ============================================
// IPTV Source Management Types
// ============================================

export interface IPTVSource {
  id: number
  source_type: 'm3u' | 'xtream' | 'stalker'
  name: string
  is_public: boolean
  import_live: boolean
  import_vod: boolean
  import_series: boolean
  last_synced_at: string | null
  last_sync_stats: Record<string, number> | null
  is_active: boolean
  created_at: string
  has_url: boolean
  has_credentials: boolean
}

export interface IPTVSourceListResponse {
  sources: IPTVSource[]
  total: number
}

export interface IPTVSourceUpdateRequest {
  name?: string
  is_active?: boolean
  import_live?: boolean
  import_vod?: boolean
  import_series?: boolean
}

export interface SyncResponse {
  status: 'success' | 'error' | 'processing'
  message: string
  stats?: Record<string, number>
  error?: string
  job_id?: string // For background task tracking
}

// ============================================
// YouTube Import Types
// ============================================

export interface YouTubeAnalyzeRequest {
  youtube_url: string
  meta_type: 'movie' | 'series' | 'sports' | 'tv'
}

export interface YouTubeAnalyzeResponse {
  status: 'success' | 'error'
  video_id?: string
  title?: string
  channel_name?: string
  channel_id?: string
  thumbnail?: string
  duration_seconds?: number
  is_live?: boolean
  resolution?: string
  matches?: TorrentMatch[]
  error?: string
}

export interface YouTubeImportRequest {
  youtube_url: string
  meta_type: 'movie' | 'series' | 'sports' | 'tv'
  meta_id?: string
  title?: string
  poster?: string
  background?: string
  resolution?: string
  quality?: string
  codec?: string
  languages?: string
  catalogs?: string
  is_anonymous?: boolean
  force_import?: boolean
}

// ============================================
// HTTP Import Types
// ============================================

export interface HTTPAnalyzeRequest {
  url: string
  meta_type: 'movie' | 'series' | 'sports' | 'tv'
}

export interface HTTPAnalyzeResponse {
  status: 'success' | 'error'
  url?: string
  detected_format?: string
  detected_extractor?: string
  is_valid?: boolean
  error?: string
}

export interface HTTPImportRequest {
  url: string
  meta_type: 'movie' | 'series' | 'sports' | 'tv'
  meta_id?: string
  title?: string
  extractor_name?: string // MediaFlow extractor name
  request_headers?: Record<string, string>
  response_headers?: Record<string, string>
  drm_key_id?: string // For MPD streams
  drm_key?: string
  resolution?: string
  quality?: string
  codec?: string
  languages?: string // Comma-separated
  is_anonymous?: boolean
  force_import?: boolean
}

// ============================================
// AceStream Import Types
// ============================================

export interface AceStreamAnalyzeRequest {
  content_id?: string // 40-char hex
  info_hash?: string // 40-char hex
  meta_type: 'movie' | 'series' | 'sports' | 'tv'
}

export interface AceStreamAnalyzeResponse {
  status: 'success' | 'error'
  content_id?: string
  info_hash?: string
  content_id_valid?: boolean
  info_hash_valid?: boolean
  already_exists?: boolean
  error?: string
}

export interface AceStreamImportRequest {
  content_id?: string // 40-char hex or acestream:// URL
  info_hash?: string // 40-char hex
  meta_type: 'movie' | 'series' | 'sports' | 'tv'
  title: string // Required - stream/media title
  meta_id?: string // Optional external ID (e.g. IMDb tt1234567)
  languages?: string // Comma-separated
  resolution?: string
  quality?: string
  codec?: string
  poster?: string // Poster image URL
  background?: string // Background/backdrop image URL
  logo?: string // Logo image URL
  is_anonymous?: boolean
  force_import?: boolean
}

// ============================================
// NZB Import Types
// ============================================

export interface NZBFile {
  filename: string
  size: number
  index: number
}

export interface NZBMatch {
  id: string
  title: string
  year?: number
  poster?: string
  type: string
  source: string
  confidence?: number
  imdb_id?: string
  imdb_rating?: number
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
}

export interface NZBAnalyzeResponse {
  status: string
  nzb_guid?: string
  nzb_title?: string
  total_size?: number
  total_size_readable?: string
  file_count?: number
  files?: NZBFile[]
  parsed_title?: string
  year?: number
  resolution?: string
  quality?: string
  codec?: string
  audio?: string[]
  matches?: NZBMatch[]
  error?: string
  indexer?: string
  group_name?: string
  poster?: string
  posted_at?: string
  is_passworded?: boolean
}

export type NZBMetaType = 'movie' | 'series'

export interface NZBImportRequest {
  meta_type: NZBMetaType
  meta_id?: string
  title?: string
  indexer?: string
  resolution?: string
  quality?: string
  codec?: string
  languages?: string
  force_import?: boolean
  is_anonymous?: boolean
  file_data?: string
}

export interface NZBURLImportRequest {
  nzb_url: string
  meta_type: NZBMetaType
  meta_id?: string
  title?: string
  indexer?: string
  is_anonymous?: boolean
}

export const contentImportApi = {
  /**
   * Analyze a magnet link
   */
  analyzeMagnet: async (data: MagnetAnalyzeRequest): Promise<TorrentAnalyzeResponse> => {
    return apiClient.post<TorrentAnalyzeResponse>('/import/magnet/analyze', data)
  },

  /**
   * Analyze a torrent file
   */
  analyzeTorrent: async (file: File, metaType: TorrentMetaType): Promise<TorrentAnalyzeResponse> => {
    const formData = new FormData()
    formData.append('torrent_file', file)
    formData.append('meta_type', metaType)
    return apiClient.upload<TorrentAnalyzeResponse>('/import/torrent/analyze', formData)
  },

  /**
   * Import a magnet link as a contribution
   */
  importMagnet: async (
    data: Omit<TorrentImportRequest, 'torrent_file'> & { magnet_link: string },
  ): Promise<ImportResponse> => {
    const formData = new FormData()
    formData.append('magnet_link', data.magnet_link)
    formData.append('meta_type', data.meta_type)
    if (data.meta_id) formData.append('meta_id', data.meta_id)
    if (data.title) formData.append('title', data.title)
    if (data.poster) formData.append('poster', data.poster)
    if (data.background) formData.append('background', data.background)
    if (data.logo) formData.append('logo', data.logo)
    if (data.resolution) formData.append('resolution', data.resolution)
    if (data.quality) formData.append('quality', data.quality)
    if (data.codec) formData.append('codec', data.codec)
    if (data.audio) formData.append('audio', data.audio)
    if (data.hdr) formData.append('hdr', data.hdr)
    if (data.languages) formData.append('languages', data.languages)
    if (data.catalogs) formData.append('catalogs', data.catalogs)
    if (data.episode_name_parser) formData.append('episode_name_parser', data.episode_name_parser)
    if (data.created_at) formData.append('created_at', data.created_at)
    if (data.force_import) formData.append('force_import', 'true')
    if (data.is_add_title_to_poster) formData.append('is_add_title_to_poster', 'true')
    if (data.is_anonymous) formData.append('is_anonymous', 'true')
    if (data.file_data) formData.append('file_data', data.file_data)
    if (data.sports_category) formData.append('sports_category', data.sports_category)
    return apiClient.upload<ImportResponse>('/import/magnet', formData)
  },

  /**
   * Import a torrent file as a contribution
   */
  importTorrent: async (
    data: Omit<TorrentImportRequest, 'magnet_link'> & { torrent_file: File },
  ): Promise<ImportResponse> => {
    const formData = new FormData()
    formData.append('torrent_file', data.torrent_file)
    formData.append('meta_type', data.meta_type)
    if (data.meta_id) formData.append('meta_id', data.meta_id)
    if (data.title) formData.append('title', data.title)
    if (data.poster) formData.append('poster', data.poster)
    if (data.background) formData.append('background', data.background)
    if (data.logo) formData.append('logo', data.logo)
    if (data.resolution) formData.append('resolution', data.resolution)
    if (data.quality) formData.append('quality', data.quality)
    if (data.codec) formData.append('codec', data.codec)
    if (data.audio) formData.append('audio', data.audio)
    if (data.hdr) formData.append('hdr', data.hdr)
    if (data.languages) formData.append('languages', data.languages)
    if (data.catalogs) formData.append('catalogs', data.catalogs)
    if (data.episode_name_parser) formData.append('episode_name_parser', data.episode_name_parser)
    if (data.created_at) formData.append('created_at', data.created_at)
    if (data.force_import) formData.append('force_import', 'true')
    if (data.is_add_title_to_poster) formData.append('is_add_title_to_poster', 'true')
    if (data.is_anonymous) formData.append('is_anonymous', 'true')
    if (data.file_data) formData.append('file_data', data.file_data)
    if (data.sports_category) formData.append('sports_category', data.sports_category)
    return apiClient.upload<ImportResponse>('/import/torrent', formData)
  },

  /**
   * Analyze an M3U playlist (preview before import)
   */
  analyzeM3U: async (data: { m3u_url?: string; m3u_file?: File }): Promise<M3UAnalyzeResponse> => {
    const formData = new FormData()
    if (data.m3u_url) formData.append('m3u_url', data.m3u_url)
    if (data.m3u_file) formData.append('m3u_file', data.m3u_file)
    return apiClient.upload<M3UAnalyzeResponse>('/import/m3u/analyze', formData)
  },

  /**
   * Import an M3U playlist
   */
  importM3U: async (data: {
    m3u_url?: string
    m3u_file?: File
    redis_key?: string
    source?: string
    is_public?: boolean
    overrides?: M3UImportOverride[]
    save_source?: boolean
    source_name?: string
  }): Promise<ImportResponse> => {
    const formData = new FormData()
    if (data.m3u_url) formData.append('m3u_url', data.m3u_url)
    if (data.m3u_file) formData.append('m3u_file', data.m3u_file)
    if (data.redis_key) formData.append('redis_key', data.redis_key)
    if (data.source) formData.append('source', data.source)
    if (data.is_public !== undefined) formData.append('is_public', String(data.is_public))
    if (data.overrides) formData.append('overrides', JSON.stringify(data.overrides))
    if (data.save_source !== undefined) formData.append('save_source', String(data.save_source))
    if (data.source_name) formData.append('source_name', data.source_name)
    return apiClient.upload<ImportResponse>('/import/m3u', formData)
  },

  // ============================================
  // Xtream Codes API
  // ============================================

  /**
   * Analyze an Xtream Codes server
   */
  analyzeXtream: async (credentials: XtreamCredentials): Promise<XtreamAnalyzeResponse> => {
    return apiClient.post<XtreamAnalyzeResponse>('/import/xtream/analyze', credentials)
  },

  /**
   * Import from an Xtream Codes server
   */
  importXtream: async (data: XtreamImportRequest): Promise<ImportResponse> => {
    return apiClient.post<ImportResponse>('/import/xtream', data)
  },

  // ============================================
  // IPTV Source Management API
  // ============================================

  /**
   * List all IPTV sources for the current user
   */
  listSources: async (): Promise<IPTVSourceListResponse> => {
    return apiClient.get<IPTVSourceListResponse>('/import/sources')
  },

  /**
   * Get a specific IPTV source
   */
  getSource: async (sourceId: number): Promise<IPTVSource> => {
    return apiClient.get<IPTVSource>(`/import/sources/${sourceId}`)
  },

  /**
   * Update an IPTV source
   */
  updateSource: async (sourceId: number, data: IPTVSourceUpdateRequest): Promise<IPTVSource> => {
    return apiClient.patch<IPTVSource>(`/import/sources/${sourceId}`, data)
  },

  /**
   * Delete an IPTV source
   */
  deleteSource: async (sourceId: number): Promise<{ status: string; message: string }> => {
    return apiClient.delete<{ status: string; message: string }>(`/import/sources/${sourceId}`)
  },

  /**
   * Sync (re-import) an IPTV source
   */
  syncSource: async (sourceId: number): Promise<SyncResponse> => {
    return apiClient.post<SyncResponse>(`/import/sources/${sourceId}/sync`, {})
  },

  // ============================================
  // Import Job Status API
  // ============================================

  /**
   * Get the status of a background import job
   */
  getImportJobStatus: async (jobId: string): Promise<ImportJobStatus> => {
    return apiClient.get<ImportJobStatus>(`/import/job/${jobId}`)
  },

  // ============================================
  // IPTV Settings API
  // ============================================

  /**
   * Get IPTV import feature settings
   */
  getIPTVImportSettings: async (): Promise<IPTVImportSettings> => {
    return apiClient.get<IPTVImportSettings>('/import/iptv-settings')
  },

  // ============================================
  // YouTube Import API
  // ============================================

  /**
   * Analyze a YouTube URL
   */
  analyzeYouTube: async (data: YouTubeAnalyzeRequest): Promise<YouTubeAnalyzeResponse> => {
    return apiClient.post<YouTubeAnalyzeResponse>('/import/youtube/analyze', data)
  },

  /**
   * Import a YouTube video as a stream
   */
  importYouTube: async (data: YouTubeImportRequest): Promise<ImportResponse> => {
    const formData = new FormData()
    formData.append('youtube_url', data.youtube_url)
    formData.append('meta_type', data.meta_type)
    if (data.meta_id) formData.append('meta_id', data.meta_id)
    if (data.title) formData.append('title', data.title)
    if (data.poster) formData.append('poster', data.poster)
    if (data.background) formData.append('background', data.background)
    if (data.resolution) formData.append('resolution', data.resolution)
    if (data.quality) formData.append('quality', data.quality)
    if (data.codec) formData.append('codec', data.codec)
    if (data.languages) formData.append('languages', data.languages)
    if (data.catalogs) formData.append('catalogs', data.catalogs)
    if (data.force_import) formData.append('force_import', 'true')
    if (data.is_anonymous) formData.append('is_anonymous', 'true')
    return apiClient.upload<ImportResponse>('/import/youtube', formData)
  },

  // ============================================
  // HTTP Import API
  // ============================================

  /**
   * Get list of supported MediaFlow extractors
   */
  getMediaFlowExtractors: async (): Promise<{ extractors: string[] }> => {
    return apiClient.get<{ extractors: string[] }>('/import/http/extractors')
  },

  /**
   * Analyze an HTTP URL
   */
  analyzeHTTP: async (data: HTTPAnalyzeRequest): Promise<HTTPAnalyzeResponse> => {
    return apiClient.post<HTTPAnalyzeResponse>('/import/http/analyze', data)
  },

  /**
   * Import an HTTP URL as a stream
   */
  importHTTP: async (data: HTTPImportRequest): Promise<ImportResponse> => {
    const formData = new FormData()
    formData.append('url', data.url)
    formData.append('meta_type', data.meta_type)
    if (data.meta_id) formData.append('meta_id', data.meta_id)
    if (data.title) formData.append('title', data.title)
    if (data.extractor_name) formData.append('extractor_name', data.extractor_name)
    if (data.request_headers) formData.append('request_headers', JSON.stringify(data.request_headers))
    if (data.response_headers) formData.append('response_headers', JSON.stringify(data.response_headers))
    if (data.drm_key_id) formData.append('drm_key_id', data.drm_key_id)
    if (data.drm_key) formData.append('drm_key', data.drm_key)
    if (data.resolution) formData.append('resolution', data.resolution)
    if (data.quality) formData.append('quality', data.quality)
    if (data.codec) formData.append('codec', data.codec)
    if (data.languages) formData.append('languages', data.languages)
    if (data.force_import) formData.append('force_import', 'true')
    if (data.is_anonymous) formData.append('is_anonymous', 'true')
    return apiClient.upload<ImportResponse>('/import/http', formData)
  },

  // ============================================
  // AceStream Import API
  // ============================================

  /**
   * Analyze AceStream content
   */
  analyzeAceStream: async (data: AceStreamAnalyzeRequest): Promise<AceStreamAnalyzeResponse> => {
    return apiClient.post<AceStreamAnalyzeResponse>('/import/acestream/analyze', data)
  },

  /**
   * Import AceStream content as a stream
   */
  importAceStream: async (data: AceStreamImportRequest): Promise<ImportResponse> => {
    const formData = new FormData()
    formData.append('meta_type', data.meta_type)
    formData.append('title', data.title)
    if (data.content_id) formData.append('content_id', data.content_id)
    if (data.info_hash) formData.append('info_hash', data.info_hash)
    if (data.meta_id) formData.append('meta_id', data.meta_id)
    if (data.languages) formData.append('languages', data.languages)
    if (data.resolution) formData.append('resolution', data.resolution)
    if (data.quality) formData.append('quality', data.quality)
    if (data.codec) formData.append('codec', data.codec)
    if (data.poster) formData.append('poster', data.poster)
    if (data.background) formData.append('background', data.background)
    if (data.logo) formData.append('logo', data.logo)
    if (data.force_import) formData.append('force_import', 'true')
    if (data.is_anonymous) formData.append('is_anonymous', 'true')
    return apiClient.upload<ImportResponse>('/import/acestream', formData)
  },

  // ============================================
  // NZB Import API
  // ============================================

  /**
   * Analyze an NZB file
   */
  analyzeNZBFile: async (file: File, metaType: NZBMetaType): Promise<NZBAnalyzeResponse> => {
    const formData = new FormData()
    formData.append('nzb_file', file)
    formData.append('meta_type', metaType)
    return apiClient.upload<NZBAnalyzeResponse>('/import/nzb/analyze/file', formData)
  },

  /**
   * Analyze an NZB URL
   */
  analyzeNZBUrl: async (url: string, metaType: NZBMetaType): Promise<NZBAnalyzeResponse> => {
    return apiClient.post<NZBAnalyzeResponse>('/import/nzb/analyze/url', {
      nzb_url: url,
      meta_type: metaType,
    })
  },

  /**
   * Import an NZB file as a contribution
   */
  importNZBFile: async (data: NZBImportRequest & { nzb_file: File }): Promise<ImportResponse> => {
    const formData = new FormData()
    formData.append('nzb_file', data.nzb_file)
    formData.append('meta_type', data.meta_type)
    if (data.meta_id) formData.append('meta_id', data.meta_id)
    if (data.title) formData.append('title', data.title)
    if (data.indexer) formData.append('indexer', data.indexer)
    if (data.resolution) formData.append('resolution', data.resolution)
    if (data.quality) formData.append('quality', data.quality)
    if (data.codec) formData.append('codec', data.codec)
    if (data.languages) formData.append('languages', data.languages)
    if (data.force_import) formData.append('force_import', 'true')
    if (data.is_anonymous) formData.append('is_anonymous', 'true')
    if (data.file_data) formData.append('file_data', data.file_data)
    return apiClient.upload<ImportResponse>('/import/nzb', formData)
  },

  /**
   * Import an NZB via URL as a contribution
   */
  importNZBUrl: async (data: NZBURLImportRequest): Promise<ImportResponse> => {
    return apiClient.post<ImportResponse>('/import/nzb/url', data)
  },
}

// ============================================
// IPTV Import Settings Types
// ============================================

export interface IPTVImportSettings {
  enabled: boolean
  allow_public_sharing: boolean
}
