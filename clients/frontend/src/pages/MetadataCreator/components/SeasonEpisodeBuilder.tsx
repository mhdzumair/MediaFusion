import { useState, useCallback } from 'react'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Plus, Trash2, ChevronDown, ChevronRight, Hash, Loader2, Edit2, Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { UserSeasonCreate, UserEpisodeCreate, SeasonResponse } from '@/lib/api'

interface SeasonEpisodeBuilderProps {
  seasons: UserSeasonCreate[]
  onChange: (seasons: UserSeasonCreate[]) => void
  existingSeasons?: SeasonResponse[]
  onDeleteExistingSeason?: (seasonNumber: number) => Promise<void>
  onDeleteExistingEpisode?: (episodeId: number) => Promise<void>
  isLoading?: boolean
}

export function SeasonEpisodeBuilder({
  seasons,
  onChange,
  existingSeasons,
  onDeleteExistingSeason,
  onDeleteExistingEpisode,
  isLoading,
}: SeasonEpisodeBuilderProps) {
  const [expandedSeasons, setExpandedSeasons] = useState<Set<number>>(new Set([0]))
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [itemToDelete, setItemToDelete] = useState<{
    type: 'season' | 'episode'
    seasonNumber?: number
    episodeId?: number
    episodeIndex?: number
    seasonIndex?: number
  } | null>(null)
  const [editingEpisode, setEditingEpisode] = useState<{
    seasonIndex: number
    episodeIndex: number
  } | null>(null)

  const toggleSeason = useCallback((index: number) => {
    setExpandedSeasons((prev) => {
      const next = new Set(prev)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }, [])

  const addSeason = useCallback(() => {
    const nextSeasonNumber = seasons.length > 0 ? Math.max(...seasons.map((s) => s.season_number)) + 1 : 1

    onChange([
      ...seasons,
      {
        season_number: nextSeasonNumber,
        name: `Season ${nextSeasonNumber}`,
        episodes: [],
      },
    ])
    setExpandedSeasons((prev) => new Set([...prev, seasons.length]))
  }, [seasons, onChange])

  const updateSeason = useCallback(
    (index: number, updates: Partial<UserSeasonCreate>) => {
      const newSeasons = [...seasons]
      newSeasons[index] = { ...newSeasons[index], ...updates }
      onChange(newSeasons)
    },
    [seasons, onChange],
  )

  const removeSeason = useCallback(
    (index: number) => {
      onChange(seasons.filter((_, i) => i !== index))
      setExpandedSeasons((prev) => {
        const next = new Set(prev)
        next.delete(index)
        return next
      })
    },
    [seasons, onChange],
  )

  const addEpisode = useCallback(
    (seasonIndex: number) => {
      const season = seasons[seasonIndex]
      const nextEpisodeNumber =
        season.episodes && season.episodes.length > 0
          ? Math.max(...season.episodes.map((e) => e.episode_number)) + 1
          : 1

      const newEpisode: UserEpisodeCreate = {
        episode_number: nextEpisodeNumber,
        title: `Episode ${nextEpisodeNumber}`,
      }

      const newSeasons = [...seasons]
      newSeasons[seasonIndex] = {
        ...season,
        episodes: [...(season.episodes || []), newEpisode],
      }
      onChange(newSeasons)
    },
    [seasons, onChange],
  )

  const updateEpisode = useCallback(
    (seasonIndex: number, episodeIndex: number, updates: Partial<UserEpisodeCreate>) => {
      const newSeasons = [...seasons]
      const episodes = [...(newSeasons[seasonIndex].episodes || [])]
      episodes[episodeIndex] = { ...episodes[episodeIndex], ...updates }
      newSeasons[seasonIndex] = { ...newSeasons[seasonIndex], episodes }
      onChange(newSeasons)
    },
    [seasons, onChange],
  )

  const removeEpisode = useCallback(
    (seasonIndex: number, episodeIndex: number) => {
      const newSeasons = [...seasons]
      const episodes = [...(newSeasons[seasonIndex].episodes || [])]
      episodes.splice(episodeIndex, 1)
      newSeasons[seasonIndex] = { ...newSeasons[seasonIndex], episodes }
      onChange(newSeasons)
    },
    [seasons, onChange],
  )

  const handleDeleteClick = useCallback((item: typeof itemToDelete) => {
    setItemToDelete(item)
    setDeleteDialogOpen(true)
  }, [])

  const handleDeleteConfirm = useCallback(async () => {
    if (!itemToDelete) return

    if (itemToDelete.type === 'season') {
      if (itemToDelete.seasonNumber !== undefined && onDeleteExistingSeason) {
        await onDeleteExistingSeason(itemToDelete.seasonNumber)
      } else if (itemToDelete.seasonIndex !== undefined) {
        removeSeason(itemToDelete.seasonIndex)
      }
    } else if (itemToDelete.type === 'episode') {
      if (itemToDelete.episodeId !== undefined && onDeleteExistingEpisode) {
        await onDeleteExistingEpisode(itemToDelete.episodeId)
      } else if (itemToDelete.seasonIndex !== undefined && itemToDelete.episodeIndex !== undefined) {
        removeEpisode(itemToDelete.seasonIndex, itemToDelete.episodeIndex)
      }
    }

    setDeleteDialogOpen(false)
    setItemToDelete(null)
  }, [itemToDelete, onDeleteExistingSeason, onDeleteExistingEpisode, removeSeason, removeEpisode])

  const addBulkEpisodes = useCallback(
    (seasonIndex: number, count: number) => {
      const season = seasons[seasonIndex]
      const startNumber =
        season.episodes && season.episodes.length > 0
          ? Math.max(...season.episodes.map((e) => e.episode_number)) + 1
          : 1

      const newEpisodes: UserEpisodeCreate[] = Array.from({ length: count }, (_, i) => ({
        episode_number: startNumber + i,
        title: `Episode ${startNumber + i}`,
      }))

      const newSeasons = [...seasons]
      newSeasons[seasonIndex] = {
        ...season,
        episodes: [...(season.episodes || []), ...newEpisodes],
      }
      onChange(newSeasons)
    },
    [seasons, onChange],
  )

  return (
    <div className="space-y-4">
      {/* Existing Seasons (from server) */}
      {existingSeasons && existingSeasons.length > 0 && (
        <div className="space-y-2">
          <Label className="text-sm text-muted-foreground">Existing Seasons</Label>
          {existingSeasons.map((season) => (
            <Card key={season.id} className="border-border/50 bg-muted/20">
              <Collapsible>
                <CollapsibleTrigger asChild>
                  <CardHeader className="py-3 cursor-pointer hover:bg-muted/30 transition-colors">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <ChevronRight className="h-4 w-4 transition-transform ui-expanded:rotate-90" />
                        <Badge variant="outline" className="gap-1">
                          <Hash className="h-3 w-3" />
                          Season {season.season_number}
                        </Badge>
                        {season.name && <span className="text-sm text-muted-foreground">{season.name}</span>}
                        <Badge variant="secondary" className="text-xs">
                          {season.episode_count} episodes
                        </Badge>
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-red-500 hover:text-red-600 hover:bg-red-500/10"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDeleteClick({
                            type: 'season',
                            seasonNumber: season.season_number,
                          })
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </CardHeader>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <CardContent className="pt-0 pb-3">
                    <div className="space-y-1 pl-6">
                      {season.episodes.map((episode) => (
                        <div
                          key={episode.id}
                          className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-muted/30"
                        >
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground w-8">E{episode.episode_number}</span>
                            <span className="text-sm">{episode.title}</span>
                          </div>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6 text-red-500 hover:text-red-600 hover:bg-red-500/10"
                            onClick={() =>
                              handleDeleteClick({
                                type: 'episode',
                                episodeId: episode.id,
                              })
                            }
                          >
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </CollapsibleContent>
              </Collapsible>
            </Card>
          ))}
        </div>
      )}

      {/* New Seasons */}
      {seasons.length > 0 && (
        <div className="space-y-2">
          <Label className="text-sm text-muted-foreground">
            {existingSeasons && existingSeasons.length > 0 ? 'New Seasons' : 'Seasons'}
          </Label>
          <ScrollArea className="max-h-[400px]">
            <div className="space-y-2 pr-4">
              {seasons.map((season, seasonIndex) => (
                <Card key={seasonIndex} className="border-border/50 bg-card/50">
                  <Collapsible open={expandedSeasons.has(seasonIndex)} onOpenChange={() => toggleSeason(seasonIndex)}>
                    <CollapsibleTrigger asChild>
                      <CardHeader className="py-3 cursor-pointer hover:bg-muted/30 transition-colors">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            {expandedSeasons.has(seasonIndex) ? (
                              <ChevronDown className="h-4 w-4" />
                            ) : (
                              <ChevronRight className="h-4 w-4" />
                            )}
                            <Badge variant="outline" className="gap-1 border-primary/50 text-primary">
                              <Hash className="h-3 w-3" />
                              Season {season.season_number}
                            </Badge>
                            {season.name && <span className="text-sm text-muted-foreground">{season.name}</span>}
                            <Badge variant="secondary" className="text-xs">
                              {season.episodes?.length || 0} episodes
                            </Badge>
                          </div>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 text-red-500 hover:text-red-600 hover:bg-red-500/10"
                            onClick={(e) => {
                              e.stopPropagation()
                              handleDeleteClick({
                                type: 'season',
                                seasonIndex,
                              })
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </CardHeader>
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      <CardContent className="pt-0 pb-4 space-y-4">
                        {/* Season Details */}
                        <div className="grid gap-3 sm:grid-cols-2 pl-6">
                          <div className="space-y-1.5">
                            <Label className="text-xs">Season Number</Label>
                            <Input
                              type="number"
                              min="0"
                              value={season.season_number}
                              onChange={(e) =>
                                updateSeason(seasonIndex, {
                                  season_number: parseInt(e.target.value) || 0,
                                })
                              }
                              className="h-8"
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label className="text-xs">Season Name</Label>
                            <Input
                              value={season.name || ''}
                              onChange={(e) =>
                                updateSeason(seasonIndex, {
                                  name: e.target.value || undefined,
                                })
                              }
                              placeholder="Optional name"
                              className="h-8"
                            />
                          </div>
                        </div>

                        {/* Episodes */}
                        <div className="pl-6 space-y-2">
                          <div className="flex items-center justify-between">
                            <Label className="text-xs">Episodes</Label>
                            <div className="flex gap-1">
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => addBulkEpisodes(seasonIndex, 5)}
                              >
                                +5
                              </Button>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => addBulkEpisodes(seasonIndex, 10)}
                              >
                                +10
                              </Button>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs gap-1"
                                onClick={() => addEpisode(seasonIndex)}
                              >
                                <Plus className="h-3 w-3" />
                                Episode
                              </Button>
                            </div>
                          </div>

                          {season.episodes && season.episodes.length > 0 ? (
                            <ScrollArea className="max-h-[200px]">
                              <div className="space-y-1">
                                {season.episodes.map((episode, episodeIndex) => (
                                  <div
                                    key={episodeIndex}
                                    className={cn(
                                      'flex items-center gap-2 py-1.5 px-2 rounded',
                                      'hover:bg-muted/30 group',
                                    )}
                                  >
                                    {editingEpisode?.seasonIndex === seasonIndex &&
                                    editingEpisode?.episodeIndex === episodeIndex ? (
                                      <>
                                        <Input
                                          type="number"
                                          min="1"
                                          value={episode.episode_number}
                                          onChange={(e) =>
                                            updateEpisode(seasonIndex, episodeIndex, {
                                              episode_number: parseInt(e.target.value) || 1,
                                            })
                                          }
                                          className="h-7 w-14 text-xs"
                                        />
                                        <Input
                                          value={episode.title}
                                          onChange={(e) =>
                                            updateEpisode(seasonIndex, episodeIndex, {
                                              title: e.target.value,
                                            })
                                          }
                                          className="h-7 flex-1 text-xs"
                                          autoFocus
                                        />
                                        <Button
                                          type="button"
                                          variant="ghost"
                                          size="icon"
                                          className="h-6 w-6 text-green-500"
                                          onClick={() => setEditingEpisode(null)}
                                        >
                                          <Check className="h-3 w-3" />
                                        </Button>
                                      </>
                                    ) : (
                                      <>
                                        <span className="text-xs text-muted-foreground w-8">
                                          E{episode.episode_number}
                                        </span>
                                        <span className="text-sm flex-1 truncate">{episode.title}</span>
                                        <div className="opacity-0 group-hover:opacity-100 flex gap-0.5">
                                          <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="h-6 w-6"
                                            onClick={() => setEditingEpisode({ seasonIndex, episodeIndex })}
                                          >
                                            <Edit2 className="h-3 w-3" />
                                          </Button>
                                          <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="h-6 w-6 text-red-500 hover:text-red-600 hover:bg-red-500/10"
                                            onClick={() =>
                                              handleDeleteClick({
                                                type: 'episode',
                                                seasonIndex,
                                                episodeIndex,
                                              })
                                            }
                                          >
                                            <Trash2 className="h-3 w-3" />
                                          </Button>
                                        </div>
                                      </>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </ScrollArea>
                          ) : (
                            <p className="text-xs text-muted-foreground py-2">No episodes yet. Add some above.</p>
                          )}
                        </div>
                      </CardContent>
                    </CollapsibleContent>
                  </Collapsible>
                </Card>
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      {/* Add Season Button */}
      <Button
        type="button"
        variant="outline"
        className="w-full border-dashed border-primary/50 text-primary hover:bg-primary/10"
        onClick={addSeason}
      >
        <Plus className="h-4 w-4 mr-2" />
        Add Season
      </Button>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {itemToDelete?.type === 'season' ? 'Season' : 'Episode'}</AlertDialogTitle>
            <AlertDialogDescription>
              {itemToDelete?.type === 'season'
                ? 'Are you sure you want to delete this season and all its episodes? This action cannot be undone.'
                : 'Are you sure you want to delete this episode? This action cannot be undone.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-red-600 hover:bg-red-700"
              disabled={isLoading}
            >
              {isLoading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Trash2 className="h-4 w-4 mr-2" />}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
