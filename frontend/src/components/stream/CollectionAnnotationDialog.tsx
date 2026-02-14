import { useState, useCallback, useMemo } from 'react'
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
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Textarea } from '@/components/ui/textarea'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Loader2, Search, Film, Tv, X, Layers, FileVideo, CheckCircle2, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useDebounce } from '@/hooks/useDebounce'
import { useCombinedMetadataSearch, type CombinedSearchResult } from '@/hooks'
import { useCreateStreamSuggestion } from '@/hooks/useStreamSuggestions'
import { useToast } from '@/hooks/use-toast'

// Types
interface FileInfo {
  file_id: number
  file_name: string
  size?: number | null
  file_index?: number
}

interface FileWithMedia extends FileInfo {
  linkedMedia?: {
    id: number
    title: string
    poster?: string
    type: string
  } | null
}

interface CollectionAnnotationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  streamId: number
  streamName: string
  files: FileInfo[]
  isLoading?: boolean
  onSuccess?: () => void
}

// Format file size
function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null || bytes === 0) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let unitIndex = 0
  let size = bytes
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  return `${size.toFixed(unitIndex > 0 ? 1 : 0)} ${units[unitIndex]}`
}

// Extract just the filename from a full path
function getFilenameOnly(fullPath: string): string {
  const parts = fullPath.split('/')
  return parts[parts.length - 1] || fullPath
}

