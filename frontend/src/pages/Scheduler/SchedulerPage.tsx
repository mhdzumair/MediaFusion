import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
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
  History,
  Server,
  Rss,
  Settings,
  Bug,
  ChevronRight,
  Power,
  PowerOff,
  FlaskConical,
} from 'lucide-react'
import {
  useSchedulerJobs,
  useSchedulerStats,
  useRunSchedulerJob,
  useRunSchedulerJobInline,
  useSchedulerJobHistory,
} from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import type { SchedulerCategory, SchedulerJobInfo } from '@/lib/api'
import { cn } from '@/lib/utils'

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

// Job Detail Dialog
function JobDetailDialog({
  job,
  open,
  onOpenChange,
}: {
  job: SchedulerJobInfo | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { data: history, isLoading: historyLoading } = useSchedulerJobHistory(open && job ? job.id : undefined, 10)

  if (!job) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {getCategoryIcon(job.category)}
            {job.display_name}
          </DialogTitle>
          <DialogDescription>{job.description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-4">
          {/* Job Details */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Status</label>
              <div className="flex items-center gap-2">
                {job.is_running ? (
                  <Badge className="bg-blue-500/10 text-blue-500 border-blue-500/30">
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    Running
                  </Badge>
                ) : job.is_enabled ? (
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
              <label className="text-xs font-medium text-muted-foreground">Schedule</label>
              <p className="font-mono text-sm">{job.crontab}</p>
              <p className="text-xs text-muted-foreground">{formatCrontab(job.crontab)}</p>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Last Run</label>
              <p className="text-sm">{job.time_since_last_run}</p>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">Next Run</label>
              <p className="text-sm">{job.next_run_in || 'N/A'}</p>
            </div>
          </div>

          {/* Last Run State */}
          {job.last_run_state && (
            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground">Last Run Stats</label>
              <div className="p-3 rounded-lg bg-muted/50 font-mono text-xs max-h-32 overflow-auto">
                <pre>{JSON.stringify(job.last_run_state, null, 2)}</pre>
              </div>
            </div>
          )}

          {/* History */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground flex items-center gap-2">
              <History className="h-4 w-4" />
              Recent History
            </label>
            {historyLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-8 rounded" />
                ))}
              </div>
            ) : history?.entries.length === 0 ? (
              <p className="text-sm text-muted-foreground">No execution history available</p>
            ) : (
              <div className="space-y-1 max-h-40 overflow-auto">
                {history?.entries.map((entry, i) => (
                  <div
                    key={i}
                    className={cn(
                      'flex items-center justify-between p-2 rounded text-xs',
                      entry.status === 'success'
                        ? 'bg-emerald-500/10'
                        : entry.status === 'failed'
                          ? 'bg-red-500/10'
                          : 'bg-muted/50',
                    )}
                  >
                    <span className="text-muted-foreground">{entry.run_at}</span>
                    <div className="flex items-center gap-2">
                      {entry.items_scraped !== null && <span>{entry.items_scraped} items</span>}
                      {entry.duration_seconds !== null && <span>{entry.duration_seconds.toFixed(1)}s</span>}
                      {entry.status === 'success' ? (
                        <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                      ) : entry.status === 'failed' ? (
                        <XCircle className="h-3 w-3 text-red-500" />
                      ) : (
                        <Clock className="h-3 w-3" />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
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
}: {
  job: SchedulerJobInfo
  onRun: () => void
  onRunInline: () => void
  onViewDetails: () => void
  isRunning: boolean
  isRunningInline: boolean
}) {
  return (
    <TableRow className="hover:bg-muted/20">
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
        {job.is_running ? (
          <Badge className="bg-blue-500/10 text-blue-500 border-blue-500/30">
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            Running
          </Badge>
        ) : job.is_enabled ? (
          <Badge className="bg-emerald-500/10 text-emerald-500 border-emerald-500/30">Active</Badge>
        ) : (
          <Badge className="bg-red-500/10 text-red-500 border-red-500/30">Disabled</Badge>
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

export function SchedulerPage() {
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<SchedulerCategory | 'all'>('all')
  const [selectedJob, setSelectedJob] = useState<SchedulerJobInfo | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [confirmRun, setConfirmRun] = useState<ConfirmRunState | null>(null)

  const {
    data: jobsData,
    isLoading,
    refetch,
  } = useSchedulerJobs({
    category: categoryFilter === 'all' ? undefined : categoryFilter,
  })
  const { data: stats } = useSchedulerStats()
  const runJob = useRunSchedulerJob()
  const runJobInline = useRunSchedulerJobInline()
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
        await runJob.mutateAsync(job.id)
        toast({
          title: 'Job Queued',
          description: `${job.display_name} has been queued for execution.`,
        })
      }
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

  return (
    <div className="space-y-6">
      {/* Header */}
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
        <Button onClick={() => refetch()} variant="outline" className="rounded-xl">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

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
                <TableHead>Job</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Schedule</TableHead>
                <TableHead>Status</TableHead>
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
                  onRun={() => setConfirmRun({ job, mode: 'queue' })}
                  onRunInline={() => setConfirmRun({ job, mode: 'inline' })}
                  onViewDetails={() => handleViewDetails(job)}
                  isRunning={runJob.isPending && runJob.variables === job.id}
                  isRunningInline={runJobInline.isPending && runJobInline.variables === job.id}
                />
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {/* Job Detail Dialog */}
      <JobDetailDialog job={selectedJob} open={detailsOpen} onOpenChange={setDetailsOpen} />

      {/* Confirm Run Dialog */}
      <AlertDialog open={!!confirmRun} onOpenChange={() => setConfirmRun(null)}>
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
                  {confirmRun?.mode === 'inline' ? 'Run Inline' : 'Queue Now'}
                </>
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
