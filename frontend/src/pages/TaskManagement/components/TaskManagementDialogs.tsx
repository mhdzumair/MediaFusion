import { Badge } from '@/components/ui/badge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Activity } from 'lucide-react'

import type { TaskRecord } from '@/lib/api'
import type { ScraperMetricsData, ScraperHistoryResponse, ScraperMetricsSummary } from '@/lib/api/metrics'
import {
  formatDate,
  formatDuration,
  formatDurationMs,
  formatMediaLabel,
  statusBadgeClass,
} from '../taskManagementUtils'

interface TaskDetailsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedTaskId: string | null
  isLoading: boolean
  task: TaskRecord | undefined
}

export function TaskDetailsDialog({ open, onOpenChange, selectedTaskId, isLoading, task }: TaskDetailsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[760px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            Task Details
          </DialogTitle>
          <DialogDescription>{selectedTaskId}</DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <Skeleton className="h-40 rounded-lg" />
        ) : task ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Actor</p>
                <p>{task.actor_name || '—'}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Queue</p>
                <p>{task.queue_name || '—'}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Created</p>
                <p>{formatDate(task.created_at)}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Finished</p>
                <p>{formatDate(task.finished_at)}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Status</p>
                <Badge variant="outline" className={statusBadgeClass(task.status)}>
                  {task.cancellation_requested && task.status !== 'cancelled'
                    ? `${task.status} (cancel requested)`
                    : task.status}
                </Badge>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Duration</p>
                <p>{formatDurationMs(task.duration_ms)}</p>
              </div>
            </div>

            {task.error_message && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500">
                <p className="font-medium mb-1">{task.error_type || 'Error'}</p>
                <p>{task.error_message}</p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs text-muted-foreground mb-2">Arguments</p>
                <ScrollArea className="h-40 rounded-lg bg-muted/40 p-3 font-mono text-xs">
                  <pre>{JSON.stringify(task.args_preview, null, 2)}</pre>
                </ScrollArea>
              </div>
              <div>
                <p className="text-xs text-muted-foreground mb-2">Keyword Arguments</p>
                <ScrollArea className="h-40 rounded-lg bg-muted/40 p-3 font-mono text-xs">
                  <pre>{JSON.stringify(task.kwargs_preview, null, 2)}</pre>
                </ScrollArea>
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Task details are unavailable.</p>
        )}
      </DialogContent>
    </Dialog>
  )
}

interface ScraperDetailsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  scraperName: string | null
  scraper: ScraperMetricsData | null
  historyLoading: boolean
  history: ScraperHistoryResponse | undefined
}

export function ScraperDetailsDialog({
  open,
  onOpenChange,
  scraperName,
  scraper,
  historyLoading,
  history,
}: ScraperDetailsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[900px]">
        <DialogHeader>
          <DialogTitle>Scraper Full Summary</DialogTitle>
          <DialogDescription>{scraperName || 'No scraper selected'}</DialogDescription>
        </DialogHeader>
        {scraper ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Total Runs</p>
                <p>{scraper.aggregated?.total_runs ?? 0}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Success Rate</p>
                <p>{scraper.aggregated?.success_rate?.toFixed(1) ?? '0'}%</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Latest Duration</p>
                <p>{formatDuration(scraper.latest?.duration_seconds ?? 0)}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Latest Timestamp</p>
                <p>{formatDate(scraper.latest?.timestamp || null)}</p>
              </div>
            </div>

            <div className="rounded-lg border border-border/50 p-3">
              <p className="text-sm font-medium mb-2">Latest Structured Summary</p>
              <ScrollArea className="h-56 rounded-lg bg-muted/30 p-3 font-mono text-xs">
                <pre>{JSON.stringify(scraper.latest, null, 2)}</pre>
              </ScrollArea>
            </div>

            <div className="rounded-lg border border-border/50 p-3">
              <p className="text-sm font-medium mb-2">Formatted Summary (console equivalent)</p>
              <ScrollArea className="h-56 rounded-lg bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap">
                <pre>{scraper.latest?.formatted_summary || 'No formatted summary available.'}</pre>
              </ScrollArea>
            </div>

            <div className="rounded-lg border border-border/50 p-3">
              <p className="text-sm font-medium mb-2">Recent Runs</p>
              {historyLoading ? (
                <Skeleton className="h-40 rounded-lg" />
              ) : (
                <div className="space-y-2">
                  {history?.history.map((run, index) => (
                    <div key={`${run.timestamp}-${index}`} className="rounded-lg bg-muted/30 p-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span>{formatDate(run.timestamp)}</span>
                        <Badge
                          variant="outline"
                          className={statusBadgeClass(run.total_items.errors > 0 ? 'error' : 'success')}
                        >
                          {run.total_items.errors > 0 ? 'errors' : 'ok'}
                        </Badge>
                      </div>
                      <div className="mt-1 text-muted-foreground">
                        found {run.total_items.found} | processed {run.total_items.processed} | skipped{' '}
                        {run.total_items.skipped} | errors {run.total_items.errors} | duration{' '}
                        {formatDuration(run.duration_seconds)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No scraper selected.</p>
        )}
      </DialogContent>
    </Dialog>
  )
}

interface SearchRunDetailsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  run: ScraperMetricsSummary | null
}

export function SearchRunDetailsDialog({ open, onOpenChange, run }: SearchRunDetailsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[900px]">
        <DialogHeader>
          <DialogTitle>Media Search Run Details</DialogTitle>
          <DialogDescription>{run ? formatMediaLabel(run) : 'No run selected'}</DialogDescription>
        </DialogHeader>
        {run ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Scraper</p>
                <p>{run.scraper_name}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Timestamp</p>
                <p>{formatDate(run.timestamp)}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Duration</p>
                <p>{formatDuration(run.duration_seconds)}</p>
              </div>
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground">Items</p>
                <p>
                  found {run.total_items.found} | processed {run.total_items.processed} | skipped{' '}
                  {run.total_items.skipped} | errors {run.total_items.errors}
                </p>
              </div>
            </div>

            <div className="rounded-lg border border-border/50 p-3">
              <p className="text-sm font-medium mb-2">Formatted Summary (console equivalent)</p>
              <ScrollArea className="h-56 rounded-lg bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap">
                <pre>{run.formatted_summary || 'No formatted summary available.'}</pre>
              </ScrollArea>
            </div>

            <div className="rounded-lg border border-border/50 p-3">
              <p className="text-sm font-medium mb-2">Structured Summary</p>
              <ScrollArea className="h-56 rounded-lg bg-muted/30 p-3 font-mono text-xs">
                <pre>{JSON.stringify(run, null, 2)}</pre>
              </ScrollArea>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No run selected.</p>
        )}
      </DialogContent>
    </Dialog>
  )
}

interface BulkActionConfirmDialogProps {
  open: boolean
  action: 'cancel' | 'retry' | null
  taskCount: number
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

export function BulkActionConfirmDialog({
  open,
  action,
  taskCount,
  onOpenChange,
  onConfirm,
}: BulkActionConfirmDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {action === 'cancel' ? 'Confirm bulk cancellation' : 'Confirm bulk retry'}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {action === 'cancel'
              ? `This will request cancellation for ${taskCount} visible task(s).`
              : `This will retry ${taskCount} visible failed/cancelled task(s).`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            {action === 'cancel' ? 'Confirm Cancel' : 'Confirm Retry'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
