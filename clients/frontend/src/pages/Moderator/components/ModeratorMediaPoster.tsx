import { Film } from 'lucide-react'

import { Poster } from '@/components/ui/poster'
import { useRpdb } from '@/contexts/RpdbContext'

type PosterCatalogType = 'movie' | 'series' | 'tv'

export interface ModeratorMediaPosterProps {
  mediaType: string | null | undefined
  mediaId: number | null | undefined
  imdbId?: string | null
  posterUrl?: string | null
  title?: string | null
  fallbackIconSizeClassName?: string
}

export function ModeratorMediaPoster({
  mediaType,
  mediaId,
  imdbId,
  posterUrl,
  title,
  fallbackIconSizeClassName = 'h-5 w-5',
}: ModeratorMediaPosterProps) {
  const { rpdbApiKey } = useRpdb()

  const catalogType: PosterCatalogType | null =
    mediaType === 'movie' || mediaType === 'series' || mediaType === 'tv' ? mediaType : null
  const normalizedImdbId = imdbId?.toLowerCase().startsWith('tt') ? imdbId : null
  const metaId = normalizedImdbId ?? (mediaId ? `mf:${mediaId}` : null)

  if (catalogType && metaId) {
    return (
      <Poster
        metaId={metaId}
        catalogType={catalogType}
        poster={posterUrl ?? undefined}
        rpdbApiKey={catalogType !== 'tv' ? rpdbApiKey : null}
        title={title || 'Media poster'}
        className="h-full w-full rounded-md"
      />
    )
  }

  if (posterUrl) {
    return <img src={posterUrl} alt={title || 'Media poster'} className="h-full w-full object-cover" />
  }

  return (
    <div className="flex h-full w-full items-center justify-center text-muted-foreground">
      <Film className={fallbackIconSizeClassName} />
    </div>
  )
}
