import { useState, useCallback, useEffect } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Info,
  Play,
  Loader2,
  CheckCircle2,
  AlertCircle,
  FileVideo,
  ArrowDown,
  FolderTree,
  FileText,
  Eraser,
  CheckSquare,
  Square,
  HelpCircle,
  Search,
  Film,
  Tv,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useDebounce } from '@/hooks/useDebounce'
import { useCombinedMetadataSearch, type CombinedSearchResult } from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import { userMetadataApi } from '@/lib/api'
import { resolveExternalImportTarget, toImportedInternalResult } from './externalImport'

export interface FileLink {
  file_id: number
  file_name: string
  file_index?: number | null
  size?: number | null // File size in bytes
  season_number: number | null
  episode_number: number | null
  episode_end?: number | null
  linked_media_id?: number | null
  linked_media_title?: string | null
  linked_media_type?: string | null
}

export interface EditedFileLink extends FileLink {
  included: boolean
  target_media_id?: number | null
  target_media_title?: string | null
  target_media_type?: string | null
  isModified?: boolean
}

type ViewMode = 'full' | 'filename'
type AnnotationMode = 'episode' | 'media'

interface FileAnnotationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  streamName: string
  initialFiles: FileLink[]
  onSave: (files: EditedFileLink[]) => Promise<void>
  onSaveMediaLinks?: (files: EditedFileLink[]) => Promise<void>
  allowMediaLinking?: boolean
  isLoading?: boolean
}

// Extract just the filename from a full path
function getFilenameOnly(fullPath: string): string {
  const parts = fullPath.split('/')
  return parts[parts.length - 1] || fullPath
}

