import { useState, useCallback, useEffect } from 'react'
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
import { useCombinedMetadataSearch, type CombinedSearchResult } from '@/hooks'
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
  className?: string
}

// API to get existing links
const streamLinkingApi = {
  getMediaForStream: async (streamId: number): Promise<{ stream_id: number; media_entries: MediaLinkInfo[] }> => {
    return apiClient.get(`/stream-links/stream/${streamId}`)
  },
}

export function StreamRelinkButton({
  streamId,
  streamName,
  currentMediaId: _currentMediaId,
  currentMediaTitle,
  variant = 'button',
  className,
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
  const [selectedMedia, setSelectedMedia] = useState<CombinedSearchResult | null>(null)
  const [fileIndex, setFileIndex] = useState<string>('')
  const [reason, setReason] = useState('')

  const debouncedQuery = useDebounce(searchQuery, 300)

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
      setFileIndex('')
      setReason('')
      setLinkAction('add')
      setExistingLinks([])
    }
  }, [open, loadExistingLinks])

  // Handle media selection - only allow internal results (they have media_id)
  const handleSelectMedia = useCallback(
    (result: CombinedSearchResult) => {
      if (result.source !== 'internal' || !result.internal_id) {
        toast({
          title: 'Cannot link to external metadata',
          description: 'External results must be imported first. Go to Content Import to add this metadata.',
          variant: 'destructive',
        })
        return
      }
      setSelectedMedia(result)
      setSearchOpen(false)
      setSearchQuery('')
    },
    [toast],
  )

  // Submit suggestion
  const handleSubmit = useCallback(async () => {
    if (!selectedMedia || !selectedMedia.internal_id) return

    try {
      await createSuggestion.mutateAsync({
        streamId,
        data: {
          suggestion_type: linkAction === 'relink' ? 'relink_media' : 'add_media_link',
          target_media_id: selectedMedia.internal_id,
          file_index: fileIndex ? parseInt(fileIndex) : undefined,
          reason: reason || `Link stream to "${selectedMedia.title}"`,
          current_value: currentMediaTitle || existingLinks.map((l) => l.title).join(', ') || undefined,
          suggested_value: selectedMedia.title,
        },
      })

      toast({
        title: 'Suggestion Submitted',
        description:
          createSuggestion.data?.status === 'auto_approved'
            ? 'Your change has been auto-approved and applied.'
            : 'Your suggestion has been submitted for moderator review.',
      })

      setOpen(false)
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
    reason,
    currentMediaTitle,
    existingLinks,
    createSuggestion,
    toast,
  ])

  return (
    <>
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

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-[550px] max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Link2 className="h-5 w-5 text-primary" />
              Link Stream to Media
            </DialogTitle>
            <DialogDescription>
              Suggest a link change for this stream. Your suggestion will be reviewed.
            </DialogDescription>
          </DialogHeader>

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
                <PopoverContent className="w-[400px] p-0" align="start">
                  <div className="p-2 border-b">
                    <Input
                      placeholder="Search movies, series..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="h-9"
                      autoFocus
                    />
                  </div>
                  <ScrollArea className="max-h-[250px]">
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
                          const isExternal = result.source === 'external'
                          return (
                            <button
                              key={result.id}
                              onClick={() => handleSelectMedia(result)}
                              disabled={isExternal}
                              className={`w-full flex items-center gap-2 p-2 rounded-md text-left ${
                                isExternal ? 'opacity-50 cursor-not-allowed' : 'hover:bg-muted cursor-pointer'
                              }`}
                              title={isExternal ? 'External results must be imported first' : undefined}
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
                                      External
                                    </Badge>
                                  )}
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

          {/* Optional file index for multi-file torrents */}
          {selectedMedia && (
            <div className="space-y-2">
              <Label className="text-xs text-muted-foreground">File Index (optional, for multi-file torrents)</Label>
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

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={!selectedMedia || createSuggestion.isPending}
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
