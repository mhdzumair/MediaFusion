import { useEffect, useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  useBulkCancelTasks,
  useBulkRetryTasks,
  useCancelTask,
  useRedisMetrics,
  useRetryTask,
  useScraperMetrics,
  useScraperSearchRuns,
  useScraperHistory,
  useTaskDetail,
  useTaskList,
  useTaskOverview,
  useTaskStreamUpdates,
  useToast,
  useWorkerMemoryMetrics,
} from '@/hooks'
import type { TaskRecord } from '@/lib/api'
import type { ScraperMetricsSummary } from '@/lib/api/metrics'
import { Ban, Cpu, Database, Loader2, RefreshCw, RotateCcw, Workflow } from 'lucide-react'
import { SchedulerManagementSection } from './components/SchedulerManagementSection'
import {
  BulkActionConfirmDialog,
  ScraperDetailsDialog,
  SearchRunDetailsDialog,
  TaskDetailsDialog,
} from './components/TaskManagementDialogs'
import {
  formatBytes,
  formatDate,
  formatDuration,
  formatDurationMs,
  formatMediaLabel,
  getTaskStatusLabel,
  isTaskCancellable,
  isTaskRetryable,
  shortTaskId,
  statusBadgeClass,
  summarizeTopMap,
} from './taskManagementUtils'

function getScraperHealth(scraper: {
  latest: { skip_scraping: boolean } | null
  aggregated: { success_rate?: number | null; total_errors: number } | null
}): 'healthy' | 'warning' | 'error' | 'skipped' | 'unknown' {
  if (!scraper.latest && !scraper.aggregated) return 'unknown'
  if (scraper.latest?.skip_scraping) return 'skipped'
  const successRate = scraper.aggregated?.success_rate ?? null
  const totalErrors = scraper.aggregated?.total_errors ?? 0
  if (successRate !== null && successRate < 50) return 'error'
  if (successRate !== null && successRate < 80) return 'warning'
  if (totalErrors > 0 && successRate === null) return 'warning'
  return 'healthy'
}

function scraperHealthBadgeClass(health: ReturnType<typeof getScraperHealth>): string {
  if (health === 'healthy') return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
  if (health === 'warning') return 'bg-primary/10 text-primary border-primary/30'
  if (health === 'error') return 'bg-red-500/10 text-red-500 border-red-500/30'
  if (health === 'skipped') return 'bg-blue-500/10 text-blue-500 border-blue-500/30'
  return 'bg-muted text-muted-foreground border-border'
}

type ScraperHealthFilter = 'all' | 'healthy' | 'warning' | 'error' | 'skipped'
const TASKS_PAGE_SIZE = 50

