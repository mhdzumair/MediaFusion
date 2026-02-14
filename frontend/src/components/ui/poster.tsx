import { useState, useEffect, useMemo, useRef } from 'react'
import { cn } from '@/lib/utils'
import { Skeleton } from './skeleton'
import { Film } from 'lucide-react'

interface PosterProps {
  metaId: string
  catalogType: 'movie' | 'series' | 'tv'
  poster?: string | null
  rpdbApiKey?: string | null
  title?: string
  className?: string
  aspectRatio?: 'portrait' | 'square'
  showFallbackIcon?: boolean
  /** Override poster URL that takes priority over RPDB (e.g., episode stills) */
  overridePoster?: string | null
}

/**
 * Poster component with:
 * - Loading skeleton animation
 * - RPDB poster support (when API key configured)
 * - Automatic fallback chain: RPDB -> actual poster -> MediaFusion poster endpoint
 * - Lazy loading with IntersectionObserver
 * - Error handling with fallback
 */
export function Poster({
  metaId,
  catalogType,
  poster,
  rpdbApiKey,
  title,
  className,
  aspectRatio = 'portrait',
  showFallbackIcon = true,
  overridePoster,
}: PosterProps) {
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)
  const [fallbackIndex, setFallbackIndex] = useState(0)
  
  // Track the previous primary URL to avoid unnecessary resets
  const prevPrimaryUrlRef = useRef<string | null>(null)

  // Generate fallback URLs - memoized to prevent unnecessary recalculations
  const fallbackUrls = useMemo(() => {
    const urls: string[] = []
    
    // 0. Override poster takes priority (e.g., episode stills)
    if (overridePoster) {
      urls.push(overridePoster)
    }
    
    // 1. RPDB poster (for IMDB IDs with API key) - skip if override is set
    if (!overridePoster && metaId.startsWith('tt') && rpdbApiKey) {
      urls.push(`https://api.ratingposterdb.com/${rpdbApiKey}/imdb/poster-default/${metaId}.jpg?fallback=true`)
    }
    
    // 2. Actual poster from database
    if (poster) {
      urls.push(poster)
    }
    
    // 3. MediaFusion poster endpoint (fallback)
    const baseUrl = import.meta.env.VITE_API_URL || window.location.origin
    urls.push(`${baseUrl}/poster/${catalogType}/${metaId}.jpg`)
    
    return urls
  }, [metaId, catalogType, poster, rpdbApiKey, overridePoster])

  // Current source is derived from fallbackUrls and fallbackIndex
  const currentSrc = fallbackUrls[fallbackIndex] || null
  
  // Get the primary URL (first in fallback chain)
  const primaryUrl = fallbackUrls[0] || null

  // Only reset when the PRIMARY URL changes (not when secondary fallbacks change)
  // This prevents flashing when metadata refreshes but RPDB poster stays the same
  useEffect(() => {
    if (prevPrimaryUrlRef.current !== primaryUrl) {
      setFallbackIndex(0)
      setHasError(false)
      setIsLoading(true)
      prevPrimaryUrlRef.current = primaryUrl
    }
  }, [primaryUrl])

  // Handle image load success - but check if it's a redirect to the default poster
  const handleLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget
    // Check if the image was redirected to the non-existent default poster
    // The browser's currentSrc will show the final URL after redirects
    if (img.currentSrc?.includes('/static/images/default_poster') || 
        img.currentSrc?.includes('default_poster')) {
      // Treat this as an error and try next fallback
      handleError()
      return
    }
    setIsLoading(false)
    setHasError(false)
  }

  // Handle image load error - try next fallback
  const handleError = () => {
    if (fallbackIndex < fallbackUrls.length - 1) {
      setFallbackIndex(prev => prev + 1)
    } else {
      setIsLoading(false)
      setHasError(true)
    }
  }

  // Only skip aspect ratio if BOTH height and width are explicitly set
  // If only width is set, we still need the aspect ratio to determine height
  const hasExplicitHeight = className?.includes('h-')
  const aspectClasses = hasExplicitHeight 
    ? '' 
    : (aspectRatio === 'portrait' ? 'aspect-[2/3]' : 'aspect-square')

  return (
    <div
      className={cn(
        'relative overflow-hidden bg-muted rounded-lg',
        aspectClasses,
        className
      )}
    >
      {/* Loading skeleton */}
      {isLoading && !hasError && (
        <Skeleton className="absolute inset-0 rounded-lg" />
      )}

      {/* Image */}
      {currentSrc && !hasError && (
        <img
          src={currentSrc}
          alt={title || 'Poster'}
          className={cn(
            'absolute inset-0 w-full h-full object-cover rounded-lg transition-opacity duration-300',
            isLoading ? 'opacity-0' : 'opacity-100'
          )}
          onLoad={(e) => handleLoad(e)}
          onError={handleError}
          loading="lazy"
        />
      )}

      {/* Error fallback */}
      {hasError && showFallbackIcon && (
        <div className="absolute inset-0 flex items-center justify-center bg-muted">
          <Film className="w-12 h-12 text-muted-foreground/50" />
        </div>
      )}

      {/* Title overlay for error state */}
      {hasError && title && (
        <div className="absolute bottom-0 left-0 right-0 p-2 bg-gradient-to-t from-black/80 to-transparent">
          <p className="text-xs text-white truncate">{title}</p>
        </div>
      )}
    </div>
  )
}

