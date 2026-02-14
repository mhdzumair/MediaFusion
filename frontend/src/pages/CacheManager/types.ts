// Cache Manager Types

export interface CacheTypeInfo {
  name: string
  pattern: string
  icon: string
  color: string
  description: string
}

export interface CacheKeyInfo {
  key: string
  type: string
  ttl: number
  size: number
  is_binary?: boolean
}

export interface CacheKeysResponse {
  keys: CacheKeyInfo[]
  total: number
  cursor: string
  has_more: boolean
}

export interface CacheValueResponse {
  key: string
  type: string
  ttl: number
  value: unknown
  size: number
  is_binary?: boolean
}

// Matches backend RedisInfoStats
export interface RedisInfoStats {
  connected: boolean
  version?: string
  memory_used: string
  memory_peak?: string
  total_keys: number
  connected_clients: number
  uptime_days?: number
  hit_rate?: number
  ops_per_sec?: number
}

// Matches backend CacheTypeStats
export interface CacheTypeStats {
  name: string
  description: string
  keys_count: number
  memory_bytes?: number
}

// Matches backend CacheStatsResponse
export interface CacheStats {
  redis: RedisInfoStats
  cache_types: CacheTypeStats[]
}

export interface ClearCacheRequest {
  cache_type?: string
  pattern?: string
}

export interface ClearCacheResponse {
  success: boolean
  message: string
  keys_deleted: number
  admin_username?: string
}

export interface DeleteKeyResponse {
  success: boolean
  message: string
  admin_username?: string
}

export interface ActionHistoryItem {
  id: string
  action: string
  target: string
  timestamp: Date
  result: string
  admin?: string
}

// Cache type definitions with metadata
export const CACHE_TYPES: CacheTypeInfo[] = [
  { name: 'Scrapers', pattern: 'scrapy:*', icon: 'Radio', color: 'violet', description: 'Scraper job data and messages' },
  { name: 'Metadata', pattern: 'meta_cache:*', icon: 'FileJson', color: 'blue', description: 'Movie/TV show metadata' },
  { name: 'Catalog', pattern: 'catalog:*', icon: 'Database', color: 'emerald', description: 'Catalog browse cache' },
  { name: 'Streams', pattern: 'stream*', icon: 'Film', color: 'amber', description: 'Stream data cache' },
  { name: 'Debrid', pattern: 'debrid_cache:*', icon: 'Server', color: 'rose', description: 'Debrid service cache' },
  { name: 'Profiles', pattern: 'user_data:*', icon: 'Users', color: 'cyan', description: 'User profile data' },
  { name: 'Events', pattern: 'events:*', icon: 'Calendar', color: 'orange', description: 'Sports events cache' },
  { name: 'Genres', pattern: 'genres:*', icon: 'Layers', color: 'pink', description: 'Genre mappings' },
  { name: 'Lookup', pattern: '*_id:*', icon: 'Search', color: 'indigo', description: 'ID lookup cache' },
  { name: 'Scheduler', pattern: 'scheduler:*', icon: 'Clock', color: 'slate', description: 'Scheduler job state' },
  { name: 'Streaming', pattern: 'streaming:*', icon: 'Zap', color: 'yellow', description: 'Active streaming sessions' },
  { name: 'Images', pattern: '*.jpg', icon: 'Image', color: 'teal', description: 'Cached poster images' },
  { name: 'Rate Limit', pattern: 'rate_limit:*', icon: 'Shield', color: 'red', description: 'Rate limiting counters' },
]

// Helper to get color classes for cache types
export const getTypeColorClasses = (color: string) => {
  const colorMap: Record<string, { bg: string; text: string; border: string }> = {
    violet: { bg: 'bg-violet-500/10', text: 'text-violet-400', border: 'border-violet-500/30' },
    blue: { bg: 'bg-blue-500/10', text: 'text-blue-400', border: 'border-blue-500/30' },
    emerald: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/30' },
    amber: { bg: 'bg-amber-500/10', text: 'text-amber-400', border: 'border-amber-500/30' },
    rose: { bg: 'bg-rose-500/10', text: 'text-rose-400', border: 'border-rose-500/30' },
    cyan: { bg: 'bg-cyan-500/10', text: 'text-cyan-400', border: 'border-cyan-500/30' },
    orange: { bg: 'bg-orange-500/10', text: 'text-orange-400', border: 'border-orange-500/30' },
    pink: { bg: 'bg-pink-500/10', text: 'text-pink-400', border: 'border-pink-500/30' },
    indigo: { bg: 'bg-indigo-500/10', text: 'text-indigo-400', border: 'border-indigo-500/30' },
    slate: { bg: 'bg-slate-500/10', text: 'text-slate-400', border: 'border-slate-500/30' },
    yellow: { bg: 'bg-yellow-500/10', text: 'text-yellow-400', border: 'border-yellow-500/30' },
    teal: { bg: 'bg-teal-500/10', text: 'text-teal-400', border: 'border-teal-500/30' },
    red: { bg: 'bg-red-500/10', text: 'text-red-400', border: 'border-red-500/30' },
  }
  return colorMap[color] || colorMap.slate
}

// Redis type badge colors
export const REDIS_TYPE_BADGES: Record<string, { color: string; icon: string }> = {
  string: { color: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30', icon: 'Type' },
  hash: { color: 'bg-violet-500/20 text-violet-400 border-violet-500/30', icon: 'Hash' },
  list: { color: 'bg-blue-500/20 text-blue-400 border-blue-500/30', icon: 'List' },
  set: { color: 'bg-amber-500/20 text-amber-400 border-amber-500/30', icon: 'Layers' },
  zset: { color: 'bg-rose-500/20 text-rose-400 border-rose-500/30', icon: 'SortAsc' },
}

// Utility functions
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`
}

export function formatTTL(seconds: number): string {
  if (seconds < 0) return 'No expiry'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

export function formatTimestamp(value: string | number): { display: string; isTimestamp: boolean } {
  const numValue = typeof value === 'string' ? parseInt(value, 10) : value
  // Check if it looks like a Unix timestamp (between 2020 and 2030)
  if (!isNaN(numValue) && numValue > 1577836800 && numValue < 1893456000) {
    const date = new Date(numValue * 1000)
    return { display: date.toLocaleString(), isTimestamp: true }
  }
  return { display: String(value), isTimestamp: false }
}

