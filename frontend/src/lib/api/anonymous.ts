/**
 * Anonymous Configuration API
 *
 * Handles configuration for users without an account.
 * Uses the /encrypt-user-data endpoint to generate manifest URLs.
 */

import { getStoredApiKey } from './instance'

// Import ProfileConfig type from the configure page types
// This matches the frontend format used in the Configure page
export interface QBittorrentConfigType {
  qur: string // qbittorrent_url
  qus: string // qbittorrent_username
  qpw: string // qbittorrent_password
  stl: number // seeding_time_limit
  srl: number // seeding_ratio_limit
  pva: number // play_video_after
  cat: string // category
  wur: string // webdav_url
  wus: string // webdav_username
  wpw: string // webdav_password
  wdp: string // webdav_downloads_path
}

export interface SABnzbdConfigType {
  u: string // url
  ak: string // api_key
  cat: string // category
  wur?: string // webdav_url
  wus?: string // webdav_username
  wpw?: string // webdav_password
  wdp: string // webdav_downloads_path
}

export interface NZBGetConfigType {
  u: string // url
  un: string // username
  pw: string // password
  cat: string // category
  wur?: string // webdav_url
  wus?: string // webdav_username
  wpw?: string // webdav_password
  wdp: string // webdav_downloads_path
}

export interface NzbDAVConfigType {
  u: string // url
  ak: string // api_key
  cat: string // category
}

export interface EasynewsConfigType {
  un: string // username
  pw: string // password
}

export interface StreamingProviderConfigType {
  n?: string // name
  sv: string // service
  tk?: string // token
  em?: string // email
  pw?: string // password
  u?: string // url
  ewc?: boolean // enable_watchlist_catalogs
  oscs?: boolean // only_show_cached_streams
  stsn?: string // stremthru_store_name
  qbc?: QBittorrentConfigType
  sbc?: SABnzbdConfigType // sabnzbd_config
  ngc?: NZBGetConfigType // nzbget_config
  ndc?: NzbDAVConfigType // nzbdav_config
  enc?: EasynewsConfigType // easynews_config
  pr?: number // priority
  en?: boolean // enabled
  umf?: boolean // use_mediaflow
}

export interface MediaFlowConfigType {
  pu?: string // proxy_url
  ap?: string // api_password
  pip?: string // public_ip
  pls?: boolean // proxy_live_streams
  ewp?: boolean // enable_web_playback
  pds?: boolean // legacy proxy_debrid_streams (backward compatibility)
}

export interface RPDBConfigType {
  ak: string // api_key
}

export interface MDBListItemType {
  i: number // id
  t: string // title
  ct: 'movie' | 'series' // catalog_type
  uf?: boolean // use_filters
  s?: string // sort
  o?: 'asc' | 'desc' // order
}

export interface MDBListConfigType {
  ak: string // api_key
  l?: MDBListItemType[] // lists
}

export interface CatalogConfigType {
  ci: string // catalog_id
  en?: boolean // enabled
  s?: string | null // sort
  o?: 'asc' | 'desc' // order
}

export interface StreamTemplateConfigType {
  t?: string // title template
  d?: string // description template
}

export interface SortingOptionType {
  k: string
  d: 'asc' | 'desc'
}

export interface NewznabIndexerConfigType {
  i: string // id
  n: string // name
  u: string // url
  ak: string // api_key
  en?: boolean // enabled
  p?: number // priority
  mc?: number[] // movie_categories
  tc?: number[] // tv_categories
  uz?: boolean // use_zyclops
  zb?: string[] // zyclops_backbones
}

export interface IndexerInstanceConfigType {
  en?: boolean // enabled
  u?: string // url
  ak?: string // api_key
  ug?: boolean // use_global
}

export interface TorznabEndpointConfigType {
  i: string // id
  n: string // name
  u: string // url
  h?: Record<string, string> | null // headers
  en?: boolean // enabled
  c?: number[] // categories
  p?: number // priority
}

export interface IndexerConfigType {
  pr?: IndexerInstanceConfigType | null // prowlarr
  jk?: IndexerInstanceConfigType | null // jackett
  tz?: TorznabEndpointConfigType[] // torznab_endpoints
  nz?: NewznabIndexerConfigType[] // newznab_indexers
}

export interface TelegramChannelConfigType {
  i: string // id
  n: string // name
  u?: string // username
  cid?: string // chat_id
  en?: boolean // enabled
  p?: number // priority
}

