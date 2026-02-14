import { useState, useCallback, useMemo } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import {
  Loader2,
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  Search,
  HardDrive,
  Image as ImageIcon,
  Link2,
  AlertTriangle,
  Calendar,
  FileText,
  FileVideo,
  Settings2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TorrentAnalyzeResponse, ImportResponse } from '@/lib/api'
import type { ContentType, SportsCategory, ImportMode } from '@/lib/constants'
import { ContentTypeSelector } from './ContentTypeSelector'
import { TechSpecsEditor } from './TechSpecsEditor'
import { MatchResultsGrid, type ExtendedMatch } from './MatchResultsGrid'
import { CatalogSelector } from './CatalogSelector'
import { ImportFileAnnotationDialog } from './ImportFileAnnotationDialog'
import { ValidationWarningDialog } from './ValidationWarningDialog'
import { MultiContentWizard } from './MultiContentWizard'
import type { FileAnnotation, TorrentImportFormData } from './types'
import { useAuth } from '@/contexts/AuthContext'

type ImportStep = 'review' | 'metadata' | 'confirm'

interface TorrentImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  analysis: TorrentAnalyzeResponse | null
  magnetLink?: string // Not directly used in component but kept for parent context
  torrentFile?: File | null // Not directly used in component but kept for parent context
  onImport: (formData: TorrentImportFormData) => Promise<ImportResponse>
  onReanalyze?: (contentType: ContentType) => void
  isImporting?: boolean
  initialContentType?: ContentType // Content type selected before analysis
  importMode?: ImportMode // Import mode for multi-content support
  onImportModeChange?: (mode: ImportMode) => void
}

