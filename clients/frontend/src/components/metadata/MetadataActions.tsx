import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Heart, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useContentLikes, useLikeContent, useUnlikeContent } from '@/hooks'

interface MetadataActionsProps {
  mediaId: number
  className?: string
}

/**
 * MetadataActions - Provides like/unlike functionality for content
 * Note: Use MetadataEditSheet for suggesting metadata edits
 */
export function MetadataActions({ mediaId, className }: MetadataActionsProps) {
  const { data: likesSummary } = useContentLikes(mediaId)
  const likeContent = useLikeContent()
  const unlikeContent = useUnlikeContent()

  const userLiked = likesSummary?.user_liked || false
  const likesCount = likesSummary?.likes_count || 0
  const isLiking = likeContent.isPending || unlikeContent.isPending

  const handleLikeToggle = async () => {
    if (userLiked) {
      await unlikeContent.mutateAsync(mediaId)
    } else {
      await likeContent.mutateAsync(mediaId)
    }
  }

  return (
    <TooltipProvider>
      <div className={cn('flex items-center gap-2', className)}>
        {/* Like button */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant={userLiked ? 'default' : 'ghost'}
              size="sm"
              className={cn('h-8 gap-1.5 transition-all', userLiked && 'bg-rose-500 hover:bg-rose-600 text-white')}
              onClick={handleLikeToggle}
              disabled={isLiking}
            >
              {isLiking ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Heart className={cn('h-4 w-4', userLiked && 'fill-current')} />
              )}
              {likesCount > 0 && <span className="text-sm font-medium">{likesCount}</span>}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>{userLiked ? 'Unlike' : 'Like this content'}</p>
          </TooltipContent>
        </Tooltip>
      </div>
    </TooltipProvider>
  )
}

/**
 * Compact like button for content cards
 */
export function ContentLikeButton({
  mediaId,
  compact = false,
  className,
}: {
  mediaId: number
  compact?: boolean
  className?: string
}) {
  const { data: likesSummary } = useContentLikes(mediaId)
  const likeContent = useLikeContent()
  const unlikeContent = useUnlikeContent()

  const userLiked = likesSummary?.user_liked || false
  const likesCount = likesSummary?.likes_count || 0
  const isLiking = likeContent.isPending || unlikeContent.isPending

  const handleLikeToggle = async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (userLiked) {
      await unlikeContent.mutateAsync(mediaId)
    } else {
      await likeContent.mutateAsync(mediaId)
    }
  }

  if (compact) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className={cn('h-7 px-2 gap-1', userLiked && 'text-rose-500', className)}
        onClick={handleLikeToggle}
        disabled={isLiking}
      >
        {isLiking ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Heart className={cn('h-3 w-3', userLiked && 'fill-current')} />
        )}
        {likesCount > 0 && <span className="text-xs">{likesCount}</span>}
      </Button>
    )
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant={userLiked ? 'default' : 'ghost'}
            size="sm"
            className={cn(
              'h-8 gap-1.5 transition-all',
              userLiked && 'bg-rose-500 hover:bg-rose-600 text-white',
              className,
            )}
            onClick={handleLikeToggle}
            disabled={isLiking}
          >
            {isLiking ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Heart className={cn('h-4 w-4', userLiked && 'fill-current')} />
            )}
            {likesCount > 0 && <span className="text-sm font-medium">{likesCount}</span>}
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          <p>{userLiked ? 'Unlike' : 'Like this content'}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/**
 * Badge showing likes count
 */
export function ContentLikesBadge({ mediaId, className }: { mediaId: number; className?: string }) {
  const { data: likesSummary, isLoading } = useContentLikes(mediaId)

  if (isLoading || !likesSummary) return null

  const { likes_count } = likesSummary

  if (likes_count === 0) return null

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="outline" className={cn('text-xs gap-1 border-rose-500/50 text-rose-500', className)}>
            <Heart className="h-3 w-3 fill-current" />
            {likes_count}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <p>
            {likes_count} {likes_count === 1 ? 'person likes' : 'people like'} this
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
