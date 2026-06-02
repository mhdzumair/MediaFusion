import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { patchDatabaseSearchParams, parsePositiveInt } from '../databaseManagerUrl'

export interface FilterState {
  id: string
  column: string
  operator: string
  value: string
}

const PER_PAGE_OPTIONS = [10, 25, 50, 100] as const

function parsePerPage(value: string | null): number {
  const n = parsePositiveInt(value, 25)
  return (PER_PAGE_OPTIONS as readonly number[]).includes(n) ? n : 25
}

function parseFilters(raw: string | null): FilterState[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw) as FilterState[]
    if (!Array.isArray(parsed)) return []
    return parsed.filter((f) => f && typeof f.column === 'string')
  } catch {
    return []
  }
}

export interface BrowserUrlPatch {
  table?: string | null
  page?: number
  per_page?: number
  order_by?: string | null
  order_dir?: 'asc' | 'desc'
  filters?: FilterState[] | null
}

export function useTableBrowserUrl() {
  const [searchParams, setSearchParams] = useSearchParams()

  const selectedTable = searchParams.get('table')
  const page = parsePositiveInt(searchParams.get('page'), 1)
  const perPage = parsePerPage(searchParams.get('per_page'))
  const orderBy = searchParams.get('order_by') || undefined
  const orderDir: 'asc' | 'desc' = searchParams.get('order_dir') === 'desc' ? 'desc' : 'asc'
  const filters = useMemo(() => parseFilters(searchParams.get('filters')), [searchParams])

  const updateBrowserParams = useCallback(
    (patch: BrowserUrlPatch) => {
      const updates: Array<{ key: string; value: string | null }> = [{ key: 'tab', value: 'browser' }]

      if ('table' in patch) {
        updates.push({ key: 'table', value: patch.table ?? null })
      }
      if ('page' in patch) {
        const p = patch.page ?? 1
        updates.push({ key: 'page', value: p <= 1 ? null : String(p) })
      }
      if ('per_page' in patch) {
        const pp = patch.per_page ?? 25
        updates.push({ key: 'per_page', value: pp === 25 ? null : String(pp) })
      }
      if ('order_by' in patch) {
        updates.push({ key: 'order_by', value: patch.order_by ?? null })
      }
      if ('order_dir' in patch) {
        const dir = patch.order_dir ?? 'asc'
        updates.push({ key: 'order_dir', value: dir === 'asc' ? null : dir })
      }
      if ('filters' in patch) {
        const f = patch.filters
        if (!f || f.length === 0) {
          updates.push({ key: 'filters', value: null })
        } else {
          updates.push({ key: 'filters', value: JSON.stringify(f) })
        }
      }

      setSearchParams(patchDatabaseSearchParams(searchParams, updates), { replace: true })
    },
    [searchParams, setSearchParams],
  )

  return {
    selectedTable,
    page,
    perPage,
    orderBy,
    orderDir,
    filters,
    updateBrowserParams,
  }
}
