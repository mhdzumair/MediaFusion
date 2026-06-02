import type { DatabaseTab } from './types'
import type { SlowQueriesParams } from '@/lib/api/admin'

export const DATABASE_TABS: DatabaseTab[] = ['overview', 'browser', 'queries', 'maintenance']

const SLOW_QUERY_ORDER_BY = ['mean_exec_time', 'max_exec_time', 'total_exec_time'] as const

export const DEFAULT_MIN_MEAN_TIME_MS = 5

export function parseDatabaseTab(value: string | null): DatabaseTab {
  if (value && DATABASE_TABS.includes(value as DatabaseTab)) {
    return value as DatabaseTab
  }
  return 'overview'
}

export function parseSlowQueryOrderBy(value: string | null): SlowQueriesParams['order_by'] {
  if (value && SLOW_QUERY_ORDER_BY.includes(value as (typeof SLOW_QUERY_ORDER_BY)[number])) {
    return value as SlowQueriesParams['order_by']
  }
  return 'mean_exec_time'
}

export function parseMinMeanTimeMs(value: string | null): number {
  if (value === null || value === '') return DEFAULT_MIN_MEAN_TIME_MS
  const n = parseFloat(value)
  if (!Number.isFinite(n) || n < 0) return DEFAULT_MIN_MEAN_TIME_MS
  return n
}

export function parsePositiveInt(value: string | null, fallback: number, max?: number): number {
  const n = parseInt(value ?? '', 10)
  if (!Number.isFinite(n) || n < 1) return fallback
  if (max !== undefined) return Math.min(n, max)
  return n
}

/** Apply param updates; omit keys when value matches default (removed from URL). */
export function patchDatabaseSearchParams(
  current: URLSearchParams,
  updates: Array<{ key: string; value: string | null }>,
): URLSearchParams {
  const next = new URLSearchParams(current)
  for (const { key, value } of updates) {
    if (value === null || value === '') {
      next.delete(key)
    } else {
      next.set(key, value)
    }
  }
  return next
}

export function setDatabaseTab(current: URLSearchParams, tab: DatabaseTab): URLSearchParams {
  return patchDatabaseSearchParams(current, [{ key: 'tab', value: tab === 'overview' ? null : tab }])
}
