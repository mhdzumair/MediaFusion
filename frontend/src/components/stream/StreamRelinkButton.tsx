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
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Loader2, Link2, Unlink, Search, Film, Tv, AlertCircle, HardDrive, CheckCircle2 } from 'lucide-react'
import { getBestExternalId, useCombinedMetadataSearch, type CombinedSearchResult } from '@/hooks'
import { useDebounce } from '@/hooks/useDebounce'
import { useToast } from '@/hooks/use-toast'
import { useCreateStreamSuggestion } from '@/hooks/useStreamSuggestions'
import { apiClient } from '@/lib/api/client'

// Types for stream linking
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

type ManualIdMode = 'external' | 'mediafusion'

// API to get existing links
const streamLinkingApi = {
  getMediaForStream: async (streamId: number): Promise<{ stream_id: number; media_entries: MediaLinkInfo[] }> => {
    return apiClient.get(`/stream-links/stream/${streamId}`)
  },
}

function normalizeExternalIdInput(rawValue: string): string {
  const value = rawValue.trim()
  if (!value) return ''
  if (/^\d+$/.test(value)) return `tmdb:${value}`
  return value
}

function parseMediaFusionMediaIdInput(rawValue: string): number | null {
  const value = rawValue.trim()
  if (!value) return null

  const match = value.match(/^(?:mf:|mediafusion:)?(\d+)$/i)
  if (!match) return null

  const parsed = Number.parseInt(match[1], 10)
  if (!Number.isFinite(parsed) || parsed <= 0) return null
  return parsed
}

function parseOptionalInteger(rawValue: string): number | undefined {
  const value = rawValue.trim()
  if (!value) return undefined
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? parsed : undefined
}

function getResultExternalIds(result: CombinedSearchResult): string[] {
  const ids = new Set<string>()
  if (result.external_id) ids.add(result.external_id)
  if (result.imdb_id) ids.add(result.imdb_id)
  if (result.tmdb_id) ids.add(`tmdb:${result.tmdb_id}`)
  if (result.tvdb_id) ids.add(`tvdb:${result.tvdb_id}`)

  if (result.external_ids) {
    Object.entries(result.external_ids).forEach(([provider, providerId]) => {
      if (!providerId) return
      const normalizedId = String(providerId)
      if (provider === 'imdb') {
        ids.add(normalizedId.startsWith('tt') ? normalizedId : `tt${normalizedId}`)
        return
      }
      ids.add(`${provider}:${normalizedId}`)
    })
  }

  return Array.from(ids)
}