// Get folder structure (everything except the filename)
function getFolderPath(fullPath: string): string {
  const parts = fullPath.split('/')
  if (parts.length <= 1) return ''
  return parts.slice(0, -1).join('/') + '/'
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

interface SelectedMedia {
  id: number
  title: string
  poster?: string
  type: string
}

function MediaSearchPopover({
  value,
  onSelect,
  onClear,
  disabled,
  initialQuery,
}: {
  value?: SelectedMedia | null
  onSelect: (result: CombinedSearchResult) => void
  onClear: () => void
  disabled?: boolean
  initialQuery?: string
}) {
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState(initialQuery || '')
  const [searchYear, setSearchYear] = useState('')
  const [importingResultId, setImportingResultId] = useState<string | null>(null)
  const debouncedQuery = useDebounce(searchQuery, 300)
  const trimmedSearchYear = searchYear.trim()
  const parsedSearchYear = trimmedSearchYear ? Number(trimmedSearchYear) : undefined
  const validSearchYear = Number.isFinite(parsedSearchYear) ? parsedSearchYear : undefined

  const {
    data: results = [],
    isLoading,
    isFetching,
  } = useCombinedMetadataSearch(
    { query: debouncedQuery, type: 'movie', limit: 15, year: validSearchYear },
    { enabled: debouncedQuery.length >= 2 && open },
  )

  const [prevOpen, setPrevOpen] = useState(open)
  if (open && !prevOpen && initialQuery && !searchQuery) {
    setPrevOpen(open)
    setSearchQuery(initialQuery)
  } else if (open !== prevOpen) {
    setPrevOpen(open)
  }

  const handleSelect = useCallback(
    async (result: CombinedSearchResult) => {
      if (result.source === 'internal' && result.internal_id) {
        onSelect(result)
        setOpen(false)
        setSearchQuery('')
        setSearchYear('')
        return
      }

      const importTarget = resolveExternalImportTarget(result)
      if (!importTarget) {
        toast({
          title: 'Cannot import this result',
          description: 'No supported external ID was found for auto-import.',
          variant: 'destructive',
        })
        return
      }

      setImportingResultId(result.id)
      try {
        const imported = await userMetadataApi.importFromExternal({
          provider: importTarget.provider,
          external_id: importTarget.externalId,
          media_type: result.type === 'series' ? 'series' : 'movie',
        })

        onSelect(toImportedInternalResult(imported, result))
        setOpen(false)
        setSearchQuery('')
        setSearchYear('')
        toast({
          title: 'Imported and selected',
          description: imported.title,
        })
      } catch (error) {
        toast({
          title: 'Import failed',
          description: error instanceof Error ? error.message : 'Failed to import metadata',
          variant: 'destructive',
        })
      } finally {
        setImportingResultId(null)
      }
    },
    [onSelect, toast],
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
          Search movie...
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[calc(100vw-2rem)] sm:w-[350px] p-0 overflow-hidden flex flex-col"
        align="start"
        style={{ height: '380px', maxHeight: 'calc(var(--radix-popover-content-available-height) - 10px)' }}
      >
        <div className="p-2 border-b shrink-0">
          <div className="flex gap-2">
            <Input
              placeholder="Search movies..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-8 text-sm"
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
              className="h-8 w-24 shrink-0 text-sm"
            />
          </div>
        </div>
        <ScrollArea className="flex-1 min-h-0">
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
                const importTarget = isExternal ? resolveExternalImportTarget(result) : null
                const isImporting = importingResultId === result.id
                const isBusy = importingResultId !== null
                const isDisabled = isImporting || isBusy || (isExternal && !importTarget)
                return (
                  <button
                    key={result.id}
                    onClick={() => void handleSelect(result)}
                    disabled={isDisabled}
                    className={cn(
                      'w-full flex items-center gap-2 p-1.5 rounded-md text-left',
                      isDisabled ? 'opacity-50 cursor-not-allowed' : 'hover:bg-muted cursor-pointer',
                    )}
                    title={
                      isImporting
                        ? 'Importing...'
                        : isExternal && !importTarget
                          ? 'External source is not supported for auto-import yet'
                          : isExternal
                            ? 'External - click to import and select'
                            : undefined
                    }
                  >
                    {result.poster ? (
                      <img src={result.poster} alt="" className="w-6 h-9 rounded object-cover flex-shrink-0" />
                    ) : (
                      <div className="w-6 h-9 rounded bg-muted flex items-center justify-center flex-shrink-0">
                        <Film className="h-3 w-3" />
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
                    {isImporting && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
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

export function FileAnnotationDialog({
  open,
  onOpenChange,
  streamName,
  initialFiles,
  onSave,
  onSaveMediaLinks,
  allowMediaLinking = false,
  isLoading = false,
}: FileAnnotationDialogProps) {
  const [editedFiles, setEditedFiles] = useState<EditedFileLink[]>([])
  const [highlightedIndices, setHighlightedIndices] = useState<Set<number>>(new Set())
  const [saveError, setSaveError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('filename')
  const [annotationMode, setAnnotationMode] = useState<AnnotationMode>('episode')
  const [confirmMediaSaveOpen, setConfirmMediaSaveOpen] = useState(false)

  // Initialize edited files from initial files
  useEffect(() => {
    if (open && initialFiles.length > 0) {
      const sorted = [...initialFiles].sort((a, b) =>
        a.file_name.localeCompare(b.file_name, undefined, {
          numeric: true,
          sensitivity: 'base',
        }),
      )
      setEditedFiles(
        sorted.map((f) => ({
          ...f,
          included: true,
          target_media_id: f.linked_media_id ?? null,
          target_media_title: f.linked_media_title ?? null,
          target_media_type: f.linked_media_type ?? null,
          isModified: false,
        })),
      )
      setSaveError(null)
      setAnnotationMode('episode')
      setConfirmMediaSaveOpen(false)
    }
  }, [open, initialFiles])

  useEffect(() => {
    if (!open) {
      setConfirmMediaSaveOpen(false)
    }
  }, [open])

  const updateFile = useCallback(
    (fileId: number, field: keyof EditedFileLink, value: number | null | boolean) => {
      setEditedFiles((prev) =>
        prev.map((f) => {
          if (f.file_id !== fileId) return f
          const original = initialFiles.find((o) => o.file_id === fileId)
          const updated = { ...f, [field]: value }

          // Check if modified from original
          if (original) {
            updated.isModified =
              updated.season_number !== original.season_number ||
              updated.episode_number !== original.episode_number ||
              updated.episode_end !== original.episode_end
          }
          return updated
        }),
      )
    },
    [initialFiles],
  )

  const updateFileMedia = useCallback((fileId: number, media: SelectedMedia | null) => {
    setEditedFiles((prev) =>
      prev.map((f) => {
        if (f.file_id !== fileId) return f
        return {
          ...f,
          target_media_id: media?.id ?? null,
          target_media_title: media?.title ?? null,
          target_media_type: media?.type ?? null,
        }
      }),
    )
  }, [])

  const isEpisodeModified = useCallback(
    (file: EditedFileLink) => {
      const original = initialFiles.find((o) => o.file_id === file.file_id)
      if (!file.included) {
        // Excluding a file that had annotation data counts as a modification
        return (
          (original?.season_number ?? null) !== null ||
          (original?.episode_number ?? null) !== null ||
          (original?.episode_end ?? null) !== null
        )
      }
      return (
        file.season_number !== (original?.season_number ?? null) ||
        file.episode_number !== (original?.episode_number ?? null) ||
        (file.episode_end ?? null) !== (original?.episode_end ?? null)
      )
    },
    [initialFiles],
  )

  const isMediaModified = useCallback(
    (file: EditedFileLink) => {
      const original = initialFiles.find((o) => o.file_id === file.file_id)
      return (file.target_media_id ?? null) !== (original?.linked_media_id ?? null)
    },
    [initialFiles],
  )

  const isFileModified = useCallback(
    (file: EditedFileLink) => (annotationMode === 'media' ? isMediaModified(file) : isEpisodeModified(file)),
    [annotationMode, isEpisodeModified, isMediaModified],
  )

  const toggleFileInclusion = useCallback((fileId: number) => {
    setEditedFiles((prev) => prev.map((f) => (f.file_id === fileId ? { ...f, included: !f.included } : f)))
  }, [])

  // Select/deselect all files
  const toggleAllFiles = useCallback((include: boolean) => {
    setEditedFiles((prev) => prev.map((f) => ({ ...f, included: include })))
  }, [])

  // Clear all episode data
  const clearAllEpisodeData = useCallback(() => {
    setEditedFiles((prev) =>
      prev.map((f) => {
        const original = initialFiles.find((o) => o.file_id === f.file_id)
        return {
          ...f,
          season_number: null,
          episode_number: null,
          episode_end: null,
          isModified:
            original?.season_number !== null || original?.episode_number !== null || original?.episode_end !== null,
        }
      }),
    )
  }, [initialFiles])

  const clearAllMediaData = useCallback(() => {
    setEditedFiles((prev) =>
      prev.map((f) => ({
        ...f,
        target_media_id: null,
        target_media_title: null,
        target_media_type: null,
      })),
    )
  }, [])

  // Apply same season to all following files
  const applySeasonToFollowing = useCallback(
    (startIndex: number) => {
      const startFile = editedFiles[startIndex]
      if (!startFile || startFile.season_number === null) return

      const seasonNum = startFile.season_number
      const indicesToHighlight: number[] = []

      setEditedFiles((prev) =>
        prev.map((f, idx) => {
          if (idx < startIndex || !f.included) return f
          indicesToHighlight.push(idx)
          const original = initialFiles.find((o) => o.file_id === f.file_id)
          return {
            ...f,
            season_number: seasonNum,
            isModified:
              seasonNum !== original?.season_number ||
              f.episode_number !== original?.episode_number ||
              f.episode_end !== original?.episode_end,
          }
        }),
      )

      // Visual feedback
      setHighlightedIndices(new Set(indicesToHighlight))
      setTimeout(() => setHighlightedIndices(new Set()), 1500)
    },
    [editedFiles, initialFiles],
  )

  // Apply consecutive episode numbering from index
  // If the starting file has an episode number, continue from there
  // Otherwise start from 1
  const applyEpisodeNumbering = useCallback(
    (startIndex: number) => {
      const startFile = editedFiles[startIndex]
      // Start from current episode number if set, otherwise 1
      let episodeCounter = startFile?.episode_number ?? 1
      let lastSeason: number | null = null
      const indicesToHighlight: number[] = []

      setEditedFiles((prev) =>
        prev.map((f, idx) => {
          if (idx < startIndex || !f.included) return f

          // Reset episode counter if season changes (and we're not at the start)
          const currentSeason = f.season_number
          if (idx !== startIndex && lastSeason !== null && currentSeason !== null && currentSeason !== lastSeason) {
            episodeCounter = 1
          }

          indicesToHighlight.push(idx)
          const newEpisodeNumber = episodeCounter++
          if (currentSeason !== null) {
            lastSeason = currentSeason
          }

          const original = initialFiles.find((o) => o.file_id === f.file_id)
          return {
            ...f,
            episode_number: newEpisodeNumber,
            isModified:
              f.season_number !== original?.season_number ||
              newEpisodeNumber !== original?.episode_number ||
              f.episode_end !== original?.episode_end,
          }
        }),
      )

      // Visual feedback
      setHighlightedIndices(new Set(indicesToHighlight))
      setTimeout(() => setHighlightedIndices(new Set()), 1500)
    },
    [editedFiles, initialFiles],
  )

  const handleSaveChanges = useCallback(async () => {
    try {
      setSaveError(null)
      // Save all modified files, including excluded ones (exclusion = remove annotation)
      const modifiedFiles = editedFiles.filter((f) => isFileModified(f))
      if (annotationMode === 'media') {
        if (!onSaveMediaLinks) {
          throw new Error('Media link save handler is not configured')
        }
        await onSaveMediaLinks(modifiedFiles)
      } else {
        await onSave(modifiedFiles)
      }
      onOpenChange(false)
    } catch (error) {
      setSaveError((error as Error)?.message || 'Failed to save changes')
    }
  }, [annotationMode, editedFiles, isFileModified, onOpenChange, onSave, onSaveMediaLinks])

  const handleSave = useCallback(() => {
    if (annotationMode === 'media') {
      setConfirmMediaSaveOpen(true)
      return
    }
    void handleSaveChanges()
  }, [annotationMode, handleSaveChanges])

  const handleConfirmMediaSave = useCallback(() => {
    setConfirmMediaSaveOpen(false)
    void handleSaveChanges()
  }, [handleSaveChanges])

  const modifiedCount = editedFiles.filter((f) => isFileModified(f)).length
  const includedCount = editedFiles.filter((f) => f.included).length

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        scrollMode="contained"
        className="sm:max-w-[900px] max-h-[85vh] min-h-0 flex flex-col p-0 gap-0 overflow-hidden"
      >
        <DialogHeader className="px-6 pt-6 pb-4 border-b flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <FileVideo className="h-5 w-5 text-emerald-500" />
            Annotate Video Files
          </DialogTitle>
          <DialogDescription className="text-sm">
            {annotationMode === 'media'
              ? 'Link files to specific movies in '
              : 'Fix season and episode numbers for files in '}
            <span className="font-medium text-foreground break-all">{streamName}</span>
          </DialogDescription>
        </DialogHeader>

        {/* Toolbar */}
        <div className="px-6 py-3 border-b bg-muted/30 flex-shrink-0">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              {allowMediaLinking && (
                <>
                  <div className="bg-background/50 p-0.5 rounded-lg flex gap-0.5">
                    <Button
                      variant="ghost"
                      size="sm"
                      className={cn('h-7 px-2 text-xs', annotationMode === 'episode' && 'bg-emerald-500/20')}
                      onClick={() => setAnnotationMode('episode')}
                    >
                      Episodes
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className={cn('h-7 px-2 text-xs', annotationMode === 'media' && 'bg-blue-500/20 text-blue-400')}
                      onClick={() => setAnnotationMode('media')}
                    >
                      Movies
                    </Button>
                  </div>
                  <Separator orientation="vertical" className="h-6" />
                </>
              )}

              {/* View Mode Toggle */}
              <div className="bg-background/50 p-0.5 rounded-lg flex gap-0.5">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn('h-7 px-2', viewMode === 'filename' && 'bg-emerald-500/20')}
                        onClick={() => setViewMode('filename')}
                      >
                        <FileText className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Filename only</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn('h-7 px-2', viewMode === 'full' && 'bg-emerald-500/20')}
                        onClick={() => setViewMode('full')}
                      >
                        <FolderTree className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Full path with folder structure</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>

              <Separator orientation="vertical" className="h-6" />

              {/* Selection buttons */}
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => toggleAllFiles(true)}>
                      <CheckSquare className="h-3.5 w-3.5 mr-1" />
                      All
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Select all files</TooltipContent>
                </Tooltip>
              </TooltipProvider>

              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => toggleAllFiles(false)}
                    >
                      <Square className="h-3.5 w-3.5 mr-1" />
                      None
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Deselect all files</TooltipContent>
                </Tooltip>
              </TooltipProvider>

              <Separator orientation="vertical" className="h-6" />

              {/* Clear all button */}
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs text-primary hover:text-primary"
                      onClick={annotationMode === 'media' ? clearAllMediaData : clearAllEpisodeData}
                    >
                      <Eraser className="h-3.5 w-3.5 mr-1" />
                      Clear All
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {annotationMode === 'media' ? 'Clear all selected movie links' : 'Clear all season/episode data'}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>

            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="outline" className="font-normal">
                {editedFiles.length} files
              </Badge>
              <Badge variant="outline" className="font-normal">
                {includedCount} selected
              </Badge>
              {modifiedCount > 0 && (
                <Badge className="bg-emerald-500/20 text-emerald-500 border-emerald-500/30">
                  {modifiedCount} modified
                </Badge>
              )}
            </div>
          </div>
        </div>

        {/* Info Alert */}
        <div className="px-6 py-2 bg-blue-500/5 border-b flex-shrink-0">
          <div className="flex items-start gap-2 text-xs text-blue-400">
            <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
            {annotationMode === 'media' ? (
              <span>
                Assign each file to the correct movie. This is useful for collection torrents where one upload contains
                multiple movies.
              </span>
            ) : (
              <span>
                Use <kbd className="px-1 py-0.5 rounded bg-blue-500/20 text-[10px]">↓</kbd> to apply season to following
                files, and <kbd className="px-1 py-0.5 rounded bg-blue-500/20 text-[10px]">▶</kbd> for consecutive
                episode numbering (continues from current or starts at 1).
              </span>
            )}
          </div>
        </div>

        {/* File List */}
        <ScrollArea className="flex-1 min-h-0">
          <div className="px-6 py-4 space-y-2">
            {editedFiles.map((file, index) => {
              const filename = getFilenameOnly(file.file_name)
              const folderPath = getFolderPath(file.file_name)
              const fileIsModified = isFileModified(file)
              const selectedMedia =
                file.target_media_id != null
                  ? {
                      id: file.target_media_id,
                      title: file.target_media_title || `Media #${file.target_media_id}`,
                      type: file.target_media_type || 'movie',
                    }
                  : null

              return (
                <div
                  key={file.file_id}
                  className={cn(
                    'p-3 rounded-lg border transition-all duration-300',
                    !file.included && 'opacity-40 bg-muted/20',
                    fileIsModified && file.included && 'border-emerald-500/50 bg-emerald-500/5',
                    annotationMode === 'episode' && highlightedIndices.has(index) && 'ring-2 ring-emerald-500/50',
                    file.included && !fileIsModified && 'border-border/40 bg-background/50 hover:border-border',
                  )}
                >
                  {/* Header Row */}
                  <div className="flex items-center gap-2 mb-2">
                    <Switch
                      checked={file.included}
                      onCheckedChange={() => toggleFileInclusion(file.file_id)}
                      className="data-[state=checked]:bg-emerald-500 scale-90 flex-shrink-0"
                    />

                    <div className="flex-1 min-w-0">
                      {viewMode === 'full' && folderPath && (
                        <p className="text-[10px] text-muted-foreground/60 font-mono break-all">{folderPath}</p>
                      )}
                      <p className={cn('font-mono break-all', viewMode === 'filename' ? 'text-sm' : 'text-xs')}>
                        {filename}
                      </p>
                    </div>

                    {file.size != null && file.size > 0 && (
                      <Badge
                        variant="outline"
                        className="text-[10px] font-normal text-muted-foreground flex-shrink-0 whitespace-nowrap"
                      >
                        {formatFileSize(file.size)}
                      </Badge>
                    )}

                    {fileIsModified && file.included && (
                      <Badge
                        variant="secondary"
                        className="bg-emerald-500/20 text-emerald-400 text-[10px] flex-shrink-0"
                      >
                        Modified
                      </Badge>
                    )}
                  </div>

                  {/* Input Row */}
                  {annotationMode === 'media' ? (
                    <div className="space-y-1">
                      <Label className="text-[10px] text-muted-foreground">Linked Movie</Label>
                      <MediaSearchPopover
                        value={selectedMedia}
                        onSelect={(result) => {
                          if (result.source !== 'internal' || !result.internal_id) return
                          updateFileMedia(file.file_id, {
                            id: result.internal_id,
                            title: result.title,
                            poster: result.poster,
                            type: result.type,
                          })
                        }}
                        onClear={() => updateFileMedia(file.file_id, null)}
                        disabled={!file.included}
                        initialQuery={parseFilenameForSearch(filename)}
                      />
                    </div>
                  ) : (
                    <div className="grid grid-cols-3 gap-2">
                      {/* Season */}
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <Label className="text-[10px] text-muted-foreground">Season</Label>
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-5 w-5"
                                  onClick={() => applySeasonToFollowing(index)}
                                  disabled={!file.included || file.season_number === null}
                                >
                                  <ArrowDown className="h-3 w-3" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>Apply this season to all following files</TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        </div>
                        <Input
                          type="number"
                          min={0}
                          value={file.season_number ?? ''}
                          onChange={(e) =>
                            updateFile(file.file_id, 'season_number', e.target.value ? parseInt(e.target.value) : null)
                          }
                          disabled={!file.included}
                          className="h-8 text-sm"
                          placeholder="S"
                        />
                      </div>

                      {/* Episode */}
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <Label className="text-[10px] text-muted-foreground">Episode</Label>
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-5 w-5"
                                  onClick={() => applyEpisodeNumbering(index)}
                                  disabled={!file.included}
                                >
                                  <Play className="h-3 w-3" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent className="max-w-xs">
                                <p>Apply consecutive numbering from here.</p>
                                <p className="text-muted-foreground mt-1">
                                  Continues from current episode number, or starts at 1 if empty. Resets when season
                                  changes.
                                </p>
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        </div>
                        <Input
                          type="number"
                          min={0}
                          value={file.episode_number ?? ''}
                          onChange={(e) =>
                            updateFile(file.file_id, 'episode_number', e.target.value ? parseInt(e.target.value) : null)
                          }
                          disabled={!file.included}
                          className="h-8 text-sm"
                          placeholder="E"
                        />
                      </div>

                      {/* Episode End */}
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-1">
                            <Label className="text-[10px] text-muted-foreground">Ep. End</Label>
                            <TooltipProvider>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <HelpCircle className="h-3 w-3 text-muted-foreground/50 cursor-help" />
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs">
                                  <p className="font-medium">Multi-episode files</p>
                                  <p className="text-muted-foreground mt-1">
                                    If a single file contains multiple consecutive episodes (e.g., E01-E03), set Episode
                                    to 1 and Episode End to 3.
                                  </p>
                                </TooltipContent>
                              </Tooltip>
                            </TooltipProvider>
                          </div>
                        </div>
                        <Input
                          type="number"
                          min={0}
                          value={file.episode_end ?? ''}
                          onChange={(e) =>
                            updateFile(file.file_id, 'episode_end', e.target.value ? parseInt(e.target.value) : null)
                          }
                          disabled={!file.included}
                          className="h-8 text-sm"
                          placeholder="Multi"
                        />
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </ScrollArea>

        {/* Error */}
        {saveError && (
          <div className="px-6 py-2 bg-red-500/10 border-t flex-shrink-0">
            <div className="flex items-center gap-2 text-xs text-red-400">
              <AlertCircle className="h-3.5 w-3.5" />
              {saveError}
            </div>
          </div>
        )}

        {/* Footer */}
        <DialogFooter className="px-6 py-4 border-t bg-muted/30 flex-shrink-0">
          <div className="flex items-center justify-between w-full">
            <div className="text-sm text-muted-foreground">
              {modifiedCount > 0 ? (
                <span className="text-emerald-500 font-medium">
                  {modifiedCount} file{modifiedCount !== 1 ? 's' : ''} modified
                </span>
              ) : (
                'No changes'
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-lg">
                Cancel
              </Button>
              <Button
                onClick={handleSave}
                disabled={modifiedCount === 0 || isLoading}
                className="rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500"
              >
                {isLoading ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Saving...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-4 w-4 mr-2" />
                    Save {modifiedCount} Change{modifiedCount !== 1 ? 's' : ''}
                  </>
                )}
              </Button>
            </div>
          </div>
        </DialogFooter>
      </DialogContent>

      <AlertDialog open={confirmMediaSaveOpen} onOpenChange={setConfirmMediaSaveOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm movie link replacement</AlertDialogTitle>
            <AlertDialogDescription>
              Saving in Movies mode will replace the current media mapping for this annotation request with your
              selected per-file movie links. Continue?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isLoading}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmMediaSave} disabled={isLoading}>
              Continue and Save
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  )
}
