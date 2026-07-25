import { Flag, Play, ThumbsUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuth } from '@/contexts/AuthContext'
import { useStreamCommunityStats } from '@/contexts/StreamCommunityContext'
import { StreamVoteButtons } from './StreamVoteButtons'

interface StreamCommunityRowProps {
  streamId: number
  watchedCount?: number
  className?: string
}

export function StreamCommunityRow({ streamId, watchedCount, className }: StreamCommunityRowProps) {
  const { isAuthenticated } = useAuth()
  const { stats, isLoading } = useStreamCommunityStats(streamId)

  const hasSignals =
    (stats && stats.issue_report_count > 0) ||
    (stats && stats.rating_total > 0) ||
    (stats?.watched_count ?? watchedCount ?? 0) > 0 ||
    isAuthenticated

  if (!hasSignals && !isLoading) return null

  const displayWatchedCount = stats?.watched_count ?? watchedCount ?? 0

  return (
    <div
      className={cn('flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground', className)}
      onClick={(e) => e.stopPropagation()}
    >
      {isLoading && !stats && <span className="animate-pulse">…</span>}
      {displayWatchedCount > 0 && (
        <span
          className="inline-flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5"
          title="Users who played this stream"
        >
          <Play className="h-3 w-3 shrink-0" />
          {displayWatchedCount} watched
        </span>
      )}
      {stats && stats.issue_report_count > 0 && (
        <span
          className="inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-600 dark:text-amber-400"
          title="Visible issue reports from the community"
        >
          <Flag className="h-3 w-3 shrink-0" />
          {stats.issue_report_count} report{stats.issue_report_count === 1 ? '' : 's'}
        </span>
      )}
      {stats && stats.rating_total > 0 && (
        <span
          className="inline-flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5"
          title="Thumb score from the community"
        >
          <ThumbsUp className="h-3 w-3 shrink-0" />
          {stats.rating_up}/{stats.rating_total} (+{stats.rating_score})
        </span>
      )}
      {isAuthenticated && <StreamVoteButtons streamId={streamId} compact showCounts />}
    </div>
  )
}