export interface TelegramConfigType {
  en?: boolean // enabled
  ch?: TelegramChannelConfigType[] // channels
  ugc?: boolean // use_global_channels
}

export interface ProfileConfig {
  sp?: StreamingProviderConfigType | null // streaming_provider (legacy)
  sps?: StreamingProviderConfigType[] // streaming_providers (multi-provider)
  cc?: CatalogConfigType[] // catalog_configs
  sc?: string[] // selected_catalogs (deprecated)
  sr?: (string | null)[] // selected_resolutions
  ec?: boolean // enable_catalogs
  eim?: boolean // enable_imdb_metadata
  ms?: number | string // max_size (bytes or 'inf')
  mns?: number // min_size (bytes, 0 = no minimum)
  mspr?: number // max_streams_per_resolution
  tsp?: SortingOptionType[] // torrent_sorting_priority
  nf?: string[] // nudity_filter
  cf?: string[] // certification_filter
  ap?: string // api_password
  ls?: (string | null)[] // language_sorting
  qf?: string[] // quality_filter
  mfc?: MediaFlowConfigType | null // mediaflow_config
  rpc?: RPDBConfigType | null // rpdb_config
  lss?: boolean // live_search_streams
  mdb?: MDBListConfigType | null // mdblist_config
  st?: StreamTemplateConfigType | null // stream_template
  ic?: IndexerConfigType | null // indexer_config
  eus?: boolean // enable_usenet_streams
  puot?: boolean // prefer_usenet_over_torrent
  ets?: boolean // enable_telegram_streams
  tgc?: TelegramConfigType | null // telegram_config
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

// Response from encrypt-user-data endpoint
export interface EncryptUserDataResponse {
  status: 'success' | 'error'
  message?: string
  encrypted_str?: string
}

export interface DecryptUserDataResponse {
  status: 'success' | 'error'
  message?: string
  config?: Record<string, unknown>
}

// UserData format expected by the backend (matches db/schemas/config.py)
export interface UserDataPayload {
  // Streaming providers (multi-provider format)
  streaming_providers?: StreamingProviderPayload[]
  // Legacy single provider (for backward compatibility)
  streaming_provider?: StreamingProviderPayload | null
  // Catalog configuration
  catalog_configs?: CatalogConfigPayload[]
  selected_catalogs?: string[]
  selected_resolutions?: (string | null)[]
  enable_catalogs?: boolean
  enable_imdb_metadata?: boolean
  max_size?: number | string
  min_size?: number
  max_streams_per_resolution?: number
  torrent_sorting_priority?: SortingOptionPayload[]
  nudity_filter?: string[]
  certification_filter?: string[]
  language_sorting?: (string | null)[]
  quality_filter?: string[]
  api_password?: string | null
  mediaflow_config?: MediaFlowConfigPayload | null
  rpdb_config?: RPDBConfigPayload | null
  mdblist_config?: MDBListConfigPayload | null
  stream_template?: StreamTemplatePayload | null
  live_search_streams?: boolean
  indexer_config?: IndexerConfigPayload | null
  enable_usenet_streams?: boolean
  prefer_usenet_over_torrent?: boolean
  enable_telegram_streams?: boolean
  telegram_config?: TelegramConfigPayload | null
  enable_acestream_streams?: boolean
  // Stream display settings
  max_streams?: number
  stream_type_grouping?: 'mixed' | 'separate'
  stream_type_order?: string[]
  provider_grouping?: 'mixed' | 'separate'
  // Stream name filter
  stream_name_filter_mode?: 'disabled' | 'include' | 'exclude'
  stream_name_filter_patterns?: string[]
  stream_name_filter_use_regex?: boolean
}

export interface StreamingProviderPayload {
  name?: string
  service: string
  stremthru_store_name?: string | null
  url?: string | null
  token?: string | null
  email?: string | null
  password?: string | null
  enable_watchlist_catalogs?: boolean
  qbittorrent_config?: QBittorrentConfigPayload | null
  only_show_cached_streams?: boolean
  use_mediaflow?: boolean
  sabnzbd_config?: SABnzbdConfigPayload | null
  nzbget_config?: NZBGetConfigPayload | null
  nzbdav_config?: NzbDAVConfigPayload | null
  easynews_config?: EasynewsConfigPayload | null
  priority?: number
  enabled?: boolean
}

export interface QBittorrentConfigPayload {
  qbittorrent_url?: string
  qbittorrent_username?: string
  qbittorrent_password?: string
  seeding_time_limit?: number
  seeding_ratio_limit?: number
  play_video_after?: number
  category?: string
  webdav_url?: string
  webdav_username?: string
  webdav_password?: string
  webdav_downloads_path?: string
}

export interface SABnzbdConfigPayload {
  url?: string
  api_key?: string
  category?: string
  webdav_url?: string
  webdav_username?: string
  webdav_password?: string
  webdav_downloads_path?: string
}

export interface NZBGetConfigPayload {
  url?: string
  username?: string
  password?: string
  category?: string
  webdav_url?: string
  webdav_username?: string
  webdav_password?: string
  webdav_downloads_path?: string
}

export interface NzbDAVConfigPayload {
  url?: string
  api_key?: string
  category?: string
}

export interface EasynewsConfigPayload {
  username?: string
  password?: string
}

export interface CatalogConfigPayload {
  catalog_id: string
  enabled?: boolean
  sort?: string | null
  order?: 'asc' | 'desc'
}

export interface StreamTemplatePayload {
  title?: string
  description?: string
}

export interface SortingOptionPayload {
  key: string
  direction?: 'asc' | 'desc'
}

export interface MediaFlowConfigPayload {
  proxy_url?: string
  api_password?: string
  public_ip?: string
  proxy_live_streams?: boolean
  enable_web_playback?: boolean
}

export interface RPDBConfigPayload {
  api_key: string
}

export interface MDBListConfigPayload {
  api_key?: string
  lists?: MDBListItemPayload[]
}

export interface MDBListItemPayload {
  id: number
  title: string
  catalog_type: 'movie' | 'series'
  media_type?: string[]
  sort?: string
  order?: 'asc' | 'desc'
  use_filters?: boolean
}

export interface IndexerConfigPayload {
  prowlarr?: IndexerInstancePayload | null
  jackett?: IndexerInstancePayload | null
  torznab_endpoints?: TorznabEndpointPayload[]
  newznab_indexers?: NewznabIndexerPayload[]
}

export interface IndexerInstancePayload {
  enabled?: boolean
  url?: string
  api_key?: string
  use_global?: boolean
}

export interface TorznabEndpointPayload {
  id: string
  name: string
  url: string
  headers?: Record<string, string> | null
  enabled?: boolean
  categories?: number[]
  priority?: number
}

export interface NewznabIndexerPayload {
  id: string
  name: string
  url: string
  api_key: string
  enabled?: boolean
  priority?: number
  movie_categories?: number[]
  tv_categories?: number[]
  use_zyclops?: boolean
  zyclops_backbones?: string[]
}

export interface TelegramChannelPayload {
  id: string
  name: string
  username?: string
  chat_id?: string
  enabled?: boolean
  priority?: number
}

export interface TelegramConfigPayload {
  enabled?: boolean
  channels?: TelegramChannelPayload[]
  use_global_channels?: boolean
}

/**
 * Convert ProfileConfig (frontend format) to UserDataPayload (backend format)
 */
function mapQBittorrentConfig(config?: QBittorrentConfigType): QBittorrentConfigPayload | null {
  if (!config) return null
  return {
    qbittorrent_url: config.qur,
    qbittorrent_username: config.qus,
    qbittorrent_password: config.qpw,
    seeding_time_limit: config.stl,
    seeding_ratio_limit: config.srl,
    play_video_after: config.pva,
    category: config.cat,
    webdav_url: config.wur,
    webdav_username: config.wus,
    webdav_password: config.wpw,
    webdav_downloads_path: config.wdp,
  }
}

function mapSABnzbdConfig(config?: SABnzbdConfigType): SABnzbdConfigPayload | null {
  if (!config) return null
  return {
    url: config.u,
    api_key: config.ak,
    category: config.cat,
    webdav_url: config.wur,
    webdav_username: config.wus,
    webdav_password: config.wpw,
    webdav_downloads_path: config.wdp,
  }
}

function mapNZBGetConfig(config?: NZBGetConfigType): NZBGetConfigPayload | null {
  if (!config) return null
  return {
    url: config.u,
    username: config.un,
    password: config.pw,
    category: config.cat,
    webdav_url: config.wur,
    webdav_username: config.wus,
    webdav_password: config.wpw,
    webdav_downloads_path: config.wdp,
  }
}

function mapNzbDAVConfig(config?: NzbDAVConfigType): NzbDAVConfigPayload | null {
  if (!config) return null
  return {
    url: config.u,
    api_key: config.ak,
    category: config.cat,
  }
}

function mapEasynewsConfig(config?: EasynewsConfigType): EasynewsConfigPayload | null {
  if (!config) return null
  return {
    username: config.un,
    password: config.pw,
  }
}

function mapStreamingProvider(provider: StreamingProviderConfigType): StreamingProviderPayload {
  return {
    name: provider.n || 'default',
    service: provider.sv,
    stremthru_store_name: provider.stsn,
    url: provider.u,
    token: provider.tk,
    email: provider.em,
    password: provider.pw,
    enable_watchlist_catalogs: provider.ewc ?? true,
    qbittorrent_config: mapQBittorrentConfig(provider.qbc),
    only_show_cached_streams: provider.oscs ?? false,
    use_mediaflow: provider.umf ?? true,
    sabnzbd_config: mapSABnzbdConfig(provider.sbc),
    nzbget_config: mapNZBGetConfig(provider.ngc),
    nzbdav_config: mapNzbDAVConfig(provider.ndc),
    easynews_config: mapEasynewsConfig(provider.enc),
    priority: provider.pr ?? 0,
    enabled: provider.en ?? true,
  }
}

function mapIndexerInstance(config?: IndexerInstanceConfigType | null): IndexerInstancePayload | null {
  if (!config) return null
  return {
    enabled: config.en,
    url: config.u,
    api_key: config.ak,
    use_global: config.ug,
  }
}

function mapTelegramConfig(config?: TelegramConfigType | null): TelegramConfigPayload | null {
  if (!config) return null
  return {
    enabled: config.en,
    channels: config.ch?.map((channel) => ({
      id: channel.i,
      name: channel.n,
      username: channel.u,
      chat_id: channel.cid,
      enabled: channel.en,
      priority: channel.p,
    })),
    use_global_channels: config.ugc,
  }
}

export function profileConfigToUserData(config: ProfileConfig, apiPassword?: string | null): UserDataPayload {
  const userData: UserDataPayload = {}

  // Convert streaming providers (sps -> streaming_providers)
  if (config.sps !== undefined) {
    userData.streaming_providers = (config.sps ?? []).map(mapStreamingProvider)

    // Also set legacy single provider for backward compatibility.
    const primaryProvider = (config.sps ?? []).find((provider) => provider.en !== false)
    if (primaryProvider) {
      userData.streaming_provider = mapStreamingProvider(primaryProvider)
    } else if (config.sp !== undefined) {
      userData.streaming_provider = config.sp ? mapStreamingProvider(config.sp) : null
    }
  } else if (config.sp !== undefined) {
    userData.streaming_provider = config.sp ? mapStreamingProvider(config.sp) : null
  }

  // Convert catalog configs (cc -> catalog_configs)
  if (config.cc !== undefined) {
    userData.catalog_configs = (config.cc ?? []).map((c) => ({
      catalog_id: c.ci,
      enabled: c.en ?? true,
      sort: c.s,
      order: c.o,
    }))
  }

  if (config.sc !== undefined) userData.selected_catalogs = config.sc

  // Simple field mappings
  // Normalize legacy empty-string "Unknown" resolution to null (backend-compatible).
  if (config.sr !== undefined) {
    userData.selected_resolutions = config.sr.map((r) => (typeof r === 'string' && r.trim() === '' ? null : r))
  }
  if (config.ec !== undefined) userData.enable_catalogs = config.ec
  if (config.eim !== undefined) userData.enable_imdb_metadata = config.eim
  if (config.ms !== undefined) userData.max_size = config.ms
  if (config.mns !== undefined) userData.min_size = config.mns
  if (config.mspr !== undefined) userData.max_streams_per_resolution = config.mspr
  if (config.nf !== undefined) userData.nudity_filter = config.nf
  if (config.cf !== undefined) userData.certification_filter = config.cf
  if (config.ls !== undefined) userData.language_sorting = config.ls
  if (config.qf !== undefined) userData.quality_filter = config.qf
  if (config.lss !== undefined) userData.live_search_streams = config.lss
  if (config.eus !== undefined) userData.enable_usenet_streams = config.eus
  if (config.puot !== undefined) userData.prefer_usenet_over_torrent = config.puot
  if (config.ets !== undefined) userData.enable_telegram_streams = config.ets
  if (config.eas !== undefined) userData.enable_acestream_streams = config.eas

  // Stream display settings
  if (config.mxs !== undefined) userData.max_streams = config.mxs
  if (config.stg !== undefined) userData.stream_type_grouping = config.stg
  if (config.sto !== undefined) userData.stream_type_order = config.sto
  if (config.pg !== undefined) userData.provider_grouping = config.pg

  // Stream name filter settings
  if (config.snfm !== undefined) userData.stream_name_filter_mode = config.snfm
  if (config.snfp !== undefined) userData.stream_name_filter_patterns = config.snfp
  if (config.snfr !== undefined) userData.stream_name_filter_use_regex = config.snfr

  // Convert torrent sorting priority (tsp)
  if (config.tsp !== undefined) {
    userData.torrent_sorting_priority = (config.tsp ?? []).map((s) => ({
      key: s.k,
      direction: s.d,
    }))
  }

  // Convert MediaFlow config (mfc -> mediaflow_config)
  if (config.mfc !== undefined) {
    userData.mediaflow_config = config.mfc
      ? {
          proxy_url: config.mfc.pu,
          api_password: config.mfc.ap,
          public_ip: config.mfc.pip,
          proxy_live_streams: config.mfc.pls,
          // Support both modern `ewp` and legacy `pds` frontend keys.
          enable_web_playback: config.mfc.ewp ?? config.mfc.pds,
        }
      : null
  }

  // Convert RPDB config (rpc -> rpdb_config)
  if (config.rpc !== undefined) {
    userData.rpdb_config = config.rpc
      ? {
          api_key: config.rpc.ak,
        }
      : null
  }

  // Convert MDBList config (mdb -> mdblist_config)
  if (config.mdb !== undefined) {
    userData.mdblist_config = config.mdb
      ? {
          api_key: config.mdb.ak,
          lists: config.mdb.l?.map((l) => ({
            id: l.i,
            title: l.t,
            catalog_type: l.ct,
            sort: l.s,
            order: l.o,
            use_filters: l.uf,
          })),
        }
      : null
  }

  // Convert stream template config (st -> stream_template)
  if (config.st !== undefined) {
    userData.stream_template = config.st
      ? {
          title: config.st.t,
          description: config.st.d,
        }
      : null
  }

  // Convert indexer config (ic -> indexer_config)
  if (config.ic !== undefined) {
    userData.indexer_config = config.ic
      ? {
          prowlarr: mapIndexerInstance(config.ic.pr),
          jackett: mapIndexerInstance(config.ic.jk),
          torznab_endpoints: config.ic.tz?.map((t) => ({
            id: t.i,
            name: t.n,
            url: t.u,
            headers: t.h,
            enabled: t.en,
            categories: t.c,
            priority: t.p,
          })),
          newznab_indexers: config.ic.nz?.map((indexer) => ({
            id: indexer.i,
            name: indexer.n,
            url: indexer.u,
            api_key: indexer.ak,
            enabled: indexer.en,
            priority: indexer.p,
            movie_categories: indexer.mc,
            tv_categories: indexer.tc,
            use_zyclops: indexer.uz,
            zyclops_backbones: indexer.zb,
          })),
        }
      : null
  }

  // Convert Telegram config (tgc -> telegram_config)
  if (config.tgc !== undefined) {
    userData.telegram_config = mapTelegramConfig(config.tgc)
  }

  // API password for private instances
  if (apiPassword) {
    userData.api_password = apiPassword
  } else if (config.ap !== undefined) {
    userData.api_password = config.ap || null
  }

  return userData
}

/**
 * Encrypt user configuration data (anonymous mode)
 * Returns the encrypted string that can be used in manifest URLs
 */
export async function encryptUserData(
  config: ProfileConfig,
  existingSecretStr?: string,
): Promise<EncryptUserDataResponse> {
  // Get API key from storage if on private instance
  const apiKey = getStoredApiKey()

  // Convert ProfileConfig to UserData format
  const userData = profileConfigToUserData(config, apiKey)

  const url = existingSecretStr ? `/encrypt-user-data/${existingSecretStr}` : '/encrypt-user-data'

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  // Add API key header if available
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(userData),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Request failed' }))
    // Handle FastAPI validation errors (detail is an array of error objects)
    let message = 'Failed to encrypt configuration'
    if (Array.isArray(error.detail)) {
      message = error.detail.map((e: { msg?: string; loc?: string[] }) => e.msg || 'Validation error').join(', ')
    } else if (typeof error.detail === 'string') {
      message = error.detail
    } else if (error.message) {
      message = error.message
    }
    return {
      status: 'error',
      message,
    }
  }

