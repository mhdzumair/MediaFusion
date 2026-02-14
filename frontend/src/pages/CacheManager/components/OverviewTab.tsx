import { 
  Server, 
  Database, 
  Gauge, 
  Users, 
  Zap, 
  Clock,
  RefreshCw,
  Radio,
  FileJson,
  Film,
  Calendar,
  Layers,
  Search,
  Shield,
  Image,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useCacheStats } from '../hooks/useCacheData'
import { CACHE_TYPES, getTypeColorClasses } from '../types'

interface OverviewTabProps {
  onCacheTypeClick: (pattern: string) => void
}

// Icon mapping for cache types
const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Radio,
  FileJson,
  Database,
  Film,
  Server,
  Users,
  Calendar,
  Layers,
  Search,
  Clock,
  Zap,
  Image,
  Shield,
}

// Stats card component
function StatCard({ 
  icon: Icon, 
  label, 
  value, 
  subValue,
  color = 'blue' 
}: { 
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string | number
  subValue?: string
  color?: string
}) {
  return (
    <Card className="bg-card/50 border-border/50">
      <CardContent className="p-4">
        <div className="flex items-center gap-3">
          <div className={cn(
            "p-2.5 rounded-xl",
            color === 'blue' && "bg-blue-500/10",
            color === 'emerald' && "bg-emerald-500/10",
            color === 'amber' && "bg-primary/10",
            color === 'violet' && "bg-primary/10",
            color === 'rose' && "bg-rose-500/10",
          )}>
            <Icon className={cn(
              "h-5 w-5",
              color === 'blue' && "text-blue-400",
              color === 'emerald' && "text-emerald-400",
              color === 'amber' && "text-primary",
              color === 'violet' && "text-primary",
              color === 'rose' && "text-rose-400",
            )} />
          </div>
          <div>
            <p className="text-sm text-muted-foreground">{label}</p>
            <p className="text-xl font-bold">{value}</p>
            {subValue && (
              <p className="text-xs text-muted-foreground">{subValue}</p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// Cache type card component
function CacheTypeCard({ 
  name, 
  icon, 
  color, 
  description,
  count,
  onClick,
}: { 
  name: string
  icon: string
  color: string
  description: string
  count: number
  onClick: () => void
}) {
  const IconComponent = iconMap[icon] || Database
  const colors = getTypeColorClasses(color)
  
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full p-4 rounded-xl border transition-all text-left",
        "hover:scale-[1.02] hover:shadow-lg",
        colors.bg,
        colors.border,
        "hover:border-opacity-60"
      )}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className={cn("p-2 rounded-lg", colors.bg)}>
            <IconComponent className={cn("h-4 w-4", colors.text)} />
          </div>
          <div>
            <p className={cn("font-medium", colors.text)}>{name}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
          </div>
        </div>
        <Badge variant="secondary" className="text-xs font-mono">
          {count.toLocaleString()}
        </Badge>
      </div>
    </button>
  )
}

export function OverviewTab({ onCacheTypeClick }: OverviewTabProps) {
  const { data: stats, isLoading, refetch, isRefetching } = useCacheStats()
  
  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          {[...Array(5)].map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {[...Array(13)].map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-xl" />
          ))}
        </div>
      </div>
    )
  }
  
  // Backend returns 'redis' not 'redis_info'
  const redisInfo = stats?.redis
  
  // Build a map of cache type name -> keys_count from the array
  const cacheTypeCounts: Record<string, number> = {}
  stats?.cache_types?.forEach(ct => {
    cacheTypeCounts[ct.name.toLowerCase()] = ct.keys_count
  })
  
  return (
    <div className="space-y-6">
      {/* Server Stats */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Redis Server</h3>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refetch()}
          disabled={isRefetching}
          className="gap-2"
        >
          <RefreshCw className={cn("h-4 w-4", isRefetching && "animate-spin")} />
          Refresh
        </Button>
      </div>
      
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard
          icon={Database}
          label="Memory Used"
          value={redisInfo?.memory_used || '0 B'}
          subValue={redisInfo?.memory_peak ? `Peak: ${redisInfo.memory_peak}` : undefined}
          color="blue"
        />
        <StatCard
          icon={Gauge}
          label="Hit Rate"
          value={redisInfo?.hit_rate != null ? `${redisInfo.hit_rate.toFixed(1)}%` : '—'}
          subValue={`${(redisInfo?.total_keys || 0).toLocaleString()} total keys`}
          color="emerald"
        />
        <StatCard
          icon={Zap}
          label="Ops/sec"
          value={redisInfo?.ops_per_sec?.toLocaleString() || '0'}
          color="amber"
        />
        <StatCard
          icon={Users}
          label="Clients"
          value={redisInfo?.connected_clients || 0}
          color="violet"
        />
        <StatCard
          icon={Clock}
          label="Uptime"
          value={redisInfo?.uptime_days != null ? `${redisInfo.uptime_days}d` : '—'}
          color="rose"
        />
      </div>
      
      {/* Cache Types */}
      <div>
        <h3 className="text-lg font-semibold mb-4">Cache Categories</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {CACHE_TYPES.map((cacheType) => (
            <CacheTypeCard
              key={cacheType.name}
              {...cacheType}
              count={cacheTypeCounts[cacheType.name.toLowerCase()] || 0}
              onClick={() => onCacheTypeClick(cacheType.pattern)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

