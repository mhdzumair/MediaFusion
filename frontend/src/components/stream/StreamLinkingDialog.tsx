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
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Loader2, Link2, Unlink, Search, Film, Tv, Plus, Trash2, AlertCircle, HardDrive } from 'lucide-react'
import { useCombinedMetadataSearch, type CombinedSearchResult } from '@/hooks'
import { useDebounce } from '@/hooks/useDebounce'
import { useToast } from '@/hooks/use-toast'
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

interface StreamInfo {
  stream_id: number
  stream_name: string
  type: string
  size: number | null
  info_hash?: string
}

interface StreamLinkingDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stream: StreamInfo | null
  onSuccess?: () => void
}

// API functions for stream linking
const streamLinkingApi = {
  getMediaForStream: async (streamId: number): Promise<{ stream_id: number; media_entries: MediaLinkInfo[] }> => {
    return apiClient.get(`/stream-links/stream/${streamId}`)
  },

  createLink: async (data: {
    stream_id: number
    media_id: number
    file_index?: number | null
    season?: number | null
    episode?: number | null
  }) => {
    return apiClient.post('/stream-links', data)
  },

  deleteLink: async (linkId: number): Promise<void> => {
    return apiClient.delete(`/stream-links/${linkId}`)
  },
}

export function StreamLinkingDialog({ open, onOpenChange, stream, onSuccess }: StreamLinkingDialogProps) {
  const { toast } = useToast()

  // State
  const [existingLinks, setExistingLinks] = useState<MediaLinkInfo[]>([])
  const [isLoadingLinks, setIsLoadingLinks] = useState(false)
  const [isCreatingLink, setIsCreatingLink] = useState(false)
  const [deletingLinkId, setDeletingLinkId] = useState<number | null>(null)

  // New link state
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedMedia, setSelectedMedia] = useState<CombinedSearchResult | null>(null)
  const [fileIndex, setFileIndex] = useState<string>('')
  const [season, setSeason] = useState<string>('')
  const [episode, setEpisode] = useState<string>('')

  const debouncedQuery = useDebounce(searchQuery, 300)

  // Use combined search but only allow linking to internal results (they have media_id)
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
    if (!stream) return

    setIsLoadingLinks(true)
    try {
      const result = await streamLinkingApi.getMediaForStream(stream.stream_id)
      setExistingLinks(result.media_entries)
    } catch (error) {
      console.error('Failed to load existing links:', error)
      toast({
        title: 'Error',
        description: 'Failed to load existing links',
        variant: 'destructive',
      })
    } finally {
      setIsLoadingLinks(false)
    }
  }, [stream, toast])

  // Load links when dialog opens
  useEffect(() => {
    if (open && stream) {
      loadExistingLinks()
    }
    if (!open) {
      // Reset state when closing
      setSearchQuery('')
      setSelectedMedia(null)
      setFileIndex('')
      setSeason('')
      setEpisode('')
      setExistingLinks([])
    }
  }, [open, stream, loadExistingLinks])

  // Handle media selection - only allow internal results (they have media_id)
  const handleSelectMedia = useCallback((result: CombinedSearchResult) => {
    if (result.source !== 'internal' || !result.internal_id) {
      // External results can't be linked directly - they don't have a media_id
      return
    }
    setSelectedMedia(result)
    setSearchOpen(false)
    setSearchQuery('')
  }, [])

  // Create new link
  const handleCreateLink = useCallback(async () => {
    if (!stream || !selectedMedia || !selectedMedia.internal_id) return

    setIsCreatingLink(true)
    try {
      await streamLinkingApi.createLink({
        stream_id: stream.stream_id,
        media_id: selectedMedia.internal_id,
        file_index: fileIndex ? parseInt(fileIndex) : null,
        season: season ? parseInt(season) : null,
        episode: episode ? parseInt(episode) : null,
      })

      toast({
        title: 'Link Created',
        description: `Stream linked to "${selectedMedia.title}"`,
      })

      // Reset form and reload links
      setSelectedMedia(null)
      setFileIndex('')
      setSeason('')
      setEpisode('')
      await loadExistingLinks()
      onSuccess?.()
    } catch (error) {
      toast({
        title: 'Error',
        description: error instanceof Error ? error.message : 'Failed to create link',
        variant: 'destructive',
      })
    } finally {
      setIsCreatingLink(false)
    }
  }, [stream, selectedMedia, fileIndex, season, episode, toast, loadExistingLinks, onSuccess])

  // Delete link
  const handleDeleteLink = useCallback(
    async (linkId: number) => {
      setDeletingLinkId(linkId)
      try {
        await streamLinkingApi.deleteLink(linkId)

        toast({
          title: 'Link Removed',
          description: 'Stream link has been removed',
        })

        await loadExistingLinks()
        onSuccess?.()
      } catch (error) {
        toast({
          title: 'Error',
          description: error instanceof Error ? error.message : 'Failed to remove link',
          variant: 'destructive',
        })
      } finally {
        setDeletingLinkId(null)
      }
    },
    [toast, loadExistingLinks, onSuccess],
  )

  // Format size
  const formatSize = (bytes: number | null): string => {
    if (!bytes) return ''
    const units = ['B', 'KB', 'MB', 'GB', 'TB']
    let unitIndex = 0
    let size = bytes
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024
      unitIndex++
    }
    return `${size.toFixed(1)} ${units[unitIndex]}`
  }

  if (!stream) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[600px] max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-primary" />
            Link Stream to Metadata
          </DialogTitle>
          <DialogDescription>Manage metadata links for this stream</DialogDescription>
        </DialogHeader>

        {/* Stream Info */}
        <div className="p-3 rounded-lg bg-muted/30 border border-border/50">
          <div className="flex items-start gap-3">
            <div className="p-2 rounded bg-primary/10">
              <HardDrive className="h-5 w-5 text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <h4 className="font-medium text-sm truncate">{stream.stream_name}</h4>
              <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
                <Badge variant="outline" className="text-[10px]">
                  {stream.type}
                </Badge>
                {stream.size && <span>{formatSize(stream.size)}</span>}
                {stream.info_hash && <span className="font-mono truncate max-w-[120px]">{stream.info_hash}</span>}
              </div>
            </div>
          </div>
        </div>

        <Separator />

        {/* Existing Links */}
        <div className="space-y-2">
          <Label className="text-sm text-muted-foreground">Current Links</Label>

          {isLoadingLinks ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : existingLinks.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-6 text-center">
              <AlertCircle className="h-8 w-8 text-muted-foreground/50 mb-2" />
              <p className="text-sm text-muted-foreground">No metadata linked yet</p>
            </div>
          ) : (
            <ScrollArea className="max-h-[200px]">
              <div className="space-y-2">
                {existingLinks.map((link) => (
                  <div
                    key={link.link_id}
                    className="flex items-center gap-3 p-2 rounded-lg border border-border/50 bg-background/50 group"
                  >
                    <div className="p-1.5 rounded bg-muted">
                      {link.type === 'series' ? (
                        <Tv className="h-4 w-4 text-green-500" />
                      ) : (
                        <Film className="h-4 w-4 text-blue-500" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{link.title}</p>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        {link.year && <span>{link.year}</span>}
                        <span className="font-mono">{link.external_id}</span>
                        {link.season !== null && <span>S{link.season}</span>}
                        {link.episode !== null && <span>E{link.episode}</span>}
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 opacity-0 group-hover:opacity-100 text-red-500 hover:text-red-600 hover:bg-red-500/10"
                      onClick={() => handleDeleteLink(link.link_id)}
                      disabled={deletingLinkId === link.link_id}
                    >
                      {deletingLinkId === link.link_id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </Button>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}
        </div>

        <Separator />

        {/* Add New Link */}
        <div className="space-y-3">
          <Label className="text-sm text-muted-foreground">Add New Link</Label>

          {/* Metadata Search */}
          <div className="space-y-2">
            <Label className="text-xs">Search Metadata</Label>
            {selectedMedia ? (
              <div className="flex items-center gap-2 p-2 rounded-lg border border-primary/30 bg-primary/5">
                {selectedMedia.poster ? (
                  <img src={selectedMedia.poster} alt="" className="w-8 h-12 rounded object-cover" />
                ) : (
                  <div className="w-8 h-12 rounded bg-muted flex items-center justify-center">
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
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setSelectedMedia(null)}>
                  <Unlink className="h-3.5 w-3.5" />
                </Button>
              </div>
            ) : (
              <Popover open={searchOpen} onOpenChange={setSearchOpen}>
                <PopoverTrigger asChild>
                  <Button variant="outline" className="w-full justify-start text-muted-foreground">
                    <Search className="h-4 w-4 mr-2" />
                    Search for metadata...
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[400px] p-0" align="start">
                  <div className="p-2 border-b">
                    <Input
                      placeholder="Search movies, series, or user metadata..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="h-9"
                      autoFocus
                    />
                  </div>
                  <ScrollArea className="max-h-[300px]">
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
                        {/* Show loading indicator at top if still fetching more */}
                        {isFetchingSearch && (
                          <div className="flex items-center justify-center py-2 text-xs text-muted-foreground gap-1.5">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            <span>Loading more...</span>
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
                                      {result.provider?.toUpperCase() || 'External'}
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

          {/* Optional fields */}
          {selectedMedia && (
            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">File Index</Label>
                <Input
                  type="number"
                  min={0}
                  value={fileIndex}
                  onChange={(e) => setFileIndex(e.target.value)}
                  placeholder="Optional"
                  className="h-8 text-sm"
                />
              </div>
              {selectedMedia.type === 'series' && (
                <>
                  <div className="space-y-1">
                    <Label className="text-xs">Season</Label>
                    <Input
                      type="number"
                      min={0}
                      value={season}
                      onChange={(e) => setSeason(e.target.value)}
                      placeholder="S"
                      className="h-8 text-sm"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">Episode</Label>
                    <Input
                      type="number"
                      min={0}
                      value={episode}
                      onChange={(e) => setEpisode(e.target.value)}
                      placeholder="E"
                      className="h-8 text-sm"
                    />
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button
            onClick={handleCreateLink}
            disabled={!selectedMedia || isCreatingLink}
            className="bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
          >
            {isCreatingLink ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Plus className="h-4 w-4 mr-2" />}
            Add Link
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
