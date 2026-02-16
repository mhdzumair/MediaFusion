import { useState } from 'react'
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  Clock,
  Loader2,
  RefreshCw,
  Trash2,
  AlertTriangle,
  ArrowUpDown,
  Zap,
  AlertCircle,
  Server,
  Users,
  X,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast } from '@/hooks/use-toast'
import {
  useRequestMetricsStatus,
  useEndpointStats,
  useEndpointDetail,
  useRecentRequests,
  useClearRequestMetrics,
  requestMetricsKeys,
} from '@/hooks/useRequestMetrics'
import type { EndpointStatsSummary, RecentRequestItem } from '@/lib/api/requestMetrics'

// ============================================
// Helpers
// ============================================

function timeAgo(isoString: string): string {
  const date = new Date(isoString)
  const now = new Date()
  const seconds = Math.floor((now.getTime() - date.getTime()) / 1000)

  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function formatDuration(seconds: number): string {
  if (seconds < 0.001) return `${(seconds * 1_000_000).toFixed(0)}us`
  if (seconds < 1) return `${(seconds * 1000).toFixed(1)}ms`
  return `${seconds.toFixed(2)}s`
}

function statusBadgeVariant(code: number): 'success' | 'warning' | 'destructive' | 'muted' {
  if (code >= 200 && code < 300) return 'success'
  if (code >= 300 && code < 400) return 'muted'
  if (code >= 400 && code < 500) return 'warning'
  return 'destructive'
}

function methodBadgeVariant(method: string): 'default' | 'info' | 'warning' | 'destructive' | 'success' {
  switch (method.toUpperCase()) {
    case 'GET':
      return 'info'
    case 'POST':
      return 'success'
    case 'PUT':
    case 'PATCH':
      return 'warning'
    case 'DELETE':
      return 'destructive'
    default:
      return 'default'
  }
}

// ============================================
// Endpoint Detail Dialog
// ============================================

function EndpointDetailDialog({
  method,
  route,
  open,
  onClose,
}: {
  method: string | null
  route: string | null
  open: boolean
  onClose: () => void
}) {
  const { data, isLoading } = useEndpointDetail(open ? method : null, open ? route : null)

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            Endpoint Detail
          </DialogTitle>
          <DialogDescription>
            {data ? `${data.method} ${data.route}` : 'Loading...'}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : data ? (
          <div className="flex-1 min-h-0 space-y-4">
            {/* Method + Route */}
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant={methodBadgeVariant(data.method)}>{data.method}</Badge>
              <span className="font-mono text-sm">{data.route}</span>
            </div>

            {/* Request count + Unique visitors + Last seen */}
            <div className="grid grid-cols-3 gap-3 text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Server className="h-3.5 w-3.5" />
                <span>{data.total_requests.toLocaleString()} requests</span>
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <Users className="h-3.5 w-3.5" />
                <span>{(data.unique_visitors ?? 0).toLocaleString()} unique visitors</span>
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <Clock className="h-3.5 w-3.5" />
                <span>Last: {timeAgo(data.last_seen)}</span>
              </div>
            </div>

            {/* Latency Percentiles */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-2">Latency Percentiles</p>
              <div className="grid grid-cols-3 gap-3">
                <Card>
                  <CardContent className="p-3 text-center">
                    <p className="text-xs text-muted-foreground">p50</p>
                    <p className="text-lg font-semibold">{formatDuration(data.p50)}</p>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-3 text-center">
                    <p className="text-xs text-muted-foreground">p95</p>
                    <p className="text-lg font-semibold">{formatDuration(data.p95)}</p>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="p-3 text-center">
                    <p className="text-xs text-muted-foreground">p99</p>
                    <p className="text-lg font-semibold">{formatDuration(data.p99)}</p>
                  </CardContent>
                </Card>
              </div>
            </div>

            {/* Timing Stats */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-2">Response Times</p>
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-muted/50 rounded-md p-3 text-center">
                  <p className="text-xs text-muted-foreground">Avg</p>
                  <p className="text-sm font-medium">{formatDuration(data.avg_time)}</p>
                </div>
                <div className="bg-muted/50 rounded-md p-3 text-center">
                  <p className="text-xs text-muted-foreground">Min</p>
                  <p className="text-sm font-medium">{formatDuration(data.min_time)}</p>
                </div>
                <div className="bg-muted/50 rounded-md p-3 text-center">
                  <p className="text-xs text-muted-foreground">Max</p>
                  <p className="text-sm font-medium">{formatDuration(data.max_time)}</p>
                </div>
              </div>
            </div>

            {/* Status Code Breakdown */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-2">Status Code Breakdown</p>
              <div className="flex flex-wrap gap-2">
                {data.status_2xx > 0 && (
                  <Badge variant="success">2xx: {data.status_2xx.toLocaleString()}</Badge>
                )}
                {data.status_3xx > 0 && (
                  <Badge variant="muted">3xx: {data.status_3xx.toLocaleString()}</Badge>
                )}
                {data.status_4xx > 0 && (
                  <Badge variant="warning">4xx: {data.status_4xx.toLocaleString()}</Badge>
                )}
                {data.status_5xx > 0 && (
                  <Badge variant="destructive">5xx: {data.status_5xx.toLocaleString()}</Badge>
                )}
              </div>
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={onClose}>
                Close
              </Button>
            </div>
          </div>
        ) : (
          <div className="py-8 text-center text-muted-foreground">Endpoint metrics not found. They may have expired.</div>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ============================================
// Endpoint Row
// ============================================

function EndpointRow({
  item,
  onClick,
}: {
  item: EndpointStatsSummary
  onClick: () => void
}) {
  const errorRate = item.total_requests > 0 ? ((item.error_count / item.total_requests) * 100) : 0

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      className="w-full p-3 md:p-4 rounded-lg border border-border/50 bg-card/50 hover:bg-muted/50 transition-all hover:border-border text-left group cursor-pointer"
    >
      <div className="flex items-start justify-between gap-3">
        {/* Left side */}
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant={methodBadgeVariant(item.method)} className="text-[11px]">
              {item.method}
            </Badge>
            <span className="text-sm font-mono truncate">{item.route}</span>
          </div>
          <div className="flex items-center gap-3 flex-wrap text-[11px] text-muted-foreground">
            <span>{item.total_requests.toLocaleString()} requests</span>
            <span>{(item.unique_visitors ?? 0).toLocaleString()} visitors</span>
            <span>avg {formatDuration(item.avg_time)}</span>
            {errorRate > 0 && (
              <span className="text-destructive">{errorRate.toFixed(1)}% errors</span>
            )}
            <span>{timeAgo(item.last_seen)}</span>
          </div>
        </div>

        {/* Right side - quick stats */}
        <div className="flex items-center gap-2 shrink-0">
          {item.status_2xx > 0 && <Badge variant="success" className="text-[10px]">{item.status_2xx}</Badge>}
          {item.status_4xx > 0 && <Badge variant="warning" className="text-[10px]">{item.status_4xx}</Badge>}
          {item.status_5xx > 0 && <Badge variant="destructive" className="text-[10px]">{item.status_5xx}</Badge>}
        </div>
      </div>
    </div>
  )
}

// ============================================
// Recent Request Row
// ============================================

function RecentRequestRow({ item }: { item: RecentRequestItem }) {
  return (
    <div className="w-full p-3 rounded-lg border border-border/50 bg-card/50">
      <div className="flex items-center gap-3 flex-wrap">
        <Badge variant={methodBadgeVariant(item.method)} className="text-[11px]">
          {item.method}
        </Badge>
        <Badge variant={statusBadgeVariant(item.status_code)} className="text-[11px]">
          {item.status_code}
        </Badge>
        <span className="text-sm font-mono truncate flex-1 min-w-0">{item.path}</span>
        <span className="text-[11px] text-muted-foreground shrink-0">
          {formatDuration(item.process_time)}
        </span>
        <span className="text-[11px] text-muted-foreground shrink-0">
          {timeAgo(item.timestamp)}
        </span>
      </div>
    </div>
  )
}

// ============================================
// Endpoints Tab
// ============================================

function EndpointsTab() {
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [sortBy, setSortBy] = useState('total_requests')
  const [sortOrder, setSortOrder] = useState('desc')
  const [selectedMethod, setSelectedMethod] = useState<string | null>(null)
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const {
    data: listData,
    isLoading,
    isFetching,
  } = useEndpointStats({
    page,
    per_page: perPage,
    sort_by: sortBy,
    sort_order: sortOrder,
  })

  const handleViewDetail = (method: string, route: string) => {
    setSelectedMethod(method)
    setSelectedRoute(route)
    setDialogOpen(true)
  }

  const toggleSort = (field: string) => {
    if (sortBy === field) {
      setSortOrder((prev) => (prev === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortBy(field)
      setSortOrder('desc')
    }
    setPage(1)
  }

  return (
    <>
      {/* Sort controls */}
      <div className="flex items-center gap-2 flex-wrap mb-4">
        <span className="text-xs text-muted-foreground">Sort by:</span>
        {[
          { key: 'total_requests', label: 'Requests' },
          { key: 'unique_visitors', label: 'Visitors' },
          { key: 'avg_time', label: 'Avg Time' },
          { key: 'error_count', label: 'Errors' },
          { key: 'max_time', label: 'Max Time' },
        ].map((opt) => (
          <Button
            key={opt.key}
            variant={sortBy === opt.key ? 'default' : 'outline'}
            size="sm"
            className="h-7 text-xs gap-1"
            onClick={() => toggleSort(opt.key)}
          >
            {opt.label}
            {sortBy === opt.key && (
              <ArrowUpDown className="h-3 w-3" />
            )}
          </Button>
        ))}

        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted-foreground whitespace-nowrap">Per page:</span>
          <Select
            value={String(perPage)}
            onValueChange={(v) => {
              setPerPage(Number(v))
              setPage(1)
            }}
          >
            <SelectTrigger className="w-[65px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="10">10</SelectItem>
              <SelectItem value="20">20</SelectItem>
              <SelectItem value="50">50</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Endpoint List */}
      <Card>
        <CardContent className="p-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : !listData || listData.items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center space-y-2">
              <Activity className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">No endpoint metrics have been recorded yet.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {listData.items.map((item) => (
                <EndpointRow
                  key={item.endpoint_key}
                  item={item}
                  onClick={() => handleViewDetail(item.method, item.route)}
                />
              ))}
            </div>
          )}

          {/* Pagination */}
          {listData && listData.pages > 1 && (
            <div className="flex items-center justify-between pt-4 border-t border-border/50 mt-4">
              <span className="text-xs text-muted-foreground">
                {listData.total} endpoint{listData.total !== 1 ? 's' : ''} total
              </span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  Page {page} of {listData.pages}
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-7 w-7"
                    disabled={page <= 1 || isFetching}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-7 w-7"
                    disabled={page >= listData.pages || isFetching}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Detail Dialog */}
      <EndpointDetailDialog
        method={selectedMethod}
        route={selectedRoute}
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false)
          setSelectedMethod(null)
          setSelectedRoute(null)
        }}
      />
    </>
  )
}

