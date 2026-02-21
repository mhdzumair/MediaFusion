import { useState, useMemo } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Progress } from '@/components/ui/progress'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  BarChart3,
  Database,
  HardDrive,
  FileStack,
  Clock,
  Server,
  Zap,
  Activity,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Calendar,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Info,
  Play,
  Pause,
  Film,
  Tv,
  Radio,
  Users,
  UserCheck,
  GitPullRequest,
  Eye,
  Download,
  Bookmark,
  Rss,
  Award,
  Shield,
} from 'lucide-react'
import {
  useTorrentCount,
  useTorrentSources,
  useMetadataCount,
  useRedisMetrics,
  useDebridCacheMetrics,
  useTorrentUploaders,
  useWeeklyUploaders,
  useSchedulerJobs,
  useUserStats,
  useContributionMetrics,
  useActivityStats,
  useSystemOverview,
  useScraperMetrics,
  useScraperHistory,
} from '@/hooks'
import type { SchedulerJobInfo, RedisMetrics, ScraperMetricsData, ScraperMetricsSummary } from '@/lib/api/metrics'

function formatNumber(num: number): string {
  if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}K`
  return num.toString()
}

function formatDateToYYYYMMDD(date: Date): string {
  return date.toISOString().split('T')[0]
}

function getStartOfWeek(date: Date): Date {
  const d = new Date(date)
  const day = d.getDay()
  const diff = d.getDate() - day + (day === 0 ? -6 : 1)
  return new Date(d.setDate(diff))
}

// Simple pie chart component using SVG
function PieChart({ data }: { data: { label: string; value: number; color: string }[] }) {
  const total = data.reduce((acc, item) => acc + item.value, 0)
  if (total === 0) return null

  const paths = data.map((item, index) => {
    const startAngle = data.slice(0, index).reduce((sum, i) => sum + (i.value / total) * 360, 0)
    const endAngle = startAngle + (item.value / total) * 360
    const angle = endAngle - startAngle

    const startRad = (startAngle - 90) * (Math.PI / 180)
    const endRad = (endAngle - 90) * (Math.PI / 180)

    const x1 = 50 + 40 * Math.cos(startRad)
    const y1 = 50 + 40 * Math.sin(startRad)
    const x2 = 50 + 40 * Math.cos(endRad)
    const y2 = 50 + 40 * Math.sin(endRad)

    const largeArc = angle > 180 ? 1 : 0

    const pathD = `M 50 50 L ${x1} ${y1} A 40 40 0 ${largeArc} 1 ${x2} ${y2} Z`

    return <path key={index} d={pathD} fill={item.color} className="transition-opacity hover:opacity-80" />
  })

  return (
    <div className="flex flex-col items-center gap-4">
      <svg viewBox="0 0 100 100" className="w-48 h-48">
        {paths}
      </svg>
      <div className="flex flex-wrap justify-center gap-3">
        {data.map((item, index) => (
          <div key={index} className="flex items-center gap-2 text-sm">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: item.color }} />
            <span className="text-muted-foreground">{item.label}:</span>
            <span className="font-medium">{formatNumber(item.value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// Scheduler Job Card Component
function SchedulerJobCard({ job }: { job: SchedulerJobInfo }) {
  const state = job.last_run_state
  const hasStats = state !== null

  // Determine card color based on log counts
  let statusColor = 'border-l-emerald-500'
  let statusBg = 'bg-emerald-500/10'

  if (!job.is_enabled) {
    statusColor = 'border-l-gray-500'
    statusBg = 'bg-gray-500/10'
  } else if (hasStats) {
    const logInfo = state?.log_count_info ?? 0
    const logWarning = state?.log_count_warning ?? 0
    const logError = state?.log_count_error ?? 0
    const maxLog = Math.max(logInfo, logWarning, logError)

    if (maxLog === logError && logError > 0) {
      statusColor = 'border-l-red-500'
      statusBg = 'bg-red-500/10'
    } else if (maxLog === logWarning && logWarning > 0) {
      statusColor = 'border-l-yellow-500'
      statusBg = 'bg-primary/10'
    }
  }

  return (
    <Card className={`glass border-border/50 border-l-4 ${statusColor}`}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2">
            <h4 className="font-medium truncate">{job.display_name}</h4>
            {job.is_running && (
              <Badge variant="default" className="bg-emerald-500 text-xs">
                <Play className="h-3 w-3 mr-1" />
                Running
              </Badge>
            )}
          </div>
          <Badge variant={job.is_enabled ? 'secondary' : 'outline'} className="text-xs">
            {job.is_enabled ? 'Enabled' : 'Disabled'}
          </Badge>
        </div>

        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Clock className="h-3.5 w-3.5" />
            <span>Last Run: {job.time_since_last_run}</span>
          </div>

          {job.is_enabled && job.next_run_in && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Calendar className="h-3.5 w-3.5" />
              <span>Next: {job.next_run_in}</span>
            </div>
          )}

          {hasStats && (
            <div className={`mt-3 p-2 rounded-lg ${statusBg}`}>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="flex items-center gap-1">
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                  <span>Scraped: {state?.item_scraped_count ?? 0}</span>
                </div>
                <div className="flex items-center gap-1">
                  <XCircle className="h-3 w-3 text-red-500" />
                  <span>Dropped: {state?.item_dropped_count ?? 0}</span>
                </div>
              </div>
              <div className="flex items-center gap-3 mt-2 text-xs">
                <span className="flex items-center gap-1 text-blue-400">
                  <Info className="h-3 w-3" />
                  {state?.log_count_info ?? 0}
                </span>
                <span className="flex items-center gap-1 text-primary">
                  <AlertTriangle className="h-3 w-3" />
                  {state?.log_count_warning ?? 0}
                </span>
                <span className="flex items-center gap-1 text-red-400">
                  <XCircle className="h-3 w-3" />
                  {state?.log_count_error ?? 0}
                </span>
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

// Scraper Metrics Card Component
function ScraperMetricsCard({
  scraper,
  onViewHistory,
}: {
  scraper: ScraperMetricsData
  onViewHistory: (name: string) => void
}) {
  const latest = scraper.latest
  const aggregated = scraper.aggregated

  // Determine status color based on success rate or errors
  let statusColor = 'border-l-emerald-500'
  let statusBg = 'bg-emerald-500/10'

  if (aggregated) {
    if (aggregated.success_rate !== null && aggregated.success_rate !== undefined) {
      if (aggregated.success_rate < 50) {
        statusColor = 'border-l-red-500'
        statusBg = 'bg-red-500/10'
      } else if (aggregated.success_rate < 80) {
        statusColor = 'border-l-yellow-500'
        statusBg = 'bg-primary/10'
      }
    }
  }

  return (
    <Card className={`glass border-border/50 border-l-4 ${statusColor}`}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-3">
          <h4 className="font-medium truncate">{scraper.scraper_name}</h4>
          {aggregated?.success_rate !== null && aggregated?.success_rate !== undefined && (
            <Badge
              variant={
                aggregated.success_rate >= 80 ? 'default' : aggregated.success_rate >= 50 ? 'secondary' : 'destructive'
              }
            >
              {aggregated.success_rate.toFixed(0)}% success
            </Badge>
          )}
        </div>

        {aggregated && (
          <div className={`p-3 rounded-lg ${statusBg} mb-3`}>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex items-center gap-1">
                <span className="text-muted-foreground">Runs:</span>
                <span className="font-medium">{aggregated.total_runs}</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="text-muted-foreground">Avg Time:</span>
                <span className="font-medium">{aggregated.avg_duration_seconds?.toFixed(1) ?? 'N/A'}s</span>
              </div>
              <div className="flex items-center gap-1">
                <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                <span>Processed: {formatNumber(aggregated.total_items_processed)}</span>
              </div>
              <div className="flex items-center gap-1">
                <XCircle className="h-3 w-3 text-red-500" />
                <span>Errors: {formatNumber(aggregated.total_errors)}</span>
              </div>
            </div>
          </div>
        )}

        {latest && !latest.skip_scraping && (
          <div className="text-xs text-muted-foreground space-y-1">
            <div className="flex justify-between">
              <span>Last run:</span>
              <span>{new Date(latest.timestamp).toLocaleString()}</span>
            </div>
            {latest.meta_title && (
              <div className="flex justify-between">
                <span>Content:</span>
                <span className="truncate max-w-[150px]">{latest.meta_title}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span>Found/Processed:</span>
              <span>
                {latest.total_items.found}/{latest.total_items.processed}
              </span>
            </div>
          </div>
        )}

        {latest?.skip_scraping && (
          <div className="text-xs text-muted-foreground italic">Last run was skipped (recent scrape)</div>
        )}

        <Button variant="ghost" size="sm" className="w-full mt-3" onClick={() => onViewHistory(scraper.scraper_name)}>
          View History
        </Button>
      </CardContent>
    </Card>
  )
}

// Scraper History Modal/Panel Component
function ScraperHistoryPanel({
  scraperName,
  history,
  isLoading,
  onClose,
}: {
  scraperName: string | null
  history: ScraperMetricsSummary[]
  isLoading: boolean
  onClose: () => void
}) {
  if (!scraperName) return null

  return (
    <Card className="glass border-border/50">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Clock className="h-5 w-5 text-blue-500" />
            Run History: {scraperName}
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={onClose}>
            <XCircle className="h-4 w-4" />
          </Button>
        </div>
        <CardDescription>Last 20 scraper runs</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="p-3 rounded-lg bg-muted/50">
                <Skeleton className="h-4 w-1/3 mb-2" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            ))}
          </div>
        ) : history.length === 0 ? (
          <p className="text-center text-muted-foreground py-8">No history available</p>
        ) : (
          <ScrollArea className="h-[500px] pr-2">
            <div className="space-y-3">
              {history.map((run, idx) => {
                const hasErrors = run.total_items.errors > 0
                const wasSkipped = run.skip_scraping

                return (
                  <div
                    key={idx}
                    className={`p-3 rounded-lg border ${wasSkipped ? 'bg-muted/30 border-muted' : hasErrors ? 'bg-red-500/5 border-red-500/20' : 'bg-muted/50 border-border/30'}`}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium">{new Date(run.timestamp).toLocaleString()}</span>
                      <Badge
                        variant={wasSkipped ? 'outline' : hasErrors ? 'destructive' : 'secondary'}
                        className="text-xs"
                      >
                        {wasSkipped ? 'Skipped' : hasErrors ? 'Errors' : `${run.duration_seconds.toFixed(1)}s`}
                      </Badge>
                    </div>

                    {!wasSkipped && (
                      <>
                        {run.meta_title && (
                          <p className="text-xs text-muted-foreground truncate mb-1">
                            {run.meta_title} {run.season && `S${run.season}`}
                            {run.episode && `E${run.episode}`}
                          </p>
                        )}
                        <div className="flex items-center gap-4 text-xs">
                          <span className="flex items-center gap-1">
                            <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                            {run.total_items.found} found
                          </span>
                          <span className="flex items-center gap-1">
                            <Activity className="h-3 w-3 text-blue-500" />
                            {run.total_items.processed} processed
                          </span>
                          {run.total_items.errors > 0 && (
                            <span className="flex items-center gap-1 text-red-400">
                              <XCircle className="h-3 w-3" />
                              {run.total_items.errors} errors
                            </span>
                          )}
                        </div>

                        {/* Quality distribution preview */}
                        {Object.keys(run.quality_distribution).length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-2">
                            {Object.entries(run.quality_distribution)
                              .slice(0, 5)
                              .map(([quality, count]) => (
                                <Badge key={quality} variant="outline" className="text-[10px] px-1.5 py-0">
                                  {quality}: {count}
                                </Badge>
                              ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  )
}

// Redis Metrics Display Component
function RedisMetricsDisplay({ metrics, isLoading }: { metrics?: RedisMetrics; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-2">
        {[...Array(4)].map((_, i) => (
          <Card key={i} className="glass border-border/50">
            <CardContent className="p-4">
              <Skeleton className="h-4 w-1/3 mb-4" />
              <div className="space-y-2">
                {[...Array(3)].map((_, j) => (
                  <div key={j} className="flex justify-between">
                    <Skeleton className="h-3 w-1/3" />
                    <Skeleton className="h-3 w-1/4" />
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    )
  }

  if (!metrics || metrics.error) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        <Server className="h-12 w-12 mx-auto mb-4 opacity-50" />
        <p>{metrics?.error || 'No Redis metrics available'}</p>
      </div>
    )
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {/* Memory Section */}
      <Card className="glass border-border/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <HardDrive className="h-4 w-4 text-primary" />
            Memory
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex justify-between py-1.5 border-b border-border/30">
            <span className="text-muted-foreground">Used Memory</span>
            <span className="font-medium">{metrics.memory?.used_memory_human ?? 'N/A'}</span>
          </div>
          <div className="flex justify-between py-1.5 border-b border-border/30">
            <span className="text-muted-foreground">Peak Memory</span>
            <span className="font-medium">{metrics.memory?.used_memory_peak_human ?? 'N/A'}</span>
          </div>
          <div className="flex justify-between py-1.5 border-b border-border/30">
            <span className="text-muted-foreground">Max Memory</span>
            <span className="font-medium">{metrics.memory?.maxmemory_human ?? 'Unlimited'}</span>
          </div>
          <div className="flex justify-between py-1.5">
            <span className="text-muted-foreground">Fragmentation Ratio</span>
            <span className="font-medium">{metrics.memory?.mem_fragmentation_ratio?.toFixed(2) ?? 'N/A'}</span>
          </div>
        </CardContent>
      </Card>

      {/* Connections Section */}
      <Card className="glass border-border/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <Server className="h-4 w-4 text-blue-500" />
            Connections
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex justify-between py-1.5 border-b border-border/30">
            <span className="text-muted-foreground">Connected Clients</span>
            <span className="font-medium">{metrics.connections?.connected_clients ?? 'N/A'}</span>
          </div>
          <div className="flex justify-between py-1.5 border-b border-border/30">
            <span className="text-muted-foreground">Blocked Clients</span>
            <span className="font-medium">{metrics.connections?.blocked_clients ?? 0}</span>
          </div>
          <div className="flex justify-between py-1.5">
            <span className="text-muted-foreground">Max Clients</span>
            <span className="font-medium">{metrics.connections?.maxclients ?? 'N/A'}</span>
          </div>
        </CardContent>
      </Card>

      {/* Performance Section */}
      <Card className="glass border-border/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <Zap className="h-4 w-4 text-emerald-500" />
            Performance
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex justify-between py-1.5 border-b border-border/30">
            <span className="text-muted-foreground">Ops/sec</span>
            <span className="font-medium">{formatNumber(metrics.performance?.instantaneous_ops_per_sec ?? 0)}</span>
          </div>
          <div className="flex justify-between py-1.5">
            <span className="text-muted-foreground">Total Commands</span>
            <span className="font-medium">{formatNumber(metrics.performance?.total_commands_processed ?? 0)}</span>
          </div>
        </CardContent>
      </Card>

      {/* Cache Section */}
      <Card className="glass border-border/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <Database className="h-4 w-4 text-orange-500" />
            Cache
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex justify-between py-1.5 border-b border-border/30">
            <span className="text-muted-foreground">Keyspace Hits</span>
            <span className="font-medium">{formatNumber(metrics.cache?.keyspace_hits ?? 0)}</span>
          </div>
          <div className="flex justify-between py-1.5 border-b border-border/30">
            <span className="text-muted-foreground">Keyspace Misses</span>
            <span className="font-medium">{formatNumber(metrics.cache?.keyspace_misses ?? 0)}</span>
          </div>
          <div className="flex justify-between py-1.5">
            <span className="text-muted-foreground">Hit Rate</span>
            <Badge variant={metrics.cache?.hit_rate && metrics.cache.hit_rate > 80 ? 'default' : 'secondary'}>
              {metrics.cache?.hit_rate?.toFixed(1) ?? 0}%
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* App Pool Stats */}
      {metrics.app_pool_stats && (
        <Card className="glass border-border/50 md:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <Activity className="h-4 w-4 text-pink-500" />
              Application Connection Pool
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4 text-center">
              <div className="p-3 rounded-lg bg-muted/50">
                <p className="text-2xl font-bold text-emerald-500">
                  {metrics.app_pool_stats.app_connections?.async?.in_use ?? 0}
                </p>
                <p className="text-xs text-muted-foreground">In Use</p>
              </div>
              <div className="p-3 rounded-lg bg-muted/50">
                <p className="text-2xl font-bold text-blue-500">
                  {metrics.app_pool_stats.app_connections?.async?.available ?? 0}
                </p>
                <p className="text-xs text-muted-foreground">Available</p>
              </div>
              <div className="p-3 rounded-lg bg-muted/50">
                <p className="text-2xl font-bold text-primary">
                  {metrics.app_pool_stats.app_connections?.async?.max ?? 0}
                </p>
                <p className="text-xs text-muted-foreground">Max</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

export function MetricsPage() {
  const [selectedWeek, setSelectedWeek] = useState(() => {
    return formatDateToYYYYMMDD(getStartOfWeek(new Date()))
  })
  const [selectedScraper, setSelectedScraper] = useState<string | null>(null)

  const { data: torrentCount, isLoading: tcLoading, refetch: refetchTc } = useTorrentCount()
  const { data: torrentSources, isLoading: tsLoading, refetch: refetchTs } = useTorrentSources()
  const { data: metadataCount, isLoading: mcLoading, refetch: refetchMc } = useMetadataCount()
  const { data: schedulerData, isLoading: sjLoading, refetch: refetchSj } = useSchedulerJobs()
  const { data: redisMetrics, isLoading: rmLoading, refetch: refetchRm } = useRedisMetrics()
  const { data: debridCache, isLoading: dcLoading, refetch: refetchDc } = useDebridCacheMetrics()
  const { data: uploaders, isLoading: uLoading } = useTorrentUploaders()
  const { data: weeklyUploaders, isLoading: wuLoading } = useWeeklyUploaders(selectedWeek)
  const { data: userStats, isLoading: usLoading, refetch: refetchUs } = useUserStats()
  const { data: contributionStats, isLoading: csLoading, refetch: refetchCs } = useContributionMetrics()
  const { data: activityStats, isLoading: asLoading, refetch: refetchAs } = useActivityStats()
  const { data: systemOverview, isLoading: soLoading, refetch: refetchSo } = useSystemOverview()
  const { data: scraperMetrics, isLoading: smLoading, refetch: refetchSm } = useScraperMetrics()
  const { data: scraperHistory, isLoading: shLoading } = useScraperHistory(selectedScraper)

  const isLoading = tcLoading || tsLoading || mcLoading || sjLoading || rmLoading || dcLoading || usLoading

  const handleRefreshAll = () => {
    refetchTc()
    refetchTs()
    refetchMc()
    refetchSj()
    refetchRm()
    refetchDc()
    refetchUs()
    refetchCs()
    refetchAs()
    refetchSo()
    refetchSm()
  }

  const handlePrevWeek = () => {
    const currentDate = new Date(selectedWeek)
    currentDate.setDate(currentDate.getDate() - 7)
    setSelectedWeek(formatDateToYYYYMMDD(currentDate))
  }

  const handleNextWeek = () => {
    const currentDate = new Date(selectedWeek)
    currentDate.setDate(currentDate.getDate() + 7)
    setSelectedWeek(formatDateToYYYYMMDD(currentDate))
  }

  // Group scheduler jobs by category and status
  const groupedJobs = useMemo(() => {
    if (!schedulerData?.jobs)
      return { enabledWithStats: [], enabledWithoutStats: [], disabledWithStats: [], disabledWithoutStats: [] }

    const enabledWithStats: SchedulerJobInfo[] = []
    const enabledWithoutStats: SchedulerJobInfo[] = []
    const disabledWithStats: SchedulerJobInfo[] = []
    const disabledWithoutStats: SchedulerJobInfo[] = []

    schedulerData.jobs.forEach((job) => {
      const hasStats = job.last_run_state !== null
      if (job.is_enabled) {
        if (hasStats) enabledWithStats.push(job)
        else enabledWithoutStats.push(job)
      } else {
        if (hasStats) disabledWithStats.push(job)
        else disabledWithoutStats.push(job)
      }
    })

    return { enabledWithStats, enabledWithoutStats, disabledWithStats, disabledWithoutStats }
  }, [schedulerData])

  // Metadata pie chart data
  const metadataPieData = useMemo(() => {
    if (!metadataCount) return []
    return [
      { label: 'Movies', value: metadataCount.movies, color: '#8b5cf6' },
      { label: 'Series', value: metadataCount.series, color: '#3b82f6' },
      { label: 'TV Channels', value: metadataCount.tv_channels, color: '#10b981' },
    ]
  }, [metadataCount])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <div className="p-2 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 shadow-lg shadow-cyan-500/20">
              <BarChart3 className="h-5 w-5 text-white" />
            </div>
            System Metrics
          </h1>
          <p className="text-muted-foreground mt-1">Monitor system health, scrapers, and resource usage</p>
        </div>
        <Button variant="outline" size="sm" className="rounded-xl" onClick={handleRefreshAll} disabled={isLoading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh All
        </Button>
      </div>

      {/* Overview Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <FileStack className="h-4 w-4 text-primary" />
              </div>
              <div>
                {tcLoading ? (
                  <Skeleton className="h-7 w-20" />
                ) : (
                  <p className="text-2xl font-bold">
                    {torrentCount?.total_torrents_readable ?? formatNumber(torrentCount?.total_torrents ?? 0)}
                  </p>
                )}
                <p className="text-xs text-muted-foreground">Total Torrents</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-blue-500/10">
                <Database className="h-4 w-4 text-blue-500" />
              </div>
              <div>
                {mcLoading ? (
                  <Skeleton className="h-7 w-20" />
                ) : (
                  <p className="text-2xl font-bold">
                    {formatNumber(
                      (metadataCount?.movies ?? 0) + (metadataCount?.series ?? 0) + (metadataCount?.tv_channels ?? 0),
                    )}
                  </p>
                )}
                <p className="text-xs text-muted-foreground">Metadata Entries</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-cyan-500/10">
                <Users className="h-4 w-4 text-cyan-500" />
              </div>
              <div>
                {usLoading ? (
                  <Skeleton className="h-7 w-20" />
                ) : (
                  <p className="text-2xl font-bold">{formatNumber(userStats?.total_users ?? 0)}</p>
                )}
                <p className="text-xs text-muted-foreground">Total Users</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-green-500/10">
                <UserCheck className="h-4 w-4 text-green-500" />
              </div>
              <div>
                {usLoading ? (
                  <Skeleton className="h-7 w-20" />
                ) : (
                  <p className="text-2xl font-bold">{formatNumber(userStats?.active_users?.daily ?? 0)}</p>
                )}
                <p className="text-xs text-muted-foreground">Active Today</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-emerald-500/10">
                <Zap className="h-4 w-4 text-emerald-500" />
              </div>
              <div>
                {rmLoading ? (
                  <Skeleton className="h-7 w-20" />
                ) : (
                  <p className="text-2xl font-bold">
                    {formatNumber(redisMetrics?.connections?.connected_clients ?? 0)}
                  </p>
                )}
                <p className="text-xs text-muted-foreground">Redis Clients</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-orange-500/10">
                <Activity className="h-4 w-4 text-orange-500" />
              </div>
              <div>
                {sjLoading ? (
                  <Skeleton className="h-7 w-20" />
                ) : (
                  <div className="flex items-center gap-2">
                    <p className="text-2xl font-bold">{schedulerData?.active ?? 0}</p>
                    <span className="text-xs text-muted-foreground">/ {schedulerData?.total ?? 0}</span>
                  </div>
                )}
                <p className="text-xs text-muted-foreground">Active Scrapers</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Detailed Metrics Tabs */}
      <Tabs defaultValue="overview" className="space-y-6">
        <TabsList className="flex flex-wrap gap-1 w-full max-w-4xl p-1 bg-muted/50 rounded-xl h-auto">
          <TabsTrigger value="overview" className="rounded-lg">
            Overview
          </TabsTrigger>
          <TabsTrigger value="users" className="rounded-lg">
            Users
          </TabsTrigger>
          <TabsTrigger value="scrapers" className="rounded-lg">
            Scrapers
          </TabsTrigger>
          <TabsTrigger value="scraper-runs" className="rounded-lg">
            Scraper Runs
          </TabsTrigger>
          <TabsTrigger value="torrents" className="rounded-lg">
            Torrents
          </TabsTrigger>
          <TabsTrigger value="metadata" className="rounded-lg">
            Metadata
          </TabsTrigger>
          <TabsTrigger value="activity" className="rounded-lg">
            Activity
          </TabsTrigger>
          <TabsTrigger value="redis" className="rounded-lg">
            Redis
          </TabsTrigger>
          <TabsTrigger value="debrid" className="rounded-lg">
            Debrid
          </TabsTrigger>
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="space-y-6">
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {/* System Stats */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Server className="h-5 w-5 text-primary" />
                  System
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {soLoading ? (
                  <div className="space-y-2">
                    {[...Array(3)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Total Torrents</span>
                      <span className="font-bold">{systemOverview?.torrents?.formatted ?? '0'}</span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Total Content</span>
                      <span className="font-bold">{formatNumber(systemOverview?.content?.total ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-1.5">
                      <span className="text-muted-foreground">Pending Moderation</span>
                      <Badge
                        variant={
                          systemOverview?.moderation?.pending_contributions &&
                          systemOverview.moderation.pending_contributions > 0
                            ? 'destructive'
                            : 'secondary'
                        }
                      >
                        {systemOverview?.moderation?.pending_contributions ?? 0}
                      </Badge>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            {/* User Summary */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Users className="h-5 w-5 text-cyan-500" />
                  Users
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {usLoading ? (
                  <div className="space-y-2">
                    {[...Array(4)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Total Users</span>
                      <span className="font-bold">{formatNumber(userStats?.total_users ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Active Today</span>
                      <span className="font-bold text-green-500">
                        {formatNumber(userStats?.active_users?.daily ?? 0)}
                      </span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Active This Week</span>
                      <span className="font-bold">{formatNumber(userStats?.active_users?.weekly ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-1.5">
                      <span className="text-muted-foreground">New This Week</span>
                      <Badge variant="outline" className="text-emerald-500 border-emerald-500">
                        +{userStats?.new_users_this_week ?? 0}
                      </Badge>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            {/* Contributions Summary */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <GitPullRequest className="h-5 w-5 text-pink-500" />
                  Contributions
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {csLoading ? (
                  <div className="space-y-2">
                    {[...Array(4)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Total</span>
                      <span className="font-bold">{formatNumber(contributionStats?.total_contributions ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Pending Review</span>
                      <Badge
                        variant={
                          contributionStats?.pending_review && contributionStats.pending_review > 0
                            ? 'default'
                            : 'secondary'
                        }
                      >
                        {contributionStats?.pending_review ?? 0}
                      </Badge>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">This Week</span>
                      <span className="font-bold">{contributionStats?.recent_contributions_week ?? 0}</span>
                    </div>
                    <div className="flex justify-between py-1.5">
                      <span className="text-muted-foreground">Contributors</span>
                      <span className="font-bold">{formatNumber(contributionStats?.unique_contributors ?? 0)}</span>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            {/* Activity Summary */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Eye className="h-5 w-5 text-blue-500" />
                  Activity
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {asLoading ? (
                  <div className="space-y-2">
                    {[...Array(4)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Watch History</span>
                      <span className="font-bold">
                        {formatNumber(activityStats?.watch_history?.total_entries ?? 0)}
                      </span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Total Plays</span>
                      <span className="font-bold">{formatNumber(activityStats?.playback?.total_plays ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Library Items</span>
                      <span className="font-bold">{formatNumber(activityStats?.library?.total_items ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-1.5">
                      <span className="text-muted-foreground">Active RSS Feeds</span>
                      <span className="font-bold">{activityStats?.rss_feeds?.active ?? 0}</span>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            {/* Content Breakdown */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Film className="h-5 w-5 text-orange-500" />
                  Content
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {mcLoading ? (
                  <div className="space-y-2">
                    {[...Array(3)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground flex items-center gap-2">
                        <Film className="h-4 w-4" /> Movies
                      </span>
                      <span className="font-bold">{formatNumber(metadataCount?.movies ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground flex items-center gap-2">
                        <Tv className="h-4 w-4" /> Series
                      </span>
                      <span className="font-bold">{formatNumber(metadataCount?.series ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-1.5">
                      <span className="text-muted-foreground flex items-center gap-2">
                        <Radio className="h-4 w-4" /> TV Channels
                      </span>
                      <span className="font-bold">{formatNumber(metadataCount?.tv_channels ?? 0)}</span>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            {/* Redis Quick Stats */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Server className="h-5 w-5 text-red-500" />
                  Redis
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {rmLoading ? (
                  <div className="space-y-2">
                    {[...Array(3)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Memory Used</span>
                      <span className="font-bold">{redisMetrics?.memory?.used_memory_human ?? 'N/A'}</span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-border/30">
                      <span className="text-muted-foreground">Ops/sec</span>
                      <span className="font-bold">
                        {formatNumber(redisMetrics?.performance?.instantaneous_ops_per_sec ?? 0)}
                      </span>
                    </div>
                    <div className="flex justify-between py-1.5">
                      <span className="text-muted-foreground">Cache Hit Rate</span>
                      <Badge
                        variant={
                          redisMetrics?.cache?.hit_rate && redisMetrics.cache.hit_rate > 80 ? 'default' : 'secondary'
                        }
                      >
                        {redisMetrics?.cache?.hit_rate?.toFixed(1) ?? 0}%
                      </Badge>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* Users Tab */}
        <TabsContent value="users" className="space-y-6">
          <div className="grid gap-6 md:grid-cols-2">
            {/* User Counts */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Users className="h-5 w-5 text-cyan-500" />
                  User Statistics
                </CardTitle>
                <CardDescription>Total and active user counts</CardDescription>
              </CardHeader>
              <CardContent>
                {usLoading ? (
                  <div className="space-y-4">
                    {[...Array(5)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div className="p-4 rounded-xl bg-cyan-500/10 text-center">
                        <p className="text-3xl font-bold text-cyan-500">{formatNumber(userStats?.total_users ?? 0)}</p>
                        <p className="text-xs text-muted-foreground mt-1">Total Users</p>
                      </div>
                      <div className="p-4 rounded-xl bg-green-500/10 text-center">
                        <p className="text-3xl font-bold text-green-500">
                          {formatNumber(userStats?.active_users?.daily ?? 0)}
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">Active Today</p>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <div className="flex justify-between py-2 border-b border-border/30">
                        <span className="text-muted-foreground">Active This Week</span>
                        <span className="font-bold">{formatNumber(userStats?.active_users?.weekly ?? 0)}</span>
                      </div>
                      <div className="flex justify-between py-2 border-b border-border/30">
                        <span className="text-muted-foreground">Active This Month</span>
                        <span className="font-bold">{formatNumber(userStats?.active_users?.monthly ?? 0)}</span>
                      </div>
                      <div className="flex justify-between py-2 border-b border-border/30">
                        <span className="text-muted-foreground">New This Week</span>
                        <Badge variant="outline" className="text-emerald-500 border-emerald-500">
                          +{userStats?.new_users_this_week ?? 0}
                        </Badge>
                      </div>
                      <div className="flex justify-between py-2 border-b border-border/30">
                        <span className="text-muted-foreground">Verified Users</span>
                        <span className="font-bold">{formatNumber(userStats?.verified_users ?? 0)}</span>
                      </div>
                      <div className="flex justify-between py-2">
                        <span className="text-muted-foreground">Total Profiles</span>
                        <span className="font-bold">{formatNumber(userStats?.total_profiles ?? 0)}</span>
                      </div>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Users by Role */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Shield className="h-5 w-5 text-primary" />
                  Users by Role
                </CardTitle>
                <CardDescription>Distribution across roles</CardDescription>
              </CardHeader>
              <CardContent>
                {usLoading ? (
                  <div className="space-y-4">
                    {[...Array(4)].map((_, i) => (
                      <Skeleton key={i} className="h-8 w-full" />
                    ))}
                  </div>
                ) : userStats?.users_by_role ? (
                  <div className="space-y-3">
                    {Object.entries(userStats.users_by_role).map(([role, count]) => (
                      <div key={role} className="flex items-center justify-between p-3 rounded-lg bg-muted/50">
                        <span className="font-medium capitalize">{role}</span>
                        <Badge variant="secondary">{formatNumber(count)}</Badge>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-center text-muted-foreground py-8">No role data available</p>
                )}
              </CardContent>
            </Card>

            {/* Contribution Levels */}
            <Card className="glass border-border/50 md:col-span-2">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Award className="h-5 w-5 text-primary" />
                  Users by Contribution Level
                </CardTitle>
                <CardDescription>Distribution by contribution level</CardDescription>
              </CardHeader>
              <CardContent>
                {usLoading ? (
                  <div className="grid grid-cols-4 gap-4">
                    {[...Array(4)].map((_, i) => (
                      <Skeleton key={i} className="h-20" />
                    ))}
                  </div>
                ) : userStats?.users_by_contribution_level ? (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {Object.entries(userStats.users_by_contribution_level).map(([level, count]) => (
                      <div key={level} className="p-4 rounded-xl bg-muted/50 text-center">
                        <p className="text-2xl font-bold">{formatNumber(count)}</p>
                        <p className="text-xs text-muted-foreground mt-1 capitalize">{level}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-center text-muted-foreground py-8">No contribution level data available</p>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* Scrapers Tab - Detailed scheduler info */}
        <TabsContent value="scrapers" className="space-y-6">
          {sjLoading ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {[...Array(6)].map((_, i) => (
                <Card key={i} className="glass border-border/50">
                  <CardContent className="p-4">
                    <Skeleton className="h-5 w-1/2 mb-3" />
                    <div className="space-y-2">
                      <Skeleton className="h-4 w-full" />
                      <Skeleton className="h-4 w-3/4" />
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <>
              {schedulerData?.global_scheduler_disabled && (
                <div className="p-4 rounded-xl bg-primary/10 border border-primary/30 flex items-center gap-3">
                  <Pause className="h-5 w-5 text-primary" />
                  <p className="text-sm text-primary">Global scheduler is disabled. No scheduled jobs are running.</p>
                </div>
              )}

              {groupedJobs.enabledWithStats.length > 0 && (
                <div>
                  <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                    <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                    Enabled Scrapers with Stats ({groupedJobs.enabledWithStats.length})
                  </h3>
                  <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {groupedJobs.enabledWithStats.map((job) => (
                      <SchedulerJobCard key={job.id} job={job} />
                    ))}
                  </div>
                </div>
              )}

              {groupedJobs.enabledWithoutStats.length > 0 && (
                <div>
                  <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                    <Clock className="h-5 w-5 text-blue-500" />
                    Enabled Scrapers - Pending First Run ({groupedJobs.enabledWithoutStats.length})
                  </h3>
                  <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {groupedJobs.enabledWithoutStats.map((job) => (
                      <SchedulerJobCard key={job.id} job={job} />
                    ))}
                  </div>
                </div>
              )}

              {groupedJobs.disabledWithStats.length > 0 && (
                <div>
                  <h3 className="text-lg font-semibold mb-4 flex items-center gap-2 text-muted-foreground">
                    <XCircle className="h-5 w-5" />
                    Disabled Scrapers with Stats ({groupedJobs.disabledWithStats.length})
                  </h3>
                  <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {groupedJobs.disabledWithStats.map((job) => (
                      <SchedulerJobCard key={job.id} job={job} />
                    ))}
                  </div>
                </div>
              )}

              {groupedJobs.disabledWithoutStats.length > 0 && (
                <div>
                  <h3 className="text-lg font-semibold mb-4 flex items-center gap-2 text-muted-foreground">
                    <Pause className="h-5 w-5" />
                    Disabled Scrapers ({groupedJobs.disabledWithoutStats.length})
                  </h3>
                  <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {groupedJobs.disabledWithoutStats.map((job) => (
                      <SchedulerJobCard key={job.id} job={job} />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </TabsContent>

        {/* Scraper Runs Tab - Live scraper metrics from individual runs */}
        <TabsContent value="scraper-runs" className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-semibold flex items-center gap-2">
                <Activity className="h-5 w-5 text-cyan-500" />
                Live Scraper Run Metrics
              </h3>
              <p className="text-sm text-muted-foreground mt-1">Real-time metrics from individual scraper executions</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => refetchSm()} disabled={smLoading}>
              <RefreshCw className={`h-4 w-4 mr-2 ${smLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>

          {selectedScraper ? (
            <ScraperHistoryPanel
              scraperName={selectedScraper}
              history={scraperHistory?.history ?? []}
              isLoading={shLoading}
              onClose={() => setSelectedScraper(null)}
            />
          ) : smLoading ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {[...Array(6)].map((_, i) => (
                <Card key={i} className="glass border-border/50">
                  <CardContent className="p-4">
                    <Skeleton className="h-5 w-1/2 mb-3" />
                    <div className="space-y-2">
                      <Skeleton className="h-4 w-full" />
                      <Skeleton className="h-4 w-3/4" />
                      <Skeleton className="h-16 w-full" />
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : scraperMetrics?.scrapers && scraperMetrics.scrapers.length > 0 ? (
            <>
              {/* Summary Stats */}
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Card className="glass border-border/50">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-xl bg-cyan-500/10">
                        <Activity className="h-4 w-4 text-cyan-500" />
                      </div>
                      <div>
                        <p className="text-2xl font-bold">{scraperMetrics.total_scrapers}</p>
                        <p className="text-xs text-muted-foreground">Active Scrapers</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                <Card className="glass border-border/50">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-xl bg-emerald-500/10">
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                      </div>
                      <div>
                        <p className="text-2xl font-bold">
                          {formatNumber(
                            scraperMetrics.scrapers.reduce(
                              (sum, s) => sum + (s.aggregated?.total_items_processed ?? 0),
                              0,
                            ),
                          )}
                        </p>
                        <p className="text-xs text-muted-foreground">Total Processed</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                <Card className="glass border-border/50">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-xl bg-blue-500/10">
                        <Clock className="h-4 w-4 text-blue-500" />
                      </div>
                      <div>
                        <p className="text-2xl font-bold">
                          {formatNumber(
                            scraperMetrics.scrapers.reduce((sum, s) => sum + (s.aggregated?.total_runs ?? 0), 0),
                          )}
                        </p>
                        <p className="text-xs text-muted-foreground">Total Runs</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                <Card className="glass border-border/50">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-xl bg-red-500/10">
                        <XCircle className="h-4 w-4 text-red-500" />
                      </div>
                      <div>
                        <p className="text-2xl font-bold">
                          {formatNumber(
                            scraperMetrics.scrapers.reduce((sum, s) => sum + (s.aggregated?.total_errors ?? 0), 0),
                          )}
                        </p>
                        <p className="text-xs text-muted-foreground">Total Errors</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>

              {/* Scraper Cards */}
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {scraperMetrics.scrapers.map((scraper) => (
                  <ScraperMetricsCard key={scraper.scraper_name} scraper={scraper} onViewHistory={setSelectedScraper} />
                ))}
              </div>
            </>
          ) : (
            <div className="text-center py-12">
              <Activity className="h-12 w-12 mx-auto mb-4 text-muted-foreground opacity-50" />
              <h3 className="text-lg font-semibold mb-2">No Scraper Metrics Yet</h3>
              <p className="text-muted-foreground max-w-md mx-auto">
                Scraper metrics will appear here after scrapers have been executed. Run a search or wait for scheduled
                scrapers to execute.
              </p>
            </div>
          )}
        </TabsContent>

        {/* Torrents Tab */}
        <TabsContent value="torrents" className="space-y-6">
          <div className="grid gap-6 md:grid-cols-2">
            {/* Torrent Sources */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg">Top 20 Torrent Sources</CardTitle>
                <CardDescription>Distribution by source</CardDescription>
              </CardHeader>
              <CardContent>
                {tsLoading ? (
                  <div className="space-y-4">
                    {[...Array(5)].map((_, i) => (
                      <div key={i} className="space-y-2">
                        <Skeleton className="h-4 w-1/3" />
                        <Skeleton className="h-2 w-full" />
                      </div>
                    ))}
                  </div>
                ) : (
                  <ScrollArea className="h-[400px] pr-2">
                    <div className="space-y-4">
                      {torrentSources?.map((source) => (
                        <div key={source.name} className="space-y-2">
                          <div className="flex items-center justify-between text-sm">
                            <span className="font-medium truncate">{source.name}</span>
                            <span className="text-muted-foreground">{formatNumber(source.count)}</span>
                          </div>
                          <Progress
                            value={(source.count / (torrentCount?.total_torrents ?? 1)) * 100}
                            className="h-1.5"
                          />
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                )}
              </CardContent>
            </Card>

            {/* Top Uploaders */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg">Top 20 Uploaders</CardTitle>
                <CardDescription>Most active contributors (all time)</CardDescription>
              </CardHeader>
              <CardContent>
                {uLoading ? (
                  <div className="space-y-3">
                    {[...Array(5)].map((_, i) => (
                      <div key={i} className="flex items-center gap-3">
                        <Skeleton className="h-8 w-8 rounded-full" />
                        <Skeleton className="h-4 w-1/2" />
                        <Skeleton className="h-4 w-16 ml-auto" />
                      </div>
                    ))}
                  </div>
                ) : (
                  <ScrollArea className="h-[400px] pr-2">
                    <div className="space-y-3">
                      {uploaders?.map((uploader, idx) => (
                        <div key={uploader.name} className="flex items-center gap-3">
                          <div className="flex items-center justify-center h-8 w-8 rounded-full bg-primary/10 text-sm font-medium">
                            {idx + 1}
                          </div>
                          <span className="font-medium truncate flex-1">{uploader.name}</span>
                          <Badge variant="secondary">{formatNumber(uploader.count)}</Badge>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Weekly Uploaders */}
          <Card className="glass border-border/50">
            <CardHeader>
              <CardTitle className="text-lg">Weekly Top Uploaders</CardTitle>
              <CardDescription>Contribution streams by week</CardDescription>
            </CardHeader>
            <CardContent>
              {/* Week Navigation */}
              <div className="flex items-center justify-center gap-4 mb-6 p-3 rounded-xl bg-muted/50">
                <Button variant="outline" size="sm" onClick={handlePrevWeek}>
                  <ChevronLeft className="h-4 w-4 mr-1" />
                  Previous
                </Button>
                <input
                  type="date"
                  value={selectedWeek}
                  onChange={(e) => setSelectedWeek(e.target.value)}
                  className="px-3 py-1.5 rounded-lg bg-background border border-border text-sm"
                />
                <Button variant="outline" size="sm" onClick={handleNextWeek}>
                  Next
                  <ChevronRight className="h-4 w-4 ml-1" />
                </Button>
              </div>

              {weeklyUploaders && !weeklyUploaders.error && (
                <div className="text-center mb-4 text-sm text-muted-foreground">
                  {new Date(weeklyUploaders.week_start).toLocaleDateString()} -{' '}
                  {new Date(weeklyUploaders.week_end).toLocaleDateString()}
                </div>
              )}

              {wuLoading ? (
                <div className="space-y-3">
                  {[...Array(5)].map((_, i) => (
                    <div key={i} className="flex items-center gap-3">
                      <Skeleton className="h-8 w-8 rounded-full" />
                      <Skeleton className="h-4 w-1/2" />
                      <Skeleton className="h-4 w-16 ml-auto" />
                    </div>
                  ))}
                </div>
              ) : weeklyUploaders?.error ? (
                <p className="text-center text-muted-foreground py-8">{weeklyUploaders.error}</p>
              ) : weeklyUploaders?.uploaders && weeklyUploaders.uploaders.length > 0 ? (
                <ScrollArea className="h-[400px] pr-2">
                  <div className="space-y-3">
                    {weeklyUploaders.uploaders.map((uploader, idx) => (
                      <div key={uploader.name} className="flex items-center gap-3">
                        <div className="flex items-center justify-center h-8 w-8 rounded-full bg-pink-500/10 text-sm font-medium">
                          {idx + 1}
                        </div>
                        <span className="font-medium truncate flex-1">{uploader.name}</span>
                        <Badge variant="secondary">{formatNumber(uploader.count)}</Badge>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              ) : (
                <p className="text-center text-muted-foreground py-8">No uploads for this week</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Metadata Tab */}
        <TabsContent value="metadata" className="space-y-6">
          <Card className="glass border-border/50">
            <CardHeader>
              <CardTitle className="text-lg">Metadata Distribution</CardTitle>
              <CardDescription>Breakdown by content type</CardDescription>
            </CardHeader>
            <CardContent>
              {mcLoading ? (
                <div className="flex justify-center py-8">
                  <Skeleton className="h-48 w-48 rounded-full" />
                </div>
              ) : metadataCount ? (
                <div className="flex flex-col items-center">
                  <PieChart data={metadataPieData} />

                  {/* Detailed breakdown */}
                  <div className="grid grid-cols-3 gap-6 mt-8 w-full max-w-lg">
                    <div className="text-center p-4 rounded-xl bg-primary/10">
                      <Film className="h-6 w-6 text-primary mx-auto mb-2" />
                      <p className="text-2xl font-bold">{formatNumber(metadataCount.movies)}</p>
                      <p className="text-xs text-muted-foreground">Movies</p>
                    </div>
                    <div className="text-center p-4 rounded-xl bg-blue-500/10">
                      <Tv className="h-6 w-6 text-blue-500 mx-auto mb-2" />
                      <p className="text-2xl font-bold">{formatNumber(metadataCount.series)}</p>
                      <p className="text-xs text-muted-foreground">Series</p>
                    </div>
                    <div className="text-center p-4 rounded-xl bg-emerald-500/10">
                      <Radio className="h-6 w-6 text-emerald-500 mx-auto mb-2" />
                      <p className="text-2xl font-bold">{formatNumber(metadataCount.tv_channels)}</p>
                      <p className="text-xs text-muted-foreground">TV Channels</p>
                    </div>
                  </div>
                </div>
              ) : (
                <p className="text-center text-muted-foreground py-8">No metadata available</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Activity Tab */}
        <TabsContent value="activity" className="space-y-6">
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {/* Watch History */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Eye className="h-5 w-5 text-blue-500" />
                  Watch History
                </CardTitle>
              </CardHeader>
              <CardContent>
                {asLoading ? (
                  <div className="space-y-2">
                    {[...Array(3)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="p-4 rounded-xl bg-blue-500/10 text-center">
                      <p className="text-3xl font-bold text-blue-500">
                        {formatNumber(activityStats?.watch_history?.total_entries ?? 0)}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1">Total Entries</p>
                    </div>
                    <div className="flex justify-between py-2 border-b border-border/30">
                      <span className="text-muted-foreground">Recent (7 days)</span>
                      <span className="font-bold">{formatNumber(activityStats?.watch_history?.recent_week ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-2">
                      <span className="text-muted-foreground">Unique Users</span>
                      <span className="font-bold">{formatNumber(activityStats?.watch_history?.unique_users ?? 0)}</span>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Playback Stats */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Play className="h-5 w-5 text-emerald-500" />
                  Playback
                </CardTitle>
              </CardHeader>
              <CardContent>
                {asLoading ? (
                  <div className="space-y-2">
                    {[...Array(2)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="p-4 rounded-xl bg-emerald-500/10 text-center">
                      <p className="text-3xl font-bold text-emerald-500">
                        {formatNumber(activityStats?.playback?.total_plays ?? 0)}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1">Total Plays</p>
                    </div>
                    <div className="flex justify-between py-2">
                      <span className="text-muted-foreground">Tracking Entries</span>
                      <span className="font-bold">{formatNumber(activityStats?.playback?.total_entries ?? 0)}</span>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Downloads */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Download className="h-5 w-5 text-primary" />
                  Downloads
                </CardTitle>
              </CardHeader>
              <CardContent>
                {asLoading ? (
                  <Skeleton className="h-20 w-full" />
                ) : (
                  <div className="p-4 rounded-xl bg-primary/10 text-center">
                    <p className="text-3xl font-bold text-primary">
                      {formatNumber(activityStats?.downloads?.total ?? 0)}
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">Total Downloads</p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Library */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Bookmark className="h-5 w-5 text-pink-500" />
                  Library
                </CardTitle>
              </CardHeader>
              <CardContent>
                {asLoading ? (
                  <Skeleton className="h-20 w-full" />
                ) : (
                  <div className="p-4 rounded-xl bg-pink-500/10 text-center">
                    <p className="text-3xl font-bold text-pink-500">
                      {formatNumber(activityStats?.library?.total_items ?? 0)}
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">Library Items</p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* RSS Feeds */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Rss className="h-5 w-5 text-orange-500" />
                  RSS Feeds
                </CardTitle>
              </CardHeader>
              <CardContent>
                {asLoading ? (
                  <div className="space-y-2">
                    {[...Array(2)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="flex justify-between py-2 border-b border-border/30">
                      <span className="text-muted-foreground">Total Feeds</span>
                      <span className="font-bold">{activityStats?.rss_feeds?.total ?? 0}</span>
                    </div>
                    <div className="flex justify-between py-2">
                      <span className="text-muted-foreground">Active Feeds</span>
                      <Badge variant="default" className="bg-emerald-500">
                        {activityStats?.rss_feeds?.active ?? 0}
                      </Badge>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Contributions Summary */}
            <Card className="glass border-border/50">
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <GitPullRequest className="h-5 w-5 text-cyan-500" />
                  Contributions
                </CardTitle>
              </CardHeader>
              <CardContent>
                {csLoading ? (
                  <div className="space-y-2">
                    {[...Array(3)].map((_, i) => (
                      <Skeleton key={i} className="h-4 w-full" />
                    ))}
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="flex justify-between py-2 border-b border-border/30">
                      <span className="text-muted-foreground">Total Contributions</span>
                      <span className="font-bold">{formatNumber(contributionStats?.total_contributions ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-2 border-b border-border/30">
                      <span className="text-muted-foreground">Stream Votes</span>
                      <span className="font-bold">{formatNumber(contributionStats?.total_stream_votes ?? 0)}</span>
                    </div>
                    <div className="flex justify-between py-2">
                      <span className="text-muted-foreground">Metadata Votes</span>
                      <span className="font-bold">{formatNumber(contributionStats?.total_metadata_votes ?? 0)}</span>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* Redis Tab */}
        <TabsContent value="redis" className="space-y-6">
          <RedisMetricsDisplay metrics={redisMetrics} isLoading={rmLoading} />
        </TabsContent>

        {/* Debrid Tab */}
        <TabsContent value="debrid" className="space-y-6">
          <Card className="glass border-border/50">
            <CardHeader>
              <CardTitle className="text-lg">Debrid Cache Status</CardTitle>
              <CardDescription>Cached torrents on debrid services</CardDescription>
            </CardHeader>
            <CardContent>
              {dcLoading ? (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {[...Array(6)].map((_, i) => (
                    <div key={i} className="p-4 rounded-xl bg-muted/50">
                      <Skeleton className="h-4 w-1/2 mb-2" />
                      <Skeleton className="h-8 w-20" />
                    </div>
                  ))}
                </div>
              ) : debridCache?.error ? (
                <div className="text-center py-8 text-muted-foreground">
                  <HardDrive className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>{debridCache.error}</p>
                </div>
              ) : debridCache?.services && Object.keys(debridCache.services).length > 0 ? (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(debridCache.services).map(([service, stats]) => (
                    <div key={service} className="p-4 rounded-xl bg-muted/50 border border-border/50">
                      <p className="text-sm text-muted-foreground mb-1 capitalize">{service}</p>
                      <p className="text-2xl font-bold">{formatNumber(stats.cached_torrents)}</p>
                      <p className="text-xs text-muted-foreground mt-1">cached torrents</p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <HardDrive className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>No debrid cache metrics available</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