/**
 * Compact poster for list views
 */
export function PosterCompact({
  metaId,
  catalogType,
  poster,
  rpdbApiKey,
  title,
  className,
  overridePoster,
}: Omit<PosterProps, 'aspectRatio' | 'showFallbackIcon'>) {
  return (
    <Poster
      metaId={metaId}
      catalogType={catalogType}
      poster={poster}
      rpdbApiKey={rpdbApiKey}
      title={title}
      className={cn('w-16 h-24', className)}
      showFallbackIcon={false}
      overridePoster={overridePoster}
    />
  )
}

/**
 * Large poster for detail views
 */
export function PosterLarge({
  metaId,
  catalogType,
  poster,
  rpdbApiKey,
  title,
  className,
}: Omit<PosterProps, 'aspectRatio'>) {
  return (
    <Poster
      metaId={metaId}
      catalogType={catalogType}
      poster={poster}
      rpdbApiKey={rpdbApiKey}
      title={title}
      className={cn('w-full max-w-[300px]', className)}
    />
  )
}

/**
 * Check if RPDB API key is Tier 1 or higher (supports backdrops)
 * Tier is determined by the first 2 characters: t1-, t2-, t3-, t4-
 */
export function isRpdbTier1Plus(rpdbApiKey: string | null | undefined): boolean {
  if (!rpdbApiKey) return false
  const tierPrefix = rpdbApiKey.substring(0, 3).toLowerCase()
  return ['t1-', 't2-', 't3-', 't4-'].includes(tierPrefix)
}

/**
 * Generate RPDB backdrop URL
 */
export function getRpdbBackdropUrl(metaId: string, rpdbApiKey: string): string {
  return `https://api.ratingposterdb.com/${rpdbApiKey}/imdb/backdrop-default/${metaId}.jpg?fallback=true`
}

interface BackdropProps {
  metaId: string
  backdrop?: string | null
  rpdbApiKey?: string | null
  className?: string
}

/**
 * Backdrop image component with RPDB support
 * Fallback chain: RPDB backdrop (Tier 1+ only) -> database backdrop
 */
export function Backdrop({
  metaId,
  backdrop,
  rpdbApiKey,
  className,
}: BackdropProps) {
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)
  const [fallbackIndex, setFallbackIndex] = useState(0)
  
  // Track the previous primary URL to avoid unnecessary resets
  const prevPrimaryUrlRef = useRef<string | null>(null)

  // Check if RPDB key is Tier 1+ (required for backdrop access)
  const canUseRpdbBackdrop = isRpdbTier1Plus(rpdbApiKey)

  // Generate fallback URLs
  const fallbackUrls = useMemo(() => {
    const urls: string[] = []
    
    // 1. RPDB backdrop (for IMDB IDs with Tier 1+ API key only)
    if (metaId.startsWith('tt') && rpdbApiKey && canUseRpdbBackdrop) {
      urls.push(getRpdbBackdropUrl(metaId, rpdbApiKey))
    }
    
    // 2. Actual backdrop from database
    if (backdrop) {
      urls.push(backdrop)
    }
    
    return urls
  }, [metaId, backdrop, rpdbApiKey, canUseRpdbBackdrop])

  const currentSrc = fallbackUrls[fallbackIndex] || null
  
  // Get the primary URL (first in fallback chain)
  const primaryUrl = fallbackUrls[0] || null

  // Only reset when the PRIMARY URL changes
  // This prevents flashing when metadata refreshes but RPDB backdrop stays the same
  useEffect(() => {
    if (prevPrimaryUrlRef.current !== primaryUrl) {
      setFallbackIndex(0)
      setHasError(false)
      setIsLoading(true)
      prevPrimaryUrlRef.current = primaryUrl
    }
  }, [primaryUrl])

  const handleLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget
    if (img.currentSrc?.includes('default_poster') || img.currentSrc?.includes('default_backdrop')) {
      handleError()
      return
    }
    setIsLoading(false)
    setHasError(false)
  }

  const handleError = () => {
    if (fallbackIndex < fallbackUrls.length - 1) {
      setFallbackIndex(prev => prev + 1)
    } else {
      setIsLoading(false)
      setHasError(true)
    }
  }

  // Don't render anything if no URLs available or all failed
  if (!currentSrc || hasError) {
    return null
  }

  return (
    <img
      src={currentSrc}
      alt=""
      className={cn(
        'transition-opacity duration-500',
        isLoading ? 'opacity-0' : 'opacity-100',
        className
      )}
      onLoad={handleLoad}
      onError={handleError}
      loading="lazy"
    />
  )
}
