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
  Newspaper,
  Link2,
  AlertTriangle,
  Calendar,
  FileText,
  Settings2,
  Image as ImageIcon,
  Hash,
  Users,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { NZBAnalyzeResponse, ImportResponse } from '@/lib/api'
import type { ContentType } from '@/lib/constants'
import { ContentTypeSelector } from './ContentTypeSelector'
import { TechSpecsEditor } from './TechSpecsEditor'
import { MatchResultsGrid, type ExtendedMatch } from './MatchResultsGrid'
import { CatalogSelector } from './CatalogSelector'
import type { NZBImportFormData } from './types'
import { useAuth } from '@/contexts/AuthContext'

type ImportStep = 'review' | 'metadata' | 'confirm'

interface NZBImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  analysis: NZBAnalyzeResponse | null
  nzbSource?: { type: 'file' | 'url'; file?: File; url?: string }
  onImport: (formData: NZBImportFormData) => Promise<ImportResponse>
  onReanalyze?: (contentType: ContentType) => void
  isImporting?: boolean
  initialContentType?: ContentType
}

export function NZBImportDialog({
  open,
  onOpenChange,
  analysis,
  nzbSource: _nzbSource,
  initialContentType = 'movie',
  onImport,
  onReanalyze,
  isImporting = false,
}: NZBImportDialogProps) {
  const { user } = useAuth()
  void _nzbSource

  const [currentStep, setCurrentStep] = useState<ImportStep>('review')

  // Form state
  const [contentType, setContentType] = useState<ContentType>(initialContentType)
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
  const [languages, setLanguages] = useState<string[]>([])

  // Catalogs
  const [selectedCatalogs, setSelectedCatalogs] = useState<string[]>([])

  // Import options
  const [forceImport, setForceImport] = useState(false)
  const [isAnonymous, setIsAnonymous] = useState(user?.contribute_anonymously ?? false)

  // Derive selected match from index
  const selectedMatch = useMemo(() => {
    if (selectedMatchIndex === null || !analysis?.matches) return null
    return analysis.matches[selectedMatchIndex] as ExtendedMatch | null
  }, [selectedMatchIndex, analysis?.matches])

  // Initialize from analysis when dialog opens
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
    setLanguages([])
    setTitle(analysis.parsed_title || analysis.nzb_title || '')

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
    setForceImport(false)
  }

  const handleMatchSelect = useCallback((match: ExtendedMatch, index: number) => {
    setSelectedMatchIndex(index)
    setMetaId(match.imdb_id || match.id)
    setTitle(match.title)
    if (match.poster) setPoster(match.poster)
    if (match.background) setBackground(match.background)
    if (match.release_date) setReleaseDate(match.release_date)
    if (match.type) {
      setContentType(match.type as ContentType)
    }
  }, [])

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
      case 'languages':
        setLanguages((value as string[]) ?? [])
        break
    }
  }, [])

  const goToStep = useCallback((step: ImportStep) => {
    setCurrentStep(step)
  }, [])

  const goBack = useCallback(() => {
    if (currentStep === 'metadata') goToStep('review')
    else if (currentStep === 'confirm') goToStep('metadata')
  }, [currentStep, goToStep])

  const goForward = useCallback(() => {
    if (currentStep === 'review') goToStep('metadata')
    else if (currentStep === 'metadata') goToStep('confirm')
  }, [currentStep, goToStep])

  const buildFormData = useCallback((): NZBImportFormData => {
    return {
      contentType,
      metaId: metaId || undefined,
      title: title || undefined,
      poster: poster || undefined,
      background: background || undefined,
      resolution: resolution || undefined,
      quality: quality || undefined,
      codec: codec || undefined,
      audio: audio.length > 0 ? audio : undefined,
      languages: languages.length > 0 ? languages : undefined,
      catalogs: selectedCatalogs.length > 0 ? selectedCatalogs : undefined,
      forceImport,
      isAnonymous,
    }
  }, [
    contentType,
    metaId,
    title,
    poster,
    background,
    resolution,
    quality,
    codec,
    audio,
    languages,
    selectedCatalogs,
    forceImport,
    isAnonymous,
  ])

  const handleImport = useCallback(async () => {
    const formData = buildFormData()
    try {
      const result = await onImport(formData)
      if (result.status === 'validation_failed') {
        setForceImport(true)
      }
    } catch (error) {
      console.error('NZB import failed:', error)
    }
  }, [buildFormData, onImport])

  const steps = [
    { id: 'review', label: 'Review', icon: Search },
    { id: 'metadata', label: 'Metadata', icon: Settings2 },
    { id: 'confirm', label: 'Confirm', icon: CheckCircle },
  ]

  if (!analysis) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[900px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden">
        {/* Header */}
        <DialogHeader className="px-6 pt-6 pb-4 border-b flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <Newspaper className="h-5 w-5 text-primary" />
            Import NZB
          </DialogTitle>
          <DialogDescription asChild>
            <div className="space-y-1">
              <p className="font-mono text-xs bg-muted/50 p-2 rounded-md break-all text-foreground/80">
                {analysis.nzb_title || 'Unknown NZB'}
              </p>
              {analysis.parsed_title && analysis.parsed_title !== analysis.nzb_title && (
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
                {/* NZB Info Summary */}
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
                    <div className="min-w-0">
                      <Label className="text-xs text-muted-foreground">GUID</Label>
                      <p className="font-mono text-xs truncate">{analysis.nzb_guid?.slice(0, 16) || 'N/A'}...</p>
                    </div>
                  </div>

                  {/* Additional NZB-specific info */}
                  {(analysis.group_name || analysis.indexer || analysis.is_passworded) && (
                    <div className="mt-3 pt-3 border-t border-border/50 flex flex-wrap gap-2">
                      {analysis.group_name && (
                        <Badge variant="secondary" className="text-xs">
                          <Users className="h-3 w-3 mr-1" />
                          {analysis.group_name}
                        </Badge>
                      )}
                      {analysis.indexer && (
                        <Badge variant="secondary" className="text-xs">
                          <Hash className="h-3 w-3 mr-1" />
                          {analysis.indexer}
                        </Badge>
                      )}
                      {analysis.is_passworded && (
                        <Badge variant="destructive" className="text-xs">
                          Password Protected
                        </Badge>
                      )}
                    </div>
                  )}
                </div>

                {/* Content Type Selection */}
                <ContentTypeSelector
                  value={contentType}
                  importMode="single"
                  onChange={(newType) => {
                    setContentType(newType)
                    setSelectedMatchIndex(null)
                    setMetaId('')
                    setPoster('')
                    setBackground('')
                    if (onReanalyze && newType !== contentType) {
                      onReanalyze(newType)
                    }
                  }}
                  showImportMode={false}
                  excludeTypes={['tv', 'sports']}
                />

                {/* Match Results */}
                {analysis.matches && analysis.matches.length > 0 && (
                  <div className="space-y-3 min-w-0">
                    <div className="flex items-center justify-between gap-2 min-w-0">
                      <Label className="text-sm font-medium shrink-0">
                        Matched Content ({analysis.matches.length})
                      </Label>
                      {selectedMatch && (
                        <Badge variant="secondary" className="text-xs max-w-[50%] truncate">
                          <CheckCircle className="h-3 w-3 mr-1 shrink-0" />
                          <span className="truncate">{selectedMatch.title}</span>
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
                        <p className="text-sm text-muted-foreground">Please enter the IMDb ID manually to continue.</p>
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
                    languages={languages}
                    availableLanguages={[]}
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
                      <span className="text-muted-foreground">Size</span>
                      <span className="font-medium">{analysis.total_size_readable || 'Unknown'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Files</span>
                      <span className="font-medium">{analysis.file_count || 0}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Catalogs</span>
                      <span className="font-medium">
                        {selectedCatalogs.length > 0 ? `${selectedCatalogs.length} selected` : 'Default'}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Options */}
                <div className="space-y-3">
                  <Label className="text-sm font-medium">Options</Label>
                  <div className="space-y-2">
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
  )
}
