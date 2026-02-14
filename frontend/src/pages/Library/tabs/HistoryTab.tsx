import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { 
  Clock, 
  Loader2,
  Trash2,
  Play,
  Download,
  Eye,
  ListPlus,
  HardDrive,
  Film,
  Tv,
  Radio,
  Calendar,
  X,
  Zap,
} from 'lucide-react'
import { useInfiniteWatchHistory, useDeleteWatchHistory, useClearWatchHistory, useProfiles } from '@/hooks'
import { useRpdb } from '@/contexts/RpdbContext'
import { PosterCompact } from '@/components/ui/poster'
import { cn } from '@/lib/utils'
import type { WatchAction, WatchHistoryItem, HistorySource } from '@/lib/api'

// Helper functions
function formatTimeAgo(date: string): string {
  const now = new Date()
  const then = new Date(date)
  const diffMs = now.getTime() - then.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`
  // Show full date with year for older entries
  return then.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatBytes(bytes?: number): string {
  if (!bytes) return ''
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${sizes[i]}`
}

function getActionConfig(action: WatchAction) {
  switch (action) {
    case 'WATCHED':
      return {
        icon: Eye,
        label: 'Watched',
        color: 'text-sky-400',
        bg: 'bg-sky-500/10',
        border: 'border-sky-500/20',
      }
    case 'DOWNLOADED':
      return {
        icon: Download,
        label: 'Downloaded',
        color: 'text-emerald-400',
        bg: 'bg-emerald-500/10',
        border: 'border-emerald-500/20',
      }
    case 'QUEUED':
      return {
        icon: ListPlus,
        label: 'Queued',
        color: 'text-amber-400',
        bg: 'bg-amber-500/10',
        border: 'border-amber-500/20',
      }
    default:
      return {
        icon: Eye,
        label: 'Watched',
        color: 'text-sky-400',
        bg: 'bg-sky-500/10',
        border: 'border-sky-500/20',
      }
  }
}

function getMediaTypeConfig(mediaType: string) {
  switch (mediaType) {
    case 'movie':
      return { icon: Film, label: 'Movie', color: 'text-purple-400' }
    case 'series':
      return { icon: Tv, label: 'Series', color: 'text-blue-400' }
    case 'tv':
      return { icon: Radio, label: 'TV', color: 'text-rose-400' }
    default:
      return { icon: Film, label: mediaType, color: 'text-muted-foreground' }
  }
}

// Get route-compatible media type for navigation
function getRouteMediaType(mediaType: string): string {
  // Map 'tv' to 'tv' (it's a valid route), keep others as is
  return mediaType
}

// Get source display configuration
function getSourceConfig(source: HistorySource | string) {
  switch (source) {
    case 'trakt':
      return { label: 'Trakt', color: 'text-red-400' }
    case 'simkl':
      return { label: 'Simkl', color: 'text-cyan-400' }
    case 'manual':
      return { label: 'Manual', color: 'text-amber-400' }
    case 'mediafusion':
    default:
      return { label: 'MediaFusion', color: 'text-primary/80' }
  }
}

// Filter pill component
function FilterPill({ 
  active, 
  onClick, 
  icon: Icon, 
  label, 
  color 
}: { 
  active: boolean
  onClick: () => void
  icon?: React.ElementType
  label: string
  color?: string
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium transition-all',
        active 
          ? 'bg-primary text-primary-foreground shadow-md' 
          : 'bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground'
      )}
    >
      {Icon && <Icon className={cn('h-3.5 w-3.5', active ? '' : color)} />}
      {label}
    </button>
  )
}

