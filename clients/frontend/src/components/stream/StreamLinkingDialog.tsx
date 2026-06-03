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
import { Loader2, Link2, Unlink, Film, Tv, Plus, Trash2, AlertCircle, HardDrive } from 'lucide-react'
import { type CombinedSearchResult } from '@/hooks'
import { useToast } from '@/hooks/use-toast'
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

// ─── API ──────────────────────────────────────────────────────────────────────

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

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatSize(bytes: number | null): string {
  if (!bytes) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let s = bytes
  while (s >= 1024 && i < units.length - 1) {
    s /= 1024
    i++
  }
  return `${s.toFixed(1)} ${units[i]}`
}

// ─── Dialog ───────────────────────────────────────────────────────────────────

export function StreamLinkingDialog({ open, onOpenChange, stream, onSuccess }: StreamLinkingDialogProps) {
  const { toast } = useToast()

  const [existingLinks, setExistingLinks] = useState<MediaLinkInfo[]>([])
  const [isLoadingLinks, setIsLoadingLinks] = useState(false)
  const [isCreatingLink, setIsCreatingLink] = useState(false)
  const [deletingLinkId, setDeletingLinkId] = useState<number | null>(null)

  // New-link form state
  const [selectedMedia, setSelectedMedia] = useState<CombinedSearchResult | null>(null)
  const [fileIndex, setFileIndex] = useState('')
  const [season, setSeason] = useState('')
  const [episode, setEpisode] = useState('')

  // ── Load existing links ─────────────────────────────────────────────────

  const loadExistingLinks = useCallback(async () => {
    if (!stream) return
    setIsLoadingLinks(true)
    try {
      const result = await streamLinkingApi.getMediaForStream(stream.stream_id)
      setExistingLinks(result.media_entries)
    } catch {
      toast({ title: 'Error', description: 'Failed to load existing links', variant: 'destructive' })
    } finally {
      setIsLoadingLinks(false)
    }
  }, [stream, toast])

  useEffect(() => {
    if (open && stream) {
      loadExistingLinks()
    }
    if (!open) {
      setSelectedMedia(null)
      setFileIndex('')
      setSeason('')
      setEpisode('')
      setExistingLinks([])
    }
  }, [open, stream, loadExistingLinks])

  // ── Select media ────────────────────────────────────────────────────────

  const handleSelectMedia = useCallback(
    (result: CombinedSearchResult) => {
      // Stream links require an internal media_id — external results are already
      // visually disabled in MetadataSearchPopover (requireInternal=true), but
      // guard here as defence-in-depth.
      if (!result.internal_id) {
        toast({
          title: 'Not in library',
          description: 'Import this title first, then link it to the stream.',
          variant: 'destructive',
        })
        return
      }
      setSelectedMedia(result)
      // Auto-set type selector to match the selected title
    },
    [toast],
  )

  // ── Create link ─────────────────────────────────────────────────────────

  const handleCreateLink = useCallback(async () => {
    if (!stream || !selectedMedia?.internal_id) return
    setIsCreatingLink(true)
    try {
      await streamLinkingApi.createLink({
        stream_id: stream.stream_id,
        media_id: selectedMedia.internal_id,
        file_index: fileIndex ? parseInt(fileIndex) : null,
        season: season ? parseInt(season) : null,
        episode: episode ? parseInt(episode) : null,
      })
      toast({ title: 'Link Created', description: `Stream linked to "${selectedMedia.title}"` })
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

  // ── Delete link ─────────────────────────────────────────────────────────

  const handleDeleteLink = useCallback(
    async (linkId: number) => {
      setDeletingLinkId(linkId)
      try {
        await streamLinkingApi.deleteLink(linkId)
        toast({ title: 'Link Removed', description: 'Stream link has been removed' })
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

  if (!stream) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        scrollMode="contained"
        className="sm:max-w-[600px] max-h-[85vh] flex flex-col overflow-hidden min-h-0"
      >
        <DialogHeader className="shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-primary" />
            Link Stream to Metadata
          </DialogTitle>
          <DialogDescription>Manage metadata links for this stream</DialogDescription>
        </DialogHeader>

        <ScrollArea className="flex-1 min-h-0 pr-1">
          <div className="space-y-3 py-1">
            {/* Stream info */}
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

            {/* Existing links */}
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

            {/* Add new link */}
            <div className="space-y-3">
              <Label className="text-sm text-muted-foreground">Add New Link</Label>

              {/* Metadata search — MetadataSearchPopover handles title search,
                  manual ID lookup via /matches, and in-library filtering */}
              <div className="space-y-1">
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
                  <MetadataSearchPopover
                    requireInternal
                    onSelect={handleSelectMedia}
                    onClear={() => setSelectedMedia(null)}
                    placeholder="Search movies, series, or enter an ID…"
                    popoverWidth="w-[calc(100vw-2rem)] sm:w-[420px]"
                  />
                )}
              </div>

              {/* Episode fields — only shown when a series is selected */}
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
          </div>
        </ScrollArea>

        <DialogFooter className="shrink-0">
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
