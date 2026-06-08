import { useMemo, useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Calendar,
  Clock,
  Play,
  RefreshCw,
  Search,
  Filter,
  Zap,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Server,
  Rss,
  Settings,
  Bug,
  Database,
  ChevronRight,
  Power,
  PowerOff,
  FlaskConical,
  Save,
  Radio,
} from 'lucide-react'
import {
  useSchedulerJobs,
  useSchedulerStats,
  useSchedulerJob,
  useRunSchedulerJob,
  useRunSchedulerJobInline,
  useSchedulerJobHistory,
  useSchedulerJobLogs,
  useSchedulerStreamUpdates,
  useUpdateSchedulerJob,
  useDmmHashlistStatus,
  useRunDmmHashlistFull,
} from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import { ApiRequestError } from '@/lib/api'
import { computeHistoryDurationSeconds } from '@/lib/api/scheduler'
import type { SchedulerCategory, SchedulerJobInfo } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ImdbDatasetImportPanel } from './ImdbDatasetImportPanel'

function getCategoryIcon(category: SchedulerCategory) {
  switch (category) {
    case 'scraper':
      return <Bug className="h-4 w-4" />
    case 'feed':
      return <Rss className="h-4 w-4" />
    case 'maintenance':
      return <Settings className="h-4 w-4" />
    case 'background':
      return <Zap className="h-4 w-4" />
    default:
      return <Server className="h-4 w-4" />
  }
}

function getCategoryColor(category: SchedulerCategory) {
  switch (category) {
    case 'scraper':
      return 'bg-primary/10 text-primary border-primary/30'
    case 'feed':
      return 'bg-blue-500/10 text-blue-500 border-blue-500/30'
    case 'maintenance':
      return 'bg-primary/10 text-primary border-primary/30'
    case 'background':
      return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
    default:
      return 'bg-muted text-muted-foreground'
  }
}

function formatCrontab(crontab: string): string {
  // Simple crontab to human-readable conversion
  const parts = crontab.split(' ')
  if (parts.length !== 5) return crontab

  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts

  if (dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    if (minute === '0' && hour === '*') return 'Every hour'
    if (minute === '*/5') return 'Every 5 minutes'
    if (minute === '*/10') return 'Every 10 minutes'
    if (minute === '*/15') return 'Every 15 minutes'
    if (minute === '*/30') return 'Every 30 minutes'
    if (hour === '*') return `Every hour at :${minute.padStart(2, '0')}`
    return `Daily at ${hour}:${minute.padStart(2, '0')}`
  }

  return crontab
}

