import { useState, useCallback, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { api } from '@/lib/api'
import type { 
  ExtensionSettings, 
  PrefilledData, 
  ContentType,
  TorrentAnalyzeResponse,
  TorrentMatch,
  ImportMode,
  SportsCategory,
} from '@/lib/types'
import { 
  Loader2, 
  Upload, 
  Search, 
  Check, 
  AlertCircle,
  Film,
  Tv,
  Trophy,
  FileUp,
  Link2,
  FileVideo,
  Zap,
} from 'lucide-react'
import { useDropzone } from 'react-dropzone'
import { cn } from '@/lib/utils'
import { AnalysisResults } from './AnalysisResults'
import { MultiContentWizard } from './MultiContentWizard'
import { StreamDetailsEditor, type StreamDetails } from './StreamDetailsEditor'
import { EpisodeAnnotationDialog } from './EpisodeAnnotationDialog'
import { CatalogSelector } from './CatalogSelector'
import { ValidationFailedDialog } from './ValidationFailedDialog'
import type { FileAnnotation, ImportResponse } from '@/lib/types'
import { 
  detectContentType, 
  detectSportsCategory, 
  SPORTS_CATEGORIES,
} from '@/lib/content-detection'

interface ImportTabProps {
  settings: ExtensionSettings
  prefilledData: PrefilledData | null
}

type ImportStep = 'input' | 'analyzing' | 'results' | 'multi-content' | 'importing' | 'complete'

export function ImportTab({ settings, prefilledData }: ImportTabProps) {
  // Input state
  const [magnetLink, setMagnetLink] = useState(prefilledData?.magnetLink || '')
  const [torrentFile, setTorrentFile] = useState<File | null>(null)
  const [contentType, setContentType] = useState<ContentType>(
    prefilledData?.contentType || settings.defaultContentType || 'movie'
  )
  const [sportsCategory, setSportsCategory] = useState<SportsCategory | ''>('')
  
  // Analysis state
  const [step, setStep] = useState<ImportStep>('input')
  const [analysisResult, setAnalysisResult] = useState<TorrentAnalyzeResponse | null>(null)
  const [selectedMatch, setSelectedMatch] = useState<TorrentMatch | null>(null)
  const [importMode, setImportMode] = useState<ImportMode>('single')
  
  // Import state
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [_importSuccess, setImportSuccess] = useState(false)
  
  // Multi-content annotations
  const [fileAnnotations, setFileAnnotations] = useState<FileAnnotation[]>([])
  
  // Stream details (editable)
  const [streamDetails, setStreamDetails] = useState<StreamDetails>({})
  
  // Episode annotation dialog state
  const [showEpisodeAnnotation, setShowEpisodeAnnotation] = useState(false)
  
  // Catalog selection
  const [selectedCatalogs, setSelectedCatalogs] = useState<string[]>([])
  
  // Error state
  const [error, setError] = useState<string | null>(null)
  
  // Quick import state
  const [quickImporting, setQuickImporting] = useState(false)
  
  // Validation failed state
  const [validationErrors, setValidationErrors] = useState<{ type: string; message: string }[]>([])
  const [showValidationDialog, setShowValidationDialog] = useState(false)
  const [lastImportRequest, setLastImportRequest] = useState<{
    annotations?: FileAnnotation[]
    forceImport?: boolean
  } | null>(null)

  // Auto-analyze if prefilled data
  useEffect(() => {
    if (prefilledData?.magnetLink && settings.autoAnalyze) {
      handleAnalyze()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Auto-detect content type and sports category from magnet link
  useEffect(() => {
    if (magnetLink) {
      const detected = detectContentType(magnetLink)
      setContentType(detected)
      
      if (detected === 'sports') {
        const category = detectSportsCategory(magnetLink)
        if (category) {
          setSportsCategory(category)
        }
      }
    }
  }, [magnetLink])

  // Auto-detect content type from torrent file name
  useEffect(() => {
    if (torrentFile) {
      const detected = detectContentType(torrentFile.name)
      setContentType(detected)
      
      if (detected === 'sports') {
        const category = detectSportsCategory(torrentFile.name)
        if (category) {
          setSportsCategory(category)
        }
      }
    }
  }, [torrentFile])

  const onDrop = useCallback((acceptedFiles: File[]) => {
    if (acceptedFiles.length > 0) {
      const file = acceptedFiles[0]
      if (file.name.endsWith('.torrent')) {
        setTorrentFile(file)
        setMagnetLink('')
        setError(null)
      } else {
        setError('Please select a .torrent file')
      }
    }
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/x-bittorrent': ['.torrent'],
    },
    maxFiles: 1,
  })

  async function handleAnalyze() {
    if (!magnetLink && !torrentFile) {
      setError('Please provide a magnet link or torrent file')
      return
    }
    
    // Validate sports category for sports content
    if (contentType === 'sports' && !sportsCategory) {
      setError('Please select a sports category')
      return
    }

    setError(null)
    setStep('analyzing')
    setAnalysisResult(null)
    setSelectedMatch(null)

    try {
      let result: TorrentAnalyzeResponse
      
      if (torrentFile) {
        result = await api.analyzeTorrent(torrentFile, contentType)
      } else {
        result = await api.analyzeMagnet(magnetLink, contentType)
      }

      if (result.status === 'error') {
        setError(result.error || 'Analysis failed')
        setStep('input')
        return
      }

      setAnalysisResult(result)
      
      // Auto-select first match if available
      if (result.matches && result.matches.length > 0) {
        setSelectedMatch(result.matches[0])
      }
      
      // Initialize stream details from analysis
      setStreamDetails({
        resolution: result.resolution,
        quality: result.quality,
        codec: result.codec,
        audio: result.audio,
        hdr: result.hdr,
        languages: result.languages,
      })
      
      setStep('results')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Analysis failed')
      setStep('input')
    }
  }

  // Quick import - uploads directly without analysis step
  async function handleQuickImport() {
    if (!magnetLink && !torrentFile) {
      setError('Please provide a magnet link or torrent file')
      return
    }
    
    // Validate sports category for sports content
    if (contentType === 'sports' && !sportsCategory) {
      setError('Please select a sports category')
      return
    }

    setError(null)
    setQuickImporting(true)

    try {
      const request = {
        meta_type: contentType,
        // Let backend auto-detect everything
        sports_category: contentType === 'sports' ? sportsCategory : undefined,
      }

      let result
      if (torrentFile) {
        result = await api.importTorrent(torrentFile, request)
      } else {
        result = await api.importMagnet({ ...request, magnet_link: magnetLink })
      }

      if (result.status === 'success' || result.status === 'processing') {
        setImportSuccess(true)
        setStep('complete')
      } else if (result.status === 'needs_annotation') {
        // Need to go through analysis flow for file annotation
        setError('This torrent requires file annotation. Please use "Analyze & Search" instead.')
      } else {
        setError(result.message || 'Quick import failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Quick import failed')
    } finally {
      setQuickImporting(false)
    }
  }

  // Handle proceeding to import (may go to multi-content wizard first)
  function handleProceedToImport() {
    if (importMode !== 'single' && analysisResult?.files && analysisResult.files.length > 1) {
      // Go to multi-content wizard
      setStep('multi-content')
    } else {
      // Direct import
      handleImport()
    }
  }

  // Handle multi-content wizard completion
  function handleMultiContentComplete(annotations: FileAnnotation[]) {
    setFileAnnotations(annotations)
    handleImport(annotations)
  }

  async function handleImport(annotations?: FileAnnotation[], forceImport?: boolean) {
    if (!analysisResult) return
    
    setImporting(true)
    setImportError(null)
    setShowValidationDialog(false)
    setStep('importing')

    try {
      const annotationsToUse = annotations || fileAnnotations
      
      // Get the meta_id from the selected match in the correct format
      // IMDB IDs are used directly, others are prefixed with provider
      const getMetaId = () => {
        if (!selectedMatch) return undefined
        // Prefer IMDB ID as it's used directly
        if (selectedMatch.imdb_id) return selectedMatch.imdb_id
        // For other providers, prefix with provider name
        if (selectedMatch.tmdb_id) return `tmdb:${selectedMatch.tmdb_id}`
        if (selectedMatch.mal_id) return `mal:${selectedMatch.mal_id}`
        if (selectedMatch.kitsu_id) return `kitsu:${selectedMatch.kitsu_id}`
        // Fallback to id field
        return selectedMatch.id
      }
      
      // Use edited stream details, falling back to analysis result
      const request = {
        meta_type: contentType,
        meta_id: getMetaId(),
        title: selectedMatch?.title || analysisResult.parsed_title,
        resolution: streamDetails.resolution || analysisResult.resolution,
        quality: streamDetails.quality || analysisResult.quality,
        codec: streamDetails.codec || analysisResult.codec,
        audio: (streamDetails.audio?.length ? streamDetails.audio : analysisResult.audio)?.join(','),
        hdr: (streamDetails.hdr?.length ? streamDetails.hdr : analysisResult.hdr)?.join(','),
        languages: (streamDetails.languages?.length ? streamDetails.languages : analysisResult.languages)?.join(','),
        force_import: forceImport || false,
        // Include file annotations for multi-content
        file_data: annotationsToUse.length > 0 ? JSON.stringify(annotationsToUse) : undefined,
        // Sports category
        sports_category: contentType === 'sports' ? sportsCategory : undefined,
        // Catalogs
        catalogs: selectedCatalogs.length > 0 ? selectedCatalogs.join(',') : undefined,
        // Poster URL
        poster: streamDetails.posterUrl,
        // Episode name parser
        episode_name_parser: streamDetails.episodeNameParser,
      }
      
      // Store the request for potential retry with force_import
      setLastImportRequest({ annotations: annotationsToUse, forceImport })

      let result: ImportResponse
      if (torrentFile) {
        result = await api.importTorrent(torrentFile, request)
      } else {
        if (!magnetLink) {
          throw new Error('No magnet link available for import')
        }
        result = await api.importMagnet({ ...request, magnet_link: magnetLink })
      }

      if (result.status === 'success' || result.status === 'processing') {
        setImportSuccess(true)
        setStep('complete')
      } else if (result.status === 'validation_failed') {
        // Handle validation failed - show dialog
        const errors = result.errors || [{ type: 'unknown', message: result.message || 'Validation failed' }]
        setValidationErrors(errors)
        setShowValidationDialog(true)
        setStep('results')
        
        // Check if we need to show episode annotation
        const errorTypes = errors.map(e => e.type)
        if ((errorTypes.includes('episodes_not_found') || errorTypes.includes('seasons_not_found')) 
            && result.torrent_data?.file_data) {
          // Update analysisResult with file data from validation response for annotation
          setAnalysisResult(prev => prev ? {
            ...prev,
            files: result.torrent_data?.file_data,
          } : prev)
        }
      } else if (result.status === 'needs_annotation' && result.torrent_data?.file_data) {
        // Need file annotation
        setAnalysisResult(prev => prev ? {
          ...prev,
          files: result.torrent_data?.file_data,
        } : prev)
        setShowEpisodeAnnotation(true)
        setStep('results')
      } else if (result.status === 'warning') {
        // Warning but treat as success
        setImportSuccess(true)
        setStep('complete')
      } else {
        setImportError(result.message || 'Import failed')
        setStep('results')
      }
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Import failed')
      setStep('results')
    } finally {
      setImporting(false)
    }
  }
  
  // Handle force import from validation dialog
  function handleForceImport() {
    handleImport(lastImportRequest?.annotations, true)
  }
  
  // Handle re-analyze from validation dialog
  function handleReanalyze() {
    setShowValidationDialog(false)
    setValidationErrors([])
    setStep('input')
  }

  function handleReset() {
    setMagnetLink('')
    setTorrentFile(null)
    setContentType(settings.defaultContentType || 'movie')
    setSportsCategory('')
    setAnalysisResult(null)
    setSelectedMatch(null)
    setImportMode('single')
    setError(null)
    setImportError(null)
    setImportSuccess(false)
    setFileAnnotations([])
    setStreamDetails({})
    setShowEpisodeAnnotation(false)
    setSelectedCatalogs([])
    setValidationErrors([])
    setShowValidationDialog(false)
    setLastImportRequest(null)
    setStep('input')
  }

  // Handle episode annotation confirmation
  function handleEpisodeAnnotationConfirm(annotations: FileAnnotation[]) {
    setFileAnnotations(annotations)
    setShowEpisodeAnnotation(false)
  }

  // Check if series/sports needs annotation
  const needsAnnotation = (contentType === 'series' || contentType === 'sports') && 
    analysisResult?.files && 
    analysisResult.files.length > 1

  // Render based on step
  if (step === 'complete') {
    return (
      <Card className="border-green-500/50">
        <CardContent className="pt-6">
          <div className="flex flex-col items-center text-center space-y-4">
            <div className="w-12 h-12 rounded-full bg-green-500/20 flex items-center justify-center">
              <Check className="h-6 w-6 text-green-500" />
            </div>
            <div>
              <h3 className="font-semibold text-green-500">Import Successful!</h3>
              <p className="text-sm text-muted-foreground mt-1">
                Your torrent has been added to MediaFusion
              </p>
            </div>
            <Button onClick={handleReset} variant="outline" className="w-full">
              Import Another
            </Button>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (step === 'analyzing' || step === 'importing') {
    return (
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-col items-center space-y-4">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <div className="text-center">
              <p className="font-medium">
                {step === 'analyzing' ? 'Analyzing torrent...' : 'Importing...'}
              </p>
              <p className="text-sm text-muted-foreground">
                {step === 'analyzing' 
                  ? 'Fetching metadata and searching for matches'
                  : 'Adding to your MediaFusion library'}
              </p>
            </div>
            <Progress value={step === 'importing' ? 75 : 50} className="w-full" />
          </div>
        </CardContent>
      </Card>
    )
  }

  // Multi-content wizard step
  if (step === 'multi-content' && analysisResult) {
    return (
      <MultiContentWizard
        analysis={analysisResult}
        importMode={importMode}
        contentType={contentType as 'movie' | 'series'}
        onComplete={handleMultiContentComplete}
        onCancel={() => setStep('results')}
      />
    )
  }

  // Episode annotation dialog
  if (showEpisodeAnnotation && analysisResult?.files) {
    return (
      <Card className="overflow-hidden">
        <EpisodeAnnotationDialog
          files={analysisResult.files}
          torrentName={analysisResult.torrent_name || analysisResult.parsed_title || 'Unknown'}
          onConfirm={handleEpisodeAnnotationConfirm}
          onCancel={() => setShowEpisodeAnnotation(false)}
        />
      </Card>
    )
  }

  if (step === 'results' && analysisResult) {
    return (
      <div className="space-y-4">
        {/* Validation Failed Dialog */}
        {showValidationDialog && validationErrors.length > 0 && (
          <ValidationFailedDialog
            errors={validationErrors}
            onCancel={() => setShowValidationDialog(false)}
            onReanalyze={handleReanalyze}
            onForceImport={handleForceImport}
          />
        )}

        <AnalysisResults
          result={analysisResult}
          contentType={contentType}
          selectedMatch={selectedMatch}
          onSelectMatch={setSelectedMatch}
          importMode={importMode}
          onImportModeChange={setImportMode}
        />

        {/* Stream Details Editor */}
        <StreamDetailsEditor
          details={streamDetails}
          onChange={setStreamDetails}
          contentType={contentType}
        />

        {/* Catalog Selector */}
        <CatalogSelector
          contentType={contentType}
          selectedCatalogs={selectedCatalogs}
          onChange={setSelectedCatalogs}
          quality={streamDetails.quality || analysisResult.quality}
        />

        {/* Episode Annotation Button for Series/Sports */}
        {needsAnnotation && (
          <Button
            variant="outline"
            onClick={() => setShowEpisodeAnnotation(true)}
            className="w-full"
          >
            <FileVideo className="h-4 w-4 mr-2" />
            Annotate Episodes ({fileAnnotations.length || analysisResult.files?.length || 0} files)
          </Button>
        )}

        {importError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{importError}</AlertDescription>
          </Alert>
        )}

        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReset} className="flex-1">
            Cancel
          </Button>
          <Button 
            onClick={handleProceedToImport} 
            disabled={importing || (!selectedMatch && contentType !== 'sports')}
            className="flex-1"
          >
            {importing ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Importing...
              </>
            ) : importMode !== 'single' ? (
              <>
                <Upload className="h-4 w-4" />
                Configure Files
              </>
            ) : (
              <>
                <Upload className="h-4 w-4" />
                Import
              </>
            )}
          </Button>
        </div>
      </div>
    )
  }

  // Input step
  return (
    <div className="space-y-4">
      {/* Content Type Selection */}
      <div className="flex gap-2">
        <ContentTypeButton
          type="movie"
          icon={Film}
          selected={contentType === 'movie'}
          onClick={() => setContentType('movie')}
        />
        <ContentTypeButton
          type="series"
          icon={Tv}
          selected={contentType === 'series'}
          onClick={() => setContentType('series')}
        />
        <ContentTypeButton
          type="sports"
          icon={Trophy}
          selected={contentType === 'sports'}
          onClick={() => setContentType('sports')}
        />
      </div>

      {/* Sports Category Selector - only shown when sports is selected */}
      {contentType === 'sports' && (
        <div className="space-y-2">
          <Label htmlFor="sports-category" className="flex items-center gap-1">
            <Trophy className="h-3 w-3" />
            Sports Category
          </Label>
          <Select 
            value={sportsCategory} 
            onValueChange={(v) => setSportsCategory(v as SportsCategory | '')}
          >
            <SelectTrigger id="sports-category">
              <SelectValue placeholder="Select sports category" />
            </SelectTrigger>
            <SelectContent>
              {SPORTS_CATEGORIES.map((cat) => (
                <SelectItem key={cat.value || 'empty'} value={cat.value || 'empty'} disabled={!cat.value}>
                  {cat.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Magnet Link Input */}
      <div className="space-y-2">
        <Label htmlFor="magnet" className="flex items-center gap-1">
          <Link2 className="h-3 w-3" />
          Magnet Link
        </Label>
        <Input
          id="magnet"
          value={magnetLink}
          onChange={(e) => {
            setMagnetLink(e.target.value)
            if (e.target.value) setTorrentFile(null)
          }}
          placeholder="magnet:?xt=urn:btih:..."
          disabled={!!torrentFile}
        />
      </div>

      {/* OR divider */}
      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-background px-2 text-muted-foreground">or</span>
        </div>
      </div>

      {/* Torrent File Upload */}
      <div
        {...getRootProps()}
        className={cn(
          "border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors",
          isDragActive 
            ? "border-primary bg-primary/5" 
            : "border-border hover:border-primary/50",
          torrentFile && "border-green-500 bg-green-500/5"
        )}
      >
        <input {...getInputProps()} />
        <div className="flex flex-col items-center gap-2">
          <FileUp className={cn(
            "h-6 w-6",
            torrentFile ? "text-green-500" : "text-muted-foreground"
          )} />
          {torrentFile ? (
            <div>
              <p className="text-sm font-medium text-green-500">{torrentFile.name}</p>
              <p className="text-xs text-muted-foreground">Click to change</p>
            </div>
          ) : (
            <div>
              <p className="text-sm">Drop .torrent file here</p>
              <p className="text-xs text-muted-foreground">or click to browse</p>
            </div>
          )}
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Action Buttons */}
      <div className="flex gap-2">
        <Button 
          onClick={handleAnalyze} 
          disabled={(!magnetLink && !torrentFile) || quickImporting || (contentType === 'sports' && !sportsCategory)}
          className="flex-1"
        >
          <Search className="h-4 w-4" />
          Analyze & Search
        </Button>
        <Button 
          onClick={handleQuickImport}
          disabled={(!magnetLink && !torrentFile) || quickImporting || (contentType === 'sports' && !sportsCategory)}
          variant="secondary"
          className="flex-1"
          title="Upload directly without searching for metadata"
        >
          {quickImporting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Zap className="h-4 w-4" />
          )}
          Quick Import
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground text-center">
        Quick Import uploads directly, letting the server auto-detect metadata
      </p>
    </div>
  )
}

interface ContentTypeButtonProps {
  type: ContentType
  icon: React.ComponentType<{ className?: string }>
  selected: boolean
  onClick: () => void
}

function ContentTypeButton({ type, icon: Icon, selected, onClick }: ContentTypeButtonProps) {
  return (
    <Button
      variant={selected ? 'default' : 'outline'}
      size="sm"
      onClick={onClick}
      className="flex-1 capitalize"
    >
      <Icon className="h-4 w-4" />
      {type}
    </Button>
  )
}
