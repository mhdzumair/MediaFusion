import { useState, useCallback, useEffect, useMemo } from 'react'
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
import { Switch } from '@/components/ui/switch'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Info,
  Play,
  Loader2,
  CheckCircle2,
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
  Link2,
  X,
  Layers,
  AlertCircle,
} from 'lucide-react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { useCombinedMetadataSearch, getBestExternalId, type CombinedSearchResult } from '@/hooks'
import { useDebounce } from '@/hooks/useDebounce'
import { userMetadataApi, type ImportProvider, type TorrentFile } from '@/lib/api'
import type { FileAnnotation } from './types'

// Provider options for manual ID input
const PROVIDER_OPTIONS: { value: ImportProvider; label: string; placeholder: string; example: string }[] = [
  { value: 'imdb', label: 'IMDB', placeholder: 'tt1234567', example: 'tt0111161' },
  { value: 'tmdb', label: 'TMDB', placeholder: '278', example: '278' },
  { value: 'tvdb', label: 'TVDB', placeholder: '81189', example: '81189' },
  { value: 'mal', label: 'MAL', placeholder: '5114', example: '5114' },
  { value: 'kitsu', label: 'Kitsu', placeholder: '1555', example: '1555' },
]

type AnnotationMode = 'episode' | 'multi-content'

interface ImportFileAnnotationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  torrentName: string
  files: TorrentFile[]
  isSports?: boolean
  onConfirm: (annotatedFiles: FileAnnotation[]) => void
  isLoading?: boolean
  /** Enable multi-content mode for movie collections */
  allowMultiContent?: boolean
  /** Default metadata type for search */
  defaultMetaType?: 'movie' | 'series'
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

type ViewMode = 'full' | 'filename'

interface EditedFile extends FileAnnotation {
  isModified?: boolean
}