export function TorrentImportDialog({
  open,
  onOpenChange,
  analysis,
  magnetLink: _magnetLink,
  initialContentType = 'movie',
  torrentFile: _torrentFile,
  onImport,
  onReanalyze,
  isImporting = false,
  importMode = 'single',
  onImportModeChange,
}: TorrentImportDialogProps) {
  const { user } = useAuth()
  // Note: magnetLink and torrentFile props are kept for parent context but handled there
  void _magnetLink
  void _torrentFile

  // Check if we're in multi-content mode
  const isMultiContentMode = importMode === 'collection' || importMode === 'pack'

  // Step management
  const [currentStep, setCurrentStep] = useState<ImportStep>('review')

  // Form state - initialize contentType from initial prop
  const [contentType, setContentType] = useState<ContentType>(initialContentType)
  const [sportsCategory, setSportsCategory] = useState<SportsCategory | undefined>()
  const [selectedMatchIndex, setSelectedMatchIndex] = useState<number | null>(null)

  // Metadata
  const [metaId, setMetaId] = useState('')
  const [title, setTitle] = useState('')
  const [poster, setPoster] = useState('')
  const [background, setBackground] = useState('')
  const [releaseDate, setReleaseDate] = useState('')

  // Tech specs
  const [resolution, setResolution] = useState<string | undefined>()
  const [quality, setQuality] = useState<string | undefined>()
  const [codec, setCodec] = useState<string | undefined>()
  const [audio, setAudio] = useState<string[]>([])
  const [hdr, setHdr] = useState<string[]>([])
  const [languages, setLanguages] = useState<string[]>([])

  // Catalogs
  const [selectedCatalogs, setSelectedCatalogs] = useState<string[]>([])

  // Series/Sports specific
  const [episodeParser, setEpisodeParser] = useState('')
  const [fileAnnotations, setFileAnnotations] = useState<FileAnnotation[]>([])

  // Import options
  const [forceImport, setForceImport] = useState(false)
  const [addTitleToPoster, setAddTitleToPoster] = useState(false)
  const [isAnonymous, setIsAnonymous] = useState(user?.contribute_anonymously ?? false)

  // Dialog states
  const [annotationDialogOpen, setAnnotationDialogOpen] = useState(false)
  const [validationDialogOpen, setValidationDialogOpen] = useState(false)
  const [validationErrors, setValidationErrors] = useState<Array<{ type: string; message: string }>>([])

  // Derive selected match from index
  const selectedMatch = useMemo(() => {
    if (selectedMatchIndex === null || !analysis?.matches) return null
    return analysis.matches[selectedMatchIndex] as ExtendedMatch | null
  }, [selectedMatchIndex, analysis?.matches])

  // Initialize from analysis when dialog opens (during render, not in effect)
  const [prevOpen, setPrevOpen] = useState(open)
  const [prevAnalysis, setPrevAnalysis] = useState(analysis)
  if (analysis && open && (!prevOpen || prevAnalysis !== analysis)) {
    setPrevOpen(open)
    setPrevAnalysis(analysis)
    setContentType(initialContentType)
    setResolution(analysis.resolution)
    setQuality(analysis.quality)
    setCodec(analysis.codec)
    setAudio(analysis.audio || [])
    setHdr(analysis.hdr || [])
    setLanguages(analysis.languages || [])
    setTitle(analysis.parsed_title || analysis.torrent_name || '')

    if (analysis.matches && analysis.matches.length > 0) {
      const firstMatch = analysis.matches[0] as ExtendedMatch
      setSelectedMatchIndex(0)
      setMetaId(firstMatch.imdb_id || firstMatch.id)
      setTitle(firstMatch.title)
      if (firstMatch.poster) setPoster(firstMatch.poster)
      if (firstMatch.background) setBackground(firstMatch.background)
      if (firstMatch.release_date) setReleaseDate(firstMatch.release_date)
      if (firstMatch.type) setContentType(firstMatch.type as ContentType)
    } else {
      setSelectedMatchIndex(null)
    }

    setCurrentStep('review')
  }

  // Handle match selection
  const handleMatchSelect = useCallback((match: ExtendedMatch, index: number) => {
    setSelectedMatchIndex(index)
    setMetaId(match.imdb_id || match.id)
    setTitle(match.title)
    if (match.poster) setPoster(match.poster)
    if (match.background) setBackground(match.background)
    // Set release date if available
    if (match.release_date) setReleaseDate(match.release_date)
    // Update content type from match
    if (match.type) {
      setContentType(match.type as ContentType)
    }
  }, [])

  // Handle tech spec changes
  const handleTechSpecChange = useCallback((field: string, value: string | string[] | undefined) => {
    switch (field) {
      case 'resolution':
        setResolution(value as string | undefined)
        break
      case 'quality':
        setQuality(value as string | undefined)
        break
      case 'codec':
        setCodec(value as string | undefined)
        break
      case 'audio':
        setAudio((value as string[]) ?? [])
        break
      case 'hdr':
        setHdr((value as string[]) ?? [])
        break
      case 'languages':
        setLanguages((value as string[]) ?? [])
        break
    }
  }, [])

  // Navigate steps
  const goToStep = useCallback((step: ImportStep) => {
    setCurrentStep(step)
  }, [])

  const goBack = useCallback(() => {
    if (currentStep === 'metadata') {
      goToStep('review')
    } else if (currentStep === 'confirm') {
      goToStep('metadata')
    }
  }, [currentStep, goToStep])

  const goForward = useCallback(() => {
    if (currentStep === 'review') {
      goToStep('metadata')
    } else if (currentStep === 'metadata') {
      goToStep('confirm')
    }
  }, [currentStep, goToStep])

  // Handle file annotation confirm
  const handleAnnotationConfirm = useCallback((files: FileAnnotation[]) => {
    setFileAnnotations(files)
    setAnnotationDialogOpen(false)
  }, [])

  // Build import form data
  const buildFormData = useCallback((): TorrentImportFormData => {
    return {
      contentType,
      sportsCategory,
      metaId: metaId || undefined,
      title: title || undefined,
      poster: poster || undefined,
      background: background || undefined,
      resolution: resolution || undefined,
      quality: quality || undefined,
      codec: codec || undefined,
      audio: audio.length > 0 ? audio : undefined,
      hdr: hdr.length > 0 ? hdr : undefined,
      languages: languages.length > 0 ? languages : undefined,
      catalogs: selectedCatalogs.length > 0 ? selectedCatalogs : undefined,
      episodeNameParser: episodeParser || undefined,
      releaseDate: releaseDate || undefined,
      forceImport,
      isAnonymous,
      fileData: fileAnnotations.length > 0 ? fileAnnotations : undefined,
    }
  }, [
    contentType,
    sportsCategory,
    metaId,
    title,
    poster,
    background,
    resolution,
    quality,
    codec,
    audio,
    hdr,
    languages,
    selectedCatalogs,
    episodeParser,
    releaseDate,
    forceImport,
    isAnonymous,
    fileAnnotations,
  ])

  // Handle import
  const handleImport = useCallback(async () => {
    const formData = buildFormData()

    try {
      const result = await onImport(formData)

      if (result.status === 'validation_failed') {
        // Show validation warning dialog
        setValidationErrors(result.errors || [{ type: 'unknown', message: result.message }])
        setValidationDialogOpen(true)
      } else if (result.status === 'needs_annotation') {
        // Open annotation dialog
        setAnnotationDialogOpen(true)
      }
      // Success/error handled by parent
    } catch (error) {
      console.error('Import failed:', error)
    }
  }, [buildFormData, onImport])

  // Handle force import from validation dialog
  const handleForceImport = useCallback(async () => {
    setValidationDialogOpen(false)
    setForceImport(true)
    // Trigger import with force flag
    const formData = { ...buildFormData(), forceImport: true }
    await onImport(formData)
  }, [buildFormData, onImport])

  // Handle re-analyze from validation dialog
  const handleReanalyze = useCallback(() => {
    setValidationDialogOpen(false)
    if (onReanalyze) {
      onReanalyze(contentType)
    }
  }, [contentType, onReanalyze])

  // Handle multi-content wizard completion
  const handleMultiContentComplete = useCallback(
    async (annotations: FileAnnotation[]) => {
      // Build form data with multi-content annotations
      const formData: TorrentImportFormData = {
        contentType,
        sportsCategory,
        // For multi-content, we don't set a single metaId - each file has its own
        title: title || analysis?.parsed_title || undefined,
        poster: poster || undefined,
        background: background || undefined,
        resolution: resolution || undefined,
        quality: quality || undefined,
        codec: codec || undefined,
        audio: audio.length > 0 ? audio : undefined,
        hdr: hdr.length > 0 ? hdr : undefined,
        languages: languages.length > 0 ? languages : undefined,
        catalogs: selectedCatalogs.length > 0 ? selectedCatalogs : undefined,
        forceImport,
        isAnonymous,
        fileData: annotations,
      }

      try {
        await onImport(formData)
      } catch (error) {
        console.error('Multi-content import failed:', error)
      }
    },
    [
      contentType,
      sportsCategory,
      title,
      poster,
      background,
      resolution,
      quality,
      codec,
      audio,
      hdr,
      languages,
      selectedCatalogs,
      forceImport,
      isAnonymous,
      onImport,
      analysis,
    ],
  )

  // Check if series/sports needs annotation
  const needsAnnotation = useMemo(() => {
    return (contentType === 'series' || contentType === 'sports') && analysis?.files && analysis.files.length > 1
  }, [contentType, analysis])

  // Step indicator
  const steps = [
    { id: 'review', label: 'Review', icon: Search },
    { id: 'metadata', label: 'Metadata', icon: Settings2 },
    { id: 'confirm', label: 'Confirm', icon: CheckCircle },
  ]

  if (!analysis) return null

  // If in multi-content mode, show the wizard instead
  if (isMultiContentMode) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[900px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden">
          <MultiContentWizard
            analysis={analysis}
            importMode={importMode}
            onComplete={handleMultiContentComplete}
            onCancel={() => onOpenChange(false)}
            isImporting={isImporting}
          />
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[900px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden">
          {/* Header */}
          <DialogHeader className="px-6 pt-6 pb-4 border-b flex-shrink-0">
            <DialogTitle className="flex items-center gap-2">
              <HardDrive className="h-5 w-5 text-primary" />
              Import Torrent
            </DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-1">
                <p className="font-mono text-xs bg-muted/50 p-2 rounded-md break-all text-foreground/80">
                  {analysis.torrent_name || 'Unknown torrent'}
                </p>
                {analysis.parsed_title && analysis.parsed_title !== analysis.torrent_name && (
                  <p className="text-xs text-muted-foreground">
                    Detected: <span className="font-medium">{analysis.parsed_title}</span>
                    {analysis.year && ` (${analysis.year})`}
                  </p>
                )}
              </div>
            </DialogDescription>
          </DialogHeader>

          {/* Step Indicator */}
          <div className="px-6 py-3 border-b bg-muted/30 flex-shrink-0">
            <div className="flex items-center justify-center gap-2">
              {steps.map((step, index) => {
                const Icon = step.icon
                const isActive = currentStep === step.id
                const isPast = steps.findIndex((s) => s.id === currentStep) > index

                return (
                  <div key={step.id} className="flex items-center">
                    {index > 0 && (
                      <div className={cn('w-8 h-0.5 mx-1', isPast ? 'bg-primary' : 'bg-muted-foreground/20')} />
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      className={cn('gap-1.5 h-8', isActive && 'bg-primary/10 text-primary', isPast && 'text-primary')}
                      onClick={() => goToStep(step.id as ImportStep)}
                    >
                      <Icon className={cn('h-3.5 w-3.5', isPast && 'text-primary')} />
                      <span className="text-xs">{step.label}</span>
                    </Button>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Content */}
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-6">
              {/* Step 1: Review */}
              {currentStep === 'review' && (
                <div className="space-y-6">
                  {/* Torrent Info Summary */}
                  <div className="p-4 rounded-xl bg-muted/50">
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                      <div>
                        <Label className="text-xs text-muted-foreground">Size</Label>
                        <p className="font-medium">{analysis.total_size_readable || 'Unknown'}</p>
                      </div>
                      <div>
                        <Label className="text-xs text-muted-foreground">Files</Label>
                        <p className="font-medium">{analysis.file_count || 0}</p>
                      </div>
                      <div>
                        <Label className="text-xs text-muted-foreground">Quality</Label>
                        <p className="font-medium">
                          {[analysis.resolution, analysis.quality].filter(Boolean).join(' ') || 'Unknown'}
                        </p>
                      </div>
                      <div>
                        <Label className="text-xs text-muted-foreground">Hash</Label>
                        <p className="font-mono text-xs truncate">{analysis.info_hash || 'N/A'}</p>
                      </div>
                    </div>
                  </div>

                  {/* Content Type Selection */}
                  <ContentTypeSelector
                    value={contentType}
                    sportsCategory={sportsCategory}
                    importMode={importMode}
                    onChange={(newType) => {
                      setContentType(newType)
                      // Clear selected match when content type changes
                      setSelectedMatchIndex(null)
                      setMetaId('')
                      setPoster('')
                      setBackground('')
                      // Reset import mode when content type changes
                      if (onImportModeChange) {
                        onImportModeChange('single')
                      }
                      // Trigger re-analysis with new content type
                      if (onReanalyze && newType !== contentType) {
                        onReanalyze(newType)
                      }
                    }}
                    onSportsCategoryChange={setSportsCategory}
                    onImportModeChange={onImportModeChange}
                    showImportMode={contentType !== 'sports'}
                  />

                  {/* Match Results */}
                  {analysis.matches && analysis.matches.length > 0 && (
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <Label className="text-sm font-medium">Matched Content ({analysis.matches.length})</Label>
                        {selectedMatch && (
                          <Badge variant="secondary" className="text-xs">
                            <CheckCircle className="h-3 w-3 mr-1" />
                            {selectedMatch.title}
                          </Badge>
                        )}
                      </div>
                      <MatchResultsGrid
                        matches={analysis.matches as ExtendedMatch[]}
                        selectedIndex={selectedMatchIndex}
                        onSelectMatch={handleMatchSelect}
                        className="h-[250px]"
                      />
                    </div>
                  )}

                  {/* Manual IMDb ID Input */}
                  {(!analysis.matches || analysis.matches.length === 0) && (
                    <div className="p-4 rounded-xl bg-primary/10 border border-primary/20">
                      <div className="flex items-start gap-3">
                        <AlertTriangle className="h-5 w-5 text-primary mt-0.5" />
                        <div className="space-y-2 flex-1">
                          <p className="font-medium text-primary">No matches found</p>
                          <p className="text-sm text-muted-foreground">
                            Please enter the IMDb ID manually to continue.
                          </p>
                          <div className="flex gap-2">
                            <Input
                              placeholder="tt1234567"
                              value={metaId}
                              onChange={(e) => setMetaId(e.target.value)}
                              className="max-w-xs"
                            />
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Step 2: Metadata */}
              {currentStep === 'metadata' && (
                <div className="space-y-6">
                  {/* Basic Metadata */}
                  <div className="space-y-4">
                    <Label className="text-sm font-medium">Basic Information</Label>
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
                          <Link2 className="h-3 w-3" />
                          IMDb/Meta ID
                        </Label>
                        <Input
                          value={metaId}
                          onChange={(e) => setMetaId(e.target.value)}
                          placeholder="tt1234567"
                          className="rounded-lg"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
                          <FileText className="h-3 w-3" />
                          Title
                        </Label>
                        <Input
                          value={title}
                          onChange={(e) => setTitle(e.target.value)}
                          placeholder="Movie/Series title"
                          className="rounded-lg"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
                          <ImageIcon className="h-3 w-3" />
                          Poster URL
                        </Label>
                        <Input
                          value={poster}
                          onChange={(e) => setPoster(e.target.value)}
                          placeholder="https://..."
                          className="rounded-lg"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
                          <Calendar className="h-3 w-3" />
                          Release Date
                        </Label>
                        <Input
                          type="date"
                          value={releaseDate}
                          onChange={(e) => setReleaseDate(e.target.value)}
                          className="rounded-lg"
                        />
                      </div>
                    </div>
                  </div>

                  {/* Technical Specs */}
                  <div className="space-y-3">
                    <Label className="text-sm font-medium">Technical Specifications</Label>
                    <TechSpecsEditor
                      resolution={resolution}
                      quality={quality}
                      codec={codec}
                      audio={audio}
                      hdr={hdr}
                      languages={languages}
                      availableLanguages={analysis.languages || []}
                      onChange={handleTechSpecChange}
                    />
                  </div>

                  {/* Catalogs */}
                  <CatalogSelector
                    contentType={contentType}
                    selectedCatalogs={selectedCatalogs}
                    onChange={setSelectedCatalogs}
                    quality={quality}
                  />

                  {/* Series/Sports Options */}
                  {(contentType === 'series' || contentType === 'sports') && (
                    <div className="space-y-4 pt-4 border-t">
                      <Label className="text-sm font-medium">Episode Options</Label>

                      {/* Episode Parser */}
                      <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground">Episode Name Parser (regex pattern)</Label>
                        <Input
                          value={episodeParser}
                          onChange={(e) => setEpisodeParser(e.target.value)}
                          placeholder="Optional: S(\d+)E(\d+)"
                          className="rounded-lg font-mono text-sm"
                        />
                        <p className="text-xs text-muted-foreground">
                          Leave empty to use default parser or annotate files manually
                        </p>
                      </div>

                      {/* File Annotation Button */}
                      {needsAnnotation && (
                        <Button variant="outline" onClick={() => setAnnotationDialogOpen(true)} className="w-full">
                          <FileVideo className="h-4 w-4 mr-2" />
                          Annotate Episode Files ({fileAnnotations.length || analysis.files?.length || 0})
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Step 3: Confirm */}
              {currentStep === 'confirm' && (
                <div className="space-y-6">
                  <div className="p-4 rounded-xl bg-muted/50">
                    <h3 className="font-medium mb-4">Import Summary</h3>
                    <div className="grid gap-3 text-sm">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Content Type</span>
                        <span className="font-medium capitalize">{contentType}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Title</span>
                        <span className="font-medium">{title || 'Not set'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">IMDb ID</span>
                        <span className="font-mono text-xs">{metaId || 'Not set'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Quality</span>
                        <span className="font-medium">
                          {[resolution, quality].filter(Boolean).join(' ') || 'Not set'}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Catalogs</span>
                        <span className="font-medium">
                          {selectedCatalogs.length > 0 ? `${selectedCatalogs.length} selected` : 'Default'}
                        </span>
                      </div>
                      {needsAnnotation && (
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Files Annotated</span>
                          <span className="font-medium">
                            {fileAnnotations.length > 0 ? `${fileAnnotations.length} files` : 'Using auto-parser'}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Options */}
                  <div className="space-y-3">
                    <Label className="text-sm font-medium">Options</Label>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between p-3 rounded-lg bg-muted/30">
                        <div className="flex items-center gap-2">
                          <ImageIcon className="h-4 w-4 text-muted-foreground" />
                          <span className="text-sm">Add title to poster</span>
                        </div>
                        <Switch checked={addTitleToPoster} onCheckedChange={setAddTitleToPoster} />
                      </div>
                      <div className="flex items-center justify-between p-3 rounded-lg bg-muted/30">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-sm">Anonymous contribution</span>
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {isAnonymous
                              ? 'Uploader will show as "Anonymous"'
                              : 'Your username will be linked to this contribution'}
                          </p>
                        </div>
                        <Switch checked={isAnonymous} onCheckedChange={setIsAnonymous} />
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </ScrollArea>

          {/* Footer */}
          <div className="px-6 py-4 border-t bg-muted/30 flex-shrink-0">
            <div className="flex items-center justify-between">
              <Button
                variant="outline"
                onClick={goBack}
                disabled={currentStep === 'review' || isImporting}
                className="rounded-lg"
              >
                <ArrowLeft className="h-4 w-4 mr-2" />
                Back
              </Button>

              <div className="flex gap-2">
                <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isImporting}>
                  Cancel
                </Button>

                {currentStep !== 'confirm' ? (
                  <Button
                    onClick={goForward}
                    disabled={!selectedMatch && !metaId}
                    className="bg-gradient-to-r from-primary to-primary/80"
                  >
                    Next
                    <ArrowRight className="h-4 w-4 ml-2" />
                  </Button>
                ) : (
                  <Button
                    onClick={handleImport}
                    disabled={isImporting || (!selectedMatch && !metaId)}
                    className="bg-gradient-to-r from-primary to-primary/80"
                  >
                    {isImporting ? (
                      <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        Importing...
                      </>
                    ) : (
                      <>
                        <CheckCircle className="h-4 w-4 mr-2" />
                        Import
                      </>
                    )}
                  </Button>
                )}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* File Annotation Dialog */}
      <ImportFileAnnotationDialog
        open={annotationDialogOpen}
        onOpenChange={setAnnotationDialogOpen}
        torrentName={analysis.torrent_name || analysis.parsed_title || 'Unknown'}
        files={analysis.files || []}
        isSports={contentType === 'sports'}
        onConfirm={handleAnnotationConfirm}
        allowMultiContent={contentType === 'movie' || contentType === 'series'}
        defaultMetaType={contentType === 'series' ? 'series' : 'movie'}
      />

      {/* Validation Warning Dialog */}
      <ValidationWarningDialog
        open={validationDialogOpen}
        onOpenChange={setValidationDialogOpen}
        errors={validationErrors}
        onCancel={() => setValidationDialogOpen(false)}
        onReanalyze={handleReanalyze}
        onForceImport={handleForceImport}
      />
    </>
  )
}
