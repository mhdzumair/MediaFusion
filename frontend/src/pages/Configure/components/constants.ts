// Configuration constants for the full profile settings
import type { ProfileConfig } from './types'

interface StreamingProviderOption {
  value: string
  label: string
  icon: string
  type: string
  disabled_key?: string
  needsToken?: boolean
  needsEmail?: boolean
  needsPassword?: boolean
  needsUrl?: boolean
  needsQBitConfig?: boolean
  needsSABnzbdConfig?: boolean
  needsNZBGetConfig?: boolean
  needsEasynewsConfig?: boolean
  hasOAuth?: boolean
  hasStoreSelect?: boolean
}

export const STREAMING_PROVIDERS: StreamingProviderOption[] = [
  { value: 'p2p', label: 'Direct Torrent (P2P)', icon: '🌐', type: 'Free' },
  { value: 'realdebrid', label: 'Real-Debrid', icon: '🔴', type: 'Premium', needsToken: true, hasOAuth: true },
  { value: 'alldebrid', label: 'AllDebrid', icon: '🟡', type: 'Premium', needsToken: true },
  { value: 'premiumize', label: 'Premiumize', icon: '🟣', type: 'Premium', needsToken: true, hasOAuth: true },
  { value: 'debridlink', label: 'Debrid-Link', icon: '🔵', type: 'Premium', needsToken: true, hasOAuth: true },
  { value: 'torbox', label: 'TorBox', icon: '📦', type: 'Premium', needsToken: true },
  { value: 'seedr', label: 'Seedr', icon: '🌱', type: 'Free/Premium', needsToken: true, hasOAuth: true },
  { value: 'offcloud', label: 'OffCloud', icon: '☁️', type: 'Free/Premium', needsToken: true },
  { value: 'pikpak', label: 'PikPak', icon: '🎬', type: 'Free/Premium', needsEmail: true, needsPassword: true },
  { value: 'easydebrid', label: 'EasyDebrid', icon: '⚡', type: 'Premium', needsToken: true },
  { value: 'debrider', label: 'Debrider', icon: '🔓', type: 'Premium', needsToken: true },
  { value: 'qbittorrent', label: 'qBittorrent + WebDAV', icon: '📥', type: 'Free', needsQBitConfig: true },
  { value: 'stremthru', label: 'StremThru', icon: '🔀', type: 'Interface', needsToken: true, needsUrl: true, hasStoreSelect: true },
  // Usenet-only providers
  { value: 'sabnzbd', label: 'SABnzbd + WebDAV', icon: '📰', type: 'Usenet', needsSABnzbdConfig: true },
  { value: 'nzbget', label: 'NZBGet + WebDAV', icon: '📰', type: 'Usenet', needsNZBGetConfig: true },
  { value: 'easynews', label: 'Easynews', icon: '📡', type: 'Usenet', needsEasynewsConfig: true },
]

export const STREMTHRU_STORES = [
  { value: 'realdebrid', label: 'Real-Debrid' },
  { value: 'debridlink', label: 'Debrid-Link' },
  { value: 'alldebrid', label: 'AllDebrid' },
  { value: 'torbox', label: 'TorBox' },
  { value: 'premiumize', label: 'Premiumize' },
]

