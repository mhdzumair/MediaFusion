import { useState } from 'react'
import {
  Bug,
  ChevronLeft,
  ChevronRight,
  Clock,
  Hash,
  Loader2,
  RefreshCw,
  Trash2,
  AlertTriangle,
  Copy,
  Check,
  X,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import { useToast } from '@/hooks/use-toast'
import {
  useExceptionStatus,
  useExceptionList,
  useExceptionDetail,
  useClearException,
  useClearAllExceptions,
  exceptionKeys,
} from '@/hooks/useExceptions'
import type { ExceptionSummary } from '@/lib/api/exceptions'

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

function formatDate(isoString: string): string {
  return new Date(isoString).toLocaleString()
}

// ============================================
// Exception Detail Dialog
// ============================================

function ExceptionDetailDialog({
  fingerprint,
  open,
  onClose,
}: {
  fingerprint: string | null
  open: boolean
  onClose: () => void
}) {
  const { data, isLoading } = useExceptionDetail(open ? fingerprint : null)
  const clearMutation = useClearException()
  const { toast } = useToast()
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    if (!data?.traceback) return
    await navigator.clipboard.writeText(data.traceback)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleClear = () => {
    if (!fingerprint) return
    clearMutation.mutate(fingerprint, {
      onSuccess: () => {
        toast({ title: 'Exception cleared', description: 'The exception has been removed.' })
        onClose()
      },
      onError: (err) => {
        toast({
          title: 'Error',
          description: err instanceof Error ? err.message : 'Failed to clear',
          variant: 'destructive',
        })
      },
    })
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Bug className="h-5 w-5 text-destructive" />
            Exception Detail
          </DialogTitle>
          <DialogDescription>
            {data
              ? `${data.type}: ${data.message.slice(0, 100)}${data.message.length > 100 ? '...' : ''}`
              : 'Loading...'}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : data ? (
          <div className="flex-1 min-h-0 space-y-4">
            {/* Meta row */}
            <div className="flex flex-wrap gap-2">
              <Badge variant="destructive">{data.type}</Badge>
              <Badge variant="outline" className="font-mono text-xs">
                {data.source}
              </Badge>
              <Badge variant="muted">
                <Hash className="h-3 w-3 mr-1" />
                {data.count}x
              </Badge>
            </div>

            {/* Timestamps */}
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Clock className="h-3.5 w-3.5" />
                <span>First: {formatDate(data.first_seen)}</span>
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <Clock className="h-3.5 w-3.5" />
                <span>Last: {formatDate(data.last_seen)}</span>
              </div>
            </div>

            {/* Message */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Message</p>
              <p className="text-sm bg-muted/50 rounded-md p-3 break-all">{data.message}</p>
            </div>

            {/* Traceback */}
            <div className="flex-1 min-h-0">
              <div className="flex items-center justify-between mb-1">
                <p className="text-xs font-medium text-muted-foreground">Traceback</p>
                <Button variant="ghost" size="sm" className="h-7 text-xs gap-1" onClick={handleCopy}>
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {copied ? 'Copied' : 'Copy'}
                </Button>
              </div>
              <ScrollArea className="h-[280px] rounded-md border border-border/50 bg-zinc-950">
                <pre className="p-3 text-xs text-zinc-300 font-mono whitespace-pre-wrap break-all leading-relaxed">
                  {data.traceback}
                </pre>
              </ScrollArea>
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={onClose}>
                Close
              </Button>
              <Button variant="destructive" size="sm" onClick={handleClear} disabled={clearMutation.isPending}>
                {clearMutation.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5 mr-1" />
                )}
                Clear
              </Button>
            </div>
          </div>
        ) : (
          <div className="py-8 text-center text-muted-foreground">Exception not found. It may have expired.</div>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ============================================
// Exception Row
// ============================================

function ExceptionRow({
  item,
  onClick,
  onClear,
  isClearing,
}: {
  item: ExceptionSummary
  onClick: () => void
  onClear: () => void
  isClearing: boolean
}) {
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
            <Badge variant="destructive" className="text-[11px]">
              {item.type}
            </Badge>
            <Badge variant="muted" className="text-[11px] font-mono gap-1">
              <Hash className="h-2.5 w-2.5" />
              {item.count}
            </Badge>
            <span className="text-[11px] text-muted-foreground">{timeAgo(item.last_seen)}</span>
          </div>
          <p className="text-sm truncate text-foreground">{item.message}</p>
          <p className="text-xs text-muted-foreground font-mono truncate">{item.source}</p>
        </div>

        {/* Clear button */}
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
          onClick={(e) => {
            e.stopPropagation()
            onClear()
          }}
          disabled={isClearing}
        >
          {isClearing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <X className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
          )}
        </Button>
      </div>
    </div>
  )
}

// ============================================
// Main Page
// ============================================

export function ExceptionTrackerPage() {
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [typeFilter, setTypeFilter] = useState('')
  const [selectedFp, setSelectedFp] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const { toast } = useToast()
  const queryClient = useQueryClient()

  const { data: status, isLoading: statusLoading } = useExceptionStatus()
  const {
    data: listData,
    isLoading: listLoading,
    isFetching,
  } = useExceptionList({
    page,
    per_page: perPage,
    exception_type: typeFilter || undefined,
  })
  const clearMutation = useClearException()
  const clearAllMutation = useClearAllExceptions()

  const handleViewDetail = (fp: string) => {
    setSelectedFp(fp)
    setDialogOpen(true)
  }

  const handleClearOne = (fp: string) => {
    clearMutation.mutate(fp, {
      onSuccess: () => {
        toast({ title: 'Cleared', description: 'Exception removed.' })
      },
      onError: (err) => {
        toast({ title: 'Error', description: err instanceof Error ? err.message : 'Failed', variant: 'destructive' })
      },
    })
  }

  const handleClearAll = () => {
    clearAllMutation.mutate(undefined, {
      onSuccess: (res) => {
        toast({ title: 'All cleared', description: `Removed ${res.cleared} exception(s).` })
        setPage(1)
      },
      onError: (err) => {
        toast({ title: 'Error', description: err instanceof Error ? err.message : 'Failed', variant: 'destructive' })
      },
    })
  }

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: exceptionKeys.all })
  }

  // Disabled state
  if (!statusLoading && status && !status.enabled) {
    return (
      <div className="space-y-6 p-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-primary/10">
            <Bug className="h-6 w-6 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">Exception Tracker</h1>
            <p className="text-muted-foreground">Monitor and analyze server exceptions</p>
          </div>
        </div>

        <Card>
          <CardContent className="p-6">
            <div className="flex flex-col items-center justify-center py-12 text-center space-y-3">
              <AlertTriangle className="h-10 w-10 text-muted-foreground/50" />
              <p className="text-lg font-medium">Exception tracking is disabled</p>
              <p className="text-sm text-muted-foreground max-w-md">
                Set the{' '}
                <code className="px-1.5 py-0.5 rounded bg-muted font-mono text-xs">ENABLE_EXCEPTION_TRACKING=true</code>{' '}
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
            <Bug className="h-6 w-6 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">Exception Tracker</h1>
            <p className="text-muted-foreground">Monitor and analyze server exceptions</p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {status && (
            <>
              <Badge variant="outline" className="font-mono text-xs px-2 py-0.5">
                {status.total_tracked} tracked
              </Badge>
              <Badge variant="muted" className="text-xs px-2 py-0.5">
                TTL: {Math.floor(status.ttl_seconds / 86400)}d
              </Badge>
            </>
          )}
          <Button variant="outline" size="icon" className="h-8 w-8" onClick={handleRefresh} disabled={isFetching}>
            <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* Filters + Actions */}
      <Card>
        <CardContent className="p-4">
          <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
            {/* Type filter */}
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <Input
                placeholder="Filter by exception type..."
                value={typeFilter}
                onChange={(e) => {
                  setTypeFilter(e.target.value)
                  setPage(1)
                }}
                className="h-8 max-w-xs text-sm"
              />
              {typeFilter && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  onClick={() => {
                    setTypeFilter('')
                    setPage(1)
                  }}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>

            {/* Per-page */}
            <div className="flex items-center gap-2">
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

            {/* Clear all */}
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  variant="destructive"
                  size="sm"
                  className="gap-1.5"
                  disabled={!listData || listData.total === 0 || clearAllMutation.isPending}
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
                  <AlertDialogTitle>Clear all exceptions?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This will permanently remove all {listData?.total ?? 0} tracked exception(s) from Redis. This action
                    cannot be undone.
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
        </CardContent>
      </Card>

      {/* Exception List */}
      <Card>
        <CardContent className="p-4">
          {statusLoading || listLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : !listData || listData.items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center space-y-2">
              <Bug className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                {typeFilter ? 'No exceptions match this filter.' : 'No exceptions have been recorded.'}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {listData.items.map((item) => (
                <ExceptionRow
                  key={item.fingerprint}
                  item={item}
                  onClick={() => handleViewDetail(item.fingerprint)}
                  onClear={() => handleClearOne(item.fingerprint)}
                  isClearing={clearMutation.isPending && clearMutation.variables === item.fingerprint}
                />
              ))}
            </div>
          )}

          {/* Pagination */}
          {listData && listData.pages > 1 && (
            <div className="flex items-center justify-between pt-4 border-t border-border/50 mt-4">
              <span className="text-xs text-muted-foreground">
                {listData.total} exception{listData.total !== 1 ? 's' : ''} total
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
                    disabled={page <= 1}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-7 w-7"
                    disabled={page >= listData.pages}
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
      <ExceptionDetailDialog
        fingerprint={selectedFp}
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false)
          setSelectedFp(null)
        }}
      />
    </div>
  )
}
