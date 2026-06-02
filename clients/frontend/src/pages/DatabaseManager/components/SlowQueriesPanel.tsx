import { useState } from 'react'
import { Timer, RefreshCw, RotateCcw, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import { useSlowQueries, useResetSlowQueries } from '../hooks/useDatabaseData'
import { useQueryStatsUrl } from '../hooks/useQueryStatsUrl'
import { formatDuration } from '../types'
import type { SlowQueriesParams } from '@/lib/api/admin'

interface SlowQueriesPanelProps {
  /** Taller scroll area when used as the main tab content */
  variant?: 'compact' | 'full'
}

export function SlowQueriesPanel({ variant = 'full' }: SlowQueriesPanelProps) {
  const { toast } = useToast()
  const { orderBy: slowQueryOrderBy, limit, minCalls, minMeanTimeMs, updateQueryStatsParams } = useQueryStatsUrl()
  const [expandedQueryIndex, setExpandedQueryIndex] = useState<number | null>(null)

  const {
    data: slowQueries,
    isLoading,
    isError,
    error,
    refetch,
    isRefetching,
  } = useSlowQueries({
    limit,
    min_calls: minCalls,
    min_mean_time_ms: minMeanTimeMs,
    order_by: slowQueryOrderBy,
  })

  const resetMutation = useResetSlowQueries()

  const queries = slowQueries?.queries ?? []

  const handleReset = async () => {
    try {
      const result = await resetMutation.mutateAsync()
      toast({
        title: 'Query stats reset',
        description: result.message,
      })
    } catch (err) {
      toast({
        title: 'Failed to reset query stats',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      })
    }
  }

  const scrollHeight = variant === 'full' ? 'h-[min(60vh,520px)]' : 'h-72'

  return (
    <Card className="bg-card/50 border-border/50">
      <CardHeader className="pb-2">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Timer className="h-5 w-5 text-primary" />
              Slow Query Statistics
            </CardTitle>
            <CardDescription className="text-sm mt-1">
              Slowest query fingerprints from <code className="text-xs">pg_stat_statements</code> — mean time ≥
              {minMeanTimeMs > 0 ? ` ${minMeanTimeMs}ms` : ' 0ms'}, ≥{minCalls} calls, utility statements excluded
            </CardDescription>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Select
              value={slowQueryOrderBy}
              onValueChange={(value) => updateQueryStatsParams({ order_by: value as SlowQueriesParams['order_by'] })}
            >
              <SelectTrigger className="h-9 w-[160px] text-sm">
                <SelectValue placeholder="Sort by" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="mean_exec_time">Mean time (default)</SelectItem>
                <SelectItem value="max_exec_time">Max time</SelectItem>
                <SelectItem value="total_exec_time">Total time</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={String(minMeanTimeMs)}
              onValueChange={(value) => updateQueryStatsParams({ min_mean_time_ms: Number(value) })}
            >
              <SelectTrigger className="h-9 w-[140px] text-sm">
                <SelectValue placeholder="Min mean" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1">≥ 1ms mean</SelectItem>
                <SelectItem value="5">≥ 5ms mean</SelectItem>
                <SelectItem value="10">≥ 10ms mean</SelectItem>
                <SelectItem value="50">≥ 50ms mean</SelectItem>
                <SelectItem value="100">≥ 100ms mean</SelectItem>
                <SelectItem value="500">≥ 500ms mean</SelectItem>
                <SelectItem value="1000">≥ 1s mean</SelectItem>
                <SelectItem value="0">No minimum</SelectItem>
              </SelectContent>
            </Select>
            {!isError && !isLoading && (
              <Badge variant="secondary" className="font-mono text-xs">
                {slowQueries?.count ?? queries.length} queries
              </Badge>
            )}
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isRefetching}>
              <RefreshCw className={cn('h-4 w-4 mr-2', isRefetching && 'animate-spin')} />
              Refresh
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleReset}
              disabled={resetMutation.isPending}
              title="Reset pg_stat_statements counters"
            >
              <RotateCcw className={cn('h-4 w-4 mr-2', resetMutation.isPending && 'animate-spin')} />
              Reset stats
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <ScrollArea className={scrollHeight}>
          {isLoading ? (
            <div className="space-y-2">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-24 rounded-lg" />
              ))}
            </div>
          ) : isError ? (
            <div className="text-center py-12 text-muted-foreground border border-dashed border-border/60 rounded-xl">
              <AlertTriangle className="h-10 w-10 mx-auto mb-3 text-primary" />
              <p className="text-sm font-medium">Unable to load slow query statistics</p>
              <p className="text-xs mt-2 max-w-md mx-auto px-4">
                {error instanceof Error
                  ? error.message
                  : 'Enable pg_stat_statements in PostgreSQL (shared_preload_libraries + CREATE EXTENSION).'}
              </p>
              <Button variant="outline" size="sm" className="mt-4" onClick={() => refetch()}>
                Try again
              </Button>
            </div>
          ) : queries.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground border border-dashed border-border/60 rounded-xl">
              <Timer className="h-10 w-10 mx-auto mb-3 opacity-50" />
              <p className="text-sm font-medium">No slow queries match these filters</p>
              <p className="text-xs mt-1">Lower the mean-time threshold or run more traffic, then refresh.</p>
            </div>
          ) : (
            <div className="space-y-3 pr-2">
              {queries.map((query, index) => {
                const isExpanded = expandedQueryIndex === index
                return (
                  <div
                    key={`${query.queryid ?? index}-${index}`}
                    className="p-4 rounded-xl border border-border/40 bg-muted/20 space-y-3"
                  >
                    <button
                      type="button"
                      className="w-full text-left"
                      onClick={() => setExpandedQueryIndex(isExpanded ? null : index)}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className={cn('text-sm font-mono text-foreground/90', !isExpanded && 'line-clamp-2')}>
                          {query.query_preview}
                        </p>
                        {isExpanded ? (
                          <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
                        ) : (
                          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                        )}
                      </div>
                    </button>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                      <div className="rounded-lg bg-muted/40 px-3 py-2">
                        <p className="text-xs text-muted-foreground">Total time</p>
                        <p className="font-mono font-medium">{formatDuration(query.total_exec_time_ms)}</p>
                      </div>
                      <div className="rounded-lg bg-muted/40 px-3 py-2">
                        <p className="text-xs text-muted-foreground">Mean time</p>
                        <p className="font-mono font-medium">{formatDuration(query.mean_exec_time_ms)}</p>
                      </div>
                      <div className="rounded-lg bg-muted/40 px-3 py-2">
                        <p className="text-xs text-muted-foreground">Calls</p>
                        <p className="font-mono font-medium">{query.calls.toLocaleString()}</p>
                      </div>
                      <div className="rounded-lg bg-muted/40 px-3 py-2">
                        <p className="text-xs text-muted-foreground">Max time</p>
                        <p className="font-mono font-medium">{formatDuration(query.max_exec_time_ms)}</p>
                      </div>
                      {query.cache_hit_pct != null && (
                        <div className="col-span-2 sm:col-span-4 rounded-lg bg-muted/40 px-3 py-2">
                          <p className="text-xs text-muted-foreground">Buffer cache hit</p>
                          <p
                            className={cn(
                              'font-mono font-medium',
                              query.cache_hit_pct >= 95
                                ? 'text-emerald-400'
                                : query.cache_hit_pct >= 80
                                  ? 'text-primary'
                                  : 'text-rose-400',
                            )}
                          >
                            {query.cache_hit_pct.toFixed(1)}%
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