function shortSha(sha: string | null | undefined): string {
  if (!sha) return '—'
  return sha.length > 12 ? sha.slice(0, 12) : sha
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

const CRON_PRESETS = [
  { label: 'Every 5 minutes', value: '*/5 * * * *' },
  { label: 'Every 15 minutes', value: '*/15 * * * *' },
  { label: 'Every 30 minutes', value: '*/30 * * * *' },
  { label: 'Every hour', value: '0 * * * *' },
  { label: 'Daily at midnight', value: '0 0 * * *' },
  { label: 'Daily at 3 AM', value: '0 3 * * *' },
]

function historyStatusClass(status: string): string {
  if (status === 'success') return 'bg-emerald-500/10'
  if (status === 'error' || status === 'failed' || status === 'dead') return 'bg-red-500/10'
  if (status === 'running' || status === 'pending') return 'bg-blue-500/10'
  return 'bg-muted/50'
}

// Job Detail Dialog
function JobDetailForm({
  job,
  globalSchedulerDisabled,
  onClose,
}: {
  job: SchedulerJobInfo
  globalSchedulerDisabled: boolean
  onClose: () => void
}) {
  const { data: history, isLoading: historyLoading } = useSchedulerJobHistory(job.id, 10)
  const { data: logs, isLoading: logsLoading } = useSchedulerJobLogs(job.id, 10)
  const updateJob = useUpdateSchedulerJob()
  const { toast } = useToast()
  const [scheduleDraft, setScheduleDraft] = useState(job.crontab)
  const [payloadDraft, setPayloadDraft] = useState(JSON.stringify(job.payload ?? {}, null, 2))

  const handleToggleEnabled = async (enabled: boolean) => {
    try {
      const updated = await updateJob.mutateAsync({ jobId: job.id, payload: { enabled } })
      toast({
        title: updated.is_enabled ? 'Job Enabled' : 'Job Disabled',
        description: `${job.display_name} has been ${updated.is_enabled ? 'enabled' : 'disabled'}.`,
      })
    } catch (error) {
      toast({
        title: 'Update Failed',
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    }
  }

  const handleSaveSchedule = async () => {
    try {
      await updateJob.mutateAsync({ jobId: job.id, payload: { schedule: scheduleDraft.trim() } })
      toast({ title: 'Schedule Updated', description: `${job.display_name} schedule saved.` })
    } catch (error) {
      const message =
        error instanceof ApiRequestError && error.status === 422
          ? error.message
          : error instanceof Error
            ? error.message
            : 'An error occurred'
      toast({ title: 'Invalid Schedule', description: message, variant: 'destructive' })
    }
  }

  const handleSavePayload = async () => {
    try {
      const parsed = JSON.parse(payloadDraft) as Record<string, unknown>
      await updateJob.mutateAsync({ jobId: job.id, payload: { payload: parsed } })
      toast({ title: 'Payload Updated', description: `${job.display_name} payload saved.` })
    } catch (error) {
      const message =
        error instanceof SyntaxError
          ? 'Payload must be valid JSON.'
          : error instanceof Error
            ? error.message
            : 'An error occurred'
      toast({ title: 'Invalid Payload', description: message, variant: 'destructive' })
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          {getCategoryIcon(job.category)}
          {job.display_name}
        </DialogTitle>
        <DialogDescription>{job.description}</DialogDescription>
      </DialogHeader>

      <div className="space-y-6 py-4">
        <div className="flex items-center justify-between rounded-lg border border-border/50 bg-muted/20 p-3">
          <div>
            <p className="text-sm font-medium">Enabled</p>
            <p className="text-xs text-muted-foreground">
              {globalSchedulerDisabled
                ? 'Global scheduler is disabled in server configuration.'
                : 'Toggle whether this job runs on its schedule.'}
            </p>
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Switch
                  checked={job.is_enabled}
                  disabled={globalSchedulerDisabled || updateJob.isPending}
                  onCheckedChange={handleToggleEnabled}
                  aria-label={`Toggle ${job.display_name}`}
                />
              </span>
            </TooltipTrigger>
            {globalSchedulerDisabled && (
              <TooltipContent>
                <p>Enable the global scheduler in server configuration first.</p>
              </TooltipContent>
            )}
          </Tooltip>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">Scheduled</label>
            <div className="flex items-center gap-2">
              {job.is_enabled ? (
                <Badge className="bg-emerald-500/10 text-emerald-500 border-emerald-500/30">
                  <Power className="mr-1 h-3 w-3" />
                  Enabled
                </Badge>
              ) : (
                <Badge className="bg-red-500/10 text-red-500 border-red-500/30">
                  <PowerOff className="mr-1 h-3 w-3" />
                  Disabled
                </Badge>
              )}
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">Running</label>
            <div className="flex items-center gap-2">
              {job.is_running ? (
                <Badge className="bg-blue-500/10 text-blue-500 border-blue-500/30">
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  Running
                </Badge>
              ) : (
                <span className="text-sm text-muted-foreground">—</span>
              )}
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">Last Run</label>
            <p className="text-sm">{job.time_since_last_run}</p>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">Next Run</label>
            <p className="text-sm">{job.is_enabled ? job.next_run_in || '—' : '—'}</p>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground">Edit Schedule</label>
          <Input
            value={scheduleDraft}
            onChange={(event) => setScheduleDraft(event.target.value)}
            className="font-mono text-sm"
            placeholder="*/15 * * * *"
          />
          <p className="text-xs text-muted-foreground">{formatCrontab(scheduleDraft)}</p>
          <div className="flex flex-wrap gap-2">
            {CRON_PRESETS.map((preset) => (
              <Button
                key={preset.value}
                type="button"
                size="sm"
                variant="outline"
                className="rounded-lg text-xs"
                onClick={() => setScheduleDraft(preset.value)}
              >
                {preset.label}
              </Button>
            ))}
          </div>
          <Button
            size="sm"
            onClick={handleSaveSchedule}
            disabled={updateJob.isPending || scheduleDraft.trim() === job.crontab}
          >
            {updateJob.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Save className="mr-2 h-4 w-4" />
            )}
            Save Schedule
          </Button>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground">Edit Payload (JSON)</label>
          <Textarea
            value={payloadDraft}
            onChange={(event) => setPayloadDraft(event.target.value)}
            className="font-mono text-xs min-h-[120px]"
          />
          <Button size="sm" onClick={handleSavePayload} disabled={updateJob.isPending}>
            {updateJob.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Save className="mr-2 h-4 w-4" />
            )}
            Save Payload
          </Button>
        </div>

        {job.last_run_state && (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">Last Run Stats</label>
            <ScrollArea className="h-32 rounded-lg bg-muted/50 p-3 font-mono text-xs">
              <pre>{JSON.stringify(job.last_run_state, null, 2)}</pre>
            </ScrollArea>
          </div>
        )}

        <Tabs defaultValue="history">
          <TabsList>
            <TabsTrigger value="history">History</TabsTrigger>
            <TabsTrigger value="logs">Logs</TabsTrigger>
          </TabsList>
          <TabsContent value="history" className="space-y-2 mt-3">
            {historyLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-8 rounded" />
                ))}
              </div>
            ) : history?.entries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No execution history available</p>
            ) : (
              <ScrollArea className="h-48">
                <div className="space-y-1">
                  {history?.entries.map((entry) => {
                    const duration = computeHistoryDurationSeconds(entry.started_at, entry.finished_at)
                    return (
                      <div
                        key={entry.job_id}
                        className={cn(
                          'flex items-center justify-between p-2 rounded text-xs',
                          historyStatusClass(entry.status),
                        )}
                      >
                        <div className="space-y-0.5">
                          <span className="text-muted-foreground">{formatDateTime(entry.created_at)}</span>
                          {entry.error && <p className="text-red-500 line-clamp-1">{entry.error}</p>}
                        </div>
                        <div className="flex items-center gap-2">
                          {typeof entry.attempts === 'number' && <span>attempt {entry.attempts}</span>}
                          {typeof duration === 'number' && <span>{duration.toFixed(1)}s</span>}
                          {entry.status === 'success' ? (
                            <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                          ) : entry.status === 'error' || entry.status === 'failed' || entry.status === 'dead' ? (
                            <XCircle className="h-3 w-3 text-red-500" />
                          ) : (
                            <Clock className="h-3 w-3" />
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </ScrollArea>
            )}
          </TabsContent>
          <TabsContent value="logs" className="space-y-2 mt-3">
            {logsLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-12 rounded" />
                ))}
              </div>
            ) : logs?.runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No run logs available</p>
            ) : (
              <ScrollArea className="h-48">
                <div className="space-y-2">
                  {logs?.runs.map((run) => (
                    <div key={run.job_id} className={cn('rounded-lg p-2 text-xs', historyStatusClass(run.status))}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-medium">Run #{run.job_id}</span>
                        <span className="text-muted-foreground">{formatDateTime(run.created_at)}</span>
                      </div>
                      {run.events.length === 0 ? (
                        <p className="text-muted-foreground">No events recorded</p>
                      ) : (
                        <div className="space-y-1 pl-2 border-l border-border/50">
                          {run.events.map((event, index) => (
                            <div key={`${run.job_id}-${index}`} className="flex justify-between gap-2">
                              <span>{event.event}</span>
                              <span className="text-muted-foreground shrink-0">
                                {formatDateTime(event.at)}
                                {event.detail ? ` · ${event.detail}` : ''}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            )}
          </TabsContent>
        </Tabs>
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
      </DialogFooter>
    </>
  )
}

function JobDetailDialog({
  job,
  open,
  onOpenChange,
  globalSchedulerDisabled,
}: {
  job: SchedulerJobInfo | null
  open: boolean
  onOpenChange: (open: boolean) => void
  globalSchedulerDisabled: boolean
}) {
  if (!job) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[700px]">
        <JobDetailForm
          key={job.id}
          job={job}
          globalSchedulerDisabled={globalSchedulerDisabled}
          onClose={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  )
}

// Job Row Component
function JobRow({
  job,
  onRun,
  onRunInline,
  onViewDetails,
  isRunning,
  isRunningInline,
  globalSchedulerDisabled,
}: {
  job: SchedulerJobInfo
  onRun: () => void
  onRunInline: () => void
  onViewDetails: () => void
  isRunning: boolean
  isRunningInline: boolean
  globalSchedulerDisabled: boolean
}) {
  const updateJob = useUpdateSchedulerJob()
  const { toast } = useToast()

  const handleToggleEnabled = async (enabled: boolean) => {
    try {
      await updateJob.mutateAsync({ jobId: job.id, payload: { enabled } })
    } catch (error) {
      toast({
        title: 'Update Failed',
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    }
  }

  return (
    <TableRow className="hover:bg-muted/20">
      <TableCell>
        <Tooltip>
          <TooltipTrigger asChild>
            <span>
              <Switch
                checked={job.is_enabled}
                disabled={globalSchedulerDisabled || updateJob.isPending || job.is_running}
                onCheckedChange={handleToggleEnabled}
                aria-label={`Toggle ${job.display_name}`}
              />
            </span>
          </TooltipTrigger>
          {globalSchedulerDisabled && (
            <TooltipContent>
              <p>Global scheduler is disabled in server configuration.</p>
            </TooltipContent>
          )}
        </Tooltip>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-3">
          <div className={cn('p-2 rounded-lg', getCategoryColor(job.category).split(' ')[0])}>
            {getCategoryIcon(job.category)}
          </div>
          <div>
            <p className="font-medium">{job.display_name}</p>
            <p className="text-xs text-muted-foreground line-clamp-1">{job.description}</p>
          </div>
        </div>
      </TableCell>
      <TableCell>
        <Badge variant="outline" className={getCategoryColor(job.category)}>
          {job.category}
        </Badge>
      </TableCell>
      <TableCell>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="font-mono text-xs cursor-help">{job.crontab}</span>
          </TooltipTrigger>
          <TooltipContent>
            <p>{formatCrontab(job.crontab)}</p>
          </TooltipContent>
        </Tooltip>
      </TableCell>
      <TableCell>
        {job.is_enabled ? (
          <Badge className="bg-emerald-500/10 text-emerald-500 border-emerald-500/30">Enabled</Badge>
        ) : (
          <Badge className="bg-red-500/10 text-red-500 border-red-500/30">Disabled</Badge>
        )}
      </TableCell>
      <TableCell>
        {job.is_running ? (
          <Badge className="bg-blue-500/10 text-blue-500 border-blue-500/30">
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            Running
          </Badge>
        ) : (
          <span className="text-sm text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="text-sm text-muted-foreground">{job.time_since_last_run}</TableCell>
      <TableCell className="text-sm text-muted-foreground">{job.next_run_in || '—'}</TableCell>
      <TableCell className="text-right">
        <div className="flex items-center justify-end gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                onClick={onRun}
                disabled={isRunning || isRunningInline || job.is_running}
                className="rounded-lg"
              >
                {isRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Queue for background execution</p>
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                onClick={onRunInline}
                disabled={isRunning || isRunningInline || job.is_running}
                className="rounded-lg text-primary hover:text-primary hover:bg-primary/10"
              >
                {isRunningInline ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Run inline (test mode - blocks until complete)</p>
            </TooltipContent>
          </Tooltip>
          <Button variant="ghost" size="sm" onClick={onViewDetails} className="rounded-lg">
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  )
}

type RunMode = 'queue' | 'inline'

interface ConfirmRunState {
  job: SchedulerJobInfo
  mode: RunMode
}

export function SchedulerPage({ embedded = false }: { embedded?: boolean } = {}) {
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<SchedulerCategory | 'all'>('all')
  const [selectedJob, setSelectedJob] = useState<SchedulerJobInfo | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [confirmRun, setConfirmRun] = useState<ConfirmRunState | null>(null)
  const [forceRun, setForceRun] = useState(false)
  const [confirmDmmFullRunOpen, setConfirmDmmFullRunOpen] = useState(false)
  const [resetDmmCheckpoints, setResetDmmCheckpoints] = useState(false)
  const [liveUpdatesEnabled, setLiveUpdatesEnabled] = useState(true)

  const listParams = useMemo(
    () => ({
      category: categoryFilter === 'all' ? undefined : categoryFilter,
    }),
    [categoryFilter],
  )

  const streamEnabled = liveUpdatesEnabled

  const { data: jobsData, isLoading: jobsQueryLoading, refetch } = useSchedulerJobs(listParams, { streamEnabled })
  const { data: stats, refetch: refetchStats } = useSchedulerStats({ streamEnabled })
  const streamState = useSchedulerStreamUpdates({
    enabled: streamEnabled,
    listParams,
    intervalMs: 3000,
  })
  const selectedJobLive = useMemo(() => {
    if (!selectedJob) return null
    return jobsData?.jobs.find((job) => job.id === selectedJob.id) ?? selectedJob
  }, [jobsData?.jobs, selectedJob])
  const isLoading = streamEnabled ? !jobsData && !streamState.lastEventAt : jobsQueryLoading
  const {
    data: dmmStatus,
    isLoading: dmmStatusLoading,
    isError: dmmStatusError,
    refetch: refetchDmmStatus,
  } = useDmmHashlistStatus()
  const { data: dmmSchedulerJob } = useSchedulerJob('dmm_hashlist_scraper')
  const runJob = useRunSchedulerJob()
  const runJobInline = useRunSchedulerJobInline()
  const runDmmHashlistFull = useRunDmmHashlistFull()
  const { toast } = useToast()

  // Filter jobs by search
  const filteredJobs =
    jobsData?.jobs.filter((job) => {
      if (search) {
        const searchLower = search.toLowerCase()
        return (
          job.display_name.toLowerCase().includes(searchLower) ||
          job.description.toLowerCase().includes(searchLower) ||
          job.id.toLowerCase().includes(searchLower)
        )
      }
      return true
    }) ?? []

  const handleRunJob = async (job: SchedulerJobInfo, mode: RunMode) => {
    try {
      if (mode === 'inline') {
        const result = await runJobInline.mutateAsync(job.id)
        if (result.success) {
          toast({
            title: 'Job Completed',
            description: `${job.display_name} completed in ${result.execution_time_seconds}s`,
          })
        } else {
          toast({
            title: 'Job Failed',
            description: result.error || 'Unknown error',
            variant: 'destructive',
          })
        }
      } else {
        await runJob.mutateAsync({ jobId: job.id, forceRun })
        toast({
          title: forceRun ? 'Job Force-Queued' : 'Job Queued',
          description: forceRun
            ? `${job.display_name} has been force-queued and will bypass interval throttling.`
            : `${job.display_name} has been queued for execution.`,
        })
      }
      setForceRun(false)
      setConfirmRun(null)
    } catch (error) {
      toast({
        title: 'Failed to Run Job',
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    }
  }

  const handleViewDetails = (job: SchedulerJobInfo) => {
    setSelectedJob(job)
    setDetailsOpen(true)
  }

  const handleRunDmmFullIngestion = async () => {
    try {
      await runDmmHashlistFull.mutateAsync({
        sync: false,
        reset_checkpoints: resetDmmCheckpoints,
        max_iterations: 200,
        incremental_commits: 100,
        backfill_commits: 100,
      })
      toast({
        title: 'Full DMM Ingestion Queued',
        description: resetDmmCheckpoints
          ? 'Full ingestion queued with checkpoint reset (fresh backfill).'
          : 'Full ingestion queued. It will continue until backfill completes or guardrails stop it.',
      })
      setConfirmDmmFullRunOpen(false)
      setResetDmmCheckpoints(false)
      refetchDmmStatus()
    } catch (error) {
      toast({
        title: 'Failed to Queue Full Ingestion',
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      {!embedded && (
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
              <div className="p-2 rounded-xl bg-gradient-to-br from-primary to-primary/80 shadow-lg shadow-primary/20">
                <Calendar className="h-5 w-5 text-white" />
              </div>
              Scheduler Management
            </h1>
            <p className="text-muted-foreground mt-1">Monitor and control scheduled background jobs</p>
          </div>
          <Button
            onClick={() => {
              if (!streamEnabled) {
                refetch()
                refetchStats()
              }
              refetchDmmStatus()
            }}
            variant="outline"
            className="rounded-xl"
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      )}

      {embedded && (
        <div className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/20 p-3">
          <div className="flex items-center gap-3">
            <Calendar className="h-4 w-4 text-primary" />
            <p className="text-sm font-medium">Scheduler Management</p>
            <Badge
              variant="outline"
              className={
                streamState.isConnected
                  ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
                  : 'bg-muted text-muted-foreground'
              }
            >
              <Radio className="mr-1 h-3 w-3" />
              {streamState.isConnected ? 'Live' : 'Offline'}
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            <Switch checked={liveUpdatesEnabled} onCheckedChange={setLiveUpdatesEnabled} aria-label="Live updates" />
            <Button
              onClick={() => {
                if (!streamEnabled) {
                  refetch()
                  refetchStats()
                }
                refetchDmmStatus()
              }}
              variant="outline"
              size="sm"
              className="rounded-lg"
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
        </div>
      )}

      {!embedded && (
        <div className="flex items-center justify-between rounded-xl border border-border/50 bg-muted/20 p-3">
          <div className="flex items-center gap-2">
            <Switch checked={liveUpdatesEnabled} onCheckedChange={setLiveUpdatesEnabled} aria-label="Live updates" />
            <div>
              <p className="text-sm font-medium">Live stream updates</p>
              <p className="text-xs text-muted-foreground">
                {streamState.lastEventAt
                  ? `last update: ${new Date(streamState.lastEventAt).toLocaleString()}`
                  : 'waiting for stream'}
              </p>
            </div>
          </div>
          <Badge
            variant="outline"
            className={
              streamState.isConnected
                ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
                : 'bg-muted text-muted-foreground'
            }
          >
            <Radio className="mr-1 h-3 w-3" />
            {streamState.isConnected ? 'Live' : 'Offline'}
          </Badge>
        </div>
      )}

      {/* Global Scheduler Warning */}
      {stats?.global_scheduler_disabled && (
        <div className="p-4 rounded-xl border border-primary/30 bg-primary/10">
          <div className="flex items-center gap-3">
            <AlertTriangle className="h-5 w-5 text-primary" />
            <div>
              <p className="font-medium text-primary dark:text-primary">Global Scheduler Disabled</p>
              <p className="text-sm text-muted-foreground">
                All scheduled jobs are paused. Enable the scheduler in server configuration.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <Server className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stats?.total_jobs ?? '—'}</p>
                <p className="text-xs text-muted-foreground">Total Jobs</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-emerald-500/10">
                <Power className="h-4 w-4 text-emerald-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stats?.active_jobs ?? '—'}</p>
                <p className="text-xs text-muted-foreground">Active</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-blue-500/10">
                <Loader2 className="h-4 w-4 text-blue-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stats?.running_jobs ?? '—'}</p>
                <p className="text-xs text-muted-foreground">Running</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-red-500/10">
                <PowerOff className="h-4 w-4 text-red-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stats?.disabled_jobs ?? '—'}</p>
                <p className="text-xs text-muted-foreground">Disabled</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* DMM Hashlist Operational Status */}
      <Card className="glass border-border/50">
        <CardContent className="p-4 space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <Database className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="font-medium">DMM Hashlist Ingestion</p>
                <p className="text-xs text-muted-foreground">
                  {dmmStatus ? `${dmmStatus.repo}@${dmmStatus.branch}` : 'Loading repository details...'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="rounded-lg"
                onClick={() => setConfirmDmmFullRunOpen(true)}
                disabled={!dmmStatus?.enabled || dmmStatusLoading || runDmmHashlistFull.isPending}
              >
                {runDmmHashlistFull.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-2 h-4 w-4" />
                )}
                Run Full Ingestion
              </Button>
              <Badge
                className={
                  dmmStatus?.enabled
                    ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
                    : 'bg-red-500/10 text-red-500 border-red-500/30'
                }
              >
                {dmmStatus?.enabled ? 'Enabled' : 'Disabled'}
              </Badge>
              <Badge
                className={
                  dmmSchedulerJob?.is_enabled === false
                    ? 'bg-primary/10 text-primary border-primary/30'
                    : 'bg-blue-500/10 text-blue-500 border-blue-500/30'
                }
              >
                {dmmSchedulerJob?.is_enabled === false ? 'Scheduler Off' : 'Scheduler On'}
              </Badge>
            </div>
          </div>

          {dmmStatusLoading ? (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[...Array(5)].map((_, index) => (
                <Skeleton key={index} className="h-14 rounded-xl" />
              ))}
            </div>
          ) : dmmStatusError ? (
            <div className="rounded-xl border border-primary/30 bg-primary/10 p-3 text-sm text-primary">
              Unable to load DMM status from the backend.
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <div className="rounded-xl border border-border/50 bg-muted/20 p-3">
                <p className="text-xs text-muted-foreground">Latest Commit</p>
                <p className="font-mono text-sm mt-1">{shortSha(dmmStatus?.latest_commit_sha)}</p>
              </div>
              <div className="rounded-xl border border-border/50 bg-muted/20 p-3">
                <p className="text-xs text-muted-foreground">Backfill Pointer</p>
                <p className="font-mono text-sm mt-1">
                  {dmmStatus?.backfill_complete ? 'complete' : shortSha(dmmStatus?.backfill_next_commit_sha)}
                </p>
              </div>
              <div className="rounded-xl border border-border/50 bg-muted/20 p-3">
                <p className="text-xs text-muted-foreground">Processed Files</p>
                <p className="text-sm mt-1">{dmmStatus?.processed_file_sha_count ?? '—'}</p>
              </div>
              <div className="rounded-xl border border-border/50 bg-muted/20 p-3">
                <p className="text-xs text-muted-foreground">Incremental / Run</p>
                <p className="text-sm mt-1">{dmmStatus?.commits_per_run ?? '—'}</p>
              </div>
              <div className="rounded-xl border border-border/50 bg-muted/20 p-3">
                <p className="text-xs text-muted-foreground">Backfill / Run</p>
                <p className="text-sm mt-1">{dmmStatus?.backfill_commits_per_run ?? '—'}</p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <ImdbDatasetImportPanel />

      {/* Filters */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search jobs..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9 rounded-xl"
          />
        </div>
        <Select value={categoryFilter} onValueChange={(v) => setCategoryFilter(v as SchedulerCategory | 'all')}>
          <SelectTrigger className="w-[160px] rounded-xl">
            <Filter className="mr-2 h-4 w-4" />
            <SelectValue placeholder="Category" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Categories</SelectItem>
            <SelectItem value="scraper">Scrapers</SelectItem>
            <SelectItem value="feed">Feeds</SelectItem>
            <SelectItem value="maintenance">Maintenance</SelectItem>
            <SelectItem value="background">Background</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Jobs Table */}
      {isLoading ? (
        <Card className="glass border-border/50">
          <CardContent className="p-6">
            <div className="space-y-4">
              {[...Array(6)].map((_, i) => (
                <Skeleton key={i} className="h-16 rounded-xl" />
              ))}
            </div>
          </CardContent>
        </Card>
      ) : filteredJobs.length === 0 ? (
        <Card className="glass border-border/50">
          <CardContent className="p-12 text-center">
            <Server className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
            <p className="mt-4 text-lg font-medium">No jobs found</p>
            <p className="text-sm text-muted-foreground mt-2">
              {search ? 'Try adjusting your search' : 'No scheduled jobs configured'}
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card className="glass border-border/50 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/30">
                <TableHead className="w-[70px]">On</TableHead>
                <TableHead>Job</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Schedule</TableHead>
                <TableHead>Scheduled</TableHead>
                <TableHead>Running</TableHead>
                <TableHead>Last Run</TableHead>
                <TableHead>Next Run</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredJobs.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  globalSchedulerDisabled={stats?.global_scheduler_disabled ?? false}
                  onRun={() => {
                    setForceRun(false)
                    setConfirmRun({ job, mode: 'queue' })
                  }}
                  onRunInline={() => setConfirmRun({ job, mode: 'inline' })}
                  onViewDetails={() => handleViewDetails(job)}
                  isRunning={runJob.isPending && runJob.variables?.jobId === job.id}
                  isRunningInline={runJobInline.isPending && runJobInline.variables === job.id}
                />
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {/* Job Detail Dialog */}
      <JobDetailDialog
        job={selectedJobLive}
        open={detailsOpen}
        onOpenChange={setDetailsOpen}
        globalSchedulerDisabled={stats?.global_scheduler_disabled ?? false}
      />

      {/* Confirm Run Dialog */}
      <AlertDialog
        open={!!confirmRun}
        onOpenChange={(open) => {
          if (!open) {
            setForceRun(false)
            setConfirmRun(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              {confirmRun?.mode === 'inline' ? (
                <>
                  <FlaskConical className="h-5 w-5 text-primary" />
                  Run Job Inline (Test Mode)?
                </>
              ) : (
                <>
                  <Play className="h-5 w-5 text-primary" />
                  Run Job Manually?
                </>
              )}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmRun?.mode === 'inline' ? (
                <>
                  This will run <strong>{confirmRun?.job.display_name}</strong> directly in the FastAPI process.
                  <br />
                  <br />
                  <span className="text-primary font-medium">⚠️ Warning:</span> This will block until the job completes
                  and may take a long time. Use only for testing purposes.
                </>
              ) : (
                <>
                  This will queue <strong>{confirmRun?.job.display_name}</strong> for immediate execution. The job will
                  run in the background.
                  <div className="mt-4 flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 p-3">
                    <div>
                      <p className="text-sm font-medium">Force run</p>
                      <p className="text-xs text-muted-foreground">Bypass interval throttling for this manual run.</p>
                    </div>
                    <Switch checked={forceRun} onCheckedChange={setForceRun} aria-label="Force run scheduled job" />
                  </div>
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => confirmRun && handleRunJob(confirmRun.job, confirmRun.mode)}
              className={
                confirmRun?.mode === 'inline' ? 'bg-primary hover:bg-primary/90' : 'bg-primary hover:bg-primary/90'
              }
            >
              {(confirmRun?.mode === 'inline' ? runJobInline.isPending : runJob.isPending) ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {confirmRun?.mode === 'inline' ? 'Executing...' : 'Queueing...'}
                </>
              ) : (
                <>
                  {confirmRun?.mode === 'inline' ? (
                    <FlaskConical className="mr-2 h-4 w-4" />
                  ) : (
                    <Play className="mr-2 h-4 w-4" />
                  )}
                  {confirmRun?.mode === 'inline' ? 'Run Inline' : forceRun ? 'Force Queue' : 'Queue Now'}
                </>
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirm Full DMM Run Dialog */}
      <AlertDialog
        open={confirmDmmFullRunOpen}
        onOpenChange={(open) => {
          setConfirmDmmFullRunOpen(open)
          if (!open) {
            setResetDmmCheckpoints(false)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Database className="h-5 w-5 text-primary" />
              Run Full DMM Ingestion?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This will queue a one-time full DMM ingestion loop and continue processing until backfill completes or
              guardrails stop it.
              <div className="mt-4 flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 p-3">
                <div>
                  <p className="text-sm font-medium">Reset checkpoints first</p>
                  <p className="text-xs text-muted-foreground">Start from scratch and reprocess full history.</p>
                </div>
                <Switch
                  checked={resetDmmCheckpoints}
                  onCheckedChange={setResetDmmCheckpoints}
                  aria-label="Reset DMM checkpoints before full ingestion"
                />
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={runDmmHashlistFull.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleRunDmmFullIngestion}
              className="bg-primary hover:bg-primary/90"
              disabled={runDmmHashlistFull.isPending}
            >
              {runDmmHashlistFull.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Queueing...
                </>
              ) : (
                <>
                  <Play className="mr-2 h-4 w-4" />
                  Queue Full Ingestion
                </>
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