  return response.json()
}

/**
 * Decrypt and load existing user configuration (anonymous update flow)
 */
export async function decryptUserData(secretStr: string): Promise<DecryptUserDataResponse> {
  const headers: Record<string, string> = {}

  const apiKey = getStoredApiKey()
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`/decrypt-user-data/${encodeURIComponent(secretStr)}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }))
    const message =
      (typeof error.detail === 'string' && error.detail) ||
      (typeof error.message === 'string' && error.message) ||
      'Failed to load existing configuration'
    return {
      status: 'error',
      message,
    }
  }

  const data = await response.json().catch(() => null)
  if (!data || data.status === 'error') {
    return {
      status: 'error',
      message: (data && typeof data.message === 'string' && data.message) || 'Failed to load existing configuration',
    }
  }

  if (!data.config || typeof data.config !== 'object') {
    return {
      status: 'error',
      message: 'Invalid configuration payload',
    }
  }

  return {
    status: 'success',
    config: data.config as Record<string, unknown>,
  }
}

/**
 * Generate manifest URLs from encrypted string
 */
export function generateManifestUrls(
  encryptedStr: string,
  hostUrl?: string,
): {
  manifestUrl: string
  stremioInstallUrl: string
} {
  const baseUrl = (hostUrl?.trim() || window.location.origin).replace(/\/+$/, '')
  const hostWithoutProtocol = baseUrl.replace(/^https?:\/\//, '')

  return {
    manifestUrl: `${baseUrl}/${encryptedStr}/manifest.json`,
    stremioInstallUrl: `stremio://${hostWithoutProtocol}/${encryptedStr}/manifest.json`,
  }
}