// History card component
function HistoryCard({ 
  item, 
  onDelete,
  rpdbApiKey,
}: { 
  item: WatchHistoryItem
  onDelete: () => void
  rpdbApiKey: string | null
}) {
  const action = item.action || 'WATCHED'
  const actionConfig = getActionConfig(action)
  const ActionIcon = actionConfig.icon
  const mediaConfig = getMediaTypeConfig(item.media_type)
  const MediaIcon = mediaConfig.icon
  const progressPercentage = item.duration && item.duration > 0 
    ? Math.round((item.progress / item.duration) * 100) 
    : 0

  // Build URL with season/episode params for series deep linking
  const contentUrl = item.media_type === 'series' && item.season && item.episode
    ? `/dashboard/content/${getRouteMediaType(item.media_type)}/${item.media_id}?season=${item.season}&episode=${item.episode}`
    : `/dashboard/content/${getRouteMediaType(item.media_type)}/${item.media_id}`

  return (
    <div className="group relative">
      <Link 
        to={contentUrl}
        className={cn(
          'block rounded-2xl overflow-hidden transition-all duration-300',
          'bg-gradient-to-br from-card/80 to-card/40 backdrop-blur-sm',
          'border border-border/40 hover:border-primary/40',
          'hover:shadow-lg hover:shadow-primary/5 hover:-translate-y-0.5'
        )}
      >
        <div className="flex gap-4 p-3">
          {/* Poster with overlay - consistent size using PosterCompact */}
          <div className="relative h-28 w-20 rounded-xl overflow-hidden bg-muted flex-shrink-0 shadow-md">
            <PosterCompact
              metaId={item.external_ids?.imdb || `mf:${item.media_id}`}
              catalogType={item.media_type === 'series' ? 'series' : item.media_type === 'tv' ? 'tv' : 'movie'}
              poster={item.poster}
              rpdbApiKey={item.media_type !== 'tv' ? rpdbApiKey : null}
              title={item.title}
              className="h-full w-full"
              overridePoster={item.episode_poster}
            />
            {/* Gradient overlay */}
            <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
            
            {/* Play button overlay */}
            <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/30">
              <div className="w-10 h-10 rounded-full bg-primary/90 flex items-center justify-center shadow-lg">
                <Play className="h-5 w-5 text-primary-foreground fill-primary-foreground ml-0.5" />
              </div>
            </div>

            {/* Action badge on poster */}
            <div className={cn(
              'absolute bottom-1.5 left-1.5 right-1.5 flex items-center justify-center gap-1 py-1 px-2 rounded-lg text-[10px] font-medium',
              actionConfig.bg, actionConfig.border, 'border backdrop-blur-sm'
            )}>
              <ActionIcon className={cn('h-3 w-3', actionConfig.color)} />
              <span className={actionConfig.color}>{actionConfig.label}</span>
            </div>
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0 py-1 flex flex-col justify-between">
            {/* Title and meta */}
            <div>
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-semibold text-sm leading-tight line-clamp-2 group-hover:text-primary transition-colors">
                  {item.title}
                </h3>
              </div>
              
              {/* Episode info */}
              {item.media_type === 'series' && item.season && item.episode && (
                <p className="text-xs text-muted-foreground mt-1">
                  Season {item.season} · Episode {item.episode}
                </p>
              )}

              {/* Media type, source, and time */}
              <div className="flex items-center flex-wrap gap-x-2 gap-y-1 mt-2">
                <span className={cn('flex items-center gap-1 text-xs', mediaConfig.color)}>
                  <MediaIcon className="h-3 w-3" />
                  {mediaConfig.label}
                </span>
                <span className="text-muted-foreground/50">·</span>
                <span className={cn('flex items-center gap-1 text-xs', getSourceConfig(item.source).color)}>
                  <Zap className="h-3 w-3" />
                  {getSourceConfig(item.source).label}
                </span>
                <span className="text-muted-foreground/50">·</span>
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Calendar className="h-3 w-3" />
                  {formatTimeAgo(item.watched_at)}
                </span>
              </div>
            </div>

            {/* Stream info or progress */}
            <div className="mt-auto">
              {item.stream_info && (action === 'DOWNLOADED' || action === 'QUEUED') && (
                <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                  {item.stream_info.quality && (
                    <Badge variant="secondary" className="h-5 px-1.5 text-[10px] font-medium">
                      {item.stream_info.quality}
                    </Badge>
                  )}
                  {item.stream_info.size && (
                    <span className="flex items-center gap-1">
                      <HardDrive className="h-3 w-3" />
                      {formatBytes(item.stream_info.size)}
                    </span>
                  )}
                  {item.stream_info.source && (
                    <span className="truncate max-w-[100px]">{item.stream_info.source}</span>
                  )}
                </div>
              )}
              
              {action === 'WATCHED' && item.duration && item.duration > 0 && (
                <div className="flex items-center gap-2">
                  <Progress 
                    value={progressPercentage} 
                    className="h-1.5 flex-1" 
                  />
                  <span className="text-[11px] text-muted-foreground font-medium min-w-[36px] text-right">
                    {progressPercentage}%
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      </Link>

      {/* Delete button */}
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                onDelete()
              }}
              className={cn(
                'absolute top-2 right-2 p-1.5 rounded-lg transition-all',
                'bg-background/80 backdrop-blur-sm border border-border/50',
                'opacity-0 group-hover:opacity-100',
                'hover:bg-destructive/10 hover:border-destructive/30 hover:text-destructive'
              )}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="left">
            <p>Remove from history</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}

// Loading skeleton
function HistoryCardSkeleton() {
  return (
    <div className="rounded-2xl overflow-hidden bg-card/50 border border-border/40 p-3">
      <div className="flex gap-4">
        <Skeleton className="h-28 w-20 rounded-xl flex-shrink-0" />
        <div className="flex-1 py-1 space-y-3">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-3 w-1/3" />
          <Skeleton className="h-1.5 w-full mt-auto" />
        </div>
      </div>
    </div>
  )
}

export function HistoryTab() {
  const loadMoreRef = useRef<HTMLDivElement>(null)
  const [clearDialogOpen, setClearDialogOpen] = useState(false)
  const [actionFilter, setActionFilter] = useState<WatchAction | 'all'>('all')
  const [typeFilter, setTypeFilter] = useState<'movie' | 'series' | 'tv' | 'all'>('all')
  const [selectedProfileId, setSelectedProfileId] = useState<number | 'all'>('all')
  const { rpdbApiKey } = useRpdb()

  // Fetch profiles for the profile selector
  const { data: profiles } = useProfiles()

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
  } = useInfiniteWatchHistory({ 
    page_size: 20,
    action: actionFilter === 'all' ? undefined : actionFilter,
    media_type: typeFilter === 'all' ? undefined : typeFilter,
    profile_id: selectedProfileId === 'all' ? undefined : selectedProfileId,
  })

  const deleteHistory = useDeleteWatchHistory()
  const clearHistory = useClearWatchHistory()

  // Infinite scroll
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const first = entries[0]
        if (first.isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { threshold: 0.1, rootMargin: '100px' }
    )

    const currentRef = loadMoreRef.current
    if (currentRef) {
      observer.observe(currentRef)
    }

    return () => {
      if (currentRef) {
        observer.unobserve(currentRef)
      }
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const items = data?.pages.flatMap(page => page.items) ?? []

  const handleClearAll = async () => {
    // Clear only the selected profile's history, or all if 'all' is selected
    await clearHistory.mutateAsync(selectedProfileId === 'all' ? undefined : selectedProfileId)
    setClearDialogOpen(false)
  }

  return (
    <div className="space-y-6">
      {/* Header with filters */}
      <div className="space-y-4">
        {/* Profile selector */}
        {profiles && profiles.length > 1 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground uppercase tracking-wider font-medium mr-1">Profile:</span>
            <Select
              value={selectedProfileId.toString()}
              onValueChange={(value) => setSelectedProfileId(value === 'all' ? 'all' : parseInt(value, 10))}
            >
              <SelectTrigger className="w-[180px] h-8 rounded-xl text-sm">
                <SelectValue placeholder="Select Profile" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Profiles</SelectItem>
                {profiles.map((profile) => (
                  <SelectItem key={profile.id} value={profile.id.toString()}>
                    <div className="flex items-center gap-2">
                      <span>{profile.name}</span>
                      {profile.is_default && (
                        <Badge variant="secondary" className="text-[10px] px-1 py-0">Default</Badge>
                      )}
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {/* Action filters */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground uppercase tracking-wider font-medium mr-1">Activity:</span>
          <FilterPill 
            active={actionFilter === 'all'} 
            onClick={() => setActionFilter('all')}
            icon={Clock}
            label="All"
          />
          <FilterPill 
            active={actionFilter === 'WATCHED'} 
            onClick={() => setActionFilter('WATCHED')}
            icon={Eye}
            label="Watched"
            color="text-sky-400"
          />
          <FilterPill 
            active={actionFilter === 'DOWNLOADED'} 
            onClick={() => setActionFilter('DOWNLOADED')}
            icon={Download}
            label="Downloaded"
            color="text-emerald-400"
          />
          <FilterPill 
            active={actionFilter === 'QUEUED'} 
            onClick={() => setActionFilter('QUEUED')}
            icon={ListPlus}
            label="Queued"
            color="text-amber-400"
          />
        </div>

        {/* Type filters */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground uppercase tracking-wider font-medium mr-1">Type:</span>
          <FilterPill 
            active={typeFilter === 'all'} 
            onClick={() => setTypeFilter('all')}
            label="All"
          />
          <FilterPill 
            active={typeFilter === 'movie'} 
            onClick={() => setTypeFilter('movie')}
            icon={Film}
            label="Movies"
            color="text-purple-400"
          />
          <FilterPill 
            active={typeFilter === 'series'} 
            onClick={() => setTypeFilter('series')}
            icon={Tv}
            label="Series"
            color="text-blue-400"
          />
          <FilterPill 
            active={typeFilter === 'tv'} 
            onClick={() => setTypeFilter('tv')}
            icon={Radio}
            label="TV"
            color="text-rose-400"
          />
        </div>

        {/* Stats and clear button */}
        <div className="flex items-center justify-between pt-2 border-t border-border/30">
          <p className="text-sm text-muted-foreground">
            {isLoading ? (
              <span className="flex items-center gap-2">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Loading...
              </span>
            ) : items.length > 0 ? (
              `${items.length} item${items.length !== 1 ? 's' : ''} in history`
            ) : (
              'No activity found'
            )}
          </p>
          
          {items.length > 0 && (
            <AlertDialog open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
              <AlertDialogTrigger asChild>
                <Button 
                  variant="ghost" 
                  size="sm" 
                  className="h-8 text-xs text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                  Clear All
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent className="rounded-2xl">
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    {selectedProfileId === 'all' ? 'Clear all history?' : 'Clear profile history?'}
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    {selectedProfileId === 'all' 
                      ? 'This will permanently delete all your watch history across all profiles including watched, downloaded, and queued items. This action cannot be undone.'
                      : `This will permanently delete the watch history for the selected profile including watched, downloaded, and queued items. This action cannot be undone.`
                    }
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel className="rounded-xl">Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleClearAll}
                    className="rounded-xl bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    {clearHistory.isPending ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Clearing...
                      </>
                    ) : (
                      'Clear All'
                    )}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </div>

      {/* History grid */}
      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[...Array(6)].map((_, i) => (
            <HistoryCardSkeleton key={i} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 px-4">
          <div className="w-20 h-20 rounded-full bg-muted/50 flex items-center justify-center mb-4">
            <Clock className="h-10 w-10 text-muted-foreground/50" />
          </div>
          <h3 className="text-lg font-medium text-foreground mb-1">No activity yet</h3>
          <p className="text-sm text-muted-foreground text-center max-w-sm">
            {actionFilter !== 'all' || typeFilter !== 'all'
              ? 'No items match your current filters. Try adjusting them.'
              : 'Start watching, downloading, or queuing content to see your activity here.'
            }
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {items.map(item => (
            <HistoryCard
              key={item.id}
              item={item}
              onDelete={() => deleteHistory.mutateAsync(item.id)}
              rpdbApiKey={rpdbApiKey}
            />
          ))}
        </div>
      )}

      {/* Infinite scroll sentinel */}
      <div ref={loadMoreRef} className="flex justify-center py-6">
        {isFetchingNextPage && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Loading more...</span>
          </div>
        )}
        {!hasNextPage && items.length > 0 && (
          <p className="text-xs text-muted-foreground/60">
            You've reached the end
          </p>
        )}
      </div>
    </div>
  )
}
