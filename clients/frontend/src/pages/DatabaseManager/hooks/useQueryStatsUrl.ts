import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { SlowQueriesParams } from '@/lib/api/admin'
import {
  DEFAULT_MIN_MEAN_TIME_MS,
  patchDatabaseSearchParams,
  parseMinMeanTimeMs,
  parsePositiveInt,
  parseSlowQueryOrderBy,
} from '../databaseManagerUrl'

export function useQueryStatsUrl() {
  const [searchParams, setSearchParams] = useSearchParams()

  const orderBy = parseSlowQueryOrderBy(searchParams.get('order_by'))
  const limit = parsePositiveInt(searchParams.get('limit'), 20, 100)
  const minCalls = parsePositiveInt(searchParams.get('min_calls'), 5)
  const minMeanTimeMs = parseMinMeanTimeMs(searchParams.get('min_mean_time_ms'))

  const updateQueryStatsParams = useCallback(
    (patch: {
      order_by?: SlowQueriesParams['order_by']
      limit?: number
      min_calls?: number
      min_mean_time_ms?: number
    }) => {
      const updates: Array<{ key: string; value: string | null }> = [{ key: 'tab', value: 'queries' }]

      if ('order_by' in patch) {
        const ob = patch.order_by ?? 'mean_exec_time'
        updates.push({ key: 'order_by', value: ob === 'mean_exec_time' ? null : ob })
      }
      if ('limit' in patch) {
        const lim = patch.limit ?? 20
        updates.push({ key: 'limit', value: lim === 20 ? null : String(lim) })
      }
      if ('min_calls' in patch) {
        const mc = patch.min_calls ?? 5
        updates.push({ key: 'min_calls', value: mc === 5 ? null : String(mc) })
      }
      if ('min_mean_time_ms' in patch) {
        const mt = patch.min_mean_time_ms ?? DEFAULT_MIN_MEAN_TIME_MS
        updates.push({
          key: 'min_mean_time_ms',
          value: mt === DEFAULT_MIN_MEAN_TIME_MS ? null : String(Math.round(mt)),
        })
      }

      setSearchParams(patchDatabaseSearchParams(searchParams, updates), { replace: true })
    },
    [searchParams, setSearchParams],
  )

  return { orderBy, limit, minCalls, minMeanTimeMs, updateQueryStatsParams }
}
