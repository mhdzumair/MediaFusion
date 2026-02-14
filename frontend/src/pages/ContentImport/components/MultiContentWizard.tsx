import { useState, useCallback, useMemo, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Progress } from '@/components/ui/progress'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  FileVideo,
  Film,
  Loader2,
  Search,
  Tv,
  X,
  Link2,
  AlertCircle,
  Layers,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useDebounce } from '@/hooks/useDebounce'
import { useCombinedMetadataSearch, getBestExternalId, type CombinedSearchResult } from '@/hooks'
import { userMetadataApi, type ImportProvider } from '@/lib/api'
import type { TorrentAnalyzeResponse } from '@/lib/api'
import type { FileAnnotation } from './types'
import type { ImportMode } from '@/lib/constants'

// Provider options for manual ID input
const PROVIDER_OPTIONS: { value: ImportProvider; label: string; placeholder: string; example: string }[] = [
  { value: 'imdb', label: 'IMDB', placeholder: 'tt1234567', example: 'tt0111161' },
  { value: 'tmdb', label: 'TMDB', placeholder: '278', example: '278' },
  { value: 'tvdb', label: 'TVDB', placeholder: '81189', example: '81189' },
  { value: 'mal', label: 'MAL', placeholder: '5114', example: '5114' },
  { value: 'kitsu', label: 'Kitsu', placeholder: '1555', example: '1555' },
]

type WizardStep = 'files' | 'link' | 'review'

