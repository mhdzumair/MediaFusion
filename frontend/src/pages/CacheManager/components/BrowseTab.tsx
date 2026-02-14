import { useState, useEffect, useRef, useCallback } from 'react'
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
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { useCacheKeys } from '../hooks/useCacheData'
import { formatBytes, formatTTL, REDIS_TYPE_BADGES } from '../types'

interface BrowseTabProps {
  initialPattern?: string
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

export function BrowseTab({ initialPattern = '', onViewKey }: BrowseTabProps) {
  const [searchPattern, setSearchPattern] = useState(initialPattern)
  const [debouncedPattern, setDebouncedPattern] = useState(initialPattern)
  const [typeFilter, setTypeFilter] = useState('all')
  const loadMoreRef = useRef<HTMLDivElement>(null)

  // Update search pattern when initialPattern changes (during render, not in effect)
  const [prevInitialPattern, setPrevInitialPattern] = useState(initialPattern)
  if (initialPattern && prevInitialPattern !== initialPattern) {
    setPrevInitialPattern(initialPattern)
    setSearchPattern(initialPattern)
    setDebouncedPattern(initialPattern)
  }

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedPattern(searchPattern || '*')
    }, 300)
    return () => clearTimeout(timer)
  }, [searchPattern])

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, error } = useCacheKeys(
    debouncedPattern,
    typeFilter,
  )

  // Infinite scroll observer
  const handleObserver = useCallback(
    (entries: IntersectionObserverEntry[]) => {
      const [entry] = entries
      if (entry.isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage()
      }
    },
    [fetchNextPage, hasNextPage, isFetchingNextPage],
  )

  useEffect(() => {
    const observer = new IntersectionObserver(handleObserver, {
      root: null,
      rootMargin: '100px',
      threshold: 0,
    })

    if (loadMoreRef.current) {
      observer.observe(loadMoreRef.current)
    }

    return () => observer.disconnect()
  }, [handleObserver])

  // Flatten pages into single array
  const allKeys = data?.pages.flatMap((page) => page.keys) || []
  const totalKeys = data?.pages[0]?.total || 0

  return (
    <div className="space-y-4">
      {/* Search and Filter */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search pattern (e.g., meta_cache:*, *.jpg)"
            value={searchPattern}
            onChange={(e) => setSearchPattern(e.target.value)}
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
      {debouncedPattern && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            Showing {allKeys.length} of {totalKeys.toLocaleString()} keys
            {typeFilter !== 'all' && ` (filtered by ${typeFilter})`}
          </span>
          <span className="font-mono text-xs">Pattern: {debouncedPattern}</span>
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
      {!isLoading && !error && allKeys.length === 0 && debouncedPattern && (
        <div className="flex items-center justify-center py-16">
          <div className="flex flex-col items-center gap-3 text-muted-foreground">
            <Search className="h-8 w-8" />
            <p className="text-sm">No keys found matching "{debouncedPattern}"</p>
          </div>
        </div>
      )}

      {/* Keys list */}
      {allKeys.length > 0 && (
        <div className="space-y-2">
          {allKeys.map((key) => (
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

          {/* Infinite scroll trigger */}
          <div ref={loadMoreRef} className="h-px" />

          {/* Loading more indicator */}
          {isFetchingNextPage && (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