export function TaskManagementPage() {
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [queueFilter, setQueueFilter] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [liveUpdatesEnabled, setLiveUpdatesEnabled] = useState(true)
  const [bulkActionConfirm, setBulkActionConfirm] = useState<'cancel' | 'retry' | null>(null)
  const [bulkActionTaskIds, setBulkActionTaskIds] = useState<string[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [scraperSearch, setScraperSearch] = useState('')
  const [scraperHealthFilter, setScraperHealthFilter] = useState<ScraperHealthFilter>('all')
  const [selectedScraperName, setSelectedScraperName] = useState<string | null>(null)
  const [scraperDetailsOpen, setScraperDetailsOpen] = useState(false)
  const [mediaSearchQuery, setMediaSearchQuery] = useState('')
  const [mediaSearchMetaId, setMediaSearchMetaId] = useState('')
  const [selectedSearchRun, setSelectedSearchRun] = useState<ScraperMetricsSummary | null>(null)
  const [searchRunDetailsOpen, setSearchRunDetailsOpen] = useState(false)
  const [memoryStatusFilter, setMemoryStatusFilter] = useState<string>('all')
  const [memoryActorFilter, setMemoryActorFilter] = useState<string>('all')
  const [taskPage, setTaskPage] = useState(0)

  const { toast } = useToast()
  const listParams = useMemo(
    () => ({
      limit: TASKS_PAGE_SIZE,
      offset: taskPage * TASKS_PAGE_SIZE,
      status: statusFilter === 'all' ? undefined : statusFilter,
      queue_name: queueFilter === 'all' ? undefined : queueFilter,
      search: search || undefined,
    }),
    [queueFilter, search, statusFilter, taskPage],
  )

  const overviewQuery = useTaskOverview(800)
  const listQuery = useTaskList(listParams)
  const detailQuery = useTaskDetail(selectedTaskId)
  const cancelTask = useCancelTask()
  const retryTask = useRetryTask()
  const bulkCancel = useBulkCancelTasks()
  const bulkRetry = useBulkRetryTasks()
  const streamState = useTaskStreamUpdates({
    enabled: liveUpdatesEnabled,
    sampleSize: 800,
    listParams,
    intervalMs: 3000,
  })

  const scraperQuery = useScraperMetrics()
  const scraperHistoryQuery = useScraperHistory(selectedScraperName, 30)
  const scraperSearchRunsQuery = useScraperSearchRuns({
    query: mediaSearchQuery.trim() || undefined,
    meta_id: mediaSearchMetaId.trim() || undefined,
    limit: 80,
  })
  const redisQuery = useRedisMetrics()
  const workerMemoryQuery = useWorkerMemoryMetrics(100)

  const statusOptions = useMemo(() => {
    const keys = Object.keys(overviewQuery.data?.global_status_counts || {})
    return ['all', ...keys]
  }, [overviewQuery.data?.global_status_counts])

  const scraperItems = scraperQuery.data?.scrapers || []
  const selectedScraper = useMemo(
    () => scraperItems.find((scraper) => scraper.scraper_name === selectedScraperName) || null,
    [scraperItems, selectedScraperName],
  )
  const filteredScrapers = useMemo(() => {
    const searchText = scraperSearch.trim().toLowerCase()
    return scraperItems.filter((scraper) => {
      const health = getScraperHealth(scraper)
      if (scraperHealthFilter !== 'all' && health !== scraperHealthFilter) {
        return false
      }
      if (!searchText) {
        return true
      }
      return scraper.scraper_name.toLowerCase().includes(searchText)
    })
  }, [scraperItems, scraperSearch, scraperHealthFilter])

  const memoryEntries = workerMemoryQuery.data?.entries || []
  const memoryStatusOptions = useMemo(() => {
    const statuses = new Set(memoryEntries.map((entry) => entry.status).filter(Boolean))
    return ['all', ...Array.from(statuses).sort()]
  }, [memoryEntries])
  const memoryActorOptions = useMemo(() => {
    const actors = new Set(memoryEntries.map((entry) => entry.actor_name).filter(Boolean))
    return ['all', ...Array.from(actors).sort()]
  }, [memoryEntries])
  const filteredMemoryEntries = useMemo(() => {
    return memoryEntries.filter((entry) => {
      if (memoryStatusFilter !== 'all' && entry.status !== memoryStatusFilter) {
        return false
      }
      if (memoryActorFilter !== 'all' && entry.actor_name !== memoryActorFilter) {
        return false
      }
      return true
    })
  }, [memoryEntries, memoryStatusFilter, memoryActorFilter])

  useEffect(() => {
    setTaskPage(0)
  }, [statusFilter, queueFilter, search])

  const totalTasks = listQuery.data?.total ?? 0
  const totalTaskPages = Math.max(1, Math.ceil(totalTasks / TASKS_PAGE_SIZE))

  useEffect(() => {
    if (taskPage > totalTaskPages - 1) {
      setTaskPage(Math.max(totalTaskPages - 1, 0))
    }
  }, [taskPage, totalTaskPages])

  const handleRefreshAll = () => {
    overviewQuery.refetch()
    listQuery.refetch()
    scraperQuery.refetch()
    redisQuery.refetch()
    workerMemoryQuery.refetch()
    if (selectedTaskId) {
      detailQuery.refetch()
    }
  }

  const handleOpenTaskDetails = (taskId: string) => {
    setSelectedTaskId(taskId)
    setDetailsOpen(true)
  }

  const handleCancelTask = async (task: TaskRecord) => {
    try {
      const response = await cancelTask.mutateAsync({
        taskId: task.task_id,
        reason: 'cancelled-from-admin-ui',
      })
      toast({
        title: response.success ? 'Cancellation Requested' : 'Cancellation Not Applied',
        description: response.message,
        variant: response.success ? 'default' : 'destructive',
      })
    } catch (error) {
      toast({
        title: 'Failed to cancel task',
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    }
  }

  const handleRetryTask = async (task: TaskRecord) => {
    try {
      const response = await retryTask.mutateAsync(task.task_id)
      toast({
        title: response.success ? 'Task Requeued' : 'Task Not Retried',
        description: response.message,
        variant: response.success ? 'default' : 'destructive',
      })
    } catch (error) {
      toast({
        title: 'Failed to retry task',
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    }
  }

  const handleBulkCancelVisible = async () => {
    const taskIds = (listQuery.data?.tasks || []).filter(isTaskCancellable).map((task) => task.task_id)
    if (taskIds.length === 0) {
      toast({
        title: 'No cancellable tasks',
        description: 'There are no cancellable tasks in the current filtered result.',
      })
      return
    }
    setBulkActionTaskIds(taskIds)
    setBulkActionConfirm('cancel')
  }

  const handleBulkRetryVisible = async () => {
    const taskIds = (listQuery.data?.tasks || []).filter(isTaskRetryable).map((task) => task.task_id)
    if (taskIds.length === 0) {
      toast({
        title: 'No retryable tasks',
        description: 'There are no retryable tasks in the current filtered result.',
      })
      return
    }
    setBulkActionTaskIds(taskIds)
    setBulkActionConfirm('retry')
  }

  const executeBulkAction = async () => {
    if (!bulkActionConfirm || bulkActionTaskIds.length === 0) {
      return
    }
    const currentAction = bulkActionConfirm
    try {
      if (currentAction === 'cancel') {
        const result = await bulkCancel.mutateAsync({
          task_ids: bulkActionTaskIds,
          reason: 'bulk-cancel-from-admin-ui',
        })
        toast({
          title: 'Bulk cancel submitted',
          description: result.message,
        })
      } else {
        const result = await bulkRetry.mutateAsync({
          task_ids: bulkActionTaskIds,
          reason: 'bulk-retry-from-admin-ui',
        })
        toast({
          title: 'Bulk retry submitted',
          description: result.message,
        })
      }
    } catch (error) {
      toast({
        title: `Bulk ${currentAction} failed`,
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    } finally {
      setBulkActionConfirm(null)
      setBulkActionTaskIds([])
    }
  }

  const openScraperDetails = (scraperName: string) => {
    setSelectedScraperName(scraperName)
    setScraperDetailsOpen(true)
  }

  const openSearchRunDetails = (run: ScraperMetricsSummary) => {
    setSelectedSearchRun(run)
    setSearchRunDetailsOpen(true)
  }

  const totalRecent = overviewQuery.data?.total_recent_tasks ?? 0
  const runningCount = overviewQuery.data?.running_task_ids.length ?? 0
  const successCount = overviewQuery.data?.global_status_counts?.success ?? 0
  const errorCount =
    (overviewQuery.data?.global_status_counts?.error ?? 0) +
    (overviewQuery.data?.global_status_counts?.enqueue_failed ?? 0)
  const cancelledCount = overviewQuery.data?.global_status_counts?.cancelled ?? 0

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <div className="p-2 rounded-xl bg-gradient-to-br from-primary to-primary/80 shadow-lg shadow-primary/20">
              <Workflow className="h-5 w-5 text-white" />
            </div>
            Task Management
          </h1>
          <p className="text-muted-foreground mt-1">
            Full admin control for queued/running tasks, scraping operations, scheduler state, and worker resources.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge
            variant="outline"
            className={
              streamState.isConnected
                ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
                : 'bg-primary/10 text-primary border-primary/30'
            }
          >
            {liveUpdatesEnabled ? (streamState.isConnected ? 'live connected' : 'live reconnecting') : 'live off'}
          </Badge>
          <Button variant="outline" className="rounded-xl" onClick={handleRefreshAll}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Recent Tasks</p>
            <p className="text-2xl font-bold">{totalRecent}</p>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Running</p>
            <p className="text-2xl font-bold text-blue-500">{runningCount}</p>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Successful</p>
            <p className="text-2xl font-bold text-emerald-500">{successCount}</p>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Errors</p>
            <p className="text-2xl font-bold text-red-500">{errorCount}</p>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Cancelled</p>
            <p className="text-2xl font-bold text-orange-500">{cancelledCount}</p>
          </CardContent>
        </Card>
      </div>

      <Tabs defaultValue="tasks" className="space-y-4">
        <TabsList className="grid grid-cols-4 w-full max-w-3xl">
          <TabsTrigger value="tasks">Tasks</TabsTrigger>
          <TabsTrigger value="scrapers">Scrapers</TabsTrigger>
          <TabsTrigger value="schedules">Schedules</TabsTrigger>
          <TabsTrigger value="resources">Resources</TabsTrigger>
        </TabsList>

        <TabsContent value="tasks" className="space-y-4">
          <div className="grid md:grid-cols-3 gap-4">
            <div className="relative">
              <Input
                placeholder="Search by task, actor, queue..."
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="rounded-xl"
              />
            </div>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="rounded-xl">
                <SelectValue placeholder="Filter by status" />
              </SelectTrigger>
              <SelectContent>
                {statusOptions.map((status) => (
                  <SelectItem key={status} value={status}>
                    {status}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={queueFilter} onValueChange={setQueueFilter}>
              <SelectTrigger className="rounded-xl">
                <SelectValue placeholder="Filter by queue" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">all</SelectItem>
                {overviewQuery.data?.queue_summaries.map((queue) => (
                  <SelectItem key={queue.queue_name} value={queue.queue_name}>
                    {queue.queue_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/50 bg-muted/20 p-3">
            <div className="flex items-center gap-2">
              <Switch checked={liveUpdatesEnabled} onCheckedChange={setLiveUpdatesEnabled} />
              <div>
                <p className="text-sm font-medium">Live stream updates</p>
                <p className="text-xs text-muted-foreground">
                  {streamState.lastEventAt
                    ? `last update: ${formatDate(streamState.lastEventAt)}`
                    : 'waiting for stream'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleBulkRetryVisible}
                disabled={bulkRetry.isPending || retryTask.isPending}
              >
                {bulkRetry.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="mr-2 h-4 w-4" />
                )}
                Retry Visible Failed
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={handleBulkCancelVisible}
                disabled={bulkCancel.isPending || cancelTask.isPending}
              >
                {bulkCancel.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Ban className="mr-2 h-4 w-4" />
                )}
                Cancel Visible Active
              </Button>
            </div>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-3">
            {overviewQuery.data?.queue_summaries.map((queue) => (
              <Card key={queue.queue_name} className="glass border-border/50">
                <CardContent className="p-4 space-y-1">
                  <p className="font-medium">{queue.queue_name}</p>
                  <p className="text-xs text-muted-foreground">{queue.stream_name}</p>
                  <div className="text-xs text-muted-foreground">
                    recent: {queue.recent_total} | running: {queue.currently_running}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          {listQuery.isLoading ? (
            <Card className="glass border-border/50">
              <CardContent className="p-6 space-y-3">
                {[...Array(6)].map((_, index) => (
                  <Skeleton key={index} className="h-12 rounded-lg" />
                ))}
              </CardContent>
            </Card>
          ) : (
            <Card className="glass border-border/50 overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Task</TableHead>
                    <TableHead>Queue</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {listQuery.data?.tasks.map((task) => (
                    <TableRow key={task.task_id}>
                      <TableCell>
                        <div>
                          <p className="font-mono text-xs">{shortTaskId(task.task_id)}</p>
                          <p className="text-xs text-muted-foreground">{task.actor_name || 'unknown actor'}</p>
                        </div>
                      </TableCell>
                      <TableCell>{task.queue_name || '—'}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={statusBadgeClass(task.status)}>
                          {getTaskStatusLabel(task)}
                          {task.is_running_now ? ' (live)' : ''}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{formatDate(task.created_at)}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDurationMs(task.duration_ms)}
                      </TableCell>
                      <TableCell className="text-right space-x-2">
                        <Button size="sm" variant="outline" onClick={() => handleOpenTaskDetails(task.task_id)}>
                          Details
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!isTaskRetryable(task) || retryTask.isPending}
                          onClick={() => handleRetryTask(task)}
                        >
                          {retryTask.isPending ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <RotateCcw className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={!isTaskCancellable(task) || cancelTask.isPending || bulkCancel.isPending}
                          onClick={() => handleCancelTask(task)}
                        >
                          {cancelTask.isPending ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Ban className="h-4 w-4" />
                          )}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="flex items-center justify-between gap-3 border-t border-border/50 px-4 py-3 text-sm">
                <p className="text-muted-foreground">
                  Showing {totalTasks === 0 ? 0 : taskPage * TASKS_PAGE_SIZE + 1}-
                  {Math.min((taskPage + 1) * TASKS_PAGE_SIZE, totalTasks)} of {totalTasks}
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setTaskPage((prev) => Math.max(prev - 1, 0))}
                    disabled={taskPage === 0}
                  >
                    Previous
                  </Button>
                  <Badge variant="outline">
                    Page {totalTasks === 0 ? 0 : taskPage + 1} / {totalTasks === 0 ? 0 : totalTaskPages}
                  </Badge>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setTaskPage((prev) => Math.min(prev + 1, totalTaskPages - 1))}
                    disabled={taskPage >= totalTaskPages - 1 || totalTasks === 0}
                  >
                    Next
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="scrapers" className="space-y-4">
          <Card className="glass border-border/50">
            <CardHeader>
              <CardTitle>Scraper Summaries</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid md:grid-cols-2 gap-3">
                <Input
                  placeholder="Filter scraper by name..."
                  value={scraperSearch}
                  onChange={(event) => setScraperSearch(event.target.value)}
                  className="rounded-xl"
                />
                <Select
                  value={scraperHealthFilter}
                  onValueChange={(value) => setScraperHealthFilter(value as ScraperHealthFilter)}
                >
                  <SelectTrigger className="rounded-xl">
                    <SelectValue placeholder="Health" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">all</SelectItem>
                    <SelectItem value="healthy">healthy</SelectItem>
                    <SelectItem value="warning">warning</SelectItem>
                    <SelectItem value="error">error</SelectItem>
                    <SelectItem value="skipped">skipped</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {scraperQuery.isLoading ? (
                <Skeleton className="h-40 rounded-lg" />
              ) : (
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {filteredScrapers.map((scraper) => {
                    const health = getScraperHealth(scraper)
                    const latest = scraper.latest
                    const aggregated = scraper.aggregated
                    return (
                      <Card key={scraper.scraper_name} className="border-border/40">
                        <CardContent className="p-4 space-y-2">
                          <div className="flex items-center justify-between gap-2">
                            <p className="font-medium truncate">{scraper.scraper_name}</p>
                            <Badge variant="outline" className={scraperHealthBadgeClass(health)}>
                              {health}
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground">runs: {aggregated?.total_runs ?? 0}</p>
                          <p className="text-xs text-muted-foreground">
                            success: {aggregated?.success_rate?.toFixed(1) ?? '0'}% | avg duration:{' '}
                            {formatDuration(aggregated?.avg_duration_seconds ?? 0)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            found: {latest?.total_items.found ?? 0} | processed: {latest?.total_items.processed ?? 0}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            skipped: {latest?.total_items.skipped ?? 0} | errors: {latest?.total_items.errors ?? 0}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            quality: {summarizeTopMap(latest?.quality_distribution)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            source: {summarizeTopMap(latest?.source_distribution)}
                          </p>
                          <div className="pt-1">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => openScraperDetails(scraper.scraper_name)}
                            >
                              Full details
                            </Button>
                          </div>
                        </CardContent>
                      </Card>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="glass border-border/50">
            <CardHeader>
              <CardTitle>Recent Media Search Runs</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid md:grid-cols-2 gap-3">
                <Input
                  placeholder="Search by title / meta id / scraper..."
                  value={mediaSearchQuery}
                  onChange={(event) => setMediaSearchQuery(event.target.value)}
                  className="rounded-xl"
                />
                <Input
                  placeholder="Exact Meta ID (e.g. tt31050594)"
                  value={mediaSearchMetaId}
                  onChange={(event) => setMediaSearchMetaId(event.target.value)}
                  className="rounded-xl"
                />
              </div>

              {scraperSearchRunsQuery.isLoading ? (
                <Skeleton className="h-40 rounded-lg" />
              ) : (
                <div className="rounded-xl border border-border/50 overflow-hidden">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Time</TableHead>
                        <TableHead>Scraper</TableHead>
                        <TableHead>Media Search</TableHead>
                        <TableHead>Duration</TableHead>
                        <TableHead>Items</TableHead>
                        <TableHead className="text-right">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(scraperSearchRunsQuery.data?.runs || []).map((run, index) => (
                        <TableRow key={`${run.scraper_name}-${run.timestamp}-${index}`}>
                          <TableCell className="text-xs text-muted-foreground">{formatDate(run.timestamp)}</TableCell>
                          <TableCell className="text-xs">{run.scraper_name}</TableCell>
                          <TableCell className="text-xs">
                            <div>
                              <p>{formatMediaLabel(run)}</p>
                              <p className="text-muted-foreground">skips: {summarizeTopMap(run.skip_reasons)}</p>
                            </div>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {formatDuration(run.duration_seconds)}
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            f:{run.total_items.found} p:{run.total_items.processed} s:{run.total_items.skipped} e:
                            {run.total_items.errors}
                          </TableCell>
                          <TableCell className="text-right">
                            <Button size="sm" variant="outline" onClick={() => openSearchRunDetails(run)}>
                              View Summary
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="schedules" className="space-y-4">
          <SchedulerManagementSection />
        </TabsContent>

        <TabsContent value="resources" className="space-y-4">
          <div className="grid md:grid-cols-2 gap-4">
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Database className="h-4 w-4 text-primary" />
                  Redis
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Used Memory</span>
                  <span>{redisQuery.data?.memory?.used_memory_human || 'N/A'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Ops/sec</span>
                  <span>{redisQuery.data?.performance?.instantaneous_ops_per_sec || 0}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Connected Clients</span>
                  <span>{redisQuery.data?.connections?.connected_clients || 0}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Cache Hit Rate</span>
                  <span>{redisQuery.data?.cache?.hit_rate?.toFixed(1) || '0'}%</span>
                </div>
              </CardContent>
            </Card>
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-blue-500" />
                  Worker Memory
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Total Events</span>
                  <span>{workerMemoryQuery.data?.summary.total_events || 0}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Last RSS</span>
                  <span>{formatBytes(workerMemoryQuery.data?.summary.last_rss_bytes)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Peak RSS</span>
                  <span>{formatBytes(workerMemoryQuery.data?.summary.peak_rss_bytes)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Error Samples</span>
                  <span>{workerMemoryQuery.data?.summary.status_counts.error || 0}</span>
                </div>
              </CardContent>
            </Card>
          </div>

          <Card className="glass border-border/50">
            <CardHeader>
              <CardTitle>Worker Memory Events</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid md:grid-cols-2 gap-3">
                <Select value={memoryStatusFilter} onValueChange={setMemoryStatusFilter}>
                  <SelectTrigger className="rounded-xl">
                    <SelectValue placeholder="Status filter" />
                  </SelectTrigger>
                  <SelectContent>
                    {memoryStatusOptions.map((status) => (
                      <SelectItem key={status} value={status}>
                        {status}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={memoryActorFilter} onValueChange={setMemoryActorFilter}>
                  <SelectTrigger className="rounded-xl">
                    <SelectValue placeholder="Actor filter" />
                  </SelectTrigger>
                  <SelectContent>
                    {memoryActorOptions.map((actor) => (
                      <SelectItem key={actor} value={actor}>
                        {actor}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {workerMemoryQuery.isLoading ? (
                <Skeleton className="h-40 rounded-lg" />
              ) : (
                <div className="rounded-xl border border-border/50 overflow-hidden">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Time</TableHead>
                        <TableHead>Actor</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>RSS Before</TableHead>
                        <TableHead>RSS After</TableHead>
                        <TableHead>Delta</TableHead>
                        <TableHead>Peak</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredMemoryEntries.slice(0, 50).map((entry, index) => (
                        <TableRow key={`${entry.message_id}-${index}`}>
                          <TableCell className="text-xs text-muted-foreground">{formatDate(entry.timestamp)}</TableCell>
                          <TableCell className="text-xs">{entry.actor_name}</TableCell>
                          <TableCell>
                            <Badge variant="outline" className={statusBadgeClass(entry.status)}>
                              {entry.status}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-xs">{formatBytes(entry.rss_before_bytes)}</TableCell>
                          <TableCell className="text-xs">{formatBytes(entry.rss_after_bytes)}</TableCell>
                          <TableCell className="text-xs">{formatBytes(entry.rss_delta_bytes)}</TableCell>
                          <TableCell className="text-xs">{formatBytes(entry.peak_rss_bytes)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <ScraperDetailsDialog
        open={scraperDetailsOpen}
        onOpenChange={setScraperDetailsOpen}
        scraperName={selectedScraperName}
        scraper={selectedScraper}
        historyLoading={scraperHistoryQuery.isLoading}
        history={scraperHistoryQuery.data}
      />

      <SearchRunDetailsDialog
        open={searchRunDetailsOpen}
        onOpenChange={setSearchRunDetailsOpen}
        run={selectedSearchRun}
      />

      <BulkActionConfirmDialog
        open={bulkActionConfirm !== null}
        action={bulkActionConfirm}
        taskCount={bulkActionTaskIds.length}
        onOpenChange={(open) => !open && setBulkActionConfirm(null)}
        onConfirm={executeBulkAction}
      />

      <TaskDetailsDialog
        open={detailsOpen}
        onOpenChange={setDetailsOpen}
        selectedTaskId={selectedTaskId}
        isLoading={detailQuery.isLoading}
        task={detailQuery.data}
      />
    </div>
  )
}
