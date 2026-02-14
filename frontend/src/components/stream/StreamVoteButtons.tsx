import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  ThumbsUp,
  ThumbsDown,
  ChevronDown,
  CheckCircle2,
  XCircle,
  Sparkles,
  AlertTriangle,
  Loader2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useStreamVotes, useVoteOnStream, useRemoveStreamVote } from '@/hooks'
import type { VoteType, QualityStatus, StreamVoteSummary } from '@/lib/api'

interface StreamVoteButtonsProps {
  streamId: number
  compact?: boolean
  showCounts?: boolean
  className?: string
}

const qualityStatusConfig: Record<QualityStatus, { label: string; icon: typeof CheckCircle2; color: string }> = {
  working: { label: 'Working', icon: CheckCircle2, color: 'text-emerald-500' },
  broken: { label: 'Broken', icon: XCircle, color: 'text-red-500' },
  good_quality: { label: 'Good Quality', icon: Sparkles, color: 'text-blue-500' },
  poor_quality: { label: 'Poor Quality', icon: AlertTriangle, color: 'text-primary' },
}

export function StreamVoteButtons({ streamId, compact = false, showCounts = true, className }: StreamVoteButtonsProps) {
  const { data: voteSummary, isLoading } = useStreamVotes(streamId)
  const voteOnStream = useVoteOnStream()
  const removeVote = useRemoveStreamVote()

  const [pendingVote, setPendingVote] = useState<VoteType | null>(null)

  const userVote = voteSummary?.user_vote
  const isVoting = voteOnStream.isPending || removeVote.isPending

  const handleVote = async (voteType: VoteType, qualityStatus?: QualityStatus) => {
    // If clicking the same vote type without quality status, remove vote
    if (userVote?.vote_type === voteType && !qualityStatus) {
      await removeVote.mutateAsync(streamId)
      return
    }

    setPendingVote(voteType)
    try {
      await voteOnStream.mutateAsync({
        streamId,
        data: {
          vote_type: voteType,
          quality_status: qualityStatus,
        },
      })
    } finally {
      setPendingVote(null)
    }
  }

  if (isLoading) {
    return (
      <div className={cn('flex items-center gap-2', className)}>
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const upvotes = voteSummary?.upvotes || 0
  const downvotes = voteSummary?.downvotes || 0
  const scorePercent = voteSummary?.score_percent || 0

  return (
    <TooltipProvider>
      <div className={cn('flex items-center gap-1', className)}>
        {/* Upvote button with dropdown */}
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>
                <Button
                  variant={userVote?.vote_type === 'up' ? 'default' : 'ghost'}
                  size={compact ? 'sm' : 'default'}
                  className={cn('gap-1', userVote?.vote_type === 'up' && 'bg-emerald-600 hover:bg-emerald-700')}
                  disabled={isVoting}
                >
                  {pendingVote === 'up' ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ThumbsUp className="h-4 w-4" />
                  )}
                  {showCounts && !compact && <span className="text-xs">{upvotes}</span>}
                  <ChevronDown className="h-3 w-3 opacity-50" />
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent>
              <p>Upvote stream ({upvotes} votes)</p>
            </TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel>Vote as...</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => handleVote('up')} className="cursor-pointer">
              <ThumbsUp className="h-4 w-4 mr-2 text-emerald-500" />
              Just Upvote
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleVote('up', 'working')} className="cursor-pointer">
              <CheckCircle2 className="h-4 w-4 mr-2 text-emerald-500" />
              Working Stream
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleVote('up', 'good_quality')} className="cursor-pointer">
              <Sparkles className="h-4 w-4 mr-2 text-blue-500" />
              Good Quality
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Downvote button with dropdown */}
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>
                <Button
                  variant={userVote?.vote_type === 'down' ? 'default' : 'ghost'}
                  size={compact ? 'sm' : 'default'}
                  className={cn('gap-1', userVote?.vote_type === 'down' && 'bg-red-600 hover:bg-red-700')}
                  disabled={isVoting}
                >
                  {pendingVote === 'down' ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ThumbsDown className="h-4 w-4" />
                  )}
                  {showCounts && !compact && <span className="text-xs">{downvotes}</span>}
                  <ChevronDown className="h-3 w-3 opacity-50" />
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent>
              <p>Downvote stream ({downvotes} votes)</p>
            </TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel>Vote as...</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => handleVote('down')} className="cursor-pointer">
              <ThumbsDown className="h-4 w-4 mr-2 text-red-500" />
              Just Downvote
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleVote('down', 'broken')} className="cursor-pointer">
              <XCircle className="h-4 w-4 mr-2 text-red-500" />
              Broken Stream
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleVote('down', 'poor_quality')} className="cursor-pointer">
              <AlertTriangle className="h-4 w-4 mr-2 text-primary" />
              Poor Quality
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Score badge */}
        {showCounts && upvotes + downvotes > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="outline"
                className={cn(
                  'ml-1 text-xs',
                  scorePercent >= 70
                    ? 'border-emerald-500 text-emerald-500'
                    : scorePercent >= 40
                      ? 'border-primary text-primary'
                      : 'border-red-500 text-red-500',
                )}
              >
                {scorePercent}%
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              <p>
                {scorePercent}% positive ({upvotes + downvotes} votes)
              </p>
            </TooltipContent>
          </Tooltip>
        )}

        {/* User's quality status indicator */}
        {userVote?.quality_status && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="secondary" className="ml-1 gap-1">
                {(() => {
                  const config = qualityStatusConfig[userVote.quality_status]
                  const Icon = config.icon
                  return (
                    <>
                      <Icon className={cn('h-3 w-3', config.color)} />
                      <span className="text-xs">{config.label}</span>
                    </>
                  )
                })()}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              <p>Your quality rating</p>
            </TooltipContent>
          </Tooltip>
        )}
      </div>
    </TooltipProvider>
  )
}