// Metadata search popover component for per-file linking
// Uses combined search to search both internal DB and external providers
function MetadataSearchPopover({
  value,
  onSelect,
  onClear,
  disabled,
  metaType = 'movie',
}: {
  value?: { id: string; title: string; poster?: string; type?: string }
  onSelect: (result: CombinedSearchResult) => void
  onClear: () => void
  disabled?: boolean
  metaType?: 'movie' | 'series'
}) {
  const [open, setOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [showManualId, setShowManualId] = useState(false)
  const [manualProvider, setManualProvider] = useState<ImportProvider>('imdb')
  const [manualId, setManualId] = useState('')
  const [isLoadingPreview, setIsLoadingPreview] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const debouncedQuery = useDebounce(searchQuery, 300)

  const {
    data: searchResults = [],
    isLoading,
    isFetching,
  } = useCombinedMetadataSearch(
    {
      query: debouncedQuery,
      type: metaType,
      limit: 15,
    },
    { enabled: debouncedQuery.length >= 2 && !showManualId },
  )

  const handleSelect = useCallback(
    (result: CombinedSearchResult) => {
      onSelect(result)
      setOpen(false)
      setSearchQuery('')
      setShowManualId(false)
    },
    [onSelect],
  )

  // Handle manual ID submission - fetches metadata from provider
  const handleManualIdSubmit = useCallback(async () => {
    if (!manualId.trim()) return

    setIsLoadingPreview(true)
    setPreviewError(null)

    try {
      const preview = await userMetadataApi.previewImport({
        provider: manualProvider,
        external_id: manualId.trim(),
        media_type: metaType,
      })

      const manualResult: CombinedSearchResult = {
        id: `manual-${manualProvider}-${manualId.trim()}`,
        title: preview.title,
        year: preview.year,
        poster: preview.poster,
        type: metaType,
        source: 'external',
        imdb_id: preview.imdb_id,
        tmdb_id: preview.tmdb_id,
        tvdb_id: preview.tvdb_id,
        external_id: preview.imdb_id || (preview.tmdb_id ? `tmdb:${preview.tmdb_id}` : manualId.trim()),
        provider: manualProvider,
        description: preview.description,
      }

      onSelect(manualResult)
      setOpen(false)
      setShowManualId(false)
      setManualId('')
      setManualProvider('imdb')
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : 'Failed to fetch metadata')
    } finally {
      setIsLoadingPreview(false)
    }
  }, [manualId, manualProvider, metaType, onSelect])

  const currentProviderOption = PROVIDER_OPTIONS.find((p) => p.value === manualProvider)

  if (value?.id) {
    return (
      <div className="flex items-center gap-1.5 p-1.5 rounded border border-primary/30 bg-primary/5">
        {/* Clear button on the LEFT for visibility */}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-5 w-5 flex-shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10"
          onClick={onClear}
          disabled={disabled}
          title="Remove link"
        >
          <X className="h-3 w-3" />
        </Button>
        {value.poster ? (
          <img src={value.poster} alt="" className="w-6 h-8 rounded object-cover flex-shrink-0" />
        ) : (
          <div className="w-6 h-8 rounded bg-muted flex items-center justify-center flex-shrink-0">
            {value.type === 'series' ? (
              <Tv className="h-3 w-3 text-muted-foreground" />
            ) : (
              <Film className="h-3 w-3 text-muted-foreground" />
            )}
          </div>
        )}
        <span className="text-xs truncate flex-1 min-w-0">{value.title}</span>
      </div>
    )
  }

  return (
    <Popover
      open={open}
      onOpenChange={(isOpen) => {
        setOpen(isOpen)
        if (!isOpen) {
          setShowManualId(false)
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 w-full justify-start text-xs text-muted-foreground"
          disabled={disabled}
        >
          <Search className="h-3 w-3 mr-1.5" />
          Link to metadata...
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[300px] p-0" align="start">
        {showManualId ? (
          // Manual ID input mode
          <div className="p-2 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium">Enter ID</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 text-[10px] px-2"
                onClick={() => {
                  setShowManualId(false)
                  setPreviewError(null)
                }}
                disabled={isLoadingPreview}
              >
                Back
              </Button>
            </div>

            {/* Provider selector */}
            <Select value={manualProvider} onValueChange={(v) => setManualProvider(v as ImportProvider)}>
              <SelectTrigger className="h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDER_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value} className="text-xs">
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* ID input */}
            <Input
              placeholder={currentProviderOption?.placeholder || 'Enter ID'}
              value={manualId}
              onChange={(e) => {
                setManualId(e.target.value)
                setPreviewError(null)
              }}
              className="h-7 text-xs"
              autoFocus
            />
            <p className="text-[10px] text-muted-foreground">
              Example: <code className="bg-muted px-0.5 rounded">{currentProviderOption?.example}</code>
            </p>

            {/* Error message */}
            {previewError && (
              <div className="flex items-start gap-1.5 p-1.5 rounded bg-destructive/10 text-destructive text-[10px]">
                <AlertCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
                <span>{previewError}</span>
              </div>
            )}

            <Button
              className="w-full h-7 text-xs"
              size="sm"
              onClick={handleManualIdSubmit}
              disabled={!manualId.trim() || isLoadingPreview}
            >
              {isLoadingPreview ? (
                <>
                  <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                  Fetching...
                </>
              ) : (
                <>
                  <Link2 className="h-3 w-3 mr-1.5" />
                  Fetch & Link
                </>
              )}
            </Button>
          </div>
        ) : (
          // Search mode
          <>
            <div className="p-2 border-b space-y-1.5">
              <Input
                placeholder="Search metadata..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-8 text-sm"
                autoFocus
              />
              <Button
                variant="ghost"
                size="sm"
                className="w-full h-6 text-[10px] text-muted-foreground hover:text-foreground"
                onClick={() => setShowManualId(true)}
              >
                Enter ID manually
              </Button>
            </div>
            <ScrollArea className="max-h-[250px]">
              {isLoading && searchResults.length === 0 && (
                <div className="flex items-center justify-center py-6">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              )}
              {!isLoading && !isFetching && searchQuery.length >= 2 && searchResults.length === 0 && (
                <div className="py-4 text-center">
                  <p className="text-xs text-muted-foreground mb-2">No results</p>
                  <Button variant="outline" size="sm" className="h-6 text-[10px]" onClick={() => setShowManualId(true)}>
                    Enter ID manually
                  </Button>
                </div>
              )}
              {!isLoading && searchQuery.length < 2 && (
                <div className="py-6 text-center text-xs text-muted-foreground">
                  Type at least 2 characters to search
                </div>
              )}
              {searchResults.length > 0 && (
                <div className="p-1">
                  {isFetching && (
                    <div className="flex items-center justify-center py-2 text-xs text-muted-foreground gap-1.5">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      <span>Loading more...</span>
                    </div>
                  )}
                  {searchResults.map((result) => (
                    <button
                      key={result.id}
                      onClick={() => handleSelect(result)}
                      className="w-full flex items-center gap-2 p-2 rounded-md hover:bg-muted cursor-pointer text-left"
                    >
                      {result.poster ? (
                        <img src={result.poster} alt="" className="w-8 h-12 rounded object-cover flex-shrink-0" />
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
                            <Badge variant="secondary" className="text-[10px] px-1 py-0 bg-green-500/20 text-green-700">
                              In Library
                            </Badge>
                          ) : (
                            result.provider && (
                              <Badge variant="secondary" className="text-[10px] px-1 py-0">
                                {result.provider.toUpperCase()}
                              </Badge>
                            )
                          )}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </ScrollArea>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}

export function ImportFileAnnotationDialog({
  open,
  onOpenChange,
  torrentName,
  files,
  isSports = false,
  onConfirm,
  isLoading = false,
  allowMultiContent = false,
  defaultMetaType = 'movie',
}: ImportFileAnnotationDialogProps) {
  const [editedFiles, setEditedFiles] = useState<EditedFile[]>([])
  const [highlightedIndices, setHighlightedIndices] = useState<Set<number>>(new Set())
  const [viewMode, setViewMode] = useState<ViewMode>('filename')
  const [annotationMode, setAnnotationMode] = useState<AnnotationMode>('episode')

  // Initialize edited files from input files
  useEffect(() => {
    if (open && files.length > 0) {
      // Sort by filename
      const sorted = [...files].sort((a, b) =>
        a.filename.localeCompare(b.filename, undefined, {
          numeric: true,
          sensitivity: 'base',
        }),
      )
      setEditedFiles(
        sorted.map((f, idx) => ({
          filename: f.filename,
          size: f.size,
          index: f.index ?? idx,
          season_number: f.season_number ?? null,
          episode_number: f.episode_number ?? null,
          episode_end: null,
          included: true,
          isModified: false,
          // Sports-specific
          title: undefined,
          overview: undefined,
          thumbnail: undefined,
          release_date: undefined,
          // Multi-content specific
          meta_id: undefined,
          meta_title: undefined,
          meta_poster: undefined,
          meta_type: undefined,
        })),
      )
      // Reset mode when dialog opens
      setAnnotationMode('episode')
    }
  }, [open, files])

  // Update file metadata link
  const updateFileMetadata = useCallback((index: number, result: CombinedSearchResult | null) => {
    setEditedFiles((prev) =>
      prev.map((f, idx) => {
        if (idx !== index) return f
        if (!result) {
          return {
            ...f,
            meta_id: undefined,
            meta_title: undefined,
            meta_poster: undefined,
            meta_type: undefined,
            isModified: true,
          }
        }
        return {
          ...f,
          meta_id: getBestExternalId(result),
          meta_title: result.title,
          meta_poster: result.poster || undefined,
          meta_type: result.type as 'movie' | 'series',
          isModified: true,
        }
      }),
    )
  }, [])

  // Clear all metadata links
  const clearAllMetadataLinks = useCallback(() => {
    setEditedFiles((prev) =>
      prev.map((f) => ({
        ...f,
        meta_id: undefined,
        meta_title: undefined,
        meta_poster: undefined,
        meta_type: undefined,
        isModified: true,
      })),
    )
  }, [])

  const updateFile = useCallback((index: number, field: keyof EditedFile, value: number | string | null | boolean) => {
    setEditedFiles((prev) =>
      prev.map((f, idx) => {
        if (idx !== index) return f
        const updated = { ...f, [field]: value, isModified: true }
        return updated
      }),
    )
  }, [])

  const toggleFileInclusion = useCallback((index: number) => {
    setEditedFiles((prev) => prev.map((f, idx) => (idx === index ? { ...f, included: !f.included } : f)))
  }, [])

  const toggleAllFiles = useCallback((include: boolean) => {
    setEditedFiles((prev) => prev.map((f) => ({ ...f, included: include })))
  }, [])

  const clearAllEpisodeData = useCallback(() => {
    setEditedFiles((prev) =>
      prev.map((f) => ({
        ...f,
        season_number: null,
        episode_number: null,
        episode_end: null,
        isModified: true,
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
          return {
            ...f,
            season_number: seasonNum,
            isModified: true,
          }
        }),
      )

      setHighlightedIndices(new Set(indicesToHighlight))
      setTimeout(() => setHighlightedIndices(new Set()), 1500)
    },
    [editedFiles],
  )

  // Apply consecutive episode numbering
  const applyEpisodeNumbering = useCallback(
    (startIndex: number) => {
      const startFile = editedFiles[startIndex]
      let episodeCounter = startFile?.episode_number ?? 1
      let lastSeason: number | null = null
      const indicesToHighlight: number[] = []

      setEditedFiles((prev) =>
        prev.map((f, idx) => {
          if (idx < startIndex || !f.included) return f

          // Reset episode counter if season changes
          const currentSeason = f.season_number
          if (idx !== startIndex && lastSeason !== null && currentSeason !== null && currentSeason !== lastSeason) {
            episodeCounter = 1
          }

          indicesToHighlight.push(idx)
          const newEpisodeNumber = episodeCounter++
          if (currentSeason !== null) {
            lastSeason = currentSeason
          }

          return {
            ...f,
            episode_number: newEpisodeNumber,
            isModified: true,
          }
        }),
      )

      setHighlightedIndices(new Set(indicesToHighlight))
      setTimeout(() => setHighlightedIndices(new Set()), 1500)
    },
    [editedFiles],
  )

  const handleConfirm = () => {
    const annotatedFiles: FileAnnotation[] = editedFiles
      .filter((f) => f.included)
      .map((f) => ({
        filename: f.filename,
        size: f.size,
        index: f.index,
        season_number: annotationMode === 'episode' ? f.season_number : null,
        episode_number: annotationMode === 'episode' ? f.episode_number : null,
        episode_end: annotationMode === 'episode' ? f.episode_end : null,
        included: f.included,
        title: isSports ? f.title : undefined,
        overview: isSports ? f.overview : undefined,
        thumbnail: isSports ? f.thumbnail : undefined,
        release_date: isSports ? f.release_date : undefined,
        // Multi-content fields
        meta_id: annotationMode === 'multi-content' ? f.meta_id : undefined,
        meta_title: annotationMode === 'multi-content' ? f.meta_title : undefined,
        meta_poster: annotationMode === 'multi-content' ? f.meta_poster : undefined,
        meta_type: annotationMode === 'multi-content' ? f.meta_type : undefined,
      }))
    onConfirm(annotatedFiles)
  }

  // Count files with metadata links
  const linkedCount = useMemo(() => editedFiles.filter((f) => f.included && f.meta_id).length, [editedFiles])

  const includedCount = editedFiles.filter((f) => f.included).length

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[900px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden">
        <DialogHeader className="px-6 pt-6 pb-4 border-b flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            {annotationMode === 'episode' ? (
              <FileVideo className="h-5 w-5 text-primary" />
            ) : (
              <Layers className="h-5 w-5 text-primary" />
            )}
            {annotationMode === 'episode' ? 'Annotate Episode Files' : 'Multi-Content Mapping'}
          </DialogTitle>
          <DialogDescription className="text-sm">
            {annotationMode === 'episode' ? (
              <>
                Set season and episode numbers for files in{' '}
                <span className="font-medium text-foreground break-all">{torrentName}</span>
              </>
            ) : (
              <>
                Link each file to different metadata in{' '}
                <span className="font-medium text-foreground break-all">{torrentName}</span>
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        {/* Toolbar */}
        <div className="px-6 py-3 border-b bg-muted/30 flex-shrink-0">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              {/* Mode Toggle (if multi-content allowed) */}
              {allowMultiContent && (
                <div className="bg-background/50 p-0.5 rounded-lg flex gap-0.5 mr-2">
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className={cn(
                            'h-7 px-2.5 text-xs',
                            annotationMode === 'episode' && 'bg-primary/20 text-primary',
                          )}
                          onClick={() => setAnnotationMode('episode')}
                        >
                          <FileVideo className="h-3.5 w-3.5 mr-1" />
                          Single Series
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Annotate as episodes of a single series</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className={cn(
                            'h-7 px-2.5 text-xs',
                            annotationMode === 'multi-content' && 'bg-primary/20 text-primary',
                          )}
                          onClick={() => setAnnotationMode('multi-content')}
                        >
                          <Layers className="h-3.5 w-3.5 mr-1" />
                          Multiple Media
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Link each file to a different movie or series</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
              )}

              {/* View Mode Toggle */}
              <div className="bg-background/50 p-0.5 rounded-lg flex gap-0.5">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn('h-7 px-2', viewMode === 'filename' && 'bg-primary/20')}
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
                        className={cn('h-7 px-2', viewMode === 'full' && 'bg-primary/20')}
                        onClick={() => setViewMode('full')}
                      >
                        <FolderTree className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Full path</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>

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

              {/* Clear all button */}
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs text-primary hover:text-primary"
                      onClick={annotationMode === 'episode' ? clearAllEpisodeData : clearAllMetadataLinks}
                    >
                      <Eraser className="h-3.5 w-3.5 mr-1" />
                      Clear All
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {annotationMode === 'episode' ? 'Clear all episode data' : 'Clear all metadata links'}
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
              {annotationMode === 'multi-content' && linkedCount > 0 && (
                <Badge variant="secondary" className="font-normal bg-primary/20 text-primary">
                  <Link2 className="h-3 w-3 mr-1" />
                  {linkedCount} linked
                </Badge>
              )}
            </div>
          </div>
        </div>

        {/* Info Alert */}
        <div className="px-6 py-2 bg-blue-500/5 border-b flex-shrink-0">
          <div className="flex items-start gap-2 text-xs text-blue-400">
            <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
            {annotationMode === 'episode' ? (
              <span>
                Use <kbd className="px-1 py-0.5 rounded bg-blue-500/20 text-[10px]">↓</kbd> to apply season to following
                files, and <kbd className="px-1 py-0.5 rounded bg-blue-500/20 text-[10px]">▶</kbd> for consecutive
                episode numbering.
              </span>
            ) : (
              <span>
                Search and link each file to different movies or series. This is useful for multi-movie collections or
                torrents containing content from different titles.
              </span>
            )}
          </div>
        </div>

        {/* File List */}
        <ScrollArea className="flex-1 min-h-0">
          <div className="px-6 py-4 space-y-2">
            {editedFiles.map((file, index) => {
              const filename = getFilenameOnly(file.filename)
              const folderPath = getFolderPath(file.filename)

              return (
                <div
                  key={file.index}
                  className={cn(
                    'p-3 rounded-lg border transition-all duration-300',
                    !file.included && 'opacity-40 bg-muted/20',
                    file.isModified && file.included && 'border-primary/50 bg-primary/5',
                    highlightedIndices.has(index) && 'ring-2 ring-primary/50',
                    file.included && !file.isModified && 'border-border/40 bg-background/50 hover:border-border',
                  )}
                >
                  {/* Header Row */}
                  <div className="flex items-center gap-2 mb-2">
                    <Switch
                      checked={file.included}
                      onCheckedChange={() => toggleFileInclusion(index)}
                      className="data-[state=checked]:bg-primary scale-90 flex-shrink-0"
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
                  </div>

                  {/* Input Row - Episode Mode */}
                  {annotationMode === 'episode' && (
                    <div className={cn('grid gap-2', isSports ? 'grid-cols-4' : 'grid-cols-3')}>
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
                            updateFile(index, 'season_number', e.target.value ? parseInt(e.target.value) : null)
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
                              <TooltipContent>Apply consecutive episode numbering</TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        </div>
                        <Input
                          type="number"
                          min={0}
                          value={file.episode_number ?? ''}
                          onChange={(e) =>
                            updateFile(index, 'episode_number', e.target.value ? parseInt(e.target.value) : null)
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
                                    If a single file contains multiple episodes (e.g., E01-E03), set Episode End to the
                                    last episode number.
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
                            updateFile(index, 'episode_end', e.target.value ? parseInt(e.target.value) : null)
                          }
                          disabled={!file.included}
                          className="h-8 text-sm"
                          placeholder="Multi"
                        />
                      </div>

                      {/* Sports: Episode Title */}
                      {isSports && (
                        <div className="space-y-1">
                          <Label className="text-[10px] text-muted-foreground">Title</Label>
                          <Input
                            value={file.title ?? ''}
                            onChange={(e) => updateFile(index, 'title', e.target.value || null)}
                            disabled={!file.included}
                            className="h-8 text-sm"
                            placeholder="Episode title"
                          />
                        </div>
                      )}
                    </div>
                  )}

                  {/* Input Row - Multi-Content Mode */}
                  {annotationMode === 'multi-content' && (
                    <div className="space-y-1">
                      <Label className="text-[10px] text-muted-foreground">Link to Metadata</Label>
                      <MetadataSearchPopover
                        value={
                          file.meta_id
                            ? {
                                id: file.meta_id,
                                title: file.meta_title || file.meta_id,
                                poster: file.meta_poster,
                                type: file.meta_type,
                              }
                            : undefined
                        }
                        onSelect={(result) => updateFileMetadata(index, result)}
                        onClear={() => updateFileMetadata(index, null)}
                        disabled={!file.included}
                        metaType={defaultMetaType}
                      />
                    </div>
                  )}

                  {/* Sports extra fields */}
                  {isSports && file.included && (
                    <div className="grid grid-cols-2 gap-2 mt-2 pt-2 border-t border-border/30">
                      <div className="space-y-1">
                        <Label className="text-[10px] text-muted-foreground">Release Date</Label>
                        <Input
                          type="date"
                          value={file.release_date ?? ''}
                          onChange={(e) => updateFile(index, 'release_date', e.target.value || null)}
                          className="h-8 text-sm"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-[10px] text-muted-foreground">Thumbnail URL</Label>
                        <Input
                          value={file.thumbnail ?? ''}
                          onChange={(e) => updateFile(index, 'thumbnail', e.target.value || null)}
                          className="h-8 text-sm"
                          placeholder="Optional thumbnail"
                        />
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </ScrollArea>

        {/* Footer */}
        <DialogFooter className="px-6 py-4 border-t bg-muted/30 flex-shrink-0">
          <div className="flex items-center justify-between w-full">
            <div className="text-sm text-muted-foreground">
              {includedCount > 0 ? (
                <span className="text-primary font-medium">
                  {includedCount} file{includedCount !== 1 ? 's' : ''} will be imported
                </span>
              ) : (
                'No files selected'
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-lg">
                Cancel
              </Button>
              <Button
                onClick={handleConfirm}
                disabled={includedCount === 0 || isLoading}
                className="rounded-lg bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
              >
                {isLoading ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Processing...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-4 w-4 mr-2" />
                    Confirm {includedCount} Files
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
