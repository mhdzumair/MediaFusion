import { useState, useMemo, useCallback } from 'react'
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
import {
  Download,
  Loader2,
  Film,
  Tv,
  HardDrive,
  FileVideo,
  Search,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Layers,
  FolderOpen,
} from 'lucide-react'
import { useCombinedMetadataSearch, getBestExternalId, useAdvancedImport, type CombinedSearchResult } from '@/hooks'
import { useDebounce } from '@/hooks/useDebounce'
import { ImportFileAnnotationDialog, MultiContentWizard } from '@/pages/ContentImport/components'
import type { MissingTorrentItem, FileAnnotationData } from '@/lib/api/watchlist'
import type { TorrentFile, TorrentAnalyzeResponse } from '@/lib/api'
import type { FileAnnotation } from '@/pages/ContentImport/components/types'
import type { ImportMode } from '@/lib/constants'
import { cn } from '@/lib/utils'

interface AdvancedImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  torrent: MissingTorrentItem
  provider: string
  profileId?: number
  onSuccess?: () => void
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

export function AdvancedImportDialog({
  open,
  onOpenChange,
  torrent,
  provider,
  profileId,
  onSuccess,
}: AdvancedImportDialogProps) {
  // Form state
  const [contentType, setContentType] = useState<'movie' | 'series'>(
    torrent.parsed_type || 'movie'
  )
  const [importMode, setImportMode] = useState<ImportMode>('single')
  const [searchQuery, setSearchQuery] = useState(torrent.parsed_title || '')
  const [selectedMedia, setSelectedMedia] = useState<CombinedSearchResult | null>(null)
  const [fileAnnotations, setFileAnnotations] = useState<FileAnnotation[]>([])
  const [annotationDialogOpen, setAnnotationDialogOpen] = useState(false)
  
  // Import state
  const [importResult, setImportResult] = useState<{
    status: 'success' | 'failed' | 'skipped'
    message: string
  } | null>(null)

  const debouncedQuery = useDebounce(searchQuery, 300)
  const advancedImport = useAdvancedImport()

  // Check if in multi-content mode
  const isMultiContentMode = importMode === 'collection' || importMode === 'pack'

  // Search for metadata (combined internal + external search)
  const { data: searchResults = [], isLoading: isSearching, isFetching: isFetchingSearch } = useCombinedMetadataSearch(
    {
      query: debouncedQuery,
      type: contentType,
      limit: 15,
    },
    { enabled: debouncedQuery.length >= 2 && !isMultiContentMode }
  )

  // Convert torrent files to TorrentFile format for annotation dialog
  const torrentFiles: TorrentFile[] = useMemo(() => {
    const videoExtensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v']
    return torrent.files
      .filter(f => videoExtensions.some(ext => f.path.toLowerCase().endsWith(ext)))
      .map((f, idx) => ({
        filename: f.path.split('/').pop() || f.path,
        size: f.size,
        index: idx,
      }))
  }, [torrent.files])

  // Create a mock analysis object for the MultiContentWizard
  const mockAnalysis: TorrentAnalyzeResponse = useMemo(() => ({
    status: 'success' as const,
    torrent_name: torrent.name,
    parsed_title: torrent.parsed_title,
    year: torrent.parsed_year,
    info_hash: torrent.info_hash,
    total_size: torrent.size,
    total_size_readable: formatBytes(torrent.size),
    file_count: torrent.files.length,
    files: torrentFiles,
    matches: [],
  }), [torrent, torrentFiles])

  const handleSelectMedia = useCallback((result: CombinedSearchResult) => {
    setSelectedMedia(result)
    setImportResult(null)
  }, [])

  const handleAnnotationConfirm = useCallback((annotations: FileAnnotation[]) => {
    setFileAnnotations(annotations)
    setAnnotationDialogOpen(false)
  }, [])

  // Handle multi-content wizard completion
  const handleMultiContentComplete = useCallback(async (annotations: FileAnnotation[]) => {
    // Build file_data from multi-content annotations
    const fileData: FileAnnotationData[] = annotations.map(f => ({
      filename: f.filename,
      size: f.size,
      index: f.index,
      season_number: f.season_number,
      episode_number: f.episode_number,
      episode_end: f.episode_end,
      included: true,
      meta_id: f.meta_id,
      meta_title: f.meta_title,
      meta_type: f.meta_type,
    }))

    try {
      const result = await advancedImport.mutateAsync({
        provider,
        profileId,
        imports: [{
          info_hash: torrent.info_hash,
          meta_type: contentType,
          // For multi-content, use the first file's meta_id as the primary
          meta_id: annotations[0]?.meta_id || '',
          title: torrent.parsed_title,
          file_data: fileData,
        }],
      })

      const detail = result.details[0]
      if (detail) {
        setImportResult({
          status: detail.status as 'success' | 'failed' | 'skipped',
          message: detail.message || (detail.status === 'success' ? 'Import successful' : 'Import failed'),
        })

        if (detail.status === 'success') {
          onSuccess?.()
        }
      }
    } catch (error) {
      setImportResult({
        status: 'failed',
        message: error instanceof Error ? error.message : 'Import failed',
      })
    }
  }, [torrent.info_hash, torrent.parsed_title, contentType, provider, profileId, advancedImport, onSuccess])

  const handleImport = useCallback(async () => {
    if (!selectedMedia) return

    // Build file_data from annotations or use defaults
    const fileData: FileAnnotationData[] = fileAnnotations.length > 0
      ? fileAnnotations
          .filter(f => f.included)
          .map(f => ({
            filename: f.filename,
            size: f.size,
            index: f.index,
            season_number: f.season_number,
            episode_number: f.episode_number,
            episode_end: f.episode_end,
            included: f.included,
            meta_id: f.meta_id,
            meta_title: f.meta_title,
            meta_type: f.meta_type,
          }))
      : torrentFiles.map(f => ({
          filename: f.filename,
          size: f.size,
          index: f.index,
          included: true,
        }))

    try {
      const result = await advancedImport.mutateAsync({
        provider,
        profileId,
        imports: [{
          info_hash: torrent.info_hash,
          meta_type: contentType,
          meta_id: getBestExternalId(selectedMedia),
          title: selectedMedia.title,
          file_data: fileData.length > 0 ? fileData : undefined,
        }],
      })

      const detail = result.details[0]
      if (detail) {
        setImportResult({
          status: detail.status as 'success' | 'failed' | 'skipped',
          message: detail.message || (detail.status === 'success' ? 'Import successful' : 'Import failed'),
        })

        if (detail.status === 'success') {
          onSuccess?.()
        }
      }
    } catch (error) {
      setImportResult({
        status: 'failed',
        message: error instanceof Error ? error.message : 'Import failed',
      })
    }
  }, [selectedMedia, fileAnnotations, torrentFiles, torrent.info_hash, contentType, provider, profileId, advancedImport, onSuccess])

  const handleClose = useCallback(() => {
    onOpenChange(false)
    // Reset state after close
    setTimeout(() => {
      setSearchQuery(torrent.parsed_title || '')
      setSelectedMedia(null)
      setFileAnnotations([])
      setImportResult(null)
      setImportMode('single')
    }, 200)
  }, [onOpenChange, torrent.parsed_title])

  // Import mode options based on content type
  const importModeOptions = contentType === 'movie'
    ? [
        { value: 'single', label: 'Single Movie', icon: Film, description: 'One movie file' },
        { value: 'collection', label: 'Movie Collection', icon: Layers, description: 'Multiple movies' },
      ]
    : [
        { value: 'single', label: 'Single Series', icon: Tv, description: 'Episodes of one show' },
        { value: 'pack', label: 'Series Pack', icon: FolderOpen, description: 'Multiple series' },
      ]

  return (
    <>
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent className={cn(
          "flex flex-col overflow-hidden",
          isMultiContentMode ? "max-w-4xl h-[85vh] p-0 gap-0" : "max-w-2xl max-h-[85vh]"
        )}>
          {isMultiContentMode ? (
            // Multi-content wizard mode
            <MultiContentWizard
              analysis={mockAnalysis}
              importMode={importMode}
              onComplete={handleMultiContentComplete}
              onCancel={handleClose}
              isImporting={advancedImport.isPending}
            />
          ) : (
            // Single content mode
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  <Download className="h-5 w-5" />
                  Advanced Import
                </DialogTitle>
                <DialogDescription>
                  Import with full metadata control and file annotation support.
                </DialogDescription>
              </DialogHeader>

              <div className="flex-1 overflow-hidden space-y-4">
                {/* Torrent Info */}
                <div className="p-3 rounded-lg border bg-muted/30 space-y-2">
                  <p className="text-sm font-medium truncate" title={torrent.name}>
                    {torrent.name}
                  </p>
                  <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <HardDrive className="h-3 w-3" />
                      {formatBytes(torrent.size)}
                    </span>
                    <span className="flex items-center gap-1">
                      <FileVideo className="h-3 w-3" />
                      {torrentFiles.length} video{torrentFiles.length !== 1 ? 's' : ''}
                    </span>
                  </div>
                </div>

                {/* Import Result */}
                {importResult && (
                  <div className={cn(
                    "p-3 rounded-lg border flex items-center gap-2",
                    importResult.status === 'success' && "bg-green-500/10 border-green-500/30",
                    importResult.status === 'failed' && "bg-red-500/10 border-red-500/30",
                    importResult.status === 'skipped' && "bg-yellow-500/10 border-yellow-500/30"
                  )}>
                    {importResult.status === 'success' && <CheckCircle2 className="h-4 w-4 text-green-500" />}
                    {importResult.status === 'failed' && <XCircle className="h-4 w-4 text-red-500" />}
                    {importResult.status === 'skipped' && <AlertCircle className="h-4 w-4 text-yellow-500" />}
                    <span className="text-sm">{importResult.message}</span>
                  </div>
                )}

                {/* Content Type & Import Mode */}
                <div className="space-y-3">
                  <Label>Content Type & Import Mode</Label>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setContentType('movie')
                        setImportMode('single')
                        setSelectedMedia(null)
                      }}
                      className={cn(
                        "p-3 rounded-lg border-2 text-left transition-all",
                        contentType === 'movie'
                          ? "border-primary bg-primary/10"
                          : "border-border/50 hover:border-primary/30"
                      )}
                    >
                      <Film className={cn(
                        "h-4 w-4 mb-1",
                        contentType === 'movie' ? "text-primary" : "text-muted-foreground"
                      )} />
                      <p className={cn(
                        "text-sm font-medium",
                        contentType === 'movie' && "text-primary"
                      )}>Movie</p>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setContentType('series')
                        setImportMode('single')
                        setSelectedMedia(null)
                      }}
                      className={cn(
                        "p-3 rounded-lg border-2 text-left transition-all",
                        contentType === 'series'
                          ? "border-primary bg-primary/10"
                          : "border-border/50 hover:border-primary/30"
                      )}
                    >
                      <Tv className={cn(
                        "h-4 w-4 mb-1",
                        contentType === 'series' ? "text-primary" : "text-muted-foreground"
                      )} />
                      <p className={cn(
                        "text-sm font-medium",
                        contentType === 'series' && "text-primary"
                      )}>Series</p>
                    </button>
                  </div>
                  
                  {/* Import Mode Sub-options */}
                  <div className="grid grid-cols-2 gap-2">
                    {importModeOptions.map((option) => {
                      const Icon = option.icon
                      const isSelected = importMode === option.value
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => setImportMode(option.value as ImportMode)}
                          className={cn(
                            "p-2 rounded-lg border text-left transition-all flex items-center gap-2",
                            isSelected
                              ? "border-primary/50 bg-primary/5"
                              : "border-border/30 hover:border-primary/30"
                          )}
                        >
                          <Icon className={cn(
                            "h-4 w-4",
                            isSelected ? "text-primary" : "text-muted-foreground"
                          )} />
                          <div>
                            <p className={cn(
                              "text-xs font-medium",
                              isSelected && "text-primary"
                            )}>{option.label}</p>
                            <p className="text-[10px] text-muted-foreground">{option.description}</p>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>

                {/* Metadata Search */}
                <div className="space-y-2">
                  <Label>Search Metadata</Label>
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Search for title..."
                      className="pl-9"
                    />
                  </div>
                </div>

                {/* Search Results */}
                {(isSearching || searchResults.length > 0) && (
                  <div className="space-y-2">
                    <Label className="text-xs text-muted-foreground flex items-center gap-2">
                      {searchResults.length} results
                      {isFetchingSearch && (
                        <span className="flex items-center gap-1 text-muted-foreground/70">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          loading more...
                        </span>
                      )}
                    </Label>
                    <ScrollArea className="h-[180px] border rounded-lg">
                      <div className="p-2 space-y-1">
                        {isSearching && searchResults.length === 0 ? (
                          <div className="flex items-center justify-center py-8">
                            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                          </div>
                        ) : (
                          searchResults.map((result) => (
                            <button
                              key={result.id}
                              type="button"
                              className={cn(
                                "w-full flex items-center gap-3 p-2 rounded-md text-left transition-colors",
                                selectedMedia?.id === result.id
                                  ? "bg-primary/20 border border-primary"
                                  : "hover:bg-muted"
                              )}
                              onClick={() => handleSelectMedia(result)}
                            >
                              {result.poster ? (
                                <img
                                  src={result.poster}
                                  alt={result.title}
                                  className="w-10 h-14 object-cover rounded"
                                />
                              ) : (
                                <div className="w-10 h-14 bg-muted rounded flex items-center justify-center">
                                  {contentType === 'movie' ? (
                                    <Film className="h-4 w-4 text-muted-foreground" />
                                  ) : (
                                    <Tv className="h-4 w-4 text-muted-foreground" />
                                  )}
                                </div>
                              )}
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium truncate">{result.title}</p>
                                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                  {result.year && <span>{result.year}</span>}
                                  {result.imdb_id && <span>• {result.imdb_id}</span>}
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
                              {selectedMedia?.id === result.id && (
                                <CheckCircle2 className="h-4 w-4 text-primary flex-shrink-0" />
                              )}
                            </button>
                          ))
                        )}
                      </div>
                    </ScrollArea>
                  </div>
                )}

                {/* Selected Media */}
                {selectedMedia && (
                  <div className="p-3 rounded-lg border border-primary bg-primary/5">
                    <div className="flex items-center gap-3">
                      {selectedMedia.poster ? (
                        <img
                          src={selectedMedia.poster}
                          alt={selectedMedia.title}
                          className="w-12 h-16 object-cover rounded"
                        />
                      ) : (
                        <div className="w-12 h-16 bg-muted rounded flex items-center justify-center">
                          {contentType === 'movie' ? (
                            <Film className="h-5 w-5 text-muted-foreground" />
                          ) : (
                            <Tv className="h-5 w-5 text-muted-foreground" />
                          )}
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="font-medium">{selectedMedia.title}</p>
                        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                          {selectedMedia.year && <span>{selectedMedia.year}</span>}
                          {selectedMedia.imdb_id && <span>• {selectedMedia.imdb_id}</span>}
                          {selectedMedia.source === 'internal' ? (
                            <Badge variant="secondary" className="text-[10px] px-1 py-0 bg-green-500/20 text-green-700">
                              In Library
                            </Badge>
                          ) : selectedMedia.provider && (
                            <Badge variant="secondary" className="text-[10px] px-1 py-0">
                              {selectedMedia.provider.toUpperCase()}
                            </Badge>
                          )}
                        </div>
                      </div>
                      <Badge variant="outline" className="text-primary border-primary">
                        Selected
                      </Badge>
                    </div>
                  </div>
                )}

                {/* File Annotation Button */}
                {torrentFiles.length > 1 && (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => setAnnotationDialogOpen(true)}
                  >
                    <FileVideo className="h-4 w-4 mr-2" />
                    Annotate Files ({fileAnnotations.length || torrentFiles.length})
                    {fileAnnotations.length > 0 && (
                      <Badge variant="secondary" className="ml-2">
                        Configured
                      </Badge>
                    )}
                  </Button>
                )}
              </div>

              <DialogFooter>
                <Button variant="outline" onClick={handleClose}>
                  {importResult?.status === 'success' ? 'Done' : 'Cancel'}
                </Button>
                {importResult?.status !== 'success' && (
                  <Button
                    onClick={handleImport}
                    disabled={!selectedMedia || advancedImport.isPending}
                  >
                    {advancedImport.isPending ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Importing...
                      </>
                    ) : (
                      <>
                        <Download className="mr-2 h-4 w-4" />
                        Import
                      </>
                    )}
                  </Button>
                )}
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* File Annotation Dialog */}
      <ImportFileAnnotationDialog
        open={annotationDialogOpen}
        onOpenChange={setAnnotationDialogOpen}
        torrentName={torrent.name}
        files={torrentFiles}
        onConfirm={handleAnnotationConfirm}
        allowMultiContent={true}
        defaultMetaType={contentType}
      />
    </>
  )
}