// ============================================
// Recent Requests Tab
// ============================================

function RecentRequestsTab() {
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [methodFilter, setMethodFilter] = useState('')
  const [routeFilter, setRouteFilter] = useState('')

  const {
    data: listData,
    isLoading,
    isFetching,
  } = useRecentRequests({
    page,
    per_page: perPage,
    method: methodFilter || undefined,
    route: routeFilter || undefined,
  })

  return (
    <>
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap mb-4">
        <Select
          value={methodFilter}
          onValueChange={(v) => {
            setMethodFilter(v === 'all' ? '' : v)
            setPage(1)
          }}
        >
          <SelectTrigger className="w-[100px] h-8 text-xs">
            <SelectValue placeholder="Method" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Methods</SelectItem>
            <SelectItem value="GET">GET</SelectItem>
            <SelectItem value="POST">POST</SelectItem>
            <SelectItem value="PUT">PUT</SelectItem>
            <SelectItem value="PATCH">PATCH</SelectItem>
            <SelectItem value="DELETE">DELETE</SelectItem>
          </SelectContent>
        </Select>

        <div className="flex items-center gap-2 flex-1 min-w-0">
          <Input
            placeholder="Filter by route..."
            value={routeFilter}
            onChange={(e) => {
              setRouteFilter(e.target.value)
              setPage(1)
            }}
            className="h-8 max-w-xs text-sm"
          />
          {routeFilter && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0"
              onClick={() => {
                setRouteFilter('')
                setPage(1)
              }}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted-foreground whitespace-nowrap">Per page:</span>
          <Select
            value={String(perPage)}
            onValueChange={(v) => {
              setPerPage(Number(v))
              setPage(1)
            }}
          >
            <SelectTrigger className="w-[65px] h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="10">10</SelectItem>
              <SelectItem value="20">20</SelectItem>
              <SelectItem value="50">50</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Request List */}
      <Card>
        <CardContent className="p-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : !listData || listData.items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center space-y-2">
              <Clock className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                {routeFilter || methodFilter
                  ? 'No requests match the current filters.'
                  : 'No recent requests have been recorded.'}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {listData.items.map((item) => (
                <RecentRequestRow key={item.request_id} item={item} />
              ))}
            </div>
          )}

          {/* Pagination */}
          {listData && listData.pages > 1 && (
            <div className="flex items-center justify-between pt-4 border-t border-border/50 mt-4">
              <span className="text-xs text-muted-foreground">
                {listData.total} request{listData.total !== 1 ? 's' : ''} total
              </span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  Page {page} of {listData.pages}
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-7 w-7"
                    disabled={page <= 1 || isFetching}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-7 w-7"
                    disabled={page >= listData.pages || isFetching}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  )
}

