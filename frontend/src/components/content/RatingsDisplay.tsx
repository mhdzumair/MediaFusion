import { Badge } from '@/components/ui/badge'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { Star, Heart, ThumbsUp, ThumbsDown, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { AllRatings, ProviderRating, CommunityRating } from '@/lib/api'

// ============================================
// Provider Icons/Logos
// ============================================

function getProviderIcon(provider: string) {
  switch (provider.toLowerCase()) {
    case 'imdb':
      return <span className="font-bold text-[10px]">IMDb</span>
    case 'tmdb':
      return <span className="font-bold text-[10px]">TMDB</span>
    case 'trakt':
      return <span className="font-bold text-[10px]">Trakt</span>
    case 'letterboxd':
      return <span className="font-bold text-[10px]">LB</span>
    case 'rottentomatoes':
      return <span className="text-[10px]">🍅</span>
    case 'metacritic':
      return <span className="font-bold text-[10px]">MC</span>
    default:
      return <Star className="h-3 w-3" />
  }
}

function getProviderColor(provider: string) {
  switch (provider.toLowerCase()) {
    case 'imdb':
      return 'bg-amber-500/90 text-black hover:bg-amber-600'
    case 'tmdb':
      return 'bg-teal-600/90 text-white hover:bg-teal-700'
    case 'trakt':
      return 'bg-red-600/90 text-white hover:bg-red-700'
    case 'letterboxd':
      return 'bg-orange-500/90 text-white hover:bg-orange-600'
    case 'rottentomatoes':
      return 'bg-red-500/90 text-white hover:bg-red-600'
    case 'metacritic':
      return 'bg-yellow-500/90 text-black hover:bg-yellow-600'
    default:
      return 'bg-muted text-foreground'
  }
}

// ============================================
// Single Rating Badge
// ============================================

interface RatingBadgeProps {
  rating: ProviderRating
  size?: 'sm' | 'default'
  className?: string
}

export function RatingBadge({ rating, size = 'sm', className }: RatingBadgeProps) {
  const displayRating = rating.is_percentage
    ? `${Math.round(rating.rating_raw || rating.rating * 10)}%`
    : rating.rating.toFixed(1)

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge 
            className={cn(
              'gap-1 cursor-default transition-colors',
              size === 'sm' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2 py-1',
              getProviderColor(rating.provider),
              className
            )}
          >
            {getProviderIcon(rating.provider)}
            <span className={size === 'sm' ? 'text-[10px]' : 'text-xs'}>{displayRating}</span>
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <div className="text-xs">
            <p className="font-medium">{rating.provider_display_name}</p>
            <p>Rating: {displayRating}{rating.is_percentage ? '' : ` / ${rating.max_rating}`}</p>
            {rating.vote_count && <p>Votes: {rating.vote_count.toLocaleString()}</p>}
            {rating.certification && <p>Status: {rating.certification}</p>}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// ============================================
// Community Rating Badge
// ============================================

interface CommunityRatingBadgeProps {
  rating: CommunityRating
  size?: 'sm' | 'default'
  className?: string
}

export function CommunityRatingBadge({ rating, size = 'sm', className }: CommunityRatingBadgeProps) {
  const hasVotes = rating.total_votes > 0
  
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge 
            variant="outline"
            className={cn(
              'gap-1 cursor-default transition-colors',
              size === 'sm' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2 py-1',
              rating.user_vote === 1 && 'border-rose-500 bg-rose-500/10',
              rating.user_vote === -1 && 'border-slate-500 bg-slate-500/10',
              className
            )}
          >
            <Heart className={cn(
              size === 'sm' ? 'h-2.5 w-2.5' : 'h-3 w-3',
              hasVotes && 'fill-rose-500 text-rose-500'
            )} />
            {hasVotes ? (
              <span className={size === 'sm' ? 'text-[10px]' : 'text-xs'}>{rating.total_votes}</span>
            ) : (
              <span className={cn('text-muted-foreground', size === 'sm' ? 'text-[10px]' : 'text-xs')}>—</span>
            )}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <div className="text-xs space-y-1">
            <p className="font-medium">Community Rating</p>
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1">
                <ThumbsUp className="h-3 w-3 text-green-500" />
                {rating.upvotes}
              </span>
              <span className="flex items-center gap-1">
                <ThumbsDown className="h-3 w-3 text-red-500" />
                {rating.downvotes}
              </span>
            </div>
            {rating.average_rating > 0 && (
              <p>Average: {rating.average_rating.toFixed(1)} / 10</p>
            )}
            <p className="flex items-center gap-1">
              <Users className="h-3 w-3" />
              {rating.total_votes} votes
            </p>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// ============================================
// All Ratings Display (Compact)
// ============================================

interface RatingsDisplayProps {
  ratings?: AllRatings
  // Fallback for backward compatibility
  imdbRating?: number
  size?: 'sm' | 'default'
  maxExternalRatings?: number  // Max number of external ratings to show
  showCommunity?: boolean
  className?: string
}

export function RatingsDisplay({
  ratings,
  imdbRating,
  size = 'sm',
  maxExternalRatings = 3,
  showCommunity = true,
  className,
}: RatingsDisplayProps) {
  // If no ratings object, show fallback IMDb rating
  if (!ratings) {
    if (imdbRating) {
      return (
        <Badge className={cn(
          'gap-1 bg-amber-500/90 text-black',
          size === 'sm' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2 py-1',
          className
        )}>
          <Star className={cn(size === 'sm' ? 'h-2.5 w-2.5' : 'h-3 w-3', 'fill-current')} />
          {imdbRating.toFixed(1)}
        </Badge>
      )
    }
    return null
  }

  const externalRatings = ratings.external_ratings?.slice(0, maxExternalRatings) || []
  const hasExternalRatings = externalRatings.length > 0
  const hasCommunityRating = showCommunity && ratings.community_rating

  if (!hasExternalRatings && !hasCommunityRating) {
    return null
  }

  return (
    <div className={cn('flex items-center gap-1 flex-wrap', className)}>
      {externalRatings.map((rating: ProviderRating) => (
        <RatingBadge key={rating.provider} rating={rating} size={size} />
      ))}
      {hasCommunityRating && (
        <CommunityRatingBadge rating={ratings.community_rating!} size={size} />
      )}
    </div>
  )
}

// ============================================
// Detailed Ratings Panel (for detail pages)
// ============================================

interface RatingsDetailPanelProps {
  ratings?: AllRatings
  className?: string
}

export function RatingsDetailPanel({ ratings, className }: RatingsDetailPanelProps) {
  if (!ratings) return null

  const externalRatings = ratings.external_ratings || []
  const communityRating = ratings.community_rating

  if (externalRatings.length === 0 && !communityRating) {
    return null
  }

  return (
    <div className={cn('space-y-3', className)}>
      {/* External Ratings */}
      {externalRatings.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Ratings</p>
          <div className="flex flex-wrap gap-2">
            {externalRatings.map((rating: ProviderRating) => (
              <RatingBadge key={rating.provider} rating={rating} size="default" />
            ))}
          </div>
        </div>
      )}

      {/* Community Rating */}
      {communityRating && communityRating.total_votes > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Community</p>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1 text-sm">
                <ThumbsUp className="h-4 w-4 text-green-500" />
                <span className="font-medium">{communityRating.upvotes}</span>
              </span>
              <span className="flex items-center gap-1 text-sm">
                <ThumbsDown className="h-4 w-4 text-red-500" />
                <span className="font-medium">{communityRating.downvotes}</span>
              </span>
            </div>
            <span className="text-xs text-muted-foreground">
              {communityRating.total_votes} total votes
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

// ============================================
// Certification Badge (Age Rating Category)
// ============================================

// Certification category colors and icons
const CERTIFICATION_CONFIG: Record<string, { color: string; icon: string }> = {
  'All Ages': { color: 'bg-green-500/90 text-white', icon: '👨‍👩‍👧‍👦' },
  'Children': { color: 'bg-lime-500/90 text-white', icon: '👧' },
  'Parental Guidance': { color: 'bg-yellow-500/90 text-black', icon: '⚠️' },
  'Teens': { color: 'bg-orange-500/90 text-white', icon: '🔶' },
  'Adults': { color: 'bg-red-600/90 text-white', icon: '🔞' },
  'Adults+': { color: 'bg-red-800/90 text-white', icon: '❌' },
  'Unknown': { color: 'bg-slate-500/90 text-white', icon: '❓' },
}

interface CertificationBadgeProps {
  certification: string
  size?: 'sm' | 'default'
  showIcon?: boolean
  className?: string
}

export function CertificationBadge({ 
  certification, 
  size = 'sm', 
  showIcon = true,
  className 
}: CertificationBadgeProps) {
  const config = CERTIFICATION_CONFIG[certification] || CERTIFICATION_CONFIG['Unknown']
  
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge 
            className={cn(
              'font-semibold cursor-default gap-1',
              size === 'sm' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2 py-1',
              config.color,
              className
            )}
          >
            {showIcon && <span>{config.icon}</span>}
            <span>{certification}</span>
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <p className="text-xs">Age Rating: {certification}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// ============================================
// Nudity Status Badge
// ============================================

const NUDITY_CONFIG: Record<string, { color: string; icon: string }> = {
  'None': { color: 'bg-green-500/90 text-white', icon: '✓' },
  'Mild': { color: 'bg-yellow-500/90 text-black', icon: '⚡' },
  'Moderate': { color: 'bg-orange-500/90 text-white', icon: '🔶' },
  'Severe': { color: 'bg-red-600/90 text-white', icon: '⛔' },
  'Unknown': { color: 'bg-slate-500/90 text-white', icon: '?' },
}

interface NudityBadgeProps {
  nudity: string
  size?: 'sm' | 'default'
  showIcon?: boolean
  className?: string
}

export function NudityBadge({ 
  nudity, 
  size = 'sm', 
  showIcon = true,
  className 
}: NudityBadgeProps) {
  // Don't show if not set or Disable
  if (!nudity || nudity === 'Disable') return null
  
  const config = NUDITY_CONFIG[nudity] || NUDITY_CONFIG['Unknown']
  
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge 
            variant="outline"
            className={cn(
              'font-medium cursor-default gap-1',
              size === 'sm' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2 py-1',
              className
            )}
          >
            {showIcon && <span>{config.icon}</span>}
            <span>Nudity: {nudity}</span>
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <p className="text-xs">
            {nudity === 'Unknown' 
              ? 'Nudity level not yet classified - help us by providing this info!' 
              : `Nudity Level: ${nudity}`
            }
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// ============================================
// Combined Content Guidance Display
// ============================================

interface ContentGuidanceProps {
  certification?: string
  nudity?: string
  size?: 'sm' | 'default'
  className?: string
}

export function ContentGuidance({ 
  certification, 
  nudity, 
  size = 'sm',
  className 
}: ContentGuidanceProps) {
  // Always show certification (even Unknown allows users to contribute)
  const showCert = !!certification
  // Show nudity if it's meaningful (not None or Disable)
  const showNudity = nudity && nudity !== 'Disable' && nudity !== 'None'

  if (!showCert && !showNudity) return null

  return (
    <div className={cn('flex items-center gap-1', className)}>
      {showCert && <CertificationBadge certification={certification!} size={size} />}
      {showNudity && <NudityBadge nudity={nudity!} size={size} />}
    </div>
  )
}

// Export for convenience
export type { AllRatings, ProviderRating, CommunityRating }

