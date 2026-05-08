import {
  Server,
  Database,
  Gauge,
  Activity,
  Zap,
  Clock,
  RefreshCw,
  HardDrive,
  Table2,
  TrendingUp,
  AlertTriangle,
  CheckCircle,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useDatabaseStats, useTableList } from '../hooks/useDatabaseData'
import { formatUptime, formatNumber, getTableTypeColor } from '../types'

interface OverviewTabProps {
  onTableClick?: (tableName: string) => void
}

// Stats card component
function StatCard({
  icon: Icon,
  label,
  value,
  subValue,
  color = 'blue',
  trend,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string | number
  subValue?: string
  color?: string
  trend?: 'up' | 'down' | 'stable'
}) {
  return (
    <Card className="bg-card/50 border-border/50">
      <CardContent className="p-4">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'p-2.5 rounded-xl',
              color === 'blue' && 'bg-blue-500/10',
              color === 'emerald' && 'bg-emerald-500/10',
              color === 'amber' && 'bg-primary/10',
              color === 'violet' && 'bg-primary/10',
              color === 'rose' && 'bg-rose-500/10',
              color === 'cyan' && 'bg-cyan-500/10',
            )}
          >
            <Icon
              className={cn(
                'h-5 w-5',
                color === 'blue' && 'text-blue-400',
                color === 'emerald' && 'text-emerald-400',
                color === 'amber' && 'text-primary',
                color === 'violet' && 'text-primary',
                color === 'rose' && 'text-rose-400',
                color === 'cyan' && 'text-cyan-400',
              )}
            />
          </div>
          <div className="flex-1">
            <p className="text-sm text-muted-foreground">{label}</p>
            <div className="flex items-center gap-2">
              <p className="text-xl font-bold">{value}</p>
              {trend && (
                <TrendingUp
                  className={cn(
                    'h-4 w-4',
                    trend === 'up' && 'text-emerald-400',
                    trend === 'down' && 'text-rose-400 rotate-180',
                    trend === 'stable' && 'text-muted-foreground rotate-90',
                  )}
                />
              )}
            </div>
            {subValue && <p className="text-xs text-muted-foreground">{subValue}</p>}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// Table row component
function TableRow({
  name,
  rowCount,
  sizeHuman,
  lastVacuum,
  onClick,
}: {
  name: string
  rowCount: number
  sizeHuman: string
  lastVacuum: string | null
  onClick: () => void
}) {
  const colors = getTableTypeColor(name)

  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full p-3 rounded-xl border transition-all text-left',
        'hover:scale-[1.01] hover:shadow-md',
        'bg-card/30 hover:bg-card/50',
        'border-border/40 hover:border-border/60',
      )}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <div className={cn('p-2 rounded-lg shrink-0', colors.bg)}>
            <Table2 className={cn('h-4 w-4', colors.text)} />
          </div>
          <div className="min-w-0">
            <p className="font-medium truncate">{name}</p>
            <p className="text-xs text-muted-foreground">
              {formatNumber(rowCount)} rows • {sizeHuman}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {lastVacuum ? (
            <Badge variant="secondary" className="text-xs gap-1">
              <CheckCircle className="h-3 w-3 text-emerald-400" />
              Vacuumed
            </Badge>
          ) : (
            <Badge variant="secondary" className="text-xs gap-1 text-primary">
              <AlertTriangle className="h-3 w-3" />
              Never
            </Badge>
          )}
        </div>
      </div>
    </button>
  )
}

// Health indicator component
function HealthIndicator({
  value,
  label,
  good,
  warning,
}: {
  value: number
  label: string
  good: number
  warning: number
}) {
  const status = value >= good ? 'good' : value >= warning ? 'warning' : 'critical'

  return (
    <div className="flex items-center gap-3">
      <div
        className={cn(
          'h-3 w-3 rounded-full',
          status === 'good' && 'bg-emerald-400',
          status === 'warning' && 'bg-amber-400',
          status === 'critical' && 'bg-rose-400',
        )}
      />
      <div className="flex-1">
        <div className="flex items-center justify-between">
          <span className="text-sm">{label}</span>
          <span
            className={cn(
              'text-sm font-medium',
              status === 'good' && 'text-emerald-400',
              status === 'warning' && 'text-primary',
              status === 'critical' && 'text-rose-400',
            )}
          >
            {value.toFixed(1)}%
          </span>
        </div>
        <Progress
          value={value}
          className={cn(
            'h-1.5 mt-1',
            status === 'good' && '[&>[data-slot=indicator]]:bg-emerald-500',
            status === 'warning' && '[&>[data-slot=indicator]]:bg-amber-500',
            status === 'critical' && '[&>[data-slot=indicator]]:bg-rose-500',
          )}
        />
      </div>
    </div>
  )
}

