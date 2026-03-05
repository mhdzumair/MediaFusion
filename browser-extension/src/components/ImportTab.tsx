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
  extractTitleFromMagnet,
} from '@/lib/content-detection'

interface ImportTabProps {
  settings: ExtensionSettings
  prefilledData: PrefilledData | null
  onImportComplete?: (details: { matchTitle?: string; matchId?: string }) => void
}

type ImportStep = 'input' | 'analyzing' | 'results' | 'multi-content' | 'importing' | 'complete'

function extractYearFromText(value: string): string {
  const match = value.match(/\b(19|20)\d{2}\b/)
  if (!match) return ''

  const year = Number(match[0])
  const maxYear = new Date().getFullYear() + 1
  if (Number.isNaN(year) || year < 1900 || year > maxYear) return ''

  return String(year)
}

function normalizeTitleForManualSearch(rawTitle: string): string {
  if (!rawTitle) return ''

  const normalizedFromMagnet =
    rawTitle.startsWith('magnet:') ? extractTitleFromMagnet(rawTitle) || rawTitle : rawTitle

  let title = normalizedFromMagnet
    .replace(/\.torrent$/i, '')
    .replace(/[._]/g, ' ')
    .replace(/\[[^\]]*]/g, ' ')
    .replace(/\{[^}]*}/g, ' ')
    .replace(/\([^)]*\b(?:x264|x265|h\.?264|h\.?265|hevc|bluray|web[-\s]?dl|webrip|brrip|dvdrip|hdrip|remux)\b[^)]*\)/gi, ' ')
    .replace(/\bS\d{1,2}E\d{1,2}\b/gi, ' ')
    .replace(/\b\d{1,2}x\d{1,2}\b/gi, ' ')
    .replace(/\b(?:2160p|1080p|720p|480p)\b/gi, ' ')
    .replace(/\b(?:x264|x265|h\.?264|h\.?265|hevc|av1|10bit|8bit)\b/gi, ' ')
    .replace(/\b(?:bluray|blu[-\s]?ray|web[-\s]?dl|webrip|brrip|dvdrip|hdrip|remux|proper|repack|extended|uncut)\b/gi, ' ')
    .replace(/\b(?:ddp?\d(?:\.\d)?|dts(?:-hd)?|aac\d?(?:\.\d)?|ac3|eac3)\b/gi, ' ')
    .replace(/\b(?:uindex|rarbg|yts|eztv|xvid|hings|lama|bone)\b/gi, ' ')
    .replace(/[-]+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()

  // Remove bare year from the title; year has its own optional field.
  title = title.replace(/\b(19|20)\d{2}\b/g, '').replace(/\s{2,}/g, ' ').trim()

  return title
}

function deserializePrefilledTorrentFile(
  serialized?: PrefilledData['torrentFileData']
): File | null {
  if (!serialized?.name || !serialized.base64) {
    return null
  }

  const binaryString = window.atob(serialized.base64)
  const uint8Array = new Uint8Array(binaryString.length)
  for (let i = 0; i < binaryString.length; i++) {
    uint8Array[i] = binaryString.charCodeAt(i)
  }
  return new File([uint8Array], serialized.name, {
    type: serialized.type || 'application/x-bittorrent',
  })
}