// Parse title from filename for search
function parseFilenameForSearch(filename: string): string {
  const name = getFilenameOnly(filename)
  const withoutExt = name.replace(/\.[^/.]+$/, '')
  const cleaned = withoutExt.replace(/[._-]/g, ' ')

  const simplified = cleaned
    .replace(/\b(19|20)\d{2}\b/g, '')
    .replace(/\b(480p|576p|720p|1080p|1080i|2160p|4k|uhd|hd|sd)\b/gi, '')
    .replace(
      /\b(bluray|blu-ray|bdrip|brrip|webrip|web-dl|webdl|web|hdtv|hdrip|dvdrip|dvdscr|dvd|hdcam|cam|ts|telesync|remux)\b/gi,
      '',
    )
    .replace(/\b(x264|x265|h264|h265|hevc|avc|xvid|divx|10bit|10-bit|8bit)\b/gi, '')
    .replace(/\b(aac|ac3|dts|dts-hd|truehd|atmos|flac|mp3|dd5\.?1|5\.1|2\.0)\b/gi, '')
    .replace(/\b(hdr|hdr10|dolby\s*vision|dv)\b/gi, '')
    .replace(/\b(yts|yify|rarbg|sparks|geckos|tigole|qxr|psa)\b/gi, '')
    .replace(/\b(proper|repack|extended|unrated|directors\s*cut|theatrical)\b/gi, '')
    .replace(/\[.*?\]/g, '')
    .replace(/\(.*?\)/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  return simplified || cleaned
}

// Media search popover
function MediaSearchPopover({
  value,
  onSelect,
  onClear,
  disabled,
  initialQuery,
}: {
  value?: { id: number; title: string; poster?: string; type: string } | null
  onSelect: (result: CombinedSearchResult) => void
  onClear: () => void
  disabled?: boolean
  initialQuery?: string
}) {
  const [open, setOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState(initialQuery || '')
  const debouncedQuery = useDebounce(searchQuery, 300)

  const {
    data: results = [],
    isLoading,
    isFetching,
  } = useCombinedMetadataSearch(
    { query: debouncedQuery, type: 'all', limit: 15 },
    { enabled: debouncedQuery.length >= 2 && open },
  )

  // Sync initialQuery when popover opens (during render, not in effect)
  const [prevOpen, setPrevOpen] = useState(open)
  if (open && !prevOpen && initialQuery && !searchQuery) {
    setPrevOpen(open)
    setSearchQuery(initialQuery)
  } else if (open !== prevOpen) {
    setPrevOpen(open)
  }

  const handleSelect = useCallback(
    (result: CombinedSearchResult) => {
      if (result.source !== 'internal' || !result.internal_id) {
        return // Can only link to internal media
      }
      onSelect(result)
      setOpen(false)
      setSearchQuery('')
    },
    [onSelect],
  )

  if (value) {
    return (
      <div className="flex items-center gap-2 p-1.5 rounded-lg border border-primary/30 bg-primary/5">
        {value.poster ? (
          <img src={value.poster} alt="" className="w-6 h-9 rounded object-cover flex-shrink-0" />
        ) : (
          <div className="w-6 h-9 rounded bg-muted flex items-center justify-center flex-shrink-0">
            {value.type === 'series' ? (
              <Tv className="h-3 w-3 text-muted-foreground" />
            ) : (
              <Film className="h-3 w-3 text-muted-foreground" />
            )}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium truncate">{value.title}</p>
        </div>
        <Button variant="ghost" size="icon" className="h-5 w-5" onClick={onClear} disabled={disabled}>
          <X className="h-3 w-3" />
        </Button>
      </div>
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start text-xs text-muted-foreground h-8"
          disabled={disabled}
        >
          <Search className="h-3 w-3 mr-1.5" />
          Search media...
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[350px] p-0" align="start">
        <div className="p-2 border-b">
          <Input
            placeholder="Search movies, series..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-8 text-sm"
            autoFocus
          />
        </div>
        <ScrollArea className="max-h-[250px]">
          {isLoading && results.length === 0 && (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          )}
          {!isLoading && !isFetching && debouncedQuery.length >= 2 && results.length === 0 && (
            <div className="py-4 text-center text-xs text-muted-foreground">No results found</div>
          )}
          {!isLoading && debouncedQuery.length < 2 && (
            <div className="py-4 text-center text-xs text-muted-foreground">Type 2+ characters</div>
          )}
          {results.length > 0 && (
            <div className="p-1">
              {results.map((result) => {
                const isExternal = result.source === 'external'
                return (
                  <button
                    key={result.id}
                    onClick={() => handleSelect(result)}
                    disabled={isExternal}
                    className={cn(
                      'w-full flex items-center gap-2 p-1.5 rounded-md text-left',
                      isExternal ? 'opacity-50 cursor-not-allowed' : 'hover:bg-muted cursor-pointer',
                    )}
                    title={isExternal ? 'External - import first' : undefined}
                  >
                    {result.poster ? (
                      <img src={result.poster} alt="" className="w-6 h-9 rounded object-cover flex-shrink-0" />
                    ) : (
                      <div className="w-6 h-9 rounded bg-muted flex items-center justify-center flex-shrink-0">
                        {result.type === 'series' ? <Tv className="h-3 w-3" /> : <Film className="h-3 w-3" />}
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium truncate">{result.title}</p>
                      <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                        {result.year && <span>{result.year}</span>}
                        {isExternal ? (
                          <Badge variant="secondary" className="text-[9px] px-1 py-0 bg-yellow-500/20 text-yellow-700">
                            External
                          </Badge>
                        ) : (
                          <Badge variant="secondary" className="text-[9px] px-1 py-0 bg-green-500/20 text-green-700">
                            Library
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
  )
}

export function CollectionAnnotationDialog({
  open,
  onOpenChange,
  streamId,
  streamName,
  files,
  isLoading = false,
  onSuccess,
}: CollectionAnnotationDialogProps) {
  const { toast } = useToast()
  const createSuggestion = useCreateStreamSuggestion()

  // State
  const [filesWithMedia, setFilesWithMedia] = useState<FileWithMedia[]>([])
  const [reason, setReason] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Initialize files when dialog opens (during render, not in effect)
  const [prevOpen, setPrevOpen] = useState(open)
  const [prevFiles, setPrevFiles] = useState(files)
  if (open && files.length > 0 && (open !== prevOpen || prevFiles !== files)) {
    setPrevOpen(open)
    setPrevFiles(files)
    const sorted = [...files].sort((a, b) =>
      a.file_name.localeCompare(b.file_name, undefined, { numeric: true, sensitivity: 'base' }),
    )
    setFilesWithMedia(sorted.map((f) => ({ ...f, linkedMedia: null })))
    setReason('')
  }

  // Handle media selection for a file
  const handleSelectMedia = useCallback((fileId: number, result: CombinedSearchResult) => {
    if (!result.internal_id) return
    setFilesWithMedia((prev) =>
      prev.map((f) =>
        f.file_id === fileId
          ? {
              ...f,
              linkedMedia: {
                id: result.internal_id!,
                title: result.title,
                poster: result.poster,
                type: result.type,
              },
            }
          : f,
      ),
    )
  }, [])

  // Clear media for a file
  const handleClearMedia = useCallback((fileId: number) => {
    setFilesWithMedia((prev) => prev.map((f) => (f.file_id === fileId ? { ...f, linkedMedia: null } : f)))
  }, [])

  // Count files with media assigned
  const assignedCount = useMemo(() => filesWithMedia.filter((f) => f.linkedMedia).length, [filesWithMedia])

  // Submit suggestions
  const handleSubmit = useCallback(async () => {
    const filesToSubmit = filesWithMedia.filter((f) => f.linkedMedia)
    if (filesToSubmit.length === 0) return

    setIsSubmitting(true)
    let successCount = 0
    let failCount = 0

    for (const file of filesToSubmit) {
      try {
        await createSuggestion.mutateAsync({
          streamId,
          data: {
            suggestion_type: 'add_media_link',
            target_media_id: file.linkedMedia!.id,
            file_index: file.file_index,
            reason: reason || `Link file "${getFilenameOnly(file.file_name)}" to "${file.linkedMedia!.title}"`,
            suggested_value: file.linkedMedia!.title,
          },
        })
        successCount++
      } catch (error) {
        console.error('Failed to submit suggestion:', error)
        failCount++
      }
    }

    setIsSubmitting(false)

    if (successCount > 0) {
      toast({
        title: 'Suggestions Submitted',
        description:
          failCount > 0
            ? `${successCount} submitted, ${failCount} failed.`
            : `${successCount} link suggestion(s) submitted for review.`,
      })
      onOpenChange(false)
      onSuccess?.()
    } else {
      toast({
        title: 'Error',
        description: 'Failed to submit suggestions',
        variant: 'destructive',
      })
    }
  }, [filesWithMedia, streamId, reason, createSuggestion, toast, onOpenChange, onSuccess])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[800px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden">
        <DialogHeader className="px-6 pt-6 pb-4 border-b flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <Layers className="h-5 w-5 text-blue-500" />
            Collection Annotation
          </DialogTitle>
          <DialogDescription className="text-sm">
            Link individual files to different media items (for multi-movie/series collections).
            <br />
            Stream: <span className="font-medium text-foreground">{streamName}</span>
          </DialogDescription>
        </DialogHeader>

        {/* Info */}
        <div className="px-6 py-2 bg-blue-500/5 border-b flex-shrink-0">
          <div className="flex items-start gap-2 text-xs text-blue-400">
            <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
            <span>
              Search and assign each file to its corresponding media. Your suggestions will be submitted for moderator
              review.
            </span>
          </div>
        </div>

        {/* Stats */}
        <div className="px-6 py-2 border-b bg-muted/30 flex-shrink-0">
          <div className="flex items-center gap-3 text-xs">
            <Badge variant="outline" className="font-normal">
              {filesWithMedia.length} files
            </Badge>
            {assignedCount > 0 && (
              <Badge className="bg-blue-500/20 text-blue-500 border-blue-500/30">{assignedCount} assigned</Badge>
            )}
          </div>
        </div>

        {/* File List */}
        <ScrollArea className="flex-1 min-h-0">
          <div className="p-4 space-y-2">
            {filesWithMedia.map((file, index) => {
              const filename = getFilenameOnly(file.file_name)
              const searchQuery = parseFilenameForSearch(filename)

              return (
                <div
                  key={file.file_id}
                  className={cn(
                    'p-3 rounded-lg border transition-colors',
                    file.linkedMedia
                      ? 'border-blue-500/50 bg-blue-500/5'
                      : 'border-border/40 bg-background/50 hover:border-border',
                  )}
                >
                  <div className="flex items-start gap-3">
                    {/* File Info */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <div className="p-1.5 rounded bg-muted">
                        <FileVideo className="h-4 w-4 text-muted-foreground" />
                      </div>
                      <span className="text-xs font-mono text-muted-foreground">#{index + 1}</span>
                    </div>

                    <div className="flex-1 min-w-0 space-y-2">
                      {/* Filename */}
                      <div>
                        <p className="text-sm font-mono truncate" title={file.file_name}>
                          {filename}
                        </p>
                        {file.size != null && file.size > 0 && (
                          <span className="text-[10px] text-muted-foreground">{formatFileSize(file.size)}</span>
                        )}
                      </div>

                      {/* Media Search */}
                      <MediaSearchPopover
                        value={file.linkedMedia}
                        onSelect={(result) => handleSelectMedia(file.file_id, result)}
                        onClear={() => handleClearMedia(file.file_id)}
                        disabled={isSubmitting}
                        initialQuery={searchQuery}
                      />
                    </div>

                    {/* Status */}
                    {file.linkedMedia && (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <CheckCircle2 className="h-5 w-5 text-blue-500 flex-shrink-0" />
                          </TooltipTrigger>
                          <TooltipContent>Linked to {file.linkedMedia.title}</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </ScrollArea>

        {/* Reason */}
        <div className="px-6 py-3 border-t bg-muted/30 flex-shrink-0">
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason for these links (optional)"
            className="h-14 resize-none text-sm"
          />
        </div>

        {/* Footer */}
        <DialogFooter className="px-6 py-4 border-t bg-muted/30 flex-shrink-0">
          <div className="flex items-center justify-between w-full">
            <div className="text-sm text-muted-foreground">
              {assignedCount > 0 ? (
                <span className="text-blue-500 font-medium">
                  {assignedCount} file{assignedCount !== 1 ? 's' : ''} to link
                </span>
              ) : (
                'Assign files to media'
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
                Cancel
              </Button>
              <Button
                onClick={handleSubmit}
                disabled={assignedCount === 0 || isSubmitting || isLoading}
                className="bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Submitting...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-4 w-4 mr-2" />
                    Submit {assignedCount} Suggestion{assignedCount !== 1 ? 's' : ''}
                  </>
                )}
              </Button>
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