export function OverviewTab({ onTableClick }: OverviewTabProps) {
  const {
    data: stats,
    isLoading: statsLoading,
    refetch: refetchStats,
    isRefetching: statsRefetching,
  } = useDatabaseStats()
  const {
    data: tables,
    isLoading: tablesLoading,
    refetch: refetchTables,
    isRefetching: tablesRefetching,
  } = useTableList()

  const isLoading = statsLoading || tablesLoading
  const isRefetching = statsRefetching || tablesRefetching

  const handleRefresh = () => {
    refetchStats()
    refetchTables()
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          {[...Array(6)].map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Skeleton className="h-96 rounded-xl" />
          <Skeleton className="h-96 rounded-xl" />
        </div>
      </div>
    )
  }

  // Calculate connection usage percentage
  const connectionUsage = stats ? (stats.connection_count / stats.max_connections) * 100 : 0

  // Sort tables by size
  const sortedTables = [...(tables?.tables || [])].sort((a, b) => b.size_bytes - a.size_bytes)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">PostgreSQL Database</h3>
          <p className="text-sm text-muted-foreground">
            {stats?.database_name} • {stats?.version?.split(' ').slice(0, 2).join(' ')}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={handleRefresh} disabled={isRefetching} className="gap-2">
          <RefreshCw className={cn('h-4 w-4', isRefetching && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <StatCard icon={HardDrive} label="Database Size" value={stats?.size_human || '0 B'} color="blue" />
        <StatCard
          icon={Table2}
          label="Total Tables"
          value={tables?.total_count || 0}
          subValue={`${tables?.total_size_human || '0 B'} total`}
          color="violet"
        />
        <StatCard
          icon={Gauge}
          label="Cache Hit Ratio"
          value={stats?.cache_hit_ratio ? `${stats.cache_hit_ratio.toFixed(1)}%` : '—'}
          color="emerald"
        />
        <StatCard
          icon={Activity}
          label="Connections"
          value={`${stats?.connection_count || 0}/${stats?.max_connections || 100}`}
          color="amber"
        />
        <StatCard icon={Zap} label="Active Queries" value={stats?.active_queries || 0} color="cyan" />
        <StatCard
          icon={Clock}
          label="Uptime"
          value={stats?.uptime_seconds ? formatUptime(stats.uptime_seconds) : '—'}
          color="rose"
        />
      </div>

      {/* Two Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Health Card */}
        <Card className="bg-card/50 border-border/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Server className="h-4 w-4 text-muted-foreground" />
              Database Health
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <HealthIndicator value={stats?.cache_hit_ratio || 0} label="Cache Hit Ratio" good={95} warning={80} />
            <HealthIndicator value={100 - connectionUsage} label="Connection Availability" good={50} warning={20} />

            <div className="pt-4 border-t border-border/50 space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Transactions Committed</span>
                <span className="font-mono">{formatNumber(stats?.transactions_committed || 0)}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Transactions Rolled Back</span>
                <span className="font-mono">{formatNumber(stats?.transactions_rolled_back || 0)}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Deadlocks</span>
                <span className={cn('font-mono', (stats?.deadlocks || 0) > 0 ? 'text-rose-400' : 'text-emerald-400')}>
                  {stats?.deadlocks || 0}
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Tables Card */}
        <Card className="bg-card/50 border-border/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Database className="h-4 w-4 text-muted-foreground" />
                Tables by Size
              </div>
              <Badge variant="secondary" className="font-mono text-xs">
                {tables?.total_count || 0} tables
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-72">
              <div className="space-y-2 pr-4">
                {sortedTables.map((table) => (
                  <TableRow
                    key={table.name}
                    name={table.name}
                    rowCount={table.row_count}
                    sizeHuman={table.size_human}
                    lastVacuum={table.last_vacuum}
                    onClick={() => onTableClick?.(table.name)}
                  />
                ))}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </div>

      {/* Table Size Distribution */}
      <Card className="bg-card/50 border-border/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-base font-medium flex items-center gap-2">
            <HardDrive className="h-4 w-4 text-muted-foreground" />
            Storage Distribution
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {sortedTables.slice(0, 8).map((table) => {
              const percentage = tables?.total_size_bytes ? (table.size_bytes / tables.total_size_bytes) * 100 : 0
              const colors = getTableTypeColor(table.name)

              return (
                <div key={table.name} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className={cn('font-medium', colors.text)}>{table.name}</span>
                    <span className="text-muted-foreground">
                      {table.size_human} ({percentage.toFixed(1)}%)
                    </span>
                  </div>
                  <div className="h-2 bg-muted/50 rounded-full overflow-hidden">
                    <div
                      className={cn('h-full rounded-full transition-all', colors.bg.replace('/10', '/60'))}
                      style={{ width: `${percentage}%` }}
                    />
                  </div>
                </div>
              )
            })}
            {sortedTables.length > 8 && (
              <p className="text-xs text-muted-foreground text-center pt-2">+ {sortedTables.length - 8} more tables</p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