function mapKodiLinkError(detail: string | null, statusCode?: number): string {
  const normalizedDetail = (detail || '').toLowerCase()

  if (statusCode === 429 || normalizedDetail.includes('rate limit')) {
    return 'Too many requests. Please wait a few seconds and try again.'
  }

  if (normalizedDetail.includes('invalid setup code') || statusCode === 404) {
    return 'Invalid or expired Kodi setup code. Generate a new code in Kodi and try again within 5 minutes.'
  }

  if (
    normalizedDetail.includes('invalid or missing api key') ||
    normalizedDetail.includes('invalid api password') ||
    normalizedDetail.includes('authentication required')
  ) {
    return 'This private instance requires a valid API key. Save the correct API key first, then link Kodi again.'
  }

  if (normalizedDetail.includes('validation error')) {
    return 'Invalid setup code format. Use the 6-character code shown in Kodi.'
  }

  return detail || 'Failed to link Kodi device'
}

/**
 * Associate a Kodi setup code with a manifest URL.
 * Called from the web UI after the user enters the 6-digit code from Kodi.
 *
 * Sends the X-API-Key header (from localStorage) for private instance auth.
 */
export async function associateKodiManifest(code: string, manifestUrl: string): Promise<{ status: string }> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  // Add API key for private instance authentication
  const apiKey = getStoredApiKey()
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch('/api/v1/kodi/associate-manifest', {
    method: 'POST',
    headers,
    body: JSON.stringify({ code, manifest_url: manifestUrl }),
  })

  const data = await response.json().catch(() => null)

  // MediaFusion API may wrap errors as HTTP 200 with { error: true, detail, status_code }.
  if (!response.ok || data?.error === true) {
    const detail = typeof data?.detail === 'string' ? data.detail : null
    const statusCode =
      typeof data?.status_code === 'number' ? data.status_code : response.ok ? undefined : response.status
    throw new Error(mapKodiLinkError(detail, statusCode))
  }

  return data
}

export const anonymousApi = {
  encryptUserData,
  decryptUserData,
  generateManifestUrls,
  associateKodiManifest,
  profileConfigToUserData,
}