export const CATALOGS = {
  'Movies': [
    { id: 'english_hdrip', name: 'English HD Movies' },
    { id: 'english_tcrip', name: 'English TCRip Movies' },
    { id: 'hindi_hdrip', name: 'Hindi HD Movies' },
    { id: 'hindi_tcrip', name: 'Hindi TCRip Movies' },
    { id: 'hindi_dubbed', name: 'Hindi Dubbed Movies' },
    { id: 'hindi_old', name: 'Hindi Old Movies' },
    { id: 'tamil_hdrip', name: 'Tamil HD Movies' },
    { id: 'tamil_tcrip', name: 'Tamil TCRip Movies' },
    { id: 'tamil_dubbed', name: 'Tamil Dubbed Movies' },
    { id: 'tamil_old', name: 'Tamil Old Movies' },
    { id: 'telugu_hdrip', name: 'Telugu HD Movies' },
    { id: 'telugu_tcrip', name: 'Telugu TCRip Movies' },
    { id: 'telugu_dubbed', name: 'Telugu Dubbed Movies' },
    { id: 'telugu_old', name: 'Telugu Old Movies' },
    { id: 'malayalam_hdrip', name: 'Malayalam HD Movies' },
    { id: 'malayalam_tcrip', name: 'Malayalam TCRip Movies' },
    { id: 'malayalam_dubbed', name: 'Malayalam Dubbed Movies' },
    { id: 'malayalam_old', name: 'Malayalam Old Movies' },
    { id: 'kannada_hdrip', name: 'Kannada HD Movies' },
    { id: 'kannada_tcrip', name: 'Kannada TCRip Movies' },
    { id: 'kannada_dubbed', name: 'Kannada Dubbed Movies' },
    { id: 'kannada_old', name: 'Kannada Old Movies' },
    { id: 'anime_movies', name: 'Anime Movies' },
    { id: 'arabic_movies', name: 'Arabic Movies' },
    { id: 'bangla_movies', name: 'Bangla Movies' },
    { id: 'punjabi_movies', name: 'Punjabi Movies' },
    { id: 'tgx_movie', name: 'TGx Movies' },
    { id: 'jackett_movies', name: 'Jackett Movies' },
    { id: 'prowlarr_movies', name: 'Prowlarr Movies' },
    { id: 'rss_feed_movies', name: 'RSS Feed Movies' },
    { id: 'contribution_movies', name: 'Contribution Movies' },
  ],
  'Series': [
    { id: 'english_series', name: 'English Series' },
    { id: 'hindi_series', name: 'Hindi Series' },
    { id: 'tamil_series', name: 'Tamil Series' },
    { id: 'telugu_series', name: 'Telugu Series' },
    { id: 'malayalam_series', name: 'Malayalam Series' },
    { id: 'kannada_series', name: 'Kannada Series' },
    { id: 'anime_series', name: 'Anime Series' },
    { id: 'arabic_series', name: 'Arabic Series' },
    { id: 'bangla_series', name: 'Bangla Series' },
    { id: 'punjabi_series', name: 'Punjabi Series' },
    { id: 'tgx_series', name: 'TGx Series' },
    { id: 'jackett_series', name: 'Jackett Series' },
    { id: 'prowlarr_series', name: 'Prowlarr Series' },
    { id: 'rss_feed_series', name: 'RSS Feed Series' },
    { id: 'contribution_series', name: 'Contribution Series' },
  ],
  'Live & Sports': [
    { id: 'live_tv', name: 'Live TV' },
    { id: 'live_sport_events', name: 'Live Sport Events' },
    { id: 'football', name: 'Football' },
    { id: 'basketball', name: 'Basketball' },
    { id: 'american_football', name: 'American Football' },
    { id: 'hockey', name: 'Hockey' },
    { id: 'baseball', name: 'Baseball' },
    { id: 'rugby', name: 'Rugby/AFL' },
    { id: 'formula_racing', name: 'Formula Racing' },
    { id: 'motogp_racing', name: 'MotoGP Racing' },
    { id: 'motor_sports', name: 'Motor Sports' },
    { id: 'fighting', name: 'Fighting (WWE, UFC)' },
    { id: 'other_sports', name: 'Other Sports' },
  ],
  'Search': [
    { id: 'mediafusion_search_movies', name: 'MediaFusion Search Movies' },
    { id: 'mediafusion_search_series', name: 'MediaFusion Search Series' },
    { id: 'mediafusion_search_tv', name: 'MediaFusion Search TV' },
  ],
}

export const RESOLUTIONS = [
  { value: '4k', label: '4K' },
  { value: '2160p', label: '2160p' },
  { value: '1440p', label: '1440p' },
  { value: '1080p', label: '1080p' },
  { value: '720p', label: '720p' },
  { value: '576p', label: '576p' },
  { value: '480p', label: '480p' },
  { value: '360p', label: '360p' },
  { value: '240p', label: '240p' },
  { value: '', label: 'Unknown' },
]

export const QUALITY_GROUPS = [
  { id: 'BluRay/UHD', label: 'BluRay/UHD', desc: 'BluRay, BluRay REMUX, BRRip, BDRip, UHDRip, REMUX' },
  { id: 'WEB/HD', label: 'WEB/HD', desc: 'WEB-DL, WEB-DLRip, WEBRip, HDRip, WEBMux' },
  { id: 'DVD/TV/SAT', label: 'DVD/TV/SAT', desc: 'DVD, DVDRip, HDTV, SATRip, TVRip, PPVRip, PDTV' },
  { id: 'CAM/Screener', label: 'CAM/Screener', desc: 'CAM, TeleSync, TeleCine, SCR' },
  { id: 'Unknown', label: 'Unknown', desc: 'Unknown quality sources' },
]

