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
  Settings2,
  Youtube,
  ExternalLink,
  Clock,
  Link2,
  FileText,
  Image as ImageIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { YouTubeAnalyzeResponse, ImportResponse } from '@/lib/api'
import type { ContentType } from '@/lib/constants'
import { TechSpecsEditor } from './TechSpecsEditor'
import { type ExtendedMatch } from './MatchResultsGrid'
import { MatchSearchSection } from './MatchSearchSection'
import { CatalogSelector } from './CatalogSelector'
import { useAuth } from '@/contexts/AuthContext'

type ImportStep = 'review' | 'metadata' | 'confirm'

export interface YouTubeImportFormData {
  contentType: ContentType
  metaId?: string
  title?: string
  poster?: string
  background?: string
  resolution?: string
  quality?: string
  codec?: string
  languages?: string[]
  catalogs?: string[]
  isAnonymous?: boolean
  forceImport?: boolean
}

interface YouTubeImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  analysis: YouTubeAnalyzeResponse | null
  youtubeUrl: string
  onImport: (formData: YouTubeImportFormData) => Promise<ImportResponse>
  isImporting?: boolean
  initialContentType?: ContentType
}

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

export function YouTubeImportDialog({
  open,
  onOpenChange,
  analysis,
  youtubeUrl,
  onImport,
  isImporting = false,
  initialContentType = 'movie',
}: YouTubeImportDialogProps) {
  const { user } = useAuth()
  void youtubeUrl

  const [currentStep, setCurrentStep] = useState<ImportStep>('review')

  // Form state
  const [contentType] = useState<ContentType>(initialContentType)
  const [selectedMatchIndex, setSelectedMatchIndex] = useState<number | null>(null)

  // Metadata
  const [metaId, setMetaId] = useState('')
  const [title, setTitle] = useState('')
  const [poster, setPoster] = useState('')
  const [background, setBackground] = useState('')

  // Tech specs
  const [resolution, setResolution] = useState<string | undefined>()
  const [quality, setQuality] = useState<string | undefined>()
  const [codec, setCodec] = useState<string | undefined>()
  const [languages, setLanguages] = useState<string[]>([])

  // Catalogs
  const [selectedCatalogs, setSelectedCatalogs] = useState<string[]>([])

  // Import options
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
    setResolution(analysis.resolution)
    setQuality(undefined)
    setCodec(undefined)
    setLanguages([])
    setTitle(analysis.title || '')

    if (analysis.matches && analysis.matches.length > 0) {
      const firstMatch = analysis.matches[0] as ExtendedMatch
      setSelectedMatchIndex(0)
      setMetaId(firstMatch.imdb_id || firstMatch.id)
      setTitle(firstMatch.title)
      if (firstMatch.poster) setPoster(firstMatch.poster)
      if (firstMatch.background) setBackground(firstMatch.background)
      if (firstMatch.languages) setLanguages(firstMatch.languages)
    } else {
      setSelectedMatchIndex(null)
      setMetaId('')
      setPoster('')
      setBackground('')
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
    if (match.languages) setLanguages(match.languages)
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
    if (currentStep === 'metadata') goToStep('review')
    else if (currentStep === 'confirm') goToStep('metadata')
  }, [currentStep, goToStep])

  const goForward = useCallback(() => {
    if (currentStep === 'review') goToStep('metadata')
    else if (currentStep === 'metadata') goToStep('confirm')
  }, [currentStep, goToStep])

  // Build import form data
  const buildFormData = useCallback((): YouTubeImportFormData => {
    return {
      contentType,
      metaId: metaId || undefined,
      title: title || undefined,
      poster: poster || undefined,
      background: background || undefined,
      resolution: resolution || undefined,
      quality: quality || undefined,
      codec: codec || undefined,
      languages: languages.length > 0 ? languages : undefined,
      catalogs: selectedCatalogs.length > 0 ? selectedCatalogs : undefined,
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
    languages,
    selectedCatalogs,
    isAnonymous,
  ])

  // Handle import
  const handleImport = useCallback(async () => {
    const formData = buildFormData()
    try {
      await onImport(formData)
    } catch (error) {
      console.error('Import failed:', error)
    }
  }, [buildFormData, onImport])

  // Step indicator
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
            <Youtube className="h-5 w-5 text-red-500" />
            Import YouTube Video
          </DialogTitle>
          <DialogDescription asChild>
            <div className="space-y-1">
              <p className="font-medium text-sm text-foreground/80">{analysis.title || 'Unknown video'}</p>
              {analysis.channel_name && (
                <p className="text-xs text-muted-foreground">
                  Channel: <span className="font-medium">{analysis.channel_name}</span>
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
                {/* Video Info Summary */}
                <div className="p-4 rounded-xl bg-muted/50">
                  <div className="flex gap-4">
                    {/* Thumbnail */}
                    <div className="flex-shrink-0">
                      <img
                        src={analysis.thumbnail || `https://img.youtube.com/vi/${analysis.video_id}/mqdefault.jpg`}
                        alt="Video thumbnail"
                        className="w-48 h-auto rounded-lg object-cover"
                      />
                    </div>

                    {/* Video Details */}
                    <div className="flex-1 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="secondary" className="font-mono text-xs">
                          {analysis.video_id}
                        </Badge>
                        {analysis.is_live && <Badge variant="destructive">Live</Badge>}
                        {analysis.resolution && <Badge variant="outline">{analysis.resolution}</Badge>}
                      </div>
                      <h3 className="font-semibold text-base">{analysis.title}</h3>
                      {analysis.channel_name && (
                        <p className="text-sm text-muted-foreground">{analysis.channel_name}</p>
                      )}
                      <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        {analysis.duration_seconds != null && analysis.duration_seconds > 0 && (
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            {formatDuration(analysis.duration_seconds)}
                          </span>
                        )}
                      </div>
                      <a
                        href={`https://www.youtube.com/watch?v=${analysis.video_id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                      >
                        <ExternalLink className="h-3 w-3" />
                        View on YouTube
                      </a>
                    </div>
                  </div>
                </div>

                {/* Match Results with Search */}
                <MatchSearchSection
                  initialMatches={(analysis.matches || []) as ExtendedMatch[]}
                  selectedIndex={selectedMatchIndex}
                  selectedMatch={selectedMatch}
                  onSelectMatch={handleMatchSelect}
                  metaId={metaId}
                  onMetaIdChange={setMetaId}
                  contentType={contentType === 'tv' ? 'movie' : contentType === 'sports' ? 'movie' : contentType}
                />
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
                        placeholder="Video title"
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
                        <ImageIcon className="h-3 w-3" />
                        Background URL
                      </Label>
                      <Input
                        value={background}
                        onChange={(e) => setBackground(e.target.value)}
                        placeholder="https://..."
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
                    languages={languages}
                    extraLanguages={selectedMatch?.languages || []}
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
                {/* Video Preview */}
                <div className="p-4 rounded-xl bg-muted/50">
                  <div className="flex gap-4">
                    <img
                      src={analysis.thumbnail || `https://img.youtube.com/vi/${analysis.video_id}/mqdefault.jpg`}
                      alt="Video thumbnail"
                      className="w-32 h-auto rounded-lg object-cover"
                    />
                    <div className="flex-1">
                      <h3 className="font-semibold">{title || analysis.title}</h3>
                      {analysis.channel_name && (
                        <p className="text-sm text-muted-foreground">{analysis.channel_name}</p>
                      )}
                    </div>
                  </div>
                </div>

                {/* Import Summary */}
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
                      <span className="text-muted-foreground">Resolution</span>
                      <span className="font-medium">{resolution || 'Not set'}</span>
                    </div>
                    {quality && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Quality</span>
                        <span className="font-medium">{quality}</span>
                      </div>
                    )}
                    {codec && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Codec</span>
                        <span className="font-medium">{codec}</span>
                      </div>
                    )}
                    {languages.length > 0 && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Languages</span>
                        <span className="font-medium">{languages.join(', ')}</span>
                      </div>
                    )}
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
                  className="bg-gradient-to-r from-red-500 to-red-600"
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