interface MultiContentWizardProps {
  analysis: TorrentAnalyzeResponse
  importMode: ImportMode
  onComplete: (annotations: FileAnnotation[]) => void
  onCancel: () => void
  isImporting?: boolean
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

// Parse title from filename using torrent name parsing patterns
function parseFilenameForSearch(filename: string): string {
  const name = getFilenameOnly(filename)
  // Remove extension
  const withoutExt = name.replace(/\.[^/.]+$/, '')
  // Replace dots/underscores/dashes with spaces
  const cleaned = withoutExt.replace(/[._-]/g, ' ')
  
  // Remove common torrent patterns (comprehensive list)
  const simplified = cleaned
    // Year patterns (keep for potential matching but remove from search)
    .replace(/\b(19|20)\d{2}\b/g, '')
    // Resolution
    .replace(/\b(480p|576p|720p|1080p|1080i|2160p|4k|uhd|hd|sd)\b/gi, '')
    // Quality/Source
    .replace(/\b(bluray|blu-ray|bdrip|brrip|webrip|web-dl|webdl|web|hdtv|hdrip|dvdrip|dvdscr|dvd|hdcam|cam|ts|telesync|r5|ppvrip|hdrip|pdtv|dsr|tvrip|satrip|remux)\b/gi, '')
    // Video codecs
    .replace(/\b(x264|x265|h264|h265|hevc|avc|xvid|divx|mpeg|mpeg2|av1|vp9|10bit|10-bit|8bit)\b/gi, '')
    // Audio codecs
    .replace(/\b(aac|ac3|dts|dts-hd|dts-ma|truehd|atmos|flac|mp3|eac3|dd5\.?1|dd7\.?1|7\.1|5\.1|2\.0|stereo|mono|dolby|digital)\b/gi, '')
    // HDR formats
    .replace(/\b(hdr|hdr10|hdr10\+|dolby\s*vision|dv|sdr)\b/gi, '')
    // Release groups (common patterns)
    .replace(/\b(yts|yify|rarbg|sparks|geckos|ntg|flux|tigole|qxr|psa|joy|fgt|ethd|mkvking|mkvcage|pahe|evo|cmrg|stuttershit|amzn|nf|dsnp|hmax|atvp|pcok|hulu|criterion|mubi)\b/gi, '')
    // Scene tags
    .replace(/\b(proper|repack|rerip|real|internal|limited|extended|unrated|directors\s*cut|theatrical|imax|3d|sbs|hou|dubbed|dual\s*audio|multi|subbed|hardcoded|hc)\b/gi, '')
    // Episode patterns (S01E01, etc.)
    .replace(/\bs\d{1,2}e\d{1,2}\b/gi, '')
    .replace(/\bseason\s*\d+\b/gi, '')
    .replace(/\bepisode\s*\d+\b/gi, '')
    .replace(/\bep?\s*\d+\b/gi, '')
    // File size patterns
    .replace(/\b\d+(\.\d+)?\s*(gb|mb|kb)\b/gi, '')
    // Common brackets content
    .replace(/\[.*?\]/g, '')
    .replace(/\(.*?\)/g, '')
    // Clean up extra whitespace
    .replace(/\s+/g, ' ')
    .trim()
  
  return simplified || cleaned
}

interface FileWithMetadata extends FileAnnotation {
  searchQuery?: string
}

// Metadata search popover for linking files - uses combined search (internal + external)
function MetadataSearchPopover({
  value,
  onSelect,
  onClear,
  disabled,
  metaType,
  initialQuery,
}: {
  value?: { id: string; title: string; poster?: string; type?: string }
  onSelect: (result: CombinedSearchResult) => void
  onClear: () => void
  disabled?: boolean
  metaType: 'movie' | 'series'
  initialQuery?: string
}) {
  const [open, setOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState(initialQuery || '')
  const [showManualId, setShowManualId] = useState(false)
  const [manualProvider, setManualProvider] = useState<ImportProvider>('imdb')
  const [manualId, setManualId] = useState('')
  const [isLoadingPreview, setIsLoadingPreview] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const debouncedQuery = useDebounce(searchQuery, 400)

  // Reset search query when initial query changes
  useEffect(() => {
    if (initialQuery && !searchQuery) {
      setSearchQuery(initialQuery)
    }
  }, [initialQuery])

  // Use combined search hook (searches both internal DB and external providers)
  const { data: searchResults = [], isLoading, isFetching } = useCombinedMetadataSearch(
    {
      query: debouncedQuery,
      type: metaType,
      limit: 15,
    },
    { enabled: debouncedQuery.length >= 2 && !showManualId }
  )

  const handleSelect = useCallback((result: CombinedSearchResult) => {
    onSelect(result)
    setOpen(false)
    setShowManualId(false)
  }, [onSelect])

  // Handle manual ID submission - fetches metadata from provider
  const handleManualIdSubmit = useCallback(async () => {
    if (!manualId.trim()) return
    
    setIsLoadingPreview(true)
    setPreviewError(null)
    
    try {
      // Fetch metadata from the selected provider
      const preview = await userMetadataApi.previewImport({
        provider: manualProvider,
        external_id: manualId.trim(),
        media_type: metaType,
      })
      
      // Create result from preview
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
      setPreviewError(error instanceof Error ? error.message : 'Failed to fetch metadata. Check the ID and try again.')
    } finally {
      setIsLoadingPreview(false)
    }
  }, [manualId, manualProvider, metaType, onSelect])

  const currentProviderOption = PROVIDER_OPTIONS.find(p => p.value === manualProvider)

  if (value?.id) {
    return (
      <div className="flex items-center gap-2 p-2 rounded-lg border border-primary/30 bg-primary/5">
        {/* Clear button on the LEFT for visibility */}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 flex-shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10"
          onClick={onClear}
          disabled={disabled}
          title="Remove link"
        >
          <X className="h-4 w-4" />
        </Button>
        {value.poster ? (
          <img
            src={value.poster}
            alt=""
            className="w-10 h-14 rounded object-cover flex-shrink-0"
          />
        ) : (
          <div className="w-10 h-14 rounded bg-muted flex items-center justify-center flex-shrink-0">
            {value.type === 'series' ? (
              <Tv className="h-4 w-4 text-muted-foreground" />
            ) : (
              <Film className="h-4 w-4 text-muted-foreground" />
            )}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">{value.title}</p>
          <Badge variant="outline" className="text-xs mt-0.5">
            {value.type}
          </Badge>
        </div>
      </div>
    )
  }

  return (
    <Popover open={open} onOpenChange={(isOpen) => {
      setOpen(isOpen)
      if (!isOpen) {
        setShowManualId(false)
      }
    }}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          className="w-full justify-start text-muted-foreground h-12"
          disabled={disabled}
        >
          <Search className="h-4 w-4 mr-2" />
          Search and link metadata...
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[350px] p-0" align="start">
        {showManualId ? (
          // Manual ID input mode
          <div className="p-3 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Enter ID Manually</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={() => {
                  setShowManualId(false)
                  setPreviewError(null)
                }}
                disabled={isLoadingPreview}
              >
                Back to Search
              </Button>
            </div>
            
            {/* Provider selector */}
            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">Provider</label>
              <Select value={manualProvider} onValueChange={(v) => setManualProvider(v as ImportProvider)}>
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROVIDER_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            
            {/* ID input */}
            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">External ID</label>
              <Input
                placeholder={currentProviderOption?.placeholder || 'Enter ID'}
                value={manualId}
                onChange={(e) => {
                  setManualId(e.target.value)
                  setPreviewError(null)
                }}
                className="h-9"
                autoFocus
              />
              <p className="text-[10px] text-muted-foreground">
                Example: <code className="bg-muted px-1 rounded">{currentProviderOption?.example}</code>
              </p>
            </div>
            
            {/* Error message */}
            {previewError && (
              <div className="flex items-start gap-2 p-2 rounded bg-destructive/10 text-destructive text-xs">
                <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <span>{previewError}</span>
              </div>
            )}
            
            <Button
              className="w-full"
              size="sm"
              onClick={handleManualIdSubmit}
              disabled={!manualId.trim() || isLoadingPreview}
            >
              {isLoadingPreview ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Fetching metadata...
                </>
              ) : (
                <>
                  <Link2 className="h-4 w-4 mr-2" />
                  Fetch & Link
                </>
              )}
            </Button>
          </div>
        ) : (
          // Search mode
          <>
            <div className="p-3 border-b space-y-2">
              <Input
                placeholder="Search movies or series..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-9"
                autoFocus
              />
              <Button
                variant="ghost"
                size="sm"
                className="w-full h-7 text-xs text-muted-foreground hover:text-foreground"
                onClick={() => setShowManualId(true)}
              >
                <Link2 className="h-3 w-3 mr-1.5" />
                Can't find it? Enter ID manually
              </Button>
            </div>
            <ScrollArea className="max-h-[300px]">
              {isLoading && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              )}
              {!isLoading && !isFetching && debouncedQuery.length >= 2 && searchResults.length === 0 && (
                <div className="py-6 text-center">
                  <p className="text-sm text-muted-foreground mb-2">No results found</p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowManualId(true)}
                  >
                    <Link2 className="h-3 w-3 mr-1.5" />
                    Enter ID manually
                  </Button>
                </div>
              )}
              {!isLoading && debouncedQuery.length < 2 && (
                <div className="py-8 text-center text-sm text-muted-foreground">
                  Type at least 2 characters to search
                </div>
              )}
              {searchResults.length > 0 && (
                <div className="p-2">
                  {/* Show loading indicator at top if still fetching more */}
                  {isFetching && (
                    <div className="flex items-center justify-center py-2 text-xs text-muted-foreground gap-1.5">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      <span>Loading more...</span>
                    </div>
                  )}
                  {searchResults.map((result, idx) => (
                    <button
                      key={result.id || idx}
                      onClick={() => handleSelect(result)}
                      className="w-full flex items-center gap-3 p-2 rounded-lg hover:bg-muted cursor-pointer text-left transition-colors"
                    >
                      {result.poster ? (
                        <img
                          src={result.poster}
                          alt=""
                          className="w-10 h-14 rounded object-cover flex-shrink-0"
                        />
                      ) : (
                        <div className="w-10 h-14 rounded bg-muted flex items-center justify-center flex-shrink-0">
                          {metaType === 'series' ? (
                            <Tv className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <Film className="h-4 w-4 text-muted-foreground" />
                          )}
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{result.title}</p>
                        <div className="flex items-center gap-1.5 text-xs text-muted-foreground mt-0.5">
                          {result.year && <span>{result.year}</span>}
                          {result.imdb_id && (
                            <Badge variant="outline" className="text-[10px] px-1 py-0">
                              {result.imdb_id}
                            </Badge>
                          )}
                          {result.source === 'internal' ? (
                            <Badge variant="secondary" className="text-[10px] px-1 py-0 bg-green-500/20 text-green-700">
                              In Library
                            </Badge>
                          ) : result.provider && (
                            <Badge variant="secondary" className="text-[10px] px-1 py-0">
                              {result.provider.toUpperCase()}
                            </Badge>
                          )}
                        </div>
                      </div>
                      <CheckCircle2 className="h-4 w-4 text-muted-foreground/30" />
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

// File card component for the wizard
function FileCard({
  file,
  index,
  isActive,
  metaType,
  onMetadataSelect,
  onMetadataClear,
  disabled,
}: {
  file: FileWithMetadata
  index: number
  isActive: boolean
  metaType: 'movie' | 'series'
  onMetadataSelect: (index: number, result: CombinedSearchResult) => void
  onMetadataClear: (index: number) => void
  disabled?: boolean
}) {
  const isLinked = !!file.meta_id
  const suggestedSearch = parseFilenameForSearch(file.filename)

  return (
    <Card className={cn(
      "transition-all",
      isActive && "ring-2 ring-primary",
      isLinked && "border-green-500/50 bg-green-500/5"
    )}>
      <CardHeader className="p-4 pb-2">
        <div className="flex items-start gap-3">
          <div className={cn(
            "p-2 rounded-lg flex-shrink-0",
            isLinked ? "bg-green-500/20" : "bg-muted"
          )}>
            <FileVideo className={cn(
              "h-5 w-5",
              isLinked ? "text-green-600" : "text-muted-foreground"
            )} />
          </div>
          <div className="flex-1 min-w-0">
            <CardTitle className="text-sm font-medium truncate">
              {getFilenameOnly(file.filename)}
            </CardTitle>
            <CardDescription className="text-xs mt-0.5">
              {formatFileSize(file.size)}
              {isLinked && (
                <Badge variant="outline" className="ml-2 text-green-600 border-green-500/50">
                  <Check className="h-3 w-3 mr-1" />
                  Linked
                </Badge>
              )}
            </CardDescription>
          </div>
          <Badge variant="outline" className="flex-shrink-0">
            #{index + 1}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        <MetadataSearchPopover
          value={file.meta_id ? {
            id: file.meta_id,
            title: file.meta_title || '',
            poster: file.meta_poster,
            type: file.meta_type,
          } : undefined}
          onSelect={(result) => onMetadataSelect(index, result)}
          onClear={() => onMetadataClear(index)}
          disabled={disabled}
          metaType={metaType}
          initialQuery={suggestedSearch}
        />
      </CardContent>
    </Card>
  )
}

export function MultiContentWizard({
  analysis,
  importMode,
  onComplete,
  onCancel,
  isImporting = false,
}: MultiContentWizardProps) {
  const [currentStep, setCurrentStep] = useState<WizardStep>('files')
  const [files, setFiles] = useState<FileWithMetadata[]>([])
  const [activeFileIndex, setActiveFileIndex] = useState(0)

  // Determine metadata type based on import mode
  const metaType = importMode === 'collection' ? 'movie' : 'series'

  // Initialize files from analysis
  useEffect(() => {
    if (analysis?.files) {
      // Filter to video files only and sort by name
      const videoFiles = analysis.files
        .filter(f => /\.(mkv|mp4|avi|mov|wmv|m4v|webm)$/i.test(f.filename))
        .sort((a, b) => a.filename.localeCompare(b.filename, undefined, { numeric: true }))

      setFiles(videoFiles.map((f, idx) => ({
        filename: f.filename,
        size: f.size,
        index: f.index ?? idx,
        season_number: null,
        episode_number: null,
        included: true,
        searchQuery: parseFilenameForSearch(f.filename),
      })))
    }
  }, [analysis])

  // Handle metadata selection for a file
  const handleMetadataSelect = useCallback((index: number, result: CombinedSearchResult) => {
    setFiles(prev => prev.map((f, i) => {
      if (i !== index) return f
      return {
        ...f,
        // Use getBestExternalId to get the best identifier
        meta_id: getBestExternalId(result),
        meta_title: result.title,
        meta_poster: result.poster,
        meta_type: metaType,
      }
    }))
    // Auto-advance to next unlinked file
    const nextUnlinked = files.findIndex((f, i) => i > index && !f.meta_id)
    if (nextUnlinked !== -1) {
      setActiveFileIndex(nextUnlinked)
    }
  }, [files, metaType])

  // Clear metadata for a file
  const handleMetadataClear = useCallback((index: number) => {
    setFiles(prev => prev.map((f, i) => {
      if (i !== index) return f
      return {
        ...f,
        meta_id: undefined,
        meta_title: undefined,
        meta_poster: undefined,
        meta_type: undefined,
      }
    }))
  }, [])

  // Count linked files
  const linkedCount = useMemo(() => 
    files.filter(f => f.meta_id).length, 
    [files]
  )

  const progress = files.length > 0 ? (linkedCount / files.length) * 100 : 0

  // Check if can proceed to next step
  const canProceed = useMemo(() => {
    if (currentStep === 'files') return files.length > 0
    if (currentStep === 'link') return linkedCount > 0
    return true
  }, [currentStep, files.length, linkedCount])

  // Handle step navigation
  const handleNext = useCallback(() => {
    if (currentStep === 'files') {
      setCurrentStep('link')
    } else if (currentStep === 'link') {
      setCurrentStep('review')
    } else if (currentStep === 'review') {
      // Complete the wizard
      onComplete(files.filter(f => f.meta_id))
    }
  }, [currentStep, files, onComplete])

  const handleBack = useCallback(() => {
    if (currentStep === 'link') {
      setCurrentStep('files')
    } else if (currentStep === 'review') {
      setCurrentStep('link')
    }
  }, [currentStep])

  // Get step number for display
  const stepNumber = currentStep === 'files' ? 1 : currentStep === 'link' ? 2 : 3

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 border-b p-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-primary/10">
              <Layers className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h2 className="text-lg font-semibold">
                {importMode === 'collection' ? 'Movie Collection Import' : 'Series Pack Import'}
              </h2>
              <p className="text-sm text-muted-foreground">
                Link each file to its metadata
              </p>
            </div>
          </div>
          <Badge variant="outline" className="text-sm">
            Step {stepNumber} of 3
          </Badge>
        </div>

        {/* Progress indicator */}
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <Progress value={progress} className="h-2" />
          </div>
          <span className="text-sm text-muted-foreground whitespace-nowrap">
            {linkedCount} / {files.length} linked
          </span>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {/* Step 1: File Overview */}
        {currentStep === 'files' && (
          <div className="h-full flex flex-col p-4">
            <div className="mb-4">
              <h3 className="font-medium">Video Files Found</h3>
              <p className="text-sm text-muted-foreground">
                {files.length} video file{files.length !== 1 ? 's' : ''} detected in this torrent
              </p>
            </div>
            <ScrollArea className="flex-1">
              <div className="space-y-2 pr-4">
                {files.map((file, index) => (
                  <div
                    key={index}
                    className="flex items-center gap-3 p-3 rounded-lg border bg-card"
                  >
                    <FileVideo className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">
                        {getFilenameOnly(file.filename)}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {formatFileSize(file.size)}
                      </p>
                    </div>
                    <Badge variant="outline">#{index + 1}</Badge>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </div>
        )}

        {/* Step 2: Link Files */}
        {currentStep === 'link' && (
          <div className="h-full flex flex-col p-4">
            <div className="mb-4">
              <h3 className="font-medium">Link Files to Metadata</h3>
              <p className="text-sm text-muted-foreground">
                Search and link each file to its corresponding {metaType}
              </p>
            </div>
            <ScrollArea className="flex-1">
              <div className="space-y-3 pr-4">
                {files.map((file, index) => (
                  <FileCard
                    key={index}
                    file={file}
                    index={index}
                    isActive={index === activeFileIndex}
                    metaType={metaType}
                    onMetadataSelect={handleMetadataSelect}
                    onMetadataClear={handleMetadataClear}
                    disabled={isImporting}
                  />
                ))}
              </div>
            </ScrollArea>
          </div>
        )}

        {/* Step 3: Review */}
        {currentStep === 'review' && (
          <div className="h-full flex flex-col p-4">
            <div className="mb-4">
              <h3 className="font-medium">Review Import</h3>
              <p className="text-sm text-muted-foreground">
                Confirm the file-to-metadata mappings before importing
              </p>
            </div>
            <ScrollArea className="flex-1">
              <div className="space-y-3 pr-4">
                {files.filter(f => f.meta_id).map((file) => {
                  // Find the original index in the files array
                  const originalIndex = files.findIndex(f => f.filename === file.filename)
                  return (
                    <div
                      key={file.filename}
                      className="flex items-center gap-4 p-4 rounded-lg border bg-card group"
                    >
                      {/* Remove button on the LEFT */}
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 flex-shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10 opacity-50 group-hover:opacity-100"
                        onClick={() => handleMetadataClear(originalIndex)}
                        disabled={isImporting}
                        title="Remove link"
                      >
                        <X className="h-4 w-4" />
                      </Button>

                      {/* File info */}
                      <div className="flex items-center gap-3 flex-1 min-w-0">
                        <FileVideo className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                        <div className="min-w-0">
                          <p className="text-sm font-medium truncate">
                            {getFilenameOnly(file.filename)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {formatFileSize(file.size)}
                          </p>
                        </div>
                      </div>

                      {/* Arrow */}
                      <Link2 className="h-4 w-4 text-muted-foreground flex-shrink-0" />

                      {/* Linked metadata */}
                      <div className="flex items-center gap-3 flex-1 min-w-0">
                        {file.meta_poster ? (
                          <img
                            src={file.meta_poster}
                            alt=""
                            className="w-10 h-14 rounded object-cover flex-shrink-0"
                          />
                        ) : (
                          <div className="w-10 h-14 rounded bg-muted flex items-center justify-center flex-shrink-0">
                            {file.meta_type === 'series' ? (
                              <Tv className="h-4 w-4 text-muted-foreground" />
                            ) : (
                              <Film className="h-4 w-4 text-muted-foreground" />
                            )}
                          </div>
                        )}
                        <div className="min-w-0">
                          <p className="text-sm font-medium truncate">{file.meta_title}</p>
                          <Badge variant="outline" className="text-xs mt-0.5">
                            {file.meta_type}
                          </Badge>
                        </div>
                      </div>

                      <CheckCircle2 className="h-5 w-5 text-green-500 flex-shrink-0" />
                    </div>
                  )
                })}

                {/* Unlinked files warning */}
                {files.some(f => !f.meta_id) && (
                  <div className="p-4 rounded-lg border border-yellow-500/50 bg-yellow-500/10">
                    <div className="flex items-start gap-3">
                      <AlertCircle className="h-5 w-5 text-yellow-600 flex-shrink-0 mt-0.5" />
                      <div>
                        <p className="text-sm font-medium text-yellow-700">
                          {files.filter(f => !f.meta_id).length} file{files.filter(f => !f.meta_id).length !== 1 ? 's' : ''} not linked
                        </p>
                        <p className="text-xs text-yellow-600 mt-1">
                          These files will be skipped during import. Go back to link them if needed.
                        </p>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </ScrollArea>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex-shrink-0 border-t p-4">
        <div className="flex items-center justify-between">
          <Button
            variant="outline"
            onClick={currentStep === 'files' ? onCancel : handleBack}
            disabled={isImporting}
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            {currentStep === 'files' ? 'Cancel' : 'Back'}
          </Button>

          <Button
            onClick={handleNext}
            disabled={!canProceed || isImporting}
          >
            {isImporting ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Importing...
              </>
            ) : currentStep === 'review' ? (
              <>
                <Check className="h-4 w-4 mr-2" />
                Import {linkedCount} Item{linkedCount !== 1 ? 's' : ''}
              </>
            ) : (
              <>
                Next
                <ArrowRight className="h-4 w-4 ml-2" />
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
