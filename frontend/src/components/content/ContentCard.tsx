import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { MoreVertical, Trash2, ExternalLink, Edit, Play, Heart, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useContentLikes, useLikeContent, useUnlikeContent } from '@/hooks'
import { useRpdb } from '@/contexts/RpdbContext'
import type { CatalogType, AllRatings } from '@/lib/api'
import type { ExternalIds } from '@/lib/api/catalog'
import { MetadataEditSheet } from '@/components/metadata'
import { Poster, PosterCompact } from '@/components/ui/poster'
import { RatingsDisplay, CertificationBadge } from './RatingsDisplay'

// ============================================
// Types
// ============================================

export interface ContentCardData {
  id: number // Internal database ID (media_id) - used for navigation and API calls
  external_ids: ExternalIds // All external IDs (imdb, tmdb, tvdb, mal)
  title: string
  type: CatalogType
  year?: number
  poster?: string
  runtime?: string
  imdb_rating?: number // Backward compatibility
  ratings?: AllRatings // New multi-provider ratings
  genres?: string[]
  likes_count?: number // Pre-loaded likes count from catalog response
  certification?: string // Age rating category (All Ages, Teens, Adults, etc.)
  nudity?: string // Nudity level (None, Mild, Moderate, Severe)
}

export interface ContentCardProps {
  item: ContentCardData
  variant?: 'grid' | 'list'
  showLike?: boolean
  showType?: boolean
  showEdit?: boolean
  onRemove?: (item: ContentCardData) => void
  onPlay?: (item: ContentCardData) => void
  onNavigate?: (item: ContentCardData) => void
  className?: string
  isSelected?: boolean
  cardRef?: React.RefObject<HTMLDivElement | null>
}

// ============================================
// Quick Like Component
// ============================================

interface QuickLikeProps {
  mediaId: number // Internal media ID
  className?: string
  size?: 'sm' | 'default'
  initialLikesCount?: number // Pre-loaded count from catalog response to avoid N+1 queries
}

export function QuickLike({ mediaId, className, size = 'sm', initialLikesCount }: QuickLikeProps) {
  // Only fetch full data when we have user interaction or no initial count
  const { data: likesSummary, isFetched } = useContentLikes(mediaId)
  const likeContent = useLikeContent()
  const unlikeContent = useUnlikeContent()

  const userLiked = likesSummary?.user_liked || false
  // Use fetched count if available, otherwise fall back to initial count
  const likesCount = isFetched ? likesSummary?.likes_count || 0 : (initialLikesCount ?? likesSummary?.likes_count ?? 0)
  const isLiking = likeContent.isPending || unlikeContent.isPending

  const handleLike = async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (userLiked) {
      await unlikeContent.mutateAsync(mediaId)
    } else {
      await likeContent.mutateAsync(mediaId)
    }
  }

  return (
    <div className={className} onClick={(e) => e.stopPropagation()}>
      <Button
        variant={userLiked ? 'default' : 'ghost'}
        size={size}
        className={cn(
          'gap-1 transition-all',
          size === 'sm' ? 'h-7 px-2' : 'h-8 px-3',
          userLiked && 'bg-rose-500 hover:bg-rose-600 text-white',
        )}
        onClick={handleLike}
        disabled={isLiking}
      >
        {isLiking ? (
          <Loader2 className={cn('animate-spin', size === 'sm' ? 'h-3 w-3' : 'h-4 w-4')} />
        ) : (
          <Heart className={cn(size === 'sm' ? 'h-3 w-3' : 'h-4 w-4', userLiked && 'fill-current')} />
        )}
        {likesCount > 0 && <span className={size === 'sm' ? 'text-xs' : 'text-sm'}>{likesCount}</span>}
      </Button>
    </div>
  )
}

// ============================================
// Grid Card Component
// ============================================

