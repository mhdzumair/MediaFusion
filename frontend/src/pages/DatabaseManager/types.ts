// Database Manager Types

// ============================================
// Database Stats Types
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

// ============================================
// Table Information Types
// ============================================

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

export interface TableDataResponse {
  table: string
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
  page: number
  per_page: number
  pages: number
}

// ============================================
// Orphan Detection Types
// ============================================

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

// ============================================
// Maintenance Types
// ============================================

export type MaintenanceOperation = 'vacuum' | 'analyze' | 'vacuum_analyze' | 'reindex'

export interface MaintenanceRequest {
  tables?: string[]
  operation: MaintenanceOperation
  full?: boolean
}

export interface MaintenanceResult {
  success: boolean
  operation: string
  tables_processed: string[]
  execution_time_ms: number
  message: string
}

// ============================================
// Bulk Operations Types
// ============================================

export interface BulkDeleteRequest {
  table: string
  ids: string[]
  id_column?: string
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

// ============================================
// Export/Import Types
// ============================================

export type ExportFormat = 'csv' | 'json' | 'sql'

export interface ExportOptions {
  format: ExportFormat
  table: string
  include_schema?: boolean
  include_data?: boolean
  limit?: number
}

export type ImportFormat = 'csv' | 'json' | 'sql'
export type ImportMode = 'insert' | 'upsert' | 'replace'

export interface ImportOptions {
  format: ImportFormat
  table: string
  mode: ImportMode
  column_mapping?: Record<string, string>
  skip_errors?: boolean
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
// UI Helper Types
// ============================================

export interface DatabaseStatCard {
  label: string
  value: string | number
  icon: string
  color: string
  description?: string
}

export interface ActionHistoryItem {
  id: string
  action: string
  target: string
  timestamp: Date
  result: string
  executionTime?: number
}

// ============================================
// Tab Types
// ============================================

export type DatabaseTab = 'overview' | 'browser' | 'maintenance'

// ============================================
// Color Utility
// ============================================

export const getStatusColor = (status: string): string => {
  const colorMap: Record<string, string> = {
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    error: 'text-rose-400',
    info: 'text-blue-400',
    pending: 'text-slate-400',
  }
  return colorMap[status] || colorMap.info
}

export const getTableTypeColor = (tableName: string): { bg: string; text: string; border: string } => {
  const colorMap: Record<string, { bg: string; text: string; border: string }> = {
    base_metadata: { bg: 'bg-blue-500/10', text: 'text-blue-400', border: 'border-blue-500/30' },
    movie_metadata: { bg: 'bg-violet-500/10', text: 'text-violet-400', border: 'border-violet-500/30' },
    series_metadata: { bg: 'bg-purple-500/10', text: 'text-purple-400', border: 'border-purple-500/30' },
    tv_metadata: { bg: 'bg-pink-500/10', text: 'text-pink-400', border: 'border-pink-500/30' },
    torrent_stream: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/30' },
    tv_stream: { bg: 'bg-amber-500/10', text: 'text-amber-400', border: 'border-amber-500/30' },
    episode_file: { bg: 'bg-cyan-500/10', text: 'text-cyan-400', border: 'border-cyan-500/30' },
    genre: { bg: 'bg-rose-500/10', text: 'text-rose-400', border: 'border-rose-500/30' },
    catalog: { bg: 'bg-orange-500/10', text: 'text-orange-400', border: 'border-orange-500/30' },
    language: { bg: 'bg-teal-500/10', text: 'text-teal-400', border: 'border-teal-500/30' },
    star: { bg: 'bg-yellow-500/10', text: 'text-yellow-400', border: 'border-yellow-500/30' },
    user: { bg: 'bg-indigo-500/10', text: 'text-indigo-400', border: 'border-indigo-500/30' },
  }

  // Find matching pattern
  const loweredName = tableName.toLowerCase()
  for (const [pattern, colors] of Object.entries(colorMap)) {
    if (loweredName.includes(pattern)) {
      return colors
    }
  }

  return { bg: 'bg-slate-500/10', text: 'text-slate-400', border: 'border-slate-500/30' }
}

// ============================================
// Utility Functions
// ============================================

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

export function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`
}

export function formatNumber(num: number): string {
  if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}K`
  return num.toString()
}

export function formatTimestamp(timestamp: string | null): string {
  if (!timestamp) return 'Never'
  try {
    const date = new Date(timestamp)
    return date.toLocaleString()
  } catch {
    return timestamp
  }
}

export function truncateText(text: string, maxLength: number = 50): string {
  if (text.length <= maxLength) return text
  return text.substring(0, maxLength) + '...'
}
