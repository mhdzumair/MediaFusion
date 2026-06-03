import {
  cloneElement,
  isValidElement,
  useState,
  useCallback,
  useEffect,
  type ReactElement,
  type ReactNode,
} from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Loader2, Link2, Unlink, Film, Tv, AlertCircle, HardDrive, CheckCircle2 } from 'lucide-react'
import { getBestExternalId, type CombinedSearchResult } from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import { useCreateStreamSuggestion } from '@/hooks/useStreamSuggestions'
import { apiClient } from '@/lib/api/client'
import { MetadataSearchPopover } from '@/components/metadata'

// ─── Types ────────────────────────────────────────────────────────────────────

interface MediaLinkInfo {
  link_id: number
  media_id: number
  external_id: string
  title: string
  year: number | null
  type: string
  file_index: number | null
  season: number | null
  episode: number | null
}

interface StreamRelinkButtonProps {
  streamId: number
  streamName?: string
  currentMediaId?: number
  currentMediaTitle?: string
  variant?: 'button' | 'icon'
  trigger?: ReactNode
  className?: string
  onSuccess?: () => void
}

// ─── API ──────────────────────────────────────────────────────────────────────

const streamLinkingApi = {
  getMediaForStream: async (streamId: number): Promise<{ stream_id: number; media_entries: MediaLinkInfo[] }> => {
    return apiClient.get(`/stream-links/stream/${streamId}`)
  },
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function normalizeExternalId(raw: string): string {
  const v = raw.trim()
  if (!v) return ''
  if (/^\d+$/.test(v)) return `tmdb:${v}`
  return v
}

function parseOptionalInt(raw: string): number | undefined {
  const v = raw.trim()
  if (!v) return undefined
  const n = Number.parseInt(v, 10)
  return Number.isFinite(n) ? n : undefined
}

function getResultExternalIds(result: CombinedSearchResult): string[] {
  const ids = new Set<string>()
  if (result.external_id) ids.add(result.external_id)
  if (result.imdb_id) ids.add(result.imdb_id)
  if (result.tmdb_id) ids.add(`tmdb:${result.tmdb_id}`)
  if (result.tvdb_id) ids.add(`tvdb:${result.tvdb_id}`)
  if (result.external_ids) {
    Object.entries(result.external_ids).forEach(([provider, id]) => {
      if (!id) return
      const s = String(id)
      ids.add(provider === 'imdb' ? (s.startsWith('tt') ? s : `tt${s}`) : `${provider}:${s}`)
    })
  }
  return Array.from(ids)
}

// ─── Component ────────────────────────────────────────────────────────────────

export function StreamRelinkButton({
  streamId,
  streamName,
  currentMediaId,
  currentMediaTitle,
  variant = 'button',
  trigger,
  className,
  onSuccess,
}: StreamRelinkButtonProps) {
  const { toast } = useToast()
  const createSuggestion = useCreateStreamSuggestion()

  const [open, setOpen] = useState(false)
  const [existingLinks, setExistingLinks] = useState<MediaLinkInfo[]>([])
  const [isLoadingLinks, setIsLoadingLinks] = useState(false)
  const [linkAction, setLinkAction] = useState<'relink' | 'add'>('add')

  const [selectedMedia, setSelectedMedia] = useState<CombinedSearchResult | null>(null)

  // Episode mapping
  const [fileIndex, setFileIndex] = useState('')
  const [seasonNumber, setSeasonNumber] = useState('')
  const [episodeNumber, setEpisodeNumber] = useState('')
  const [episodeEnd, setEpisodeEnd] = useState('')
  const [reason, setReason] = useState('')

  // ── Existing links ──────────────────────────────────────────────────────

  const loadExistingLinks = useCallback(async () => {
    setIsLoadingLinks(true)
    try {
      const result = await streamLinkingApi.getMediaForStream(streamId)
      setExistingLinks(result.media_entries)
    } catch {
      // non-critical
    } finally {
      setIsLoadingLinks(false)
    }
  }, [streamId])

  useEffect(() => {
    if (open) loadExistingLinks()
    if (!open) {
      setSelectedMedia(null)
      setFileIndex('')
      setSeasonNumber('')
      setEpisodeNumber('')
      setEpisodeEnd('')
      setReason('')
      setLinkAction('add')
      setExistingLinks([])
    }
  }, [open, loadExistingLinks])

  // ── Select via MetadataSearchPopover ────────────────────────────────────

  const handleSelectMedia = useCallback((result: CombinedSearchResult) => {
    setSelectedMedia(result)
    setSeasonNumber('')
    setEpisodeNumber('')
    setEpisodeEnd('')
  }, [])

  // ── Submit suggestion ───────────────────────────────────────────────────

  const handleSubmit = useCallback(async () => {
    const targetMediaId = selectedMedia?.source === 'internal' ? selectedMedia.internal_id : undefined
    const targetExternalId =
      selectedMedia?.source === 'external' ? normalizeExternalId(getBestExternalId(selectedMedia)) : ''

    if (!targetMediaId && !targetExternalId) return

    const parsedFileIndex = parseOptionalInt(fileIndex)
    const parsedSeason = parseOptionalInt(seasonNumber)
    const parsedEpisode = parseOptionalInt(episodeNumber)
    const parsedEpisodeEnd = parseOptionalInt(episodeEnd)

    if (parsedEpisodeEnd !== undefined && parsedEpisode === undefined) {
      toast({
        title: 'Episode number required',
        description: 'Set episode number when episode end is provided.',
        variant: 'destructive',
      })
      return
    }

    try {
      const response = await createSuggestion.mutateAsync({
        streamId,
        data: {
          suggestion_type: linkAction === 'relink' ? 'relink_media' : 'add_media_link',
          target_media_id: targetMediaId,
          target_external_id: targetExternalId || undefined,
          target_media_type: targetExternalId
            ? selectedMedia?.type === 'series'
              ? 'series'
              : selectedMedia?.type === 'tv'
                ? 'tv'
                : 'movie'
            : undefined,
          target_title: !targetMediaId ? (selectedMedia?.title ?? undefined) : undefined,
          file_index: parsedFileIndex,
          season_number: parsedSeason,
          episode_number: parsedEpisode,
          episode_end: parsedEpisodeEnd,
          reason:
            reason ||
            (targetMediaId
              ? `Link stream to "${selectedMedia?.title || `MediaFusion media #${targetMediaId}`}"`
              : `Link stream to external ID "${targetExternalId}"`),
          current_value:
            existingLinks.map((l) => `mf:${l.media_id}${l.title ? ` (${l.title})` : ''}`).join(', ') ||
            (currentMediaId
              ? `mf:${currentMediaId}${currentMediaTitle ? ` (${currentMediaTitle})` : ''}`
              : undefined) ||
            currentMediaTitle ||
            undefined,
          suggested_value: targetMediaId ? selectedMedia?.title || `mf:${targetMediaId}` : targetExternalId,
        },
      })

      toast({
        title: 'Suggestion Submitted',
        description:
          response.status === 'auto_approved'
            ? 'Your change has been auto-approved and applied.'
            : 'Your suggestion has been submitted for moderator review.',
      })

      setOpen(false)
      onSuccess?.()
    } catch (error) {
      toast({
        title: 'Error',
        description: error instanceof Error ? error.message : 'Failed to submit suggestion',
        variant: 'destructive',
      })
    }
  }, [
    streamId,
    selectedMedia,
    linkAction,
    fileIndex,
    seasonNumber,
    episodeNumber,
    episodeEnd,
    reason,
    currentMediaId,
    currentMediaTitle,
    existingLinks,
    createSuggestion,
    toast,
    onSuccess,
  ])

  // ── Derived ─────────────────────────────────────────────────────────────

  const selectedMediaType = selectedMedia?.type?.toLowerCase()
  const isEpisodeMappingTarget = selectedMediaType === 'series' || selectedMediaType === 'tv'
  const canSubmit = Boolean(
    (selectedMedia?.source === 'internal' && selectedMedia.internal_id) ||
    (selectedMedia?.source === 'external' && normalizeExternalId(getBestExternalId(selectedMedia))),
  )

  // ── Trigger ─────────────────────────────────────────────────────────────

  const defaultTrigger = (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          {variant === 'icon' ? (
            <Button variant="ghost" size="icon" className={className} onClick={() => setOpen(true)}>
              <Link2 className="h-4 w-4" />
            </Button>
          ) : (
            <Button variant="outline" size="sm" className={className} onClick={() => setOpen(true)}>
              <Link2 className="h-4 w-4 mr-2" />
              Link to Media
            </Button>
          )}
        </TooltipTrigger>
        <TooltipContent>
          <p>Link this stream to different or additional content</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )

  const triggerElement = trigger ? (
    isValidElement(trigger) ? (
      cloneElement(trigger as ReactElement<Record<string, unknown>>, {
        onClick: (event: unknown) => {
          const onClick = (trigger.props as Record<string, unknown>).onClick
          if (typeof onClick === 'function') onClick(event)
          setOpen(true)
        },
        onSelect: (event: unknown) => {
          const onSelect = (trigger.props as Record<string, unknown>).onSelect
          if (typeof onSelect === 'function') onSelect(event)
          setOpen(true)
        },
      })
    ) : (
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setOpen(true)
          }
        }}
      >
        {trigger}
      </div>
    )
  ) : (
    defaultTrigger
  )

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <>
      {triggerElement}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          scrollMode="contained"
          className="sm:max-w-[550px] max-h-[85vh] flex flex-col overflow-hidden min-h-0"
        >
          <DialogHeader className="shrink-0">
            <DialogTitle className="flex items-center gap-2">
              <Link2 className="h-5 w-5 text-primary" />
              Link Stream to Media
            </DialogTitle>
            <DialogDescription>
              Suggest a link change for this stream. Your suggestion will be reviewed.
            </DialogDescription>
          </DialogHeader>

          <ScrollArea className="flex-1 min-h-0 pr-1">
            <div className="space-y-4 py-1">
              {/* Stream info */}
              <div className="p-3 rounded-lg bg-muted/30 border border-border/50">
                <div className="flex items-start gap-3">
                  <div className="p-2 rounded bg-primary/10">
                    <HardDrive className="h-5 w-5 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h4 className="font-medium text-sm truncate">{streamName || `Stream #${streamId}`}</h4>
                    {currentMediaTitle && (
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Currently linked to: <span className="font-medium">{currentMediaTitle}</span>
                      </p>
                    )}
                  </div>
                </div>
              </div>

              <Separator />

              {/* Existing links */}
              <div className="space-y-2">
                <Label className="text-sm text-muted-foreground">Current Links</Label>
                {isLoadingLinks ? (
                  <div className="flex items-center justify-center py-4">
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  </div>
                ) : existingLinks.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-4 text-center">
                    <AlertCircle className="h-6 w-6 text-muted-foreground/50 mb-1" />
                    <p className="text-xs text-muted-foreground">No metadata linked yet</p>
                  </div>
                ) : (
                  <ScrollArea className="max-h-[120px]">
                    <div className="space-y-1.5">
                      {existingLinks.map((link) => (
                        <div
                          key={link.link_id}
                          className="flex items-center gap-2 p-2 rounded-lg border border-border/50 bg-background/50"
                        >
                          <div className="p-1 rounded bg-muted">
                            {link.type === 'series' ? (
                              <Tv className="h-3.5 w-3.5 text-green-500" />
                            ) : (
                              <Film className="h-3.5 w-3.5 text-blue-500" />
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-medium truncate">{link.title}</p>
                            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                              {link.year && <span>{link.year}</span>}
                              <span className="font-mono">{link.external_id}</span>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                )}
              </div>

              <Separator />

              {/* Action type */}
              <div className="space-y-2">
                <Label className="text-sm text-muted-foreground">Action</Label>
                <RadioGroup
                  value={linkAction}
                  onValueChange={(v) => setLinkAction(v as 'relink' | 'add')}
                  className="grid grid-cols-2 gap-2"
                >
                  <div className="flex items-center space-x-2 p-2 rounded-lg border border-border/50 hover:bg-muted/30 cursor-pointer">
                    <RadioGroupItem value="add" id="add" />
                    <Label htmlFor="add" className="text-sm cursor-pointer flex-1">
                      <span className="font-medium">Add Link</span>
                      <p className="text-[10px] text-muted-foreground">Keep existing links, add new one</p>
                    </Label>
                  </div>
                  <div className="flex items-center space-x-2 p-2 rounded-lg border border-border/50 hover:bg-muted/30 cursor-pointer">
                    <RadioGroupItem value="relink" id="relink" />
                    <Label htmlFor="relink" className="text-sm cursor-pointer flex-1">
                      <span className="font-medium">Replace Link</span>
                      <p className="text-[10px] text-muted-foreground">Remove existing, link to new</p>
                    </Label>
                  </div>
                </RadioGroup>
              </div>

              {/* Target media — unified search + manual ID via MetadataSearchPopover */}
              <div className="space-y-2">
                <Label className="text-sm text-muted-foreground">Target Media</Label>

                {selectedMedia ? (
                  <div className="flex items-center gap-2 p-2 rounded-lg border border-primary/30 bg-primary/5">
                    {selectedMedia.poster ? (
                      <img src={selectedMedia.poster} alt="" className="w-10 h-14 rounded object-cover" />
                    ) : (
                      <div className="w-10 h-14 rounded bg-muted flex items-center justify-center">
                        {selectedMedia.type === 'series' ? (
                          <Tv className="h-4 w-4 text-muted-foreground" />
                        ) : (
                          <Film className="h-4 w-4 text-muted-foreground" />
                        )}
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{selectedMedia.title}</p>
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground flex-wrap">
                        {selectedMedia.year && <span>{selectedMedia.year}</span>}
                        <Badge variant="outline" className="text-[10px] px-1 py-0">
                          {selectedMedia.type}
                        </Badge>
                        {selectedMedia.source === 'internal' ? (
                          <Badge variant="secondary" className="text-[10px] px-1 py-0 bg-green-500/20 text-green-700">
                            In Library
                          </Badge>
                        ) : (
                          <Badge variant="secondary" className="text-[10px] px-1 py-0 bg-yellow-500/20 text-yellow-700">
                            {selectedMedia.provider?.toUpperCase() ?? 'External'}
                          </Badge>
                        )}
                        {getResultExternalIds(selectedMedia)
                          .slice(0, 2)
                          .map((id) => (
                            <span key={`${selectedMedia.id}-${id}`} className="font-mono text-[10px]">
                              {id}
                            </span>
                          ))}
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-foreground"
                      onClick={() => setSelectedMedia(null)}
                    >
                      <Unlink className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ) : (
                  // MetadataSearchPopover: handles title search + manual IMDB/TMDB/TVDB/MAL/Kitsu ID
                  // entry via /metadata/search/matches — returns real poster/title/year, not placeholders.
                  // requireInternal=false because suggestion flow accepts external IDs too (resolved at approval).
                  <MetadataSearchPopover
                    metaType="all"
                    requireInternal={false}
                    onSelect={handleSelectMedia}
                    onClear={() => setSelectedMedia(null)}
                    placeholder="Search or enter an ID (IMDB / TMDB / TVDB…)"
                    popoverWidth="w-[calc(100vw-2rem)] sm:w-[420px]"
                  />
                )}
              </div>

              {/* File index */}
              {selectedMedia && (
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">
                    File Index <span className="text-[10px]">(optional — for multi-file torrents)</span>
                  </Label>
                  <Input
                    type="number"
                    min={0}
                    value={fileIndex}
                    onChange={(e) => setFileIndex(e.target.value)}
                    placeholder="Leave empty for whole stream"
                    className="h-8 text-sm"
                  />
                </div>
              )}

              {/* Episode mapping */}
              {selectedMedia && isEpisodeMappingTarget && (
                <div className="space-y-1.5 rounded-lg border border-border/50 p-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs text-muted-foreground">
                      Episode Mapping <span className="text-[10px]">(optional)</span>
                    </Label>
                    {(seasonNumber || episodeNumber || episodeEnd) && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-5 text-[10px] px-1.5 text-muted-foreground hover:text-foreground"
                        onClick={() => {
                          setSeasonNumber('')
                          setEpisodeNumber('')
                          setEpisodeEnd('')
                        }}
                      >
                        Clear
                      </Button>
                    )}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <Input
                      type="number"
                      min={0}
                      value={seasonNumber}
                      onChange={(e) => setSeasonNumber(e.target.value)}
                      placeholder="Season"
                      className="h-8 text-sm"
                    />
                    <Input
                      type="number"
                      min={0}
                      value={episodeNumber}
                      onChange={(e) => setEpisodeNumber(e.target.value)}
                      placeholder="Episode"
                      className="h-8 text-sm"
                    />
                    <Input
                      type="number"
                      min={0}
                      value={episodeEnd}
                      onChange={(e) => setEpisodeEnd(e.target.value)}
                      placeholder="Episode end"
                      className="h-8 text-sm"
                    />
                  </div>
                </div>
              )}

              {/* Reason */}
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Reason (optional)</Label>
                <Textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Why should this stream be linked to different media?"
                  className="h-16 resize-none text-sm"
                />
              </div>
            </div>
          </ScrollArea>

          <DialogFooter className="shrink-0">
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={!canSubmit || createSuggestion.isPending}
              className="bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
            >
              {createSuggestion.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Submitting...
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-4 w-4 mr-2" />
                  Submit Suggestion
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