function GridCard({
  item,
  showLike = true,
  showType = false,
  showEdit = true,
  onRemove,
  onPlay,
  onNavigate,
  className,
  isSelected = false,
  cardRef,
}: ContentCardProps) {
  const { rpdbApiKey } = useRpdb()
  // Use media_id for navigation (internal ID)
  const contentPath = `/dashboard/content/${item.type}/${item.id}`

  const handleLinkClick = () => {
    onNavigate?.(item)
  }

  // Get the meta ID for RPDB (prefers IMDB ID)
  const metaId = item.external_ids?.imdb || `mf:${item.id}`

  return (
    <div
      ref={cardRef}
      className={cn(
        'group relative rounded-2xl transition-all duration-300',
        isSelected && 'ring-2 ring-primary shadow-lg shadow-primary/40 z-10 p-2 -m-2 bg-primary/5',
        className,
      )}
    >
      <Link to={contentPath} onClick={handleLinkClick}>
        <div className="relative aspect-[2/3] rounded-xl overflow-hidden bg-muted">
          <Poster
            metaId={metaId}
            catalogType={item.type === 'tv' ? 'tv' : item.type}
            poster={item.poster}
            rpdbApiKey={item.type !== 'tv' ? rpdbApiKey : null}
            title={item.title}
            className="h-full w-full rounded-xl transition-transform group-hover:scale-105"
          />

          {/* Hover overlay */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity">
            {/* Top actions */}
            <div className="absolute top-2 right-2 flex items-center gap-1">
              {onPlay && (
                <Button
                  variant="secondary"
                  size="icon"
                  className="h-7 w-7 rounded-lg bg-primary/90 hover:bg-primary text-white"
                  onClick={(e) => {
                    e.preventDefault()
                    e.stopPropagation()
                    onPlay(item)
                  }}
                >
                  <Play className="h-3.5 w-3.5 fill-current" />
                </Button>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="secondary"
                    size="icon"
                    className="h-7 w-7 rounded-lg bg-black/50 hover:bg-black/70"
                    onClick={(e) => e.preventDefault()}
                  >
                    <MoreVertical className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem asChild>
                    <Link to={contentPath}>
                      <ExternalLink className="mr-2 h-4 w-4" />
                      View Details
                    </Link>
                  </DropdownMenuItem>
                  {showEdit && (
                    <>
                      <DropdownMenuSeparator />
                      <MetadataEditSheet
                        mediaId={item.id}
                        catalogType={item.type}
                        trigger={
                          <DropdownMenuItem onSelect={(e) => e.preventDefault()}>
                            <Edit className="mr-2 h-4 w-4" />
                            Edit Metadata
                          </DropdownMenuItem>
                        }
                      />
                    </>
                  )}
                  {onRemove && (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-destructive"
                        onClick={(e) => {
                          e.preventDefault()
                          e.stopPropagation()
                          onRemove(item)
                        }}
                      >
                        <Trash2 className="mr-2 h-4 w-4" />
                        Remove
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {/* Bottom info */}
            <div className="absolute bottom-2 left-2 right-2 flex items-center justify-between">
              <div className="flex items-center gap-1.5 flex-wrap">
                <RatingsDisplay
                  ratings={item.ratings}
                  imdbRating={item.imdb_rating}
                  size="sm"
                  maxExternalRatings={2}
                  showCommunity={false} // Community shown via QuickLike
                />
                {item.certification && (
                  <CertificationBadge certification={item.certification} size="sm" showIcon={false} />
                )}
                {showType && (
                  <Badge variant="outline" className="text-[10px] bg-black/50 capitalize">
                    {item.type}
                  </Badge>
                )}
              </div>
              {showLike && <QuickLike mediaId={item.id} initialLikesCount={item.likes_count} />}
            </div>
          </div>
        </div>

        {/* Title and info */}
        <div className="mt-2 space-y-1">
          <p className="font-medium text-sm truncate group-hover:text-primary transition-colors">{item.title}</p>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {item.year && <span>{item.year}</span>}
            {item.runtime && <span>• {item.runtime}</span>}
          </div>
        </div>
      </Link>
    </div>
  )
}

// ============================================
// List Card Component
// ============================================

function ListCard({
  item,
  showLike = true,
  showType = false,
  showEdit = true,
  onRemove,
  onPlay,
  onNavigate,
  className,
  isSelected = false,
  cardRef,
}: ContentCardProps) {
  const { rpdbApiKey } = useRpdb()
  // Use media_id for navigation (internal ID)
  const contentPath = `/dashboard/content/${item.type}/${item.id}`

  const handleLinkClick = () => {
    onNavigate?.(item)
  }

  // Get the meta ID for RPDB (prefers IMDB ID)
  const metaId = item.external_ids?.imdb || `mf:${item.id}`

  return (
    <div
      ref={cardRef}
      className={cn(
        'flex items-center gap-4 p-4 rounded-xl border transition-all duration-300 group',
        isSelected
          ? 'border-primary shadow-lg shadow-primary/30 bg-primary/10'
          : 'border-border/50 hover:border-primary/30',
        className,
      )}
    >
      <Link to={contentPath} onClick={handleLinkClick} className="flex items-center gap-4 flex-1 min-w-0">
        <PosterCompact
          metaId={metaId}
          catalogType={item.type === 'tv' ? 'tv' : item.type}
          poster={item.poster}
          rpdbApiKey={item.type !== 'tv' ? rpdbApiKey : null}
          title={item.title}
          className="flex-shrink-0"
        />
        <div className="flex-1 min-w-0">
          <p className="font-medium truncate group-hover:text-primary transition-colors">{item.title}</p>
          <div className="flex items-center gap-2 text-sm text-muted-foreground mt-1 flex-wrap">
            {item.year && <span>{item.year}</span>}
            {item.runtime && <span>• {item.runtime}</span>}
            <RatingsDisplay
              ratings={item.ratings}
              imdbRating={item.imdb_rating}
              size="sm"
              maxExternalRatings={2}
              showCommunity={false}
            />
            {showType && (
              <Badge variant="outline" className="text-xs capitalize">
                {item.type}
              </Badge>
            )}
          </div>
          {item.genres && item.genres.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {item.genres.slice(0, 3).map((g) => (
                <Badge key={g} variant="outline" className="text-xs">
                  {g}
                </Badge>
              ))}
            </div>
          )}
        </div>
      </Link>

      {/* Actions */}
      <div className="flex items-center gap-2 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        {onPlay && (
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 rounded-lg text-primary hover:bg-primary/10"
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onPlay(item)
            }}
          >
            <Play className="h-4 w-4 fill-current" />
          </Button>
        )}
        {showLike && <QuickLike mediaId={item.id} initialLikesCount={item.likes_count} />}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-8 w-8 rounded-lg">
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem asChild>
              <Link to={contentPath}>
                <ExternalLink className="mr-2 h-4 w-4" />
                View Details
              </Link>
            </DropdownMenuItem>
            {showEdit && (
              <>
                <DropdownMenuSeparator />
                <MetadataEditSheet
                  mediaId={item.id}
                  catalogType={item.type}
                  trigger={
                    <DropdownMenuItem onSelect={(e) => e.preventDefault()}>
                      <Edit className="mr-2 h-4 w-4" />
                      Edit Metadata
                    </DropdownMenuItem>
                  }
                />
              </>
            )}
            {onRemove && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="text-destructive" onClick={() => onRemove(item)}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Remove
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}

// ============================================
// Main ContentCard Component
// ============================================

export function ContentCard(props: ContentCardProps) {
  const { variant = 'grid' } = props

  if (variant === 'list') {
    return <ListCard {...props} />
  }

  return <GridCard {...props} />
}

// ============================================
// Grid and List Layout Components
// ============================================

interface ContentGridProps {
  children: React.ReactNode
  className?: string
}

export function ContentGrid({ children, className }: ContentGridProps) {
  return (
    <div className={cn('grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4', className)}>
      {children}
    </div>
  )
}

interface ContentListProps {
  children: React.ReactNode
  className?: string
}

export function ContentList({ children, className }: ContentListProps) {
  return <div className={cn('space-y-3', className)}>{children}</div>
}
