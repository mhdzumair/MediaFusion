import { Flag, ThumbsUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuth } from '@/contexts/AuthContext'
import { useStreamSignals } from '@/hooks'
import { StreamVoteButtons } from './StreamVoteButtons'

interface StreamCommunityRowProps {
  streamId: number
  className?: string
}

export function StreamCommunityRow({ streamId, className }: StreamCommunityRowProps) {
  const { isAuthenticated } = useAuth()
  const { data: signals, isLoading } = useStreamSignals(streamId)

  const hasSignals =
    (signals && signals.issue_report_count > 0) || (signals && signals.rating_total > 0) || isAuthenticated

  if (!hasSignals && !isLoading) return null

  return (
    <div
      className={cn('flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground', className)}
      onClick={(e) => e.stopPropagation()}
    >
      {isLoading && <span className="animate-pulse">…</span>}
      {signals && signals.issue_report_count > 0 && (
        <span
          className="inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-600 dark:text-amber-400"
          title="Visible issue reports from the community"
        >
          <Flag className="h-3 w-3 shrink-0" />
          {signals.issue_report_count} report{signals.issue_report_count === 1 ? '' : 's'}
        </span>
      )}
      {signals && signals.rating_total > 0 && (
        <span
          className="inline-flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5"
          title="Thumb score from the community"
        >
          <ThumbsUp className="h-3 w-3 shrink-0" />
          {signals.rating_up}/{signals.rating_total} (+{signals.rating_score})
        </span>
      )}
      {isAuthenticated && <StreamVoteButtons streamId={streamId} compact showCounts />}
    </div>
  )
}
