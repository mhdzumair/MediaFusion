import { useState, useMemo } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { 
  Play,
  Calendar,
  Tv,
  Check,
  Trash2,
  Loader2,
  Edit,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { EpisodeEditSheet, type EpisodeData } from './EpisodeEditSheet'
import { useAuth } from '@/contexts/AuthContext'

export interface EpisodeInfo {
  id?: number  // Episode database ID (for moderator actions)
  episode_number: number
  title?: string
  released?: string
  overview?: string
  thumbnail?: string  // Episode still/thumbnail image
  is_user_created?: boolean
  is_user_addition?: boolean
  stream_count?: number  // Number of streams for this episode (for showing empty episodes)
  runtime_minutes?: number  // Episode runtime in minutes
}

export interface SeasonInfo {
  season_number: number
  episodes: EpisodeInfo[]
  name?: string
  overview?: string
  poster?: string
}

interface SeriesEpisodePickerProps {
  seasons: SeasonInfo[]
  selectedSeason?: number
  selectedEpisode?: number
  onSeasonChange: (season: number) => void
  onEpisodeChange: (episode: number) => void
  onEpisodePlay?: (season: number, episode: number) => void
  // Admin props (episode deletion is admin-only)
  isAdmin?: boolean
  onDeleteEpisode?: (episodeId: number, seasonNumber: number, episodeNumber: number) => Promise<void>
  isDeletingEpisode?: boolean
  // Series info for episode edit context
  seriesTitle?: string
  className?: string
}

export function SeriesEpisodePicker({
  seasons,
  selectedSeason,
  selectedEpisode,
  onSeasonChange,
  onEpisodeChange,
  onEpisodePlay,
  isAdmin = false,
  onDeleteEpisode,
  isDeletingEpisode = false,
  seriesTitle,
  className,
}: SeriesEpisodePickerProps) {
  const [expandedSeasons, setExpandedSeasons] = useState<number[]>(
    selectedSeason ? [selectedSeason] : seasons.length > 0 ? [seasons[0].season_number] : []
  )
  const [deletingEpisodeId, setDeletingEpisodeId] = useState<number | null>(null)
  const { isAuthenticated } = useAuth()

  const currentSeason = useMemo(() => 
    seasons.find(s => s.season_number === selectedSeason),
    [seasons, selectedSeason]
  )

  const episodes = currentSeason?.episodes ?? []

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return null
    try {
      return new Date(dateStr).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
      })
    } catch {
      return dateStr
    }
  }

  const isAired = (dateStr?: string) => {
    if (!dateStr) return true // Assume aired if no date
    try {
      return new Date(dateStr) <= new Date()
    } catch {
      return true
    }
  }

  if (seasons.length === 0) {
    return null
  }

  return (
    <Card className={cn("glass border-border/50", className)}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Tv className="h-5 w-5 text-primary" />
            <CardTitle className="text-lg">Episodes</CardTitle>
          </div>
          {selectedSeason && selectedEpisode && (
            <Badge variant="secondary" className="rounded-lg">
              S{selectedSeason.toString().padStart(2, '0')}E{selectedEpisode.toString().padStart(2, '0')}
            </Badge>
          )}
        </div>
        <CardDescription>
          {seasons.length} season{seasons.length !== 1 ? 's' : ''} available
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Horizontal Season Tabs */}
        <ScrollArea className="w-full pb-2">
          <div className="flex gap-2">
            {seasons.map(season => (
              <Button
                key={season.season_number}
                variant={selectedSeason === season.season_number ? "default" : "outline"}
                size="sm"
                onClick={() => {
                  onSeasonChange(season.season_number)
                  if (!expandedSeasons.includes(season.season_number)) {
                    setExpandedSeasons(prev => [...prev, season.season_number])
                  }
                }}
                className={cn(
                  "rounded-full whitespace-nowrap transition-all",
                  selectedSeason === season.season_number && 
                    "bg-gradient-to-r from-primary to-primary/80"
                )}
              >
                {season.name || `Season ${season.season_number}`}
                <Badge 
                  variant="secondary" 
                  className={cn(
                    "ml-2 h-5 min-w-5 px-1.5 text-[10px] rounded-full",
                    selectedSeason === season.season_number 
                      ? "bg-white/20 text-white" 
                      : "bg-muted"
                  )}
                >
                  {season.episodes.length}
                </Badge>
              </Button>
            ))}
          </div>
          <ScrollBar orientation="horizontal" />
        </ScrollArea>

        {/* Episode List */}
        {currentSeason && (
          <div className="space-y-2">
            {/* Episode count indicator */}
            <div className="flex items-center justify-between text-xs text-muted-foreground px-1">
              <span>{episodes.length} episode{episodes.length !== 1 ? 's' : ''}</span>
              {episodes.filter(e => isAired(e.released)).length !== episodes.length && (
                <span className="text-primary">
                  {episodes.filter(e => !isAired(e.released)).length} upcoming
                </span>
              )}
            </div>
            
            {/* Scrollable episode list with max height */}
            <ScrollArea className="h-[400px] pr-3">
              <div className="grid gap-2">
                {episodes.map((episode) => {
                  const isSelected = selectedEpisode === episode.episode_number
                  const aired = isAired(episode.released)
                  
                  return (
                    <div
                      key={episode.episode_number}
                      className={cn(
                        "group relative flex gap-3 p-3 rounded-xl transition-all cursor-pointer",
                        "hover:bg-muted/50",
                        isSelected && "bg-primary/10 border border-primary/30",
                        !aired && "opacity-60"
                      )}
                      onClick={() => aired && onEpisodeChange(episode.episode_number)}
                    >
                      {/* Episode Thumbnail or Number */}
                      {episode.thumbnail ? (
                        <div className="flex-shrink-0 w-28 h-16 rounded-lg overflow-hidden bg-muted">
                          <img 
                            src={episode.thumbnail} 
                            alt={episode.title || `Episode ${episode.episode_number}`}
                            className="w-full h-full object-cover"
                            loading="lazy"
                          />
                          <div className={cn(
                            "absolute top-2 left-2 w-6 h-6 rounded-md flex items-center justify-center text-xs font-bold",
                            "bg-black/70 text-white backdrop-blur-sm"
                          )}>
                            {episode.episode_number}
                          </div>
                        </div>
                      ) : (
                        <div className={cn(
                          "flex-shrink-0 w-12 h-12 rounded-lg flex items-center justify-center text-sm font-medium",
                          isSelected 
                            ? "bg-gradient-to-br from-primary to-primary/80 text-white" 
                            : "bg-muted text-muted-foreground"
                        )}>
                          {episode.episode_number}
                        </div>
                      )}

                      {/* Episode Info */}
                      <div className="flex-1 min-w-0 flex flex-col justify-center">
                        <div className="flex items-center gap-2">
                          <span className={cn(
                            "font-medium line-clamp-1",
                            isSelected && "text-primary"
                          )}>
                            {episode.title || `Episode ${episode.episode_number}`}
                          </span>
                          {!aired && (
                            <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-primary/50 text-primary flex-shrink-0">
                              Upcoming
                            </Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          {episode.released && (
                            <span className="flex items-center gap-1 text-xs text-muted-foreground">
                              <Calendar className="h-3 w-3" />
                              {formatDate(episode.released)}
                            </span>
                          )}
                        </div>
                        {/* Episode overview preview */}
                        {episode.overview && (
                          <p className="text-xs text-muted-foreground line-clamp-2 mt-1">
                            {episode.overview}
                          </p>
                        )}
                      </div>

                      {/* Play/Select Button and Actions */}
                      <div className="flex-shrink-0 flex items-center gap-1">
                        {/* User Edit Button */}
                        {isAuthenticated && episode.id && (
                          <EpisodeEditSheet
                            episode={{
                              id: episode.id,
                              episode_number: episode.episode_number,
                              title: episode.title,
                              overview: episode.overview,
                              air_date: episode.released,
                              runtime_minutes: episode.runtime_minutes,
                              season_number: selectedSeason,
                              series_title: seriesTitle,
                            } as EpisodeData}
                            trigger={
                              <TooltipProvider>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      className={cn(
                                        "h-8 w-8 p-0 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity",
                                        "text-muted-foreground hover:text-primary hover:bg-primary/10"
                                      )}
                                      onClick={(e) => e.stopPropagation()}
                                    >
                                      <Edit className="h-4 w-4" />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>
                                    <p>Suggest edit</p>
                                  </TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            }
                          />
                        )}
                        
                        {/* Admin Delete Button */}
                        {isAdmin && episode.id && onDeleteEpisode && (
                          <AlertDialog>
                            <TooltipProvider>
                              <Tooltip>
                                <AlertDialogTrigger asChild>
                                  <TooltipTrigger asChild>
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      className={cn(
                                        "h-8 w-8 p-0 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity",
                                        "text-destructive hover:text-destructive hover:bg-destructive/10"
                                      )}
                                      onClick={(e) => e.stopPropagation()}
                                      disabled={isDeletingEpisode}
                                    >
                                      {deletingEpisodeId === episode.id ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                      ) : (
                                        <Trash2 className="h-4 w-4" />
                                      )}
                                    </Button>
                                  </TooltipTrigger>
                                </AlertDialogTrigger>
                                <TooltipContent>
                                  <p>Delete episode (admin)</p>
                                </TooltipContent>
                              </Tooltip>
                            </TooltipProvider>
                            <AlertDialogContent onClick={(e) => e.stopPropagation()}>
                              <AlertDialogHeader>
                                <AlertDialogTitle>Delete Episode?</AlertDialogTitle>
                                <AlertDialogDescription>
                                  This will permanently delete S{selectedSeason?.toString().padStart(2, '0')}E{episode.episode_number.toString().padStart(2, '0')}
                                  {episode.title && ` - "${episode.title}"`} from the database.
                                  <br /><br />
                                  This action cannot be undone. Use this to clean up incorrectly auto-detected episodes.
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction
                                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                                  onClick={async (e) => {
                                    e.stopPropagation()
                                    if (episode.id) {
                                      setDeletingEpisodeId(episode.id)
                                      try {
                                        await onDeleteEpisode(episode.id, selectedSeason!, episode.episode_number)
                                      } finally {
                                        setDeletingEpisodeId(null)
                                      }
                                    }
                                  }}
                                >
                                  Delete Episode
                                </AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        )}
                        
                        {aired && onEpisodePlay ? (
                          <Button
                            size="sm"
                            variant={isSelected ? "default" : "ghost"}
                            className={cn(
                              "h-9 w-9 p-0 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity",
                              isSelected && "opacity-100 bg-gradient-to-r from-primary to-primary/80"
                            )}
                            onClick={(e) => {
                              e.stopPropagation()
                              onEpisodePlay(selectedSeason!, episode.episode_number)
                            }}
                          >
                            <Play className="h-4 w-4" />
                          </Button>
                        ) : isSelected ? (
                          <Check className="h-4 w-4 text-primary" />
                        ) : null}
                      </div>
                    </div>
                  )
                })}
              </div>
            </ScrollArea>

            {episodes.length === 0 && (
              <div className="text-center py-8 text-muted-foreground">
                <Tv className="h-12 w-12 mx-auto opacity-50 mb-2" />
                <p>No episodes available for this season</p>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// Compact version for inline use
export function SeasonEpisodeSelector({
  seasons,
  selectedSeason,
  selectedEpisode,
  onSeasonChange,
  onEpisodeChange,
  className,
}: Omit<SeriesEpisodePickerProps, 'onEpisodePlay'>) {
  const currentSeason = useMemo(() => 
    seasons.find(s => s.season_number === selectedSeason),
    [seasons, selectedSeason]
  )

  return (
    <div className={cn("flex flex-wrap gap-3", className)}>
      {/* Season Pills */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground">Season</label>
        <ScrollArea className="w-auto max-w-[300px]">
          <div className="flex gap-1.5 pb-1">
            {seasons.map(season => (
              <Button
                key={season.season_number}
                variant={selectedSeason === season.season_number ? "default" : "outline"}
                size="sm"
                onClick={() => {
                  onSeasonChange(season.season_number)
                  // Auto-select first episode when changing seasons
                  if (season.episodes.length > 0) {
                    onEpisodeChange(season.episodes[0].episode_number)
                  }
                }}
                className={cn(
                  "h-8 px-3 rounded-lg",
                  selectedSeason === season.season_number && 
                    "bg-gradient-to-r from-primary to-primary/80"
                )}
              >
                {season.season_number}
              </Button>
            ))}
          </div>
          <ScrollBar orientation="horizontal" />
        </ScrollArea>
      </div>

      {/* Episode Pills */}
      {currentSeason && currentSeason.episodes.length > 0 && (
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Episode</label>
          <ScrollArea className="w-auto max-w-[400px]">
            <div className="flex gap-1.5 pb-1">
              {currentSeason.episodes.map(episode => (
                <Button
                  key={episode.episode_number}
                  variant={selectedEpisode === episode.episode_number ? "default" : "outline"}
                  size="sm"
                  onClick={() => onEpisodeChange(episode.episode_number)}
                  className={cn(
                    "h-8 min-w-8 px-3 rounded-lg",
                    selectedEpisode === episode.episode_number && 
                      "bg-gradient-to-r from-primary to-primary/80"
                  )}
                  title={episode.title || `Episode ${episode.episode_number}`}
                >
                  {episode.episode_number}
                </Button>
              ))}
            </div>
            <ScrollBar orientation="horizontal" />
          </ScrollArea>
        </div>
      )}
    </div>
  )
}