export function ImportTab({ settings, prefilledData, onImportComplete }: ImportTabProps) {
  // Input state
  const [magnetLink, setMagnetLink] = useState(prefilledData?.magnetLink || '')
  const [torrentFile, setTorrentFile] = useState<File | null>(null)
  const [contentType, setContentType] = useState<ContentType>(() => {
    if (prefilledData?.contentType) return prefilledData.contentType
    if (prefilledData?.magnetLink) return detectContentType(prefilledData.magnetLink)
    return 'movie'
  })
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
  const [manualSearchTitle, setManualSearchTitle] = useState('')
  const [manualSearchYear, setManualSearchYear] = useState('')
  const [manualSearching, setManualSearching] = useState(false)
  const [manualSearchError, setManualSearchError] = useState<string | null>(null)
  const [torrentPrefetchWarning, setTorrentPrefetchWarning] = useState<string | null>(null)

  // Keep form in sync when parent opens an advanced flow from bulk list.
  useEffect(() => {
    if (!prefilledData) return

    const nextTorrentFile = deserializePrefilledTorrentFile(prefilledData.torrentFileData)
    const nextMagnetLink = nextTorrentFile ? '' : (prefilledData.magnetLink || '')
    const sourceTitleForDetection = nextMagnetLink || nextTorrentFile?.name || ''
    const nextContentType = prefilledData.contentType
      || (sourceTitleForDetection ? detectContentType(sourceTitleForDetection) : 'movie')

    setMagnetLink(nextMagnetLink)
    setTorrentFile(nextTorrentFile)
    setTorrentPrefetchWarning(prefilledData.torrentPrefetchWarning || null)
    setContentType(nextContentType)
    if (nextContentType === 'sports') {
      const detectedCategory = detectSportsCategory(sourceTitleForDetection)
      setSportsCategory(detectedCategory || '')
    } else {
      setSportsCategory('')
    }

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
    setManualSearchTitle('')
    setManualSearchYear('')
    setManualSearchError(null)
    setTorrentPrefetchWarning(null)
    setStep('input')
  }, [
    prefilledData?.magnetLink,
    prefilledData?.torrentUrl,
    prefilledData?.torrentFileData,
    prefilledData?.contentType,
  ])

  // Load torrent file when advanced flow passes a remote torrent URL.
  useEffect(() => {
    if (!prefilledData?.torrentUrl || prefilledData?.magnetLink || prefilledData?.torrentFileData) return

    let cancelled = false

    const loadTorrentFromUrl = async () => {
      try {
        const response = await fetch(prefilledData.torrentUrl!, {
          credentials: 'include',
          referrer: prefilledData.pageUrl || undefined,
        })
        if (!response.ok) {
          throw new Error(`Failed to download torrent file (${response.status})`)
        }

        const blob = await response.blob()
        let fileName = 'prefilled.torrent'
        try {
          const parsed = new URL(prefilledData.torrentUrl!)
          const fromPath = decodeURIComponent(parsed.pathname.split('/').pop() || '')
          if (fromPath) {
            fileName = fromPath.endsWith('.torrent') ? fromPath : `${fromPath}.torrent`
          }
        } catch {
          // Keep fallback filename
        }

        if (cancelled) return

        const file = new File([blob], fileName, {
          type: blob.type || 'application/x-bittorrent',
        })
        setTorrentFile(file)
        setMagnetLink('')

        if (!prefilledData.contentType) {
          const detected = detectContentType(file.name)
          setContentType(detected)
          if (detected === 'sports') {
            const category = detectSportsCategory(file.name)
            if (category) {
              setSportsCategory(category)
            }
          }
        }
      } catch (err) {
        if (!cancelled) {
          const baseMessage = err instanceof Error ? err.message : 'Failed to load torrent file'
          setError(`${baseMessage}. This site may block cross-context torrent downloads; upload the .torrent file manually if this keeps failing.`)
        }
      }
    }

    loadTorrentFromUrl()

    return () => {
      cancelled = true
    }
  }, [prefilledData?.torrentUrl, prefilledData?.magnetLink, prefilledData?.torrentFileData, prefilledData?.contentType, prefilledData?.pageUrl])

  // Auto-analyze for prefilled magnet links.
  useEffect(() => {
    if (!prefilledData?.magnetLink || !settings.autoAnalyze || !magnetLink || step !== 'input') {
      return
    }

    const timer = window.setTimeout(() => {
      handleAnalyze()
    }, 0)

    return () => window.clearTimeout(timer)
  }, [prefilledData?.magnetLink, settings.autoAnalyze, magnetLink, step])

  // Auto-analyze for prefilled torrent URLs after the file is downloaded.
  useEffect(() => {
    const hasPrefilledTorrentSource = !!prefilledData?.torrentUrl || !!prefilledData?.torrentFileData
    if (!hasPrefilledTorrentSource || !settings.autoAnalyze || !torrentFile || magnetLink || step !== 'input') {
      return
    }

    const timer = window.setTimeout(() => {
      handleAnalyze()
    }, 0)

    return () => window.clearTimeout(timer)
  }, [prefilledData?.torrentUrl, prefilledData?.torrentFileData, settings.autoAnalyze, torrentFile, magnetLink, step])

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

      const fallbackTitle = result.torrent_name
        || result.parsed_title
        || (torrentFile ? torrentFile.name.replace(/\.torrent$/i, '') : '')
        || (magnetLink ? extractTitleFromMagnet(magnetLink) || '' : '')

      const normalizedResult: TorrentAnalyzeResponse = {
        ...result,
        torrent_name: result.torrent_name || fallbackTitle,
        parsed_title: result.parsed_title || fallbackTitle,
      }

      const rawSearchTitle = normalizedResult.parsed_title || normalizedResult.torrent_name || fallbackTitle
      const normalizedSearchTitle = normalizeTitleForManualSearch(rawSearchTitle)
      const detectedYear =
        normalizedResult.year
          ? String(normalizedResult.year)
          : extractYearFromText(rawSearchTitle)
            || (torrentFile ? extractYearFromText(torrentFile.name) : '')
            || (magnetLink ? extractYearFromText(magnetLink) : '')

      setAnalysisResult(normalizedResult)
      setManualSearchTitle(normalizedSearchTitle || rawSearchTitle)
      setManualSearchYear(detectedYear)
      setManualSearchError(null)
      
      // Auto-select first match if available
      if (normalizedResult.matches && normalizedResult.matches.length > 0) {
        setSelectedMatch(normalizedResult.matches[0])
      }
      
      // Initialize stream details from analysis
      setStreamDetails({
        resolution: normalizedResult.resolution,
        quality: normalizedResult.quality,
        codec: normalizedResult.codec,
        audio: normalizedResult.audio,
        hdr: normalizedResult.hdr,
        languages: normalizedResult.languages,
      })
      
      setStep('results')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Analysis failed')
      setStep('input')
    }
  }
  
  async function handleManualSearch() {
    const queryTitle = manualSearchTitle.trim()
    if (queryTitle.length < 2) {
      setManualSearchError('Enter a title with at least 2 characters')
      return
    }

    const year = manualSearchYear.trim()
    const query = year ? `${queryTitle} ${year}` : queryTitle

    setManualSearchError(null)
    setManualSearching(true)
    try {
      const searchResult = await api.analyzeMagnet(query, contentType)
      if (searchResult.status === 'error') {
        setManualSearchError(searchResult.error || 'Metadata search failed')
        return
      }

      const matches = searchResult.matches || []
      setAnalysisResult((prev) => {
        if (!prev) {
          return {
            ...searchResult,
            matches,
          }
        }
        return {
          ...prev,
          matches,
        }
      })

      if (matches.length > 0) {
        setSelectedMatch(matches[0])
      } else {
        setSelectedMatch(null)
        setManualSearchError('No matches found for this search')
      }
    } catch (err) {
      setManualSearchError(err instanceof Error ? err.message : 'Metadata search failed')
    } finally {
      setManualSearching(false)
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
      const isAnonymous = settings.contributeAnonymously
      const anonymousDisplayName = isAnonymous
        ? (settings.anonymousDisplayName?.trim() || undefined)
        : undefined
      const request = {
        meta_type: contentType,
        sports_category: contentType === 'sports' ? sportsCategory : undefined,
        is_anonymous: isAnonymous,
        anonymous_display_name: anonymousDisplayName,
      }

      let result
      if (torrentFile) {
        result = await api.importTorrent(torrentFile, request)
      } else {
        result = await api.importMagnet({ ...request, magnet_link: magnetLink })
      }

      if (result.status === 'success' || result.status === 'processing') {
        const completedTitle = torrentFile
          ? normalizeTitleForManualSearch(torrentFile.name)
          : normalizeTitleForManualSearch(magnetLink)
        onImportComplete?.({
          matchTitle: completedTitle || undefined,
        })
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
      const isAnonymous = settings.contributeAnonymously
      const anonymousDisplayName = isAnonymous
        ? (settings.anonymousDisplayName?.trim() || undefined)
        : undefined
      
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
        is_anonymous: isAnonymous,
        anonymous_display_name: anonymousDisplayName,
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
        onImportComplete?.({
          matchTitle: selectedMatch?.title || analysisResult.parsed_title || analysisResult.torrent_name,
          matchId: getMetaId(),
        })
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
        onImportComplete?.({
          matchTitle: selectedMatch?.title || analysisResult.parsed_title || analysisResult.torrent_name,
          matchId: getMetaId(),
        })
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
    setContentType('movie')
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
    setManualSearchTitle('')
    setManualSearchYear('')
    setManualSearchError(null)
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

        {contentType !== 'sports' && (
          <Card>
            <CardContent className="pt-4 space-y-3">
              <div>
                <h4 className="text-sm font-medium">Search Metadata Manually</h4>
                <p className="text-xs text-muted-foreground">
                  When auto-match fails, search by title and optional year.
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-[1fr_110px] gap-2">
                <Input
                  value={manualSearchTitle}
                  onChange={(e) => setManualSearchTitle(e.target.value)}
                  placeholder="Title (e.g. Stardust)"
                />
                <Input
                  value={manualSearchYear}
                  onChange={(e) => setManualSearchYear(e.target.value.replace(/[^\d]/g, '').slice(0, 4))}
                  placeholder="Year"
                />
              </div>
              <Button
                variant="outline"
                onClick={handleManualSearch}
                disabled={manualSearching || manualSearchTitle.trim().length < 2}
                className="w-full"
              >
                {manualSearching ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Searching...
                  </>
                ) : (
                  <>
                    <Search className="h-4 w-4 mr-2" />
                    Search Matches
                  </>
                )}
              </Button>
              {manualSearchError && (
                <Alert variant="destructive">
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>{manualSearchError}</AlertDescription>
                </Alert>
              )}
            </CardContent>
          </Card>
        )}

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
      {torrentPrefetchWarning && !torrentFile && prefilledData?.torrentUrl && (
        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{torrentPrefetchWarning}</AlertDescription>
        </Alert>
      )}

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
