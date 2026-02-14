import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import {
  Film,
  Tv,
  Calendar,
  Clock,
  Globe,
  Lock,
  Edit,
  ExternalLink,
  Layers,
  Tag,
  FolderOpen,
  Link2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { UserMediaResponse } from '@/lib/api'

interface MetadataDetailDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  media: UserMediaResponse | null
  onEdit: () => void
}

export function MetadataDetailDialog({
  open,
  onOpenChange,
  media,
  onEdit,
}: MetadataDetailDialogProps) {
  if (!media) return null

  const isMovie = media.type === 'movie'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[700px] max-h-[85vh] flex flex-col p-0 overflow-hidden">
        {/* Header with backdrop */}
        <div className="relative h-32 bg-gradient-to-b from-primary/20 to-background overflow-hidden">
          {media.background_url && (
            <img
              src={media.background_url}
              alt=""
              className="absolute inset-0 w-full h-full object-cover opacity-30"
            />
          )}
          <div className="absolute inset-0 bg-gradient-to-t from-background to-transparent" />
          
          {/* Poster overlay */}
          <div className="absolute -bottom-8 left-6 w-20 h-28 rounded-lg border-2 border-background overflow-hidden bg-muted shadow-xl">
            {media.poster_url ? (
              <img
                src={media.poster_url}
                alt={media.title}
                className="w-full h-full object-cover"
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center">
                {isMovie ? (
                  <Film className="h-8 w-8 text-muted-foreground" />
                ) : (
                  <Tv className="h-8 w-8 text-muted-foreground" />
                )}
              </div>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="px-6 pt-12 pb-6 flex-1 overflow-hidden flex flex-col">
          <DialogHeader className="text-left">
            <div className="flex items-start justify-between gap-4">
              <div>
                <DialogTitle className="text-xl flex items-center gap-2">
                  {media.title}
                  {media.year && (
                    <span className="text-muted-foreground font-normal">
                      ({media.year})
                    </span>
                  )}
                </DialogTitle>
                <DialogDescription className="mt-1 flex items-center gap-2 flex-wrap">
                  <Badge
                    variant="outline"
                    className={cn(
                      isMovie
                        ? 'border-blue-500/50 text-blue-500'
                        : 'border-green-500/50 text-green-500'
                    )}
                  >
                    {isMovie ? <Film className="h-3 w-3 mr-1" /> : <Tv className="h-3 w-3 mr-1" />}
                    {media.type}
                  </Badge>
                  {media.is_public ? (
                    <Badge variant="outline" className="border-green-500/50 text-green-500">
                      <Globe className="h-3 w-3 mr-1" />
                      Public
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="border-primary/50 text-primary">
                      <Lock className="h-3 w-3 mr-1" />
                      Private
                    </Badge>
                  )}
                  {media.total_streams > 0 && (
                    <Badge variant="secondary">
                      <Link2 className="h-3 w-3 mr-1" />
                      {media.total_streams} stream{media.total_streams !== 1 ? 's' : ''}
                    </Badge>
                  )}
                </DialogDescription>
              </div>
              <Button onClick={onEdit} size="sm" variant="outline">
                <Edit className="h-4 w-4 mr-1.5" />
                Edit
              </Button>
            </div>
          </DialogHeader>

          <ScrollArea className="flex-1 mt-4 -mx-6 px-6">
            <div className="space-y-6">
              {/* Description */}
              {media.description && (
                <div>
                  <h4 className="text-sm font-medium mb-2">Description</h4>
                  <p className="text-sm text-muted-foreground">{media.description}</p>
                </div>
              )}

              {/* Details */}
              <div className="grid gap-4 sm:grid-cols-2">
                {media.runtime_minutes && (
                  <div className="flex items-center gap-2 text-sm">
                    <Clock className="h-4 w-4 text-muted-foreground" />
                    <span>{media.runtime_minutes} minutes</span>
                  </div>
                )}
                {media.year && (
                  <div className="flex items-center gap-2 text-sm">
                    <Calendar className="h-4 w-4 text-muted-foreground" />
                    <span>{media.year}</span>
                  </div>
                )}
              </div>

              {/* Series Info */}
              {!isMovie && (media.total_seasons !== undefined || media.total_episodes !== undefined) && (
                <div>
                  <h4 className="text-sm font-medium mb-2 flex items-center gap-1.5">
                    <Layers className="h-4 w-4" />
                    Series Info
                  </h4>
                  <div className="flex gap-4">
                    {media.total_seasons !== undefined && (
                      <div className="text-sm">
                        <span className="text-muted-foreground">Seasons: </span>
                        <span className="font-medium">{media.total_seasons}</span>
                      </div>
                    )}
                    {media.total_episodes !== undefined && (
                      <div className="text-sm">
                        <span className="text-muted-foreground">Episodes: </span>
                        <span className="font-medium">{media.total_episodes}</span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Seasons */}
              {!isMovie && media.seasons && media.seasons.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2">Seasons</h4>
                  <div className="space-y-2">
                    {media.seasons.map((season) => (
                      <div
                        key={season.id}
                        className="p-3 rounded-lg border border-border/50 bg-muted/20"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <Badge variant="outline">Season {season.season_number}</Badge>
                            {season.name && (
                              <span className="text-sm text-muted-foreground">{season.name}</span>
                            )}
                          </div>
                          <span className="text-xs text-muted-foreground">
                            {season.episode_count} episode{season.episode_count !== 1 ? 's' : ''}
                          </span>
                        </div>
                        {season.episodes && season.episodes.length > 0 && (
                          <div className="mt-2 pl-4 border-l border-border/50 space-y-1">
                            {season.episodes.slice(0, 5).map((episode) => (
                              <div key={episode.id} className="text-xs text-muted-foreground">
                                E{episode.episode_number}: {episode.title}
                              </div>
                            ))}
                            {season.episodes.length > 5 && (
                              <div className="text-xs text-muted-foreground">
                                ... and {season.episodes.length - 5} more
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <Separator />

              {/* Genres */}
              {media.genres && media.genres.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2 flex items-center gap-1.5">
                    <Tag className="h-4 w-4" />
                    Genres
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {media.genres.map((genre) => (
                      <Badge key={genre} variant="secondary">
                        {genre}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Catalogs */}
              {media.catalogs && media.catalogs.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2 flex items-center gap-1.5">
                    <FolderOpen className="h-4 w-4" />
                    Catalogs
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {media.catalogs.map((catalog) => (
                      <Badge key={catalog} variant="outline">
                        {catalog}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* External IDs */}
              {media.external_ids && Object.keys(media.external_ids).length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2 flex items-center gap-1.5">
                    <ExternalLink className="h-4 w-4" />
                    External IDs
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(media.external_ids).map(([provider, id]) => (
                      <Badge key={provider} variant="outline" className="gap-1">
                        <span className="text-muted-foreground">{provider}:</span>
                        <span>{id}</span>
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Timestamps */}
              <div className="text-xs text-muted-foreground space-y-1">
                {media.created_at && <div>Created: {new Date(media.created_at).toLocaleString()}</div>}
                {media.updated_at && <div>Updated: {new Date(media.updated_at).toLocaleString()}</div>}
              </div>
            </div>
          </ScrollArea>
        </div>
      </DialogContent>
    </Dialog>
  )
}