export function StreamRelinkButton({
  streamId,
  streamName,
  currentMediaTitle,
  variant = 'button',
  trigger,
  className,
  onSuccess,
}: StreamRelinkButtonProps) {
  const { toast } = useToast()
  const createSuggestion = useCreateStreamSuggestion()

  // Dialog state
  const [open, setOpen] = useState(false)

  // Existing links state
  const [existingLinks, setExistingLinks] = useState<MediaLinkInfo[]>([])
  const [isLoadingLinks, setIsLoadingLinks] = useState(false)

  // Link action type
  const [linkAction, setLinkAction] = useState<'relink' | 'add'>('add')

  // Search state
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchYear, setSearchYear] = useState('')
  const [selectedMedia, setSelectedMedia] = useState<CombinedSearchResult | null>(null)
  const [manualIdMode, setManualIdMode] = useState<ManualIdMode>('external')
  const [manualExternalId, setManualExternalId] = useState('')
  const [manualMediaType, setManualMediaType] = useState<'movie' | 'series' | 'tv'>('movie')
  const [fileIndex, setFileIndex] = useState<string>('')
  const [seasonNumber, setSeasonNumber] = useState<string>('')
  const [episodeNumber, setEpisodeNumber] = useState<string>('')
  const [episodeEnd, setEpisodeEnd] = useState<string>('')
  const [reason, setReason] = useState('')

  const debouncedQuery = useDebounce(searchQuery, 300)
  const trimmedSearchYear = searchYear.trim()
  const parsedSearchYear = trimmedSearchYear ? Number(trimmedSearchYear) : undefined
  const validSearchYear = Number.isFinite(parsedSearchYear) ? parsedSearchYear : undefined

  // Use combined search
  const {
    data: searchResults = [],
    isLoading: isSearching,
    isFetching: isFetchingSearch,
  } = useCombinedMetadataSearch(
    {
      query: debouncedQuery,
      type: 'all',
      limit: 20,
      year: validSearchYear,
    },
    { enabled: debouncedQuery.length >= 2 && open },
  )

  // Load existing links when dialog opens
  const loadExistingLinks = useCallback(async () => {
    setIsLoadingLinks(true)
    try {
      const result = await streamLinkingApi.getMediaForStream(streamId)
      setExistingLinks(result.media_entries)
    } catch (error) {
      console.error('Failed to load existing links:', error)
    } finally {
      setIsLoadingLinks(false)
    }
  }, [streamId])

  // Load links when dialog opens
  useEffect(() => {
    if (open) {
      loadExistingLinks()
    }
    if (!open) {
      // Reset state when closing
      setSearchQuery('')
      setSelectedMedia(null)
      setManualIdMode('external')
      setManualExternalId('')
      setManualMediaType('movie')
      setFileIndex('')
      setSeasonNumber('')
      setEpisodeNumber('')
      setEpisodeEnd('')
      setReason('')
      setSearchYear('')
      setLinkAction('add')
      setExistingLinks([])
    }
  }, [open, loadExistingLinks])

  const handleSelectMedia = useCallback((result: CombinedSearchResult) => {
    setSelectedMedia(result)
    if (result.source === 'external') {
      const bestExternalId = normalizeExternalIdInput(getBestExternalId(result))
      if (bestExternalId) {
        setManualExternalId(bestExternalId)
      }
      setManualMediaType(result.type === 'series' ? 'series' : result.type === 'tv' ? 'tv' : 'movie')
    } else {
      setManualExternalId('')
    }

    setSearchOpen(false)
    setSearchQuery('')
    setSearchYear('')
    setSeasonNumber('')
    setEpisodeNumber('')
    setEpisodeEnd('')
  }, [])

  const handleSelectManualExternalId = useCallback(() => {
    const normalizedExternalId = normalizeExternalIdInput(manualExternalId)
    const mediaFusionMediaId = parseMediaFusionMediaIdInput(manualExternalId)

    if (manualIdMode === 'mediafusion') {
      if (!mediaFusionMediaId) {
        toast({
          title: 'Media ID required',
          description: 'Enter a valid MediaFusion media ID like 123 or mf:123.',
          variant: 'destructive',
        })
        return
      }

      setManualExternalId(String(mediaFusionMediaId))
      setSelectedMedia({
        id: `manual-mf-${mediaFusionMediaId}`,
        title: `MediaFusion media #${mediaFusionMediaId}`,
        type: manualMediaType,
        source: 'internal',
        internal_id: mediaFusionMediaId,
        external_id: `mf:${mediaFusionMediaId}`,
      })
      return
    }

    if (!normalizedExternalId) {
      toast({
        title: 'External ID required',
        description: 'Enter a valid external ID like tt1234567 or tmdb:550.',
        variant: 'destructive',
      })
      return
    }

    setManualExternalId(normalizedExternalId)
    setSelectedMedia({
      id: `manual-${normalizedExternalId}`,
      title: `Manual ${manualMediaType} (${normalizedExternalId})`,
      type: manualMediaType,
      source: 'external',
      external_id: normalizedExternalId,
      provider: normalizedExternalId.startsWith('tt') ? 'imdb' : normalizedExternalId.split(':', 1)[0] || 'external',
    })
  }, [manualExternalId, manualIdMode, manualMediaType, toast])

  // Submit suggestion
  const handleSubmit = useCallback(async () => {
    const normalizedManualExternalId = normalizeExternalIdInput(manualExternalId)
    const manualMediaFusionId = parseMediaFusionMediaIdInput(manualExternalId)
    const parsedFileIndex = parseOptionalInteger(fileIndex)
    const parsedSeasonNumber = parseOptionalInteger(seasonNumber)
    const parsedEpisodeNumber = parseOptionalInteger(episodeNumber)
    const parsedEpisodeEnd = parseOptionalInteger(episodeEnd)
    const selectedExternalId =
      selectedMedia?.source === 'external' ? normalizeExternalIdInput(getBestExternalId(selectedMedia)) : ''
    const isInternalSelection = selectedMedia?.source === 'internal' && !!selectedMedia.internal_id
    const isManualExternalSelection = selectedMedia?.source === 'external' && selectedMedia.id.startsWith('manual-')
    const hasEpisodeMapping =
      parsedSeasonNumber !== undefined || parsedEpisodeNumber !== undefined || parsedEpisodeEnd !== undefined
    const targetMediaId =
      (isInternalSelection ? selectedMedia.internal_id : undefined) ||
      (manualIdMode === 'mediafusion' ? (manualMediaFusionId ?? undefined) : undefined)
    const targetExternalId = targetMediaId
      ? ''
      : manualIdMode === 'external'
        ? normalizedManualExternalId || selectedExternalId
        : selectedExternalId

    if (!targetMediaId && !targetExternalId) return
    if (hasEpisodeMapping && parsedFileIndex === undefined) {
      toast({
        title: 'File index required',
        description: 'Set a file index when adding season/episode mapping.',
        variant: 'destructive',
      })
      return
    }
    if (parsedEpisodeEnd !== undefined && parsedEpisodeNumber === undefined) {
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
                : manualMediaType
            : undefined,
          target_title: !targetMediaId && !isManualExternalSelection ? (selectedMedia?.title ?? undefined) : undefined,
          file_index: parsedFileIndex,
          season_number: parsedSeasonNumber,
          episode_number: parsedEpisodeNumber,
          episode_end: parsedEpisodeEnd,
          reason:
            reason ||
            (targetMediaId
              ? `Link stream to "${selectedMedia?.title || `MediaFusion media #${targetMediaId}`}"`
              : `Link stream to external ID "${targetExternalId}"`),
          current_value: currentMediaTitle || existingLinks.map((l) => l.title).join(', ') || undefined,
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
    manualIdMode,
    manualExternalId,
    manualMediaType,
    linkAction,
    fileIndex,
    seasonNumber,
    episodeNumber,
    episodeEnd,
    reason,
    currentMediaTitle,
    existingLinks,
    createSuggestion,
    toast,
    onSuccess,
  ])

  const normalizedManualExternalId = normalizeExternalIdInput(manualExternalId)
  const manualMediaFusionId = parseMediaFusionMediaIdInput(manualExternalId)
  const selectedMediaType = selectedMedia?.type?.toLowerCase()
  const isEpisodeMappingSupportedTarget = selectedMediaType === 'series' || selectedMediaType === 'tv'
  const canSubmit = Boolean(
    (selectedMedia?.source === 'internal' && selectedMedia.internal_id) ||
    (selectedMedia?.source === 'external' && normalizeExternalIdInput(getBestExternalId(selectedMedia))) ||
    (manualIdMode === 'external' && normalizedManualExternalId) ||
    (manualIdMode === 'mediafusion' && manualMediaFusionId),
  )

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
          if (typeof onClick === 'function') {
            onClick(event)
          }
          setOpen(true)
        },
        onSelect: (event: unknown) => {
          const onSelect = (trigger.props as Record<string, unknown>).onSelect
          if (typeof onSelect === 'function') {
            onSelect(event)
          }
          setOpen(true)
        },
      })
    ) : (
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
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
              {/* Stream Info */}
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

              {/* Existing Links */}
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

              {/* Link Action Type */}
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

              {/* Target Media Selection */}
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
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
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
                            {selectedMedia.provider?.toUpperCase() || 'External'}
                          </Badge>
                        )}
                        {getResultExternalIds(selectedMedia)
                          .slice(0, 2)
                          .map((externalId) => (
                            <span key={`${selectedMedia.id}-${externalId}`} className="font-mono text-[10px]">
                              {externalId}
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
                  <Popover open={searchOpen} onOpenChange={setSearchOpen}>
                    <PopoverTrigger asChild>
                      <Button variant="outline" className="w-full justify-start text-muted-foreground">
                        <Search className="h-4 w-4 mr-2" />
                        Search for media...
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent
                      className="w-[calc(100vw-2rem)] sm:w-[400px] p-0 overflow-hidden flex flex-col"
                      align="start"
                      style={{
                        height: '360px',
                        maxHeight: 'calc(var(--radix-popover-content-available-height) - 10px)',
                      }}
                    >
                      <div className="p-2 border-b shrink-0">
                        <div className="flex gap-2">
                          <Input
                            placeholder="Search movies, series..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            className="h-9"
                            autoFocus
                          />
                          <Input
                            type="number"
                            inputMode="numeric"
                            min={1878}
                            max={9999}
                            step={1}
                            placeholder="Year"
                            value={searchYear}
                            onChange={(e) => setSearchYear(e.target.value)}
                            className="h-9 w-24 shrink-0"
                          />
                        </div>
                      </div>
                      <ScrollArea className="flex-1 min-h-0">
                        {isSearching && searchResults.length === 0 && (
                          <div className="flex items-center justify-center py-6">
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                          </div>
                        )}
                        {!isSearching && !isFetchingSearch && searchQuery.length >= 2 && searchResults.length === 0 && (
                          <div className="py-6 text-center text-sm text-muted-foreground">No results found</div>
                        )}
                        {!isSearching && searchQuery.length < 2 && (
                          <div className="py-6 text-center text-xs text-muted-foreground">
                            Type at least 2 characters to search
                          </div>
                        )}
                        {searchResults.length > 0 && (
                          <div className="p-1">
                            {isFetchingSearch && (
                              <div className="flex items-center justify-center py-2 text-xs text-muted-foreground gap-1.5">
                                <Loader2 className="h-3 w-3 animate-spin" />
                                <span>Loading...</span>
                              </div>
                            )}
                            {searchResults.map((result) => {
                              return (
                                <button
                                  key={result.id}
                                  onClick={() => handleSelectMedia(result)}
                                  className="w-full flex items-center gap-2 p-2 rounded-md text-left hover:bg-muted cursor-pointer"
                                >
                                  {result.poster ? (
                                    <img
                                      src={result.poster}
                                      alt=""
                                      className="w-8 h-12 rounded object-cover flex-shrink-0"
                                    />
                                  ) : (
                                    <div className="w-8 h-12 rounded bg-muted flex items-center justify-center flex-shrink-0">
                                      {result.type === 'series' ? (
                                        <Tv className="h-4 w-4 text-muted-foreground" />
                                      ) : (
                                        <Film className="h-4 w-4 text-muted-foreground" />
                                      )}
                                    </div>
                                  )}
                                  <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium truncate">{result.title}</p>
                                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                      {result.year && <span>{result.year}</span>}
                                      <Badge variant="outline" className="text-[10px] px-1 py-0">
                                        {result.type}
                                      </Badge>
                                      {result.source === 'internal' ? (
                                        <Badge
                                          variant="secondary"
                                          className="text-[10px] px-1 py-0 bg-green-500/20 text-green-700"
                                        >
                                          In Library
                                        </Badge>
                                      ) : (
                                        <Badge
                                          variant="secondary"
                                          className="text-[10px] px-1 py-0 bg-yellow-500/20 text-yellow-700"
                                        >
                                          {result.provider?.toUpperCase() || 'External'}
                                        </Badge>
                                      )}
                                      {getResultExternalIds(result)
                                        .slice(0, 2)
                                        .map((externalId) => (
                                          <span key={`${result.id}-${externalId}`} className="font-mono text-[10px]">
                                            {externalId}
                                          </span>
                                        ))}
                                    </div>
                                  </div>
                                </button>
                              )
                            })}
                          </div>
                        )}
                      </ScrollArea>
                    </PopoverContent>
                  </Popover>
                )}
              </div>

              {/* Manual ID */}
              <div className="space-y-2 p-2 rounded-lg border border-dashed border-border/70 bg-muted/20">
                <Label className="text-xs text-muted-foreground">Manual ID</Label>
                <div className="grid grid-cols-2 gap-2">
                  <div className="flex items-center gap-1 rounded border border-border/50 p-1 col-span-2">
                    <Button
                      type="button"
                      variant={manualIdMode === 'external' ? 'default' : 'ghost'}
                      size="sm"
                      className="h-6 px-2 text-xs flex-1"
                      onClick={() => setManualIdMode('external')}
                    >
                      External ID
                    </Button>
                    <Button
                      type="button"
                      variant={manualIdMode === 'mediafusion' ? 'default' : 'ghost'}
                      size="sm"
                      className="h-6 px-2 text-xs flex-1"
                      onClick={() => setManualIdMode('mediafusion')}
                    >
                      MediaFusion ID
                    </Button>
                  </div>
                  <Input
                    value={manualExternalId}
                    onChange={(e) => setManualExternalId(e.target.value)}
                    placeholder={manualIdMode === 'external' ? 'tt1234567, tmdb:550, tvdb:121361' : '123 or mf:123'}
                    className="h-8 text-sm col-span-2"
                  />
                  <div className="flex items-center gap-1 rounded border border-border/50 p-1">
                    <Button
                      type="button"
                      variant={manualMediaType === 'movie' ? 'default' : 'ghost'}
                      size="sm"
                      className="h-6 px-2 text-xs flex-1"
                      onClick={() => setManualMediaType('movie')}
                    >
                      Movie
                    </Button>
                    <Button
                      type="button"
                      variant={manualMediaType === 'series' ? 'default' : 'ghost'}
                      size="sm"
                      className="h-6 px-2 text-xs flex-1"
                      onClick={() => setManualMediaType('series')}
                    >
                      Series
                    </Button>
                    <Button
                      type="button"
                      variant={manualMediaType === 'tv' ? 'default' : 'ghost'}
                      size="sm"
                      className="h-6 px-2 text-xs flex-1"
                      onClick={() => setManualMediaType('tv')}
                    >
                      TV
                    </Button>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-8"
                    onClick={handleSelectManualExternalId}
                    disabled={manualIdMode === 'external' ? !normalizedManualExternalId : !manualMediaFusionId}
                  >
                    Use ID
                  </Button>
                </div>
                <p className="text-[10px] text-muted-foreground">
                  External IDs create/fetch metadata during approval. MediaFusion IDs link to an existing internal media
                  record directly.
                </p>
              </div>

              {/* Optional file index for multi-file torrents */}
              {selectedMedia && (
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground">
                    File Index (optional, for multi-file torrents)
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

              {/* Optional series episode mapping in the same submission */}
              {selectedMedia && isEpisodeMappingSupportedTarget && (
                <div className="space-y-2 rounded-lg border border-border/50 p-2">
                  <Label className="text-xs text-muted-foreground">
                    Episode Mapping (optional, requires file index)
                  </Label>
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
              <div className="space-y-2">
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
