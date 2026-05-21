import { useState, useEffect } from 'react'
import {
  Search,
  Eye,
  Clock,
  HardDrive,
  Loader2,
  Filter,
  Hash,
  List,
  Layers,
  SortAsc,
  Type,
  AlertCircle,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { useCacheKeys } from '../hooks/useCacheData'
import { formatBytes, formatTTL, REDIS_TYPE_BADGES } from '../types'

interface BrowseTabProps {
  initialPattern?: string
  /** When set (from Overview card), uses GET /keys?cache_category=… */
  initialBackendCategory?: string
  onViewKey: (key: string) => void
}

// Type badge component
function TypeBadge({ type }: { type: string }) {
  const typeInfo = REDIS_TYPE_BADGES[type] || REDIS_TYPE_BADGES.string
  const IconComponent =
    {
      Type,
      Hash,
      List,
      Layers,
      SortAsc,
    }[typeInfo.icon] || Type

  return (
    <Badge variant="outline" className={cn('gap-1 px-2 py-0.5 text-[10px]', typeInfo.color)}>
      <IconComponent className="h-3 w-3" />
      {type}
    </Badge>
  )
}

export function BrowseTab({ initialPattern = '', initialBackendCategory, onViewKey }: BrowseTabProps) {
  const [searchPattern, setSearchPattern] = useState(initialPattern)
  const [debouncedPattern, setDebouncedPattern] = useState(initialPattern)
  const [backendCategory, setBackendCategory] = useState<string | undefined>(initialBackendCategory)
  const [typeFilter, setTypeFilter] = useState('all')
  const filterKey = `${debouncedPattern}|${typeFilter}|${backendCategory ?? ''}`
  const [pagination, setPagination] = useState({ filterKey, pageIndex: 0, cursors: ['0'] as string[] })
  if (pagination.filterKey !== filterKey) {
    setPagination({ filterKey, pageIndex: 0, cursors: ['0'] })
  }
  const { pageIndex, cursors } = pagination
  const setPageIndex = (value: number | ((prev: number) => number)) => {
    setPagination((prev) => ({
      ...prev,
      pageIndex: typeof value === 'function' ? value(prev.pageIndex) : value,
    }))
  }
  const setCursors = (value: string[] | ((prev: string[]) => string[])) => {
    setPagination((prev) => ({
      ...prev,
      cursors: typeof value === 'function' ? value(prev.cursors) : value,
    }))
  }

  // Update search pattern when initialPattern changes (during render, not in effect)
  const [prevInitialPattern, setPrevInitialPattern] = useState(initialPattern)
  if (initialPattern && prevInitialPattern !== initialPattern) {
    setPrevInitialPattern(initialPattern)
    setSearchPattern(initialPattern)
    setDebouncedPattern(initialPattern)
  }

  const [prevInitialCategory, setPrevInitialCategory] = useState(initialBackendCategory)
  if (initialBackendCategory !== prevInitialCategory) {
    setPrevInitialCategory(initialBackendCategory)
    setBackendCategory(initialBackendCategory)
  }

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedPattern(searchPattern || '*')
    }, 300)
    return () => clearTimeout(timer)
  }, [searchPattern])

  const cursor = cursors[pageIndex] ?? '0'
  const { data, isLoading, isFetching, error } = useCacheKeys(debouncedPattern, typeFilter, backendCategory, cursor)

  const keys = data?.keys ?? []
  const totalKeys = data?.total ?? 0
  const hasMore = data?.has_more ?? false

  const goToNextPage = () => {
    if (!hasMore || !data?.cursor) return
    setCursors((prev) => {
      const next = [...prev]
      next[pageIndex + 1] = data.cursor
      return next
    })
    setPageIndex((i) => i + 1)
    window.scrollTo(0, 0)
  }

  const goToPrevPage = () => {
    if (pageIndex === 0) return
    setPageIndex((i) => i - 1)
    window.scrollTo(0, 0)
  }

  return (
    <div className="space-y-4">
      {/* Search and Filter */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search pattern (e.g., meta_cache:*, *.jpg)"
            value={searchPattern}
            onChange={(e) => {
              setSearchPattern(e.target.value)
              setBackendCategory(undefined)
            }}
            className="pl-9"
          />
        </div>
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger className="w-[160px]">
            <Filter className="h-4 w-4 mr-2" />
            <SelectValue placeholder="Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="string">String</SelectItem>
            <SelectItem value="hash">Hash</SelectItem>
            <SelectItem value="list">List</SelectItem>
            <SelectItem value="set">Set</SelectItem>
            <SelectItem value="zset">Sorted Set</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Results summary */}
      {(debouncedPattern || backendCategory) && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            Showing {keys.length} of {totalKeys.toLocaleString()} keys
            {typeFilter !== 'all' && ` (filtered by ${typeFilter})`}
            {(hasMore || pageIndex > 0) && ` · Page ${pageIndex + 1}`}
          </span>
          <span className="font-mono text-xs text-right max-w-[60%] truncate">
            {backendCategory ? `Category: ${backendCategory}` : `Pattern: ${debouncedPattern}`}
          </span>
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center py-16">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Searching keys...</p>
          </div>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="flex items-center justify-center py-16">
          <div className="flex flex-col items-center gap-3 text-destructive">
            <AlertCircle className="h-8 w-8" />
            <p className="text-sm">Failed to load keys</p>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && keys.length === 0 && (debouncedPattern || backendCategory) && (
        <div className="flex items-center justify-center py-16">
          <div className="flex flex-col items-center gap-3 text-muted-foreground">
            <Search className="h-8 w-8" />
            <p className="text-sm">
              {backendCategory
                ? `No keys in category "${backendCategory}"`
                : `No keys found matching "${debouncedPattern}"`}
            </p>
          </div>
        </div>
      )}

      {/* Keys list */}
      {keys.length > 0 && (
        <div className="space-y-2">
          {keys.map((key) => (
            <button
              key={key.key}
              onClick={() => onViewKey(key.key)}
              className="w-full p-3 rounded-lg border border-border/50 bg-card/50 hover:bg-muted/50 
                         transition-all hover:border-border text-left group"
            >
              <div className="flex items-center justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <p className="font-mono text-sm truncate group-hover:text-primary transition-colors">{key.key}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <TypeBadge type={key.type} />
                  <Badge variant="outline" className="gap-1 text-[10px]">
                    <Clock className="h-3 w-3" />
                    {formatTTL(key.ttl)}
                  </Badge>
                  <Badge variant="outline" className="gap-1 text-[10px]">
                    <HardDrive className="h-3 w-3" />
                    {formatBytes(key.size)}
                  </Badge>
                  <Eye className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
              </div>
            </button>
          ))}

          {(pageIndex > 0 || hasMore) && (
            <div className="flex justify-center items-center gap-2 pt-4">
              <Button variant="outline" size="icon" disabled={pageIndex === 0 || isFetching} onClick={goToPrevPage}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="px-4 text-sm text-muted-foreground">Page {pageIndex + 1}</span>
              <Button variant="outline" size="icon" disabled={!hasMore || isFetching} onClick={goToNextPage}>
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