// ============================================
// Main Page
// ============================================

export function RequestMetricsPage() {
  const { toast } = useToast()
  const queryClient = useQueryClient()

  const { data: status, isLoading: statusLoading } = useRequestMetricsStatus()
  const clearAllMutation = useClearRequestMetrics()

  const handleClearAll = () => {
    clearAllMutation.mutate(undefined, {
      onSuccess: (res) => {
        toast({ title: 'All cleared', description: `Removed ${res.cleared} metric key(s).` })
      },
      onError: (err) => {
        toast({ title: 'Error', description: err instanceof Error ? err.message : 'Failed', variant: 'destructive' })
      },
    })
  }

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: requestMetricsKeys.all })
  }

  // Disabled state
  if (!statusLoading && status && !status.enabled) {
    return (
      <div className="space-y-6 p-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-primary/10">
            <Activity className="h-6 w-6 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">Request Metrics</h1>
            <p className="text-muted-foreground">Monitor API request timing and usage</p>
          </div>
        </div>

        <Card>
          <CardContent className="p-6">
            <div className="flex flex-col items-center justify-center py-12 text-center space-y-3">
              <AlertTriangle className="h-10 w-10 text-muted-foreground/50" />
              <p className="text-lg font-medium">Request metrics tracking is disabled</p>
              <p className="text-sm text-muted-foreground max-w-md">
                Set the{' '}
                <code className="px-1.5 py-0.5 rounded bg-muted font-mono text-xs">ENABLE_REQUEST_METRICS=true</code>{' '}
                environment variable to enable this feature.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-primary/10">
            <Activity className="h-6 w-6 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">Request Metrics</h1>
            <p className="text-muted-foreground">Monitor API request timing and usage</p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Button variant="outline" size="icon" className="h-8 w-8" onClick={handleRefresh}>
            <RefreshCw className="h-4 w-4" />
          </Button>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="destructive"
                size="sm"
                className="gap-1.5"
                disabled={clearAllMutation.isPending}
              >
                {clearAllMutation.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
                Clear All
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Clear all request metrics?</AlertDialogTitle>
                <AlertDialogDescription>
                  This will permanently remove all tracked endpoint statistics and recent request logs from Redis.
                  This action cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleClearAll}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  Clear All
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {/* Summary Cards */}
      {statusLoading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : status ? (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <Server className="h-3.5 w-3.5" />
                Total Requests
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <p className="text-2xl font-bold">{status.total_requests.toLocaleString()}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <Users className="h-3.5 w-3.5" />
                Unique Visitors
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <p className="text-2xl font-bold">{(status.unique_visitors ?? 0).toLocaleString()}</p>
              <p className="text-xs text-muted-foreground">approx, today</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <Zap className="h-3.5 w-3.5" />
                Endpoints Tracked
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <p className="text-2xl font-bold">{status.total_endpoints}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <Clock className="h-3.5 w-3.5" />
                Recent Requests
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <p className="text-2xl font-bold">{status.total_recent.toLocaleString()}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2 pt-4 px-4">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <AlertCircle className="h-3.5 w-3.5" />
                TTL
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <p className="text-2xl font-bold">
                {status.ttl_seconds >= 86400
                  ? `${Math.floor(status.ttl_seconds / 86400)}d`
                  : `${Math.floor(status.ttl_seconds / 3600)}h`}
              </p>
              <p className="text-xs text-muted-foreground">
                Recent: {status.recent_ttl_seconds >= 3600
                  ? `${Math.floor(status.recent_ttl_seconds / 3600)}h`
                  : `${Math.floor(status.recent_ttl_seconds / 60)}m`}
              </p>
            </CardContent>
          </Card>
        </div>
      ) : null}

      {/* Tabs */}
      <Tabs defaultValue="endpoints">
        <TabsList>
          <TabsTrigger value="endpoints">Endpoints</TabsTrigger>
          <TabsTrigger value="recent">Recent Requests</TabsTrigger>
        </TabsList>

        <TabsContent value="endpoints" className="mt-4">
          <EndpointsTab />
        </TabsContent>

        <TabsContent value="recent" className="mt-4">
          <RecentRequestsTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}
