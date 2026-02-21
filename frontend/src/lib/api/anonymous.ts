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
  pr?: number // priority
  en?: boolean // enabled
}

export interface MediaFlowConfigType {
  pu?: string // proxy_url
  ap?: string // api_password
  pip?: string // public_ip
  pls?: boolean // proxy_live_streams
  pds?: boolean // proxy_debrid_streams
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

export interface SortingOptionType {
  k: string
  d: 'asc' | 'desc'
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
  ic?: IndexerConfigType | null // indexer_config
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
  live_search_streams?: boolean
  indexer_config?: IndexerConfigPayload | null
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

export interface CatalogConfigPayload {
  catalog_id: string
  enabled?: boolean
  sort?: string | null
  order?: 'asc' | 'desc'
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
  proxy_debrid_streams?: boolean
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

/**
 * Convert ProfileConfig (frontend format) to UserDataPayload (backend format)
 */
export function profileConfigToUserData(config: ProfileConfig, apiPassword?: string | null): UserDataPayload {
  const userData: UserDataPayload = {}

  // Convert streaming providers (sps -> streaming_providers)
  if (config.sps && config.sps.length > 0) {
    userData.streaming_providers = config.sps.map((sp) => ({
      name: sp.n || 'default',
      service: sp.sv,
      stremthru_store_name: sp.stsn,
      url: sp.u,
      token: sp.tk,
      email: sp.em,
      password: sp.pw,
      enable_watchlist_catalogs: sp.ewc ?? true,
      qbittorrent_config: sp.qbc
        ? {
            qbittorrent_url: sp.qbc.qur,
            qbittorrent_username: sp.qbc.qus,
            qbittorrent_password: sp.qbc.qpw,
            seeding_time_limit: sp.qbc.stl,
            seeding_ratio_limit: sp.qbc.srl,
            play_video_after: sp.qbc.pva,
            category: sp.qbc.cat,
            webdav_url: sp.qbc.wur,
            webdav_username: sp.qbc.wus,
            webdav_password: sp.qbc.wpw,
            webdav_downloads_path: sp.qbc.wdp,
          }
        : null,
      only_show_cached_streams: sp.oscs ?? false,
      priority: sp.pr ?? 0,
      enabled: sp.en ?? true,
    }))

    // Also set legacy single provider for backward compatibility
    const primaryProvider = config.sps.find((sp) => sp.en !== false)
    if (primaryProvider) {
      userData.streaming_provider = {
        name: primaryProvider.n || 'default',
        service: primaryProvider.sv,
        stremthru_store_name: primaryProvider.stsn,
        url: primaryProvider.u,
        token: primaryProvider.tk,
        email: primaryProvider.em,
        password: primaryProvider.pw,
        enable_watchlist_catalogs: primaryProvider.ewc ?? true,
        qbittorrent_config: primaryProvider.qbc
          ? {
              qbittorrent_url: primaryProvider.qbc.qur,
              qbittorrent_username: primaryProvider.qbc.qus,
              qbittorrent_password: primaryProvider.qbc.qpw,
              seeding_time_limit: primaryProvider.qbc.stl,
              seeding_ratio_limit: primaryProvider.qbc.srl,
              play_video_after: primaryProvider.qbc.pva,
              category: primaryProvider.qbc.cat,
              webdav_url: primaryProvider.qbc.wur,
              webdav_username: primaryProvider.qbc.wus,
              webdav_password: primaryProvider.qbc.wpw,
              webdav_downloads_path: primaryProvider.qbc.wdp,
            }
          : null,
        only_show_cached_streams: primaryProvider.oscs ?? false,
        priority: primaryProvider.pr ?? 0,
        enabled: primaryProvider.en ?? true,
      }
    }
  }

  // Convert catalog configs (cc -> catalog_configs)
  if (config.cc && config.cc.length > 0) {
    userData.catalog_configs = config.cc.map((c) => ({
      catalog_id: c.ci,
      enabled: c.en ?? true,
      sort: c.s,
      order: c.o,
    }))
  }

  // Simple field mappings
  // Normalize legacy empty-string "Unknown" resolution to null (backend-compatible).
  if (config.sr) {
    userData.selected_resolutions = config.sr.map((r) => (typeof r === 'string' && r.trim() === '' ? null : r))
  }
  if (config.ec !== undefined) userData.enable_catalogs = config.ec
  if (config.eim !== undefined) userData.enable_imdb_metadata = config.eim
  if (config.ms !== undefined) userData.max_size = config.ms
  if (config.mns !== undefined) userData.min_size = config.mns
  if (config.mspr !== undefined) userData.max_streams_per_resolution = config.mspr
  if (config.nf) userData.nudity_filter = config.nf
  if (config.cf) userData.certification_filter = config.cf
  if (config.ls) userData.language_sorting = config.ls
  if (config.qf) userData.quality_filter = config.qf
  if (config.lss !== undefined) userData.live_search_streams = config.lss

  // Stream display settings
  if (config.mxs !== undefined) userData.max_streams = config.mxs
  if (config.stg !== undefined) userData.stream_type_grouping = config.stg
  if (config.sto) userData.stream_type_order = config.sto
  if (config.pg !== undefined) userData.provider_grouping = config.pg

  // Stream name filter settings
  if (config.snfm !== undefined) userData.stream_name_filter_mode = config.snfm
  if (config.snfp) userData.stream_name_filter_patterns = config.snfp
  if (config.snfr !== undefined) userData.stream_name_filter_use_regex = config.snfr

  // Convert torrent sorting priority (tsp)
  if (config.tsp && config.tsp.length > 0) {
    userData.torrent_sorting_priority = config.tsp.map((s) => ({
      key: s.k,
      direction: s.d,
    }))
  }

  // Convert MediaFlow config (mfc -> mediaflow_config)
  if (config.mfc) {
    userData.mediaflow_config = {
      proxy_url: config.mfc.pu,
      api_password: config.mfc.ap,
      public_ip: config.mfc.pip,
      proxy_live_streams: config.mfc.pls,
      proxy_debrid_streams: config.mfc.pds,
    }
  }

  // Convert RPDB config (rpc -> rpdb_config)
  if (config.rpc) {
    userData.rpdb_config = {
      api_key: config.rpc.ak,
    }
  }

  // Convert MDBList config (mdb -> mdblist_config)
  if (config.mdb) {
    userData.mdblist_config = {
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
  }

  // Convert indexer config (ic -> indexer_config)
  if (config.ic) {
    userData.indexer_config = {
      prowlarr: config.ic.pr
        ? {
            enabled: config.ic.pr.en,
            url: config.ic.pr.u,
            api_key: config.ic.pr.ak,
            use_global: config.ic.pr.ug,
          }
        : null,
      jackett: config.ic.jk
        ? {
            enabled: config.ic.jk.en,
            url: config.ic.jk.u,
            api_key: config.ic.jk.ak,
            use_global: config.ic.jk.ug,
          }
        : null,
      torznab_endpoints: config.ic.tz?.map((t) => ({
        id: t.i,
        name: t.n,
        url: t.u,
        headers: t.h,
        enabled: t.en,
        categories: t.c,
        priority: t.p,
      })),
    }
  }

  // API password for private instances
  if (apiPassword) {
    userData.api_password = apiPassword
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
 * Generate manifest URLs from encrypted string
 */
export function generateManifestUrls(encryptedStr: string): {
  manifestUrl: string
  stremioInstallUrl: string
} {
  const baseUrl = window.location.origin
  const hostWithoutProtocol = baseUrl.replace('https://', '').replace('http://', '')

  return {
    manifestUrl: `${baseUrl}/${encryptedStr}/manifest.json`,
    stremioInstallUrl: `stremio://${hostWithoutProtocol}/${encryptedStr}/manifest.json`,
  }
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

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }))
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Failed to link Kodi device')
  }

  return response.json()
}

export const anonymousApi = {
  encryptUserData,
  generateManifestUrls,
  associateKodiManifest,
  profileConfigToUserData,
}
