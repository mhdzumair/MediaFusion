import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Poster } from '@/components/ui/poster'
import { useRpdb } from '@/contexts/RpdbContext'
import { buildContentStreamUrl, getPosterMetaId } from '@/lib/navigation/contentLinks'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { ArrowRight, Clock, CheckCircle, XCircle, Zap, Eye, Trash2, MoreVertical, Film } from 'lucide-react'
import type { StreamSuggestion, StreamSuggestionStatus } from '@/lib/api'

const streamStatusConfig: Record<StreamSuggestionStatus, { label: string; icon: typeof Clock; color: string }> = {
  pending: { label: 'Pending', icon: Clock, color: 'text-primary' },
  approved: { label: 'Approved', icon: CheckCircle, color: 'text-emerald-500' },
  auto_approved: { label: 'Auto-Approved', icon: Zap, color: 'text-blue-500' },
  rejected: { label: 'Rejected', icon: XCircle, color: 'text-red-500' },
}

function formatSuggestionType(type: string): string {
  const typeMap: Record<string, string> = {
    report_broken: 'Report Broken',
    field_correction: 'Field Correction',
    language_add: 'Add Language',
    language_remove: 'Remove Language',
    mark_duplicate: 'Mark Duplicate',
    relink_media: 'Relink Media',
    add_media_link: 'Add Media Link',
    other: 'Other',
  }
  return typeMap[type] || type
}

function formatFieldName(fieldName: string | null): string {
  if (!fieldName) return ''
  if (fieldName.startsWith('episode_link:')) {
    const parts = fieldName.split(':')
    if (parts.length >= 3) {
      const field = parts[2]
      const fieldDisplay: Record<string, string> = {
        season_number: 'Season',
        episode_number: 'Episode',
        episode_end: 'Episode End',
      }
      return `Episode Link (${fieldDisplay[field] || field})`
    }
  }
  return fieldName.replace(/_/g, ' ')
}

interface StreamEditSuggestionCardProps {
  suggestion: StreamSuggestion
  onViewDetails: () => void
  onWithdraw: () => void
}

export function StreamEditSuggestionCard({ suggestion, onViewDetails, onWithdraw }: StreamEditSuggestionCardProps) {
  const { rpdbApiKey } = useRpdb()
  const status = streamStatusConfig[suggestion.status]
  const StatusIcon = status?.icon ?? Clock
  const mediaId = suggestion.source_media_id || suggestion.media_id
  const contentLink = buildContentStreamUrl(suggestion.source_media_type, mediaId, suggestion.stream_id)
  const posterUrl = suggestion.source_media_poster_url
  const mediaTitle = suggestion.source_media_title
  const metaId = getPosterMetaId(suggestion.source_media_imdb_id, mediaId, suggestion.stream_id)
  const catalogType = suggestion.source_media_type === 'series' ? 'series' : 'movie'

  return (
    <div className="rounded-xl border border-border/50 bg-card/50 overflow-hidden hover:border-primary/30 transition-colors">
      <div className="flex gap-0">
        <div className="w-24 sm:w-28 shrink-0 p-3 bg-muted/20 flex flex-col items-center gap-2">
          {mediaTitle ? (
            contentLink ? (
              <Link to={contentLink} className="block w-full">
                <Poster
                  metaId={metaId}
                  catalogType={catalogType}
                  poster={posterUrl}
                  rpdbApiKey={rpdbApiKey}
                  title={mediaTitle}
                  className="w-full rounded-lg"
                />
              </Link>
            ) : (
              <Poster
                metaId={metaId}
                catalogType={catalogType}
                poster={posterUrl}
                rpdbApiKey={rpdbApiKey}
                title={mediaTitle}
                className="w-full rounded-lg"
              />
            )
          ) : (
            <div className="w-full aspect-[2/3] rounded-lg bg-muted/50 flex items-center justify-center">
              <Film className="h-8 w-8 text-muted-foreground/50" />
            </div>
          )}
          <Badge variant="secondary" className={`${status?.color} bg-opacity-10 text-[10px]`}>
            <StatusIcon className="mr-1 h-3 w-3" />
            {status?.label}
          </Badge>
        </div>

        <div className="flex-1 min-w-0 p-3 sm:py-4 sm:pr-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 space-y-1.5 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="font-medium text-sm">{formatSuggestionType(suggestion.suggestion_type)}</p>
                {suggestion.field_name && (
                  <Badge variant="outline" className="text-[10px] capitalize">
                    {formatFieldName(suggestion.field_name)}
                  </Badge>
                )}
                {suggestion.was_auto_approved && (
                  <Badge variant="outline" className="text-[10px] text-blue-500">
                    <Zap className="h-3 w-3 mr-1" />
                    Auto
                  </Badge>
                )}
              </div>

              {mediaTitle &&
                (contentLink ? (
                  <Link to={contentLink} className="text-sm text-primary hover:underline line-clamp-1">
                    {mediaTitle}
                  </Link>
                ) : (
                  <p className="text-sm line-clamp-1">{mediaTitle}</p>
                ))}

              {suggestion.stream_name && (
                <p className="text-xs text-muted-foreground font-mono line-clamp-1" title={suggestion.stream_name}>
                  {suggestion.stream_name}
                </p>
              )}

              {suggestion.field_name && (suggestion.current_value || suggestion.suggested_value) && (
                <div className="flex items-center gap-2 text-xs bg-muted/40 rounded-md px-2 py-1.5 max-w-full">
                  <span className="text-red-400 line-through truncate" title={suggestion.current_value || ''}>
                    {suggestion.current_value || '(empty)'}
                  </span>
                  <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                  <span className="text-emerald-400 truncate" title={suggestion.suggested_value || ''}>
                    {suggestion.suggested_value || '(empty)'}
                  </span>
                </div>
              )}

              <p className="text-[11px] text-muted-foreground">
                Submitted {new Date(suggestion.created_at).toLocaleDateString()}
                {suggestion.reviewed_at && ` · Reviewed ${new Date(suggestion.reviewed_at).toLocaleDateString()}`}
              </p>
            </div>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0">
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={onViewDetails}>
                  <Eye className="mr-2 h-4 w-4" />
                  View Details
                </DropdownMenuItem>
                {suggestion.status === 'pending' && (
                  <DropdownMenuItem className="text-destructive" onClick={onWithdraw}>
                    <Trash2 className="mr-2 h-4 w-4" />
                    Withdraw
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>
    </div>
  )
}