/**
 * Popular stream badge - shows when a stream has high engagement
 */
export function StreamPopularityBadge({
  streamId,
  className,
  threshold = 5, // Minimum total votes to show badge
}: {
  streamId: number
  className?: string
  threshold?: number
}) {
  const { data: voteSummary, isLoading } = useStreamVotes(streamId)

  if (isLoading || !voteSummary) return null

  const totalVotes = voteSummary.upvotes + voteSummary.downvotes
  const scorePercent = voteSummary.score_percent

  // Show badge if has enough votes AND mostly positive
  if (totalVotes < threshold || scorePercent < 70) return null

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge className={cn('gap-1 bg-gradient-to-r from-primary to-primary/80 text-white border-0', className)}>
            <Sparkles className="h-3 w-3" />
            Popular
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <p>
            Highly rated stream ({scorePercent}% positive from {totalVotes} votes)
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/**
 * Compact vote summary display (read-only)
 */
export function StreamVoteSummary({ summary, className }: { summary: StreamVoteSummary; className?: string }) {
  const { upvotes, downvotes, working_count, broken_count, score_percent } = summary
  const totalVotes = upvotes + downvotes

  if (totalVotes === 0) return null

  return (
    <TooltipProvider>
      <div className={cn('flex items-center gap-2 text-xs text-muted-foreground', className)}>
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex items-center gap-1">
              <ThumbsUp className="h-3 w-3" />
              <span>{upvotes}</span>
            </div>
          </TooltipTrigger>
          <TooltipContent>
            <p>{upvotes} upvotes</p>
          </TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex items-center gap-1">
              <ThumbsDown className="h-3 w-3" />
              <span>{downvotes}</span>
            </div>
          </TooltipTrigger>
          <TooltipContent>
            <p>{downvotes} downvotes</p>
          </TooltipContent>
        </Tooltip>

        {working_count > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex items-center gap-1 text-emerald-500">
                <CheckCircle2 className="h-3 w-3" />
                <span>{working_count}</span>
              </div>
            </TooltipTrigger>
            <TooltipContent>
              <p>{working_count} users say working</p>
            </TooltipContent>
          </Tooltip>
        )}

        {broken_count > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex items-center gap-1 text-red-500">
                <XCircle className="h-3 w-3" />
                <span>{broken_count}</span>
              </div>
            </TooltipTrigger>
            <TooltipContent>
              <p>{broken_count} users report broken</p>
            </TooltipContent>
          </Tooltip>
        )}

        <Badge
          variant="outline"
          className={cn(
            'text-xs',
            score_percent >= 70
              ? 'border-emerald-500/50 text-emerald-500'
              : score_percent >= 40
                ? 'border-primary/50 text-primary'
                : 'border-red-500/50 text-red-500',
          )}
        >
          {score_percent}%
        </Badge>
      </div>
    </TooltipProvider>
  )
}