export const SORTING_OPTIONS = [
  { key: 'language', label: 'Language', desc: 'Preferred languages first', asc: 'Least preferred languages first' },
  { key: 'cached', label: 'Cached', desc: 'Cached results first', asc: 'Uncached results first' },
  { key: 'resolution', label: 'Resolution', desc: 'Highest resolution first', asc: 'Lowest resolution first' },
  { key: 'quality', label: 'Quality', desc: 'Best quality first', asc: 'Lower quality first' },
  { key: 'size', label: 'Size', desc: 'Largest size first', asc: 'Smallest size first' },
  { key: 'seeders', label: 'Seeders', desc: 'Most seeders first', asc: 'Fewest seeders first' },
  { key: 'created_at', label: 'Created At', desc: 'Newest first', asc: 'Oldest first' },
]

export const STREAM_TYPES = [
  { value: 'torrent', label: 'Torrent', icon: '🧲' },
  { value: 'usenet', label: 'Usenet', icon: '📰' },
  { value: 'telegram', label: 'Telegram', icon: '📨' },
  { value: 'http', label: 'HTTP / Direct', icon: '🌐' },
  { value: 'acestream', label: 'AceStream', icon: '📡' },
]

export const LANGUAGES = [
  'English', 'Tamil', 'Hindi', 'Malayalam', 'Kannada', 'Telugu', 'Chinese', 'Russian',
  'Arabic', 'Japanese', 'Korean', 'Taiwanese', 'Latino', 'French', 'Spanish', 'Portuguese',
  'Italian', 'German', 'Ukrainian', 'Polish', 'Czech', 'Thai', 'Indonesian', 'Vietnamese',
  'Dutch', 'Bengali', 'Turkish', 'Greek', 'Swedish', 'Romanian', 'Hungarian', 'Finnish',
  'Norwegian', 'Danish', 'Hebrew', 'Lithuanian', 'Punjabi', 'Marathi', 'Gujarati',
  'Bhojpuri', 'Nepali', 'Urdu', 'Tagalog', 'Filipino', 'Malay', 'Mongolian', 'Armenian', 'Georgian'
]

export const CERTIFICATION_LEVELS = [
  { value: 'Disable', label: 'Disable Filter' },
  { value: 'Unknown', label: 'Unknown' },
  { value: 'All Ages', label: 'All Ages (G, U, etc.)' },
  { value: 'Children', label: 'Children (PG, etc.)' },
  { value: 'Parental Guidance', label: 'Parental Guidance (PG-12, etc.)' },
  { value: 'Teens', label: 'Teens (PG-13, R, etc.)' },
  { value: 'Adults', label: 'Adults (18+, MA, etc.)' },
  { value: 'Adults+', label: 'Adults+ (X, NC-17, etc.)' },
]

export const NUDITY_LEVELS = [
  { value: 'Disable', label: 'Disable Filter' },
  { value: 'None', label: 'None' },
  { value: 'Mild', label: 'Mild' },
  { value: 'Moderate', label: 'Moderate' },
  { value: 'Severe', label: 'Severe' },
  { value: 'Unknown', label: 'Unknown' },
]

// Default profile configuration
export const DEFAULT_CONFIG: ProfileConfig = {
  sc: [],
  sr: ['4k', '2160p', '1440p', '1080p', '720p', '576p', '480p', '360p', '240p', ''],
  ec: true,
  eim: false,
  ms: 'inf',
  mns: 0,
  mspr: 10,
  tsp: [
    { k: 'language', d: 'desc' },
    { k: 'cached', d: 'desc' },
    { k: 'resolution', d: 'desc' },
    { k: 'quality', d: 'desc' },
    { k: 'size', d: 'desc' },
    { k: 'seeders', d: 'desc' },
    { k: 'created_at', d: 'desc' },
  ],
  nf: ['Severe'],
  cf: ['Adults+'],
  ls: LANGUAGES,
  qf: ['BluRay/UHD', 'WEB/HD', 'DVD/TV/SAT', 'CAM/Screener', 'Unknown'],
  lss: false,
  mxs: 25,
  stg: 'separate',
  sto: ['torrent', 'usenet', 'telegram', 'http', 'acestream'],
  pg: 'separate',
  snfm: 'disabled',
  snfp: [],
  snfr: false,
}

