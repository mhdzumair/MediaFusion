import { apiClient } from './client'

// Types
export interface StreamingProviderInfo {
  service: string
  name?: string
  enabled: boolean
  priority: number
  has_credentials: boolean
}

export interface StreamingProvidersSummary {
  providers: StreamingProviderInfo[]
  has_debrid: boolean
  primary_service: string | null
}

// Deprecated - for backward compatibility
export interface StreamingProviderSummary {
  service: string | null
  is_configured: boolean
}

export interface Profile {
  id: number
  user_id: number
  name: string
  config: Record<string, unknown>
  is_default: boolean
  created_at: string
  streaming_providers: StreamingProvidersSummary
  streaming_provider: StreamingProviderSummary | null // Deprecated
  catalogs_enabled: number
}

export interface ProfileCreateRequest {
  name: string
  config?: Record<string, unknown>
  is_default?: boolean
}

export interface ProfileUpdateRequest {
  name?: string
  config?: Record<string, unknown>
  is_default?: boolean
}

export interface ManifestUrlResponse {
  profile_id: number
  profile_uuid: string
  profile_name: string
  manifest_url: string
  stremio_install_url: string
}

export interface SetDefaultResponse {
  success: boolean
  profile_id: number
}

export interface RpdbApiKeyResponse {
  rpdb_api_key: string | null
}

// Streaming provider types
export type StreamingService =
  | 'realdebrid'
  | 'seedr'
  | 'debridlink'
  | 'alldebrid'
  | 'offcloud'
  | 'pikpak'
  | 'torbox'
  | 'premiumize'
  | 'qbittorrent'
  | 'stremthru'
  | 'easydebrid'
  | 'debrider'

export interface StreamingProviderConfig {
  sv: StreamingService
  tk?: string // token
  em?: string // email
  pw?: string // password
  u?: string // url
  ewc?: boolean // enable_watchlist_catalogs
  dvb?: boolean // download_via_browser
  oscs?: boolean // only_show_cached_streams
  stsn?: string // stremthru_store_name
}

export interface RPDBConfig {
  ak: string // api_key
}

export interface MediaFlowConfig {
  pu?: string // proxy_url
  ap?: string // api_password
  pip?: string // public_ip
  pls?: boolean // proxy_live_streams
  pds?: boolean // proxy_debrid_streams
}

// Per-catalog configuration with sorting
export interface CatalogConfig {
  ci: string // catalog_id
  en?: boolean // enabled (default true)
  s?: 'latest' | 'popular' | 'rating' | 'year' | 'title' | 'release_date' | null // sort
  o?: 'asc' | 'desc' // order (default 'desc')
}

// Indexer configuration types
export interface IndexerInstanceConfig {
  en?: boolean // enabled
  u?: string // url
  ak?: string // api_key
  ug?: boolean // use_global
}

export interface TorznabEndpointConfig {
  i: string // id
  n: string // name
  u: string // url
  h?: Record<string, string> | null // headers
  en?: boolean // enabled
  c?: number[] // categories
  p?: number // priority
}

export interface IndexerConfig {
  pr?: IndexerInstanceConfig | null // prowlarr
  jk?: IndexerInstanceConfig | null // jackett
  tz?: TorznabEndpointConfig[] // torznab_endpoints
}

export interface ProfileConfig {
  sp?: StreamingProviderConfig | null // streaming_provider
  cc?: CatalogConfig[] // catalog_configs (new: per-catalog configuration)
  sc?: string[] // selected_catalogs (deprecated: use cc instead)
  sr?: (string | null)[] // selected_resolutions
  ec?: boolean // enable_catalogs
  eim?: boolean // enable_imdb_metadata
  ms?: number | string // max_size (bytes or 'inf')
  mns?: number // min_size (bytes, 0 = no minimum)
  mspr?: number // max_streams_per_resolution
  nf?: string[] // nudity_filter
  cf?: string[] // certification_filter
  ap?: string // api_password
  ls?: (string | null)[] // language_sorting
  qf?: string[] // quality_filter
  mfc?: MediaFlowConfig | null // mediaflow_config
  rpc?: RPDBConfig | null // rpdb_config
  lss?: boolean // live_search_streams
  ic?: IndexerConfig | null // indexer_config
  eus?: boolean // enable_usenet_streams
  puot?: boolean // prefer_usenet_over_torrent
  ets?: boolean // enable_telegram_streams
  eas?: boolean // enable_acestream_streams
  // Stream display settings
  mxs?: number // max_streams (total cap)
  stg?: 'mixed' | 'separate' // stream_type_grouping
  sto?: string[] // stream_type_order
  pg?: 'mixed' | 'separate' // provider_grouping
  // Stream name filter
  snfm?: 'disabled' | 'include' | 'exclude' // stream_name_filter_mode
  snfp?: string[] // stream_name_filter_patterns
  snfr?: boolean // stream_name_filter_use_regex
}

// API functions
export const profilesApi = {
  // List all profiles
  list: async (): Promise<Profile[]> => {
    return apiClient.get<Profile[]>('/profiles')
  },

  // Get single profile
  get: async (profileId: number): Promise<Profile> => {
    return apiClient.get<Profile>(`/profiles/${profileId}`)
  },

  // Create profile
  create: async (data: ProfileCreateRequest): Promise<Profile> => {
    return apiClient.post<Profile>('/profiles', data)
  },

  // Update profile
  update: async (profileId: number, data: ProfileUpdateRequest): Promise<Profile> => {
    return apiClient.put<Profile>(`/profiles/${profileId}`, data)
  },

  // Delete profile
  delete: async (profileId: number): Promise<void> => {
    await apiClient.delete(`/profiles/${profileId}`)
  },

  // Set as default
  setDefault: async (profileId: number): Promise<SetDefaultResponse> => {
    return apiClient.post<SetDefaultResponse>(`/profiles/${profileId}/set-default`)
  },

  // Get manifest URL
  getManifestUrl: async (profileId: number): Promise<ManifestUrlResponse> => {
    return apiClient.get<ManifestUrlResponse>(`/profiles/${profileId}/manifest-url`)
  },

  // Get RPDB API key from default profile (for poster display)
  getRpdbApiKey: async (): Promise<RpdbApiKeyResponse> => {
    return apiClient.get<RpdbApiKeyResponse>('/profiles/rpdb-key')
  },
}
