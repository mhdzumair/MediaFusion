import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Poster } from '@/components/ui/poster'
import { useRpdb } from '@/contexts/RpdbContext'
import { StreamCard } from '@/components/stream/StreamCard'
import { QualityTierBadge } from '@/components/stream/StreamGroupedList'
import { buildContentStreamUrl, getPosterMetaId } from '@/lib/navigation/contentLinks'
import type { MyStreamItem } from '@/lib/api'
import { CheckCircle, ShieldOff, Shield } from 'lucide-react'

const statusConfig = {
  active: { label: 'Active', icon: CheckCircle, color: 'text-emerald-500' },
  blocked: { label: 'Blocked', icon: ShieldOff, color: 'text-red-500' },
  inactive: { label: 'Inactive', icon: Shield, color: 'text-muted-foreground' },
} as const

function getDisplayStatus(stream: MyStreamItem): keyof typeof statusConfig {
  if (stream.is_blocked) return 'blocked'
  if (stream.is_active === false) return 'inactive'
  return 'active'
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

interface MyStreamPosterCardProps {
  stream: MyStreamItem
  onUpdated?: () => void
}

export function MyStreamPosterCard({ stream, onUpdated }: MyStreamPosterCardProps) {
  const { rpdbApiKey } = useRpdb()
  const displayStatus = getDisplayStatus(stream)
  const status = statusConfig[displayStatus]
  const StatusIcon = status.icon
  const contentLink = buildContentStreamUrl(stream.media_type, stream.media_id, stream.id)

  const hdrList =
    typeof stream.hdr_formats === 'string'
      ? stream.hdr_formats.split('|').filter(Boolean)
      : Array.isArray(stream.hdr_formats)
        ? stream.hdr_formats
        : []
  const audioStr = Array.isArray(stream.audio_formats) ? stream.audio_formats.join('|') : stream.audio_formats

  const posterMetaId = getPosterMetaId(stream.media_imdb_id, stream.media_id, stream.id ? String(stream.id) : undefined)
  const catalogType = stream.media_type === 'series' ? 'series' : 'movie'

  return (
    <div className="rounded-xl border border-border/50 bg-card/50 overflow-hidden hover:border-primary/30 transition-colors">
      <div className="flex flex-col sm:flex-row gap-0">
        <div className="sm:w-36 shrink-0 p-3 sm:p-4 flex sm:flex-col items-center gap-3 sm:gap-2 bg-muted/20">
          <Link
            to={contentLink || '#'}
            className={contentLink ? 'block w-24 sm:w-full' : 'block w-24 sm:w-full pointer-events-none'}
            onClick={(e) => !contentLink && e.preventDefault()}
          >
            <Poster
              metaId={posterMetaId}
              catalogType={catalogType}
              poster={stream.media_poster_url}
              rpdbApiKey={rpdbApiKey}
              title={stream.media_title || stream.stream_name || stream.name}
              className="w-full rounded-lg shadow-sm"
            />
          </Link>
          <Badge variant="secondary" className={`${status.color} bg-opacity-10 text-xs`}>
            <StatusIcon className="mr-1 h-3 w-3" />
            {status.label}
          </Badge>
        </div>

        <div className="flex-1 min-w-0 p-3 sm:py-4 sm:pr-4 space-y-2">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 space-y-1">
              {stream.media_title ? (
                contentLink ? (
                  <Link to={contentLink} className="font-medium text-sm hover:text-primary line-clamp-1">
                    {stream.media_title}
                  </Link>
                ) : (
                  <p className="font-medium text-sm line-clamp-1">{stream.media_title}</p>
                )
              ) : (
                <p className="font-medium text-sm line-clamp-1 text-muted-foreground">Unlinked stream</p>
              )}
              <p
                className="text-xs text-muted-foreground font-mono line-clamp-2"
                title={stream.stream_name || stream.name}
              >
                {stream.stream_name || stream.name}
              </p>
            </div>
            {(stream.size_bytes || stream.size) && (
              <span className="text-xs text-muted-foreground shrink-0">
                {stream.size_bytes ? formatBytes(stream.size_bytes) : stream.size}
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {stream.stream_type && (
              <Badge variant="outline" className="text-[10px] capitalize">
                {stream.stream_type}
              </Badge>
            )}
            <QualityTierBadge
              resolution={stream.resolution}
              quality={stream.quality}
              codec={stream.codec}
              audio={audioStr}
              hdr={hdrList}
            />
            {(stream.file_count ?? 0) > 1 && (
              <Badge variant="outline" className="text-[10px]">
                {stream.file_count} files
              </Badge>
            )}
          </div>

          <div className="flex justify-end pt-1">
            <StreamCard
              stream={stream}
              onClick={() => {}}
              showActions
              showOwnerActions
              showModeratorActions={false}
              embedded
              fileCount={stream.file_count}
              mediaType={stream.media_type === 'series' ? 'series' : 'movie'}
              onDeleted={onUpdated}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
