// Types for profile configuration

export interface SortingOption {
  k: string
  d: 'asc' | 'desc'
}

export interface QBittorrentConfig {
  qur: string  // qbittorrent_url
  qus: string  // qbittorrent_username
  qpw: string  // qbittorrent_password
  stl: number  // seeding_time_limit
  srl: number  // seeding_ratio_limit
  pva: number  // play_video_after
  cat: string  // category
  wur: string  // webdav_url
  wus: string  // webdav_username
  wpw: string  // webdav_password
  wdp: string  // webdav_downloads_path
}

// Usenet provider configurations
export interface SABnzbdConfig {
  u: string    // url
  ak: string   // api_key
  cat: string  // category
  wur?: string // webdav_url
  wus?: string // webdav_username
  wpw?: string // webdav_password
  wdp: string  // webdav_downloads_path
}

export interface NZBGetConfig {
  u: string    // url
  un: string   // username
  pw: string   // password
  cat: string  // category
  wur?: string // webdav_url
  wus?: string // webdav_username
  wpw?: string // webdav_password
  wdp: string  // webdav_downloads_path
}

export interface EasynewsConfig {
  un: string   // username
  pw: string   // password
}

// Newznab indexer configuration
export interface NewznabIndexerConfig {
  i: string    // id
  n: string    // name
  u: string    // url
  ak: string   // api_key
  en?: boolean // enabled
  p?: number   // priority
  mc?: number[] // movie_categories
  tc?: number[] // tv_categories
}

export interface StreamingProviderConfigType {
  n?: string   // name (unique identifier for multi-provider)
  sv: string
  tk?: string  // token
  em?: string  // email
  pw?: string  // password
  u?: string   // url
  ewc?: boolean // enable_watchlist_catalogs
  oscs?: boolean // only_show_cached_streams
  stsn?: string // stremthru_store_name
  qbc?: QBittorrentConfig
  // Usenet provider configs
  sbc?: SABnzbdConfig    // sabnzbd_config
  ngc?: NZBGetConfig     // nzbget_config
  enc?: EasynewsConfig   // easynews_config
  // Multi-provider fields
  pr?: number   // priority (0 = highest)
  en?: boolean  // enabled
  umf?: boolean // use_mediaflow - per provider MediaFlow toggle (default: true)
}

export interface MediaFlowConfig {
  pu?: string  // proxy_url
  ap?: string  // api_password
  pip?: string // public_ip
  pls?: boolean // proxy_live_streams
  ewp?: boolean // enable_web_playback - required for playing streams in browser
}

export interface RPDBConfig {
  ak: string // api_key
}

export interface MDBListItem {
  i: number    // id
  t: string    // title
  ct: 'movie' | 'series' // catalog_type
  uf?: boolean // use_filters
  s?: string   // sort
  o?: 'asc' | 'desc' // order
}

export interface MDBListConfig {
  ak: string           // api_key
  l?: MDBListItem[]    // lists
}

export interface CatalogConfig {
  ci: string           // catalog_id
  en?: boolean         // enabled (default: true)
  s?: 'latest' | 'popular' | 'rating' | 'year' | 'title' | 'release_date' | null  // sort
  o?: 'asc' | 'desc'   // order (default: 'desc')
}

export interface StreamTemplateConfig {
  t?: string   // title template
  d?: string   // description template
}

// Indexer configuration types
export interface IndexerInstanceConfig {
  en?: boolean   // enabled
  u?: string     // url
  ak?: string    // api_key
  ug?: boolean   // use_global
}

export interface TorznabEndpointConfig {
  i: string      // id
  n: string      // name
  u: string      // url
  h?: Record<string, string> | null  // headers
  en?: boolean   // enabled
  c?: number[]   // categories
  p?: number     // priority
}

export interface IndexerConfig {
  pr?: IndexerInstanceConfig | null    // prowlarr
  jk?: IndexerInstanceConfig | null    // jackett
  tz?: TorznabEndpointConfig[]         // torznab_endpoints
  nz?: NewznabIndexerConfig[]          // newznab_indexers
}

export interface ProfileConfig {
  sp?: StreamingProviderConfigType      // streaming_provider (legacy single provider)
  sps?: StreamingProviderConfigType[]   // streaming_providers (multi-provider)
  cc?: CatalogConfig[]               // catalog_configs (new: per-catalog configuration)
  sc?: string[]                      // selected_catalogs (deprecated: use cc instead)
  sr?: (string | null)[]             // selected_resolutions
  ec?: boolean                       // enable_catalogs
  eim?: boolean                      // enable_imdb_metadata
  ms?: number | string               // max_size (bytes or 'inf')
  mns?: number                       // min_size (bytes, 0 = no minimum)
  mspr?: number                      // max_streams_per_resolution
  tsp?: SortingOption[]              // torrent_sorting_priority
  nf?: string[]                      // nudity_filter
  cf?: string[]                      // certification_filter
  ap?: string                        // api_password
  ls?: (string | null)[]             // language_sorting
  qf?: string[]                      // quality_filter
  mfc?: MediaFlowConfig              // mediaflow_config
  rpc?: RPDBConfig                   // rpdb_config
  lss?: boolean                      // live_search_streams
  mdb?: MDBListConfig                // mdblist_config
  st?: StreamTemplateConfig          // stream_template
  ic?: IndexerConfig                 // indexer_config
  // Usenet settings
  eus?: boolean                      // enable_usenet_streams
  puot?: boolean                     // prefer_usenet_over_torrent
  // Telegram settings
  ets?: boolean                      // enable_telegram_streams
  tgc?: TelegramConfig               // telegram_config
  // AceStream settings
  eas?: boolean                      // enable_acestream_streams
  // Stream display settings
  mxs?: number                       // max_streams (total cap)
  stg?: 'mixed' | 'separate'        // stream_type_grouping
  sto?: string[]                     // stream_type_order
  pg?: 'mixed' | 'separate'         // provider_grouping
  // Stream name filter
  snfm?: 'disabled' | 'include' | 'exclude'  // stream_name_filter_mode
  snfp?: string[]                    // stream_name_filter_patterns
  snfr?: boolean                     // stream_name_filter_use_regex
}

// Telegram configuration types
export interface TelegramChannelConfig {
  i: string      // id
  n: string      // name
  u?: string     // username
  cid?: string   // chat_id
  en?: boolean   // enabled
  p?: number     // priority
}

export interface TelegramConfig {
  en?: boolean                    // enabled
  ch?: TelegramChannelConfig[]    // channels
  ugc?: boolean                   // use_global_channels
}

// Props for config section components
export interface ConfigSectionProps {
  config: ProfileConfig
  onChange: (config: ProfileConfig) => void
}

