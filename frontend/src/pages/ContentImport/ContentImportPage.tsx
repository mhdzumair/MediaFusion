import { useState, useCallback, useMemo } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area'
import {
  FileInput,
  Magnet,
  Upload,
  FileVideo,
  Tv,
  AlertTriangle,
  Newspaper,
  Youtube,
  Globe,
  Radio,
  Send,
  HardDrive,
} from 'lucide-react'
import {
  useImportMagnet,
  useImportTorrent,
  useAnalyzeMagnet,
  useAnalyzeTorrent,
  useImportNZBFile,
  useImportNZBUrl,
  useIPTVImportSettings,
} from '@/hooks'
import { getAppConfig } from '@/lib/api/instance'
import type {
  TorrentAnalyzeResponse,
  ImportResponse,
  TorrentMetaType,
  NZBAnalyzeResponse,
  YouTubeAnalyzeResponse,
} from '@/lib/api'
import { contentImportApi } from '@/lib/api'
import type { ContentType, ImportMode } from '@/lib/constants'

// Helper to convert ContentType to TorrentMetaType (defaults to 'movie' for unsupported types like 'tv')
function toTorrentMetaType(contentType: ContentType): TorrentMetaType {
  if (contentType === 'tv') return 'movie'
  return contentType
}
import {
  MagnetTab,
  TorrentTab,
  NZBTab,
  type NZBSource,
  M3UTab,
  XtreamTab,
  YouTubeTab,
  HTTPTab,
  AceStreamTab,
  TelegramTab,
  DebridTab,
  TorrentImportDialog,
  NZBImportDialog,
  YouTubeImportDialog,
  type YouTubeImportFormData,
  ImportResultBanner,
  ContentTypeSelector,
  type ImportResult,
  type TorrentBatchAnalysisItem,
  type TorrentDialogQueueItem,
  type TorrentImportSubmitOptions,
  type TorrentImportFormData,
  type NZBImportFormData,
} from './components'

interface LocationState {
  prefillMagnet?: string
  prefillType?: ContentType
}

/**
 * Maps each tab value to the disable key used in `disabled_content_types`.
 * Both m3u and xtream are controlled by the single "iptv" key.
 */
const TAB_DISABLE_KEY: Record<string, string> = {
  magnet: 'magnet',
  torrent: 'torrent',
  nzb: 'nzb',
  m3u: 'iptv',
  xtream: 'iptv',
  youtube: 'youtube',
  http: 'http',
  acestream: 'acestream',
  telegram: 'telegram',
  debrid: 'debrid',
}

/** Ordered list of all import tab values. */
const ALL_TABS = [
  'debrid',
  'magnet',
  'torrent',
  'nzb',
  'm3u',
  'xtream',
  'youtube',
  'http',
  'acestream',
  'telegram',
] as const

export function ContentImportPage() {
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const locationState = location.state as LocationState | null

  // Read initial tab from URL search params (e.g. ?tab=debrid)
  const urlTab = searchParams.get('tab')

  // Fetch app config to determine which import types are disabled
  const { data: appConfig } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
    staleTime: 5 * 60 * 1000,
  })

  const disabledTypes = useMemo(() => new Set(appConfig?.disabled_content_types ?? []), [appConfig])

  /** Check whether a tab is enabled (its disable key is not in the disabled set). */
  const isTabEnabled = useCallback((tab: string) => !disabledTypes.has(TAB_DISABLE_KEY[tab]), [disabledTypes])

  /** First enabled tab, used as the default active tab. */
  const defaultTab = useMemo(() => ALL_TABS.find(isTabEnabled) ?? 'magnet', [isTabEnabled])

  const [activeTab, setActiveTab] = useState(
    urlTab && ALL_TABS.includes(urlTab as (typeof ALL_TABS)[number]) ? urlTab : 'debrid',
  )
  const [importResult, setImportResult] = useState<ImportResult | null>(null)

  // Sync tab changes back to URL params
  const handleTabChange = useCallback(
    (tab: string) => {
      setActiveTab(tab)
      if (tab === 'debrid') {
        searchParams.delete('tab')
      } else {
        searchParams.set('tab', tab)
      }
      setSearchParams(searchParams, { replace: true })
    },
    [searchParams, setSearchParams],
  )

  // Handle URL param changes (e.g. navigating from Watchlist with ?tab=debrid)
  const [prevUrlTab, setPrevUrlTab] = useState(urlTab)
  if (urlTab !== prevUrlTab) {
    setPrevUrlTab(urlTab)
    if (urlTab && ALL_TABS.includes(urlTab as (typeof ALL_TABS)[number])) {
      setActiveTab(urlTab)
    }
  }

  // When config loads, ensure active tab is not a disabled one (during render)
  const [prevAppConfig, setPrevAppConfig] = useState(appConfig)
  if (appConfig !== prevAppConfig) {
    setPrevAppConfig(appConfig)
    if (appConfig && !isTabEnabled(activeTab)) {
      setActiveTab(defaultTab)
    }
  }

  // Content type for initial analysis
  const [selectedContentType, setSelectedContentType] = useState<ContentType>(locationState?.prefillType || 'movie')

  // Import mode for multi-content support
  const [importMode, setImportMode] = useState<ImportMode>('single')

  // Torrent import state
  const [magnetAnalysis, setMagnetAnalysis] = useState<TorrentAnalyzeResponse | null>(null)
  const [torrentDialogOpen, setTorrentDialogOpen] = useState(false)
  const [torrentQueue, setTorrentQueue] = useState<TorrentBatchAnalysisItem[]>([])
  const [currentTorrentQueueIndex, setCurrentTorrentQueueIndex] = useState(0)
  const [queuePrefillByIndex, setQueuePrefillByIndex] = useState<Record<number, Partial<TorrentImportFormData>>>({})
  const [batchSummary, setBatchSummary] = useState({ success: 0, warning: 0, error: 0 })
  const [magnetLink, setMagnetLink] = useState('')

  const importMagnet = useImportMagnet()
  const importTorrent = useImportTorrent()
  const analyzeMagnet = useAnalyzeMagnet()

  // YouTube import state
  const [youtubeAnalysis, setYoutubeAnalysis] = useState<YouTubeAnalyzeResponse | null>(null)
  const [youtubeDialogOpen, setYoutubeDialogOpen] = useState(false)
  const [youtubeUrl, setYoutubeUrl] = useState('')

  // NZB import state
  const [nzbAnalysis, setNzbAnalysis] = useState<NZBAnalyzeResponse | null>(null)
  const [nzbDialogOpen, setNzbDialogOpen] = useState(false)
  const [nzbSource, setNzbSource] = useState<NZBSource | null>(null)

  const importNZBFile = useImportNZBFile()
  const importNZBUrl = useImportNZBUrl()

  // Fetch IPTV import settings from server
  const { data: iptvSettings } = useIPTVImportSettings()

  const currentTorrentItem = useMemo(
    () => (torrentQueue.length > 0 ? torrentQueue[currentTorrentQueueIndex] || null : null),
    [torrentQueue, currentTorrentQueueIndex],
  )
  const torrentAnalysis = magnetLink ? magnetAnalysis : currentTorrentItem?.analysis || null
  const selectedFile = magnetLink ? null : currentTorrentItem?.file || null
  const queueItems = useMemo<TorrentDialogQueueItem[]>(
    () => torrentQueue.map((item, index) => ({ index, label: item.file.name })),
    [torrentQueue],
  )
  const queuePrefillData = magnetLink ? undefined : queuePrefillByIndex[currentTorrentQueueIndex]

  const resetTorrentDialogState = useCallback(() => {
    setTorrentDialogOpen(false)
    setMagnetAnalysis(null)
    setTorrentQueue([])
    setCurrentTorrentQueueIndex(0)
    setQueuePrefillByIndex({})
    setBatchSummary({ success: 0, warning: 0, error: 0 })
    setMagnetLink('')
  }, [])

  const handleTorrentDialogOpenChange = useCallback(
    (open: boolean) => {
      if (!open) {
        resetTorrentDialogState()
      } else {
        setTorrentDialogOpen(true)
      }
    },
    [resetTorrentDialogState],
  )

  // Handle magnet analysis completion
  const handleMagnetAnalysis = useCallback((analysis: TorrentAnalyzeResponse, magnet: string) => {
    setMagnetAnalysis(analysis)
    setMagnetLink(magnet)
    setTorrentQueue([])
    setCurrentTorrentQueueIndex(0)
    setQueuePrefillByIndex({})
    setBatchSummary({ success: 0, warning: 0, error: 0 })
    setTorrentDialogOpen(true)
  }, [])

  // Handle torrent analysis completion
  const handleTorrentAnalysis = useCallback((items: TorrentBatchAnalysisItem[]) => {
    if (items.length === 0) {
      setImportResult({ success: false, message: 'No valid torrent files were analyzed' })
      return
    }

    setTorrentQueue(items)
    setCurrentTorrentQueueIndex(0)
    setQueuePrefillByIndex({})
    setBatchSummary({ success: 0, warning: 0, error: 0 })
    setMagnetAnalysis(null)
    setMagnetLink('')
    setTorrentDialogOpen(true)
  }, [])

  // Hook for torrent file analysis
  const analyzeTorrent = useAnalyzeTorrent()

  // Handle re-analyze with different content type
  const handleReanalyze = useCallback(
    async (contentType: ContentType) => {
      setSelectedContentType(contentType)
      const metaType = toTorrentMetaType(contentType)
      if (magnetLink) {
        try {
          const result = await analyzeMagnet.mutateAsync({
            magnet_link: magnetLink,
            meta_type: metaType,
          })
          setMagnetAnalysis(result)
        } catch {
          setImportResult({ success: false, message: 'Re-analysis failed' })
        }
      } else if (selectedFile) {
        // Re-analyze torrent file with new content type
        try {
          const result = await analyzeTorrent.mutateAsync({
            file: selectedFile,
            metaType: metaType,
          })
          setTorrentQueue((prev) =>
            prev.map((item, index) => (index === currentTorrentQueueIndex ? { ...item, analysis: result } : item)),
          )
        } catch {
          setImportResult({ success: false, message: 'Re-analysis failed' })
        }
      }
    },
    [magnetLink, selectedFile, analyzeMagnet, analyzeTorrent, currentTorrentQueueIndex],
  )

  // Handle torrent import from dialog
  const handleTorrentImport = useCallback(
    async (formData: TorrentImportFormData, options?: TorrentImportSubmitOptions): Promise<ImportResponse> => {
      try {
        // Build the request data
        const requestData = {
          meta_type: toTorrentMetaType(formData.contentType),
          meta_id: formData.metaId,
          title: formData.title,
          poster: formData.poster,
          background: formData.background,
          logo: formData.logo,
          resolution: formData.resolution,
          quality: formData.quality,
          codec: formData.codec,
          audio: formData.audio?.join(','),
          hdr: formData.hdr?.join(','),
          languages: formData.languages?.join(','),
          catalogs: formData.catalogs?.join(','),
          episode_name_parser: formData.episodeNameParser,
          created_at: formData.releaseDate,
          force_import: formData.forceImport,
          is_add_title_to_poster: false,
          is_anonymous: formData.isAnonymous,
          anonymous_display_name: formData.anonymousDisplayName,
          file_data: formData.fileData ? JSON.stringify(formData.fileData) : undefined,
          sports_category: formData.sportsCategory,
        }

        let result: ImportResponse

        if (magnetLink) {
          result = await importMagnet.mutateAsync({
            magnet_link: magnetLink,
            ...requestData,
          })
        } else if (selectedFile) {
          result = await importTorrent.mutateAsync({
            torrent_file: selectedFile,
            ...requestData,
          })
        } else {
          return { status: 'error', message: 'No torrent source provided' }
        }

        if (!magnetLink && options?.selectedQueueIndices && options.selectedQueueIndices.length > 0) {
          const nextPrefills = { ...queuePrefillByIndex }
          for (const index of options.selectedQueueIndices) {
            if (index !== currentTorrentQueueIndex && index >= 0 && index < torrentQueue.length) {
              nextPrefills[index] = { ...formData }
            }
          }
          setQueuePrefillByIndex(nextPrefills)
        }

        if (magnetLink) {
          if (result.status === 'success') {
            setImportResult({ success: true, message: result.message || 'Import successful!' })
            resetTorrentDialogState()
          } else if (result.status === 'warning') {
            setImportResult({ success: true, message: result.message })
            resetTorrentDialogState()
          }
          return result
        }

        if (
          result.status === 'success' ||
          result.status === 'warning' ||
          result.status === 'error' ||
          result.status === 'processing'
        ) {
          const nextBatchSummary = {
            success: batchSummary.success + (result.status === 'success' ? 1 : 0),
            warning: batchSummary.warning + (result.status === 'warning' ? 1 : 0),
            error: batchSummary.error + (result.status === 'error' ? 1 : 0),
          }
          setBatchSummary(nextBatchSummary)

          const isLastQueueItem = currentTorrentQueueIndex >= torrentQueue.length - 1
          if (!isLastQueueItem) {
            setCurrentTorrentQueueIndex((prev) => prev + 1)
          } else {
            const total = nextBatchSummary.success + nextBatchSummary.warning + nextBatchSummary.error
            setImportResult({
              success: nextBatchSummary.error === 0,
              message: `Batch import complete (${total} total): ${nextBatchSummary.success} imported, ${nextBatchSummary.warning} warnings, ${nextBatchSummary.error} failed.`,
            })
            resetTorrentDialogState()
          }
        }

        return result
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : 'Import failed'
        if (!magnetLink && torrentQueue.length > 0) {
          const nextBatchSummary = {
            success: batchSummary.success,
            warning: batchSummary.warning,
            error: batchSummary.error + 1,
          }
          setBatchSummary(nextBatchSummary)

          const isLastQueueItem = currentTorrentQueueIndex >= torrentQueue.length - 1
          if (!isLastQueueItem) {
            setCurrentTorrentQueueIndex((prev) => prev + 1)
          } else {
            const total = nextBatchSummary.success + nextBatchSummary.warning + nextBatchSummary.error
            setImportResult({
              success: false,
              message: `Batch import complete (${total} total): ${nextBatchSummary.success} imported, ${nextBatchSummary.warning} warnings, ${nextBatchSummary.error} failed.`,
            })
            resetTorrentDialogState()
          }
        } else {
          setImportResult({ success: false, message: errorMessage })
        }

        return { status: 'error', message: errorMessage }
      }
    },
    [
      magnetLink,
      selectedFile,
      importMagnet,
      importTorrent,
      queuePrefillByIndex,
      currentTorrentQueueIndex,
      torrentQueue.length,
      batchSummary,
      resetTorrentDialogState,
    ],
  )

  // Handle YouTube analysis completion
  const handleYouTubeAnalysis = useCallback((analysis: YouTubeAnalyzeResponse, url: string) => {
    setYoutubeAnalysis(analysis)
    setYoutubeUrl(url)
    setYoutubeDialogOpen(true)
  }, [])

  // Handle YouTube import from dialog
  const [youtubeImporting, setYoutubeImporting] = useState(false)
  const handleYouTubeImport = useCallback(
    async (formData: YouTubeImportFormData): Promise<ImportResponse> => {
      setYoutubeImporting(true)
      try {
        const result = await contentImportApi.importYouTube({
          youtube_url: youtubeUrl,
          meta_type: formData.contentType,
          meta_id: formData.metaId,
          title: formData.title,
          poster: formData.poster,
          background: formData.background,
          resolution: formData.resolution,
          quality: formData.quality,
          codec: formData.codec,
          languages: formData.languages?.join(','),
          catalogs: formData.catalogs?.join(','),
          is_anonymous: formData.isAnonymous,
          anonymous_display_name: formData.anonymousDisplayName,
          force_import: formData.forceImport,
        })

        if (result.status === 'success') {
          setImportResult({ success: true, message: result.message || 'YouTube video imported successfully!' })
          setYoutubeDialogOpen(false)
          setYoutubeAnalysis(null)
          setYoutubeUrl('')
        } else if (result.status === 'warning') {
          setImportResult({ success: true, message: result.message })
          setYoutubeDialogOpen(false)
          setYoutubeAnalysis(null)
        } else {
          setImportResult({ success: false, message: result.message || 'Failed to import YouTube video' })
        }

        return result
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : 'YouTube import failed'
        setImportResult({ success: false, message: errorMessage })
        return { status: 'error', message: errorMessage }
      } finally {
        setYoutubeImporting(false)
      }
    },
    [youtubeUrl],
  )

  // Handle NZB analysis completion
  const handleNZBAnalysis = useCallback((analysis: NZBAnalyzeResponse, source: NZBSource) => {
    setNzbAnalysis(analysis)
    setNzbSource(source)
    setNzbDialogOpen(true)
  }, [])

  // Handle NZB import from dialog
  const handleNZBImport = useCallback(
    async (formData: NZBImportFormData): Promise<ImportResponse> => {
      try {
        const metaType = formData.contentType === 'series' ? 'series' : 'movie'
        let result: ImportResponse

        if (nzbSource?.type === 'file' && nzbSource.file) {
          result = await importNZBFile.mutateAsync({
            nzb_file: nzbSource.file,
            meta_type: metaType,
            meta_id: formData.metaId,
            title: formData.title,
            resolution: formData.resolution,
            quality: formData.quality,
            codec: formData.codec,
            languages: formData.languages?.join(','),
            force_import: formData.forceImport,
            is_anonymous: formData.isAnonymous,
            anonymous_display_name: formData.anonymousDisplayName,
          })
        } else if (nzbSource?.type === 'url' && nzbSource.url) {
          result = await importNZBUrl.mutateAsync({
            nzb_url: nzbSource.url,
            meta_type: metaType,
            meta_id: formData.metaId,
            title: formData.title,
            is_anonymous: formData.isAnonymous,
            anonymous_display_name: formData.anonymousDisplayName,
          })
        } else {
          return { status: 'error', message: 'No NZB source provided' }
        }

        if (result.status === 'success') {
          setImportResult({ success: true, message: result.message || 'NZB imported successfully!' })
          setNzbDialogOpen(false)
          setNzbAnalysis(null)
          setNzbSource(null)
        } else if (result.status === 'warning') {
          setImportResult({ success: true, message: result.message })
          setNzbDialogOpen(false)
          setNzbAnalysis(null)
        }

        return result
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : 'NZB import failed'
        setImportResult({ success: false, message: errorMessage })
        return { status: 'error', message: errorMessage }
      }
    },
    [nzbSource, importNZBFile, importNZBUrl],
  )

  const handleSuccess = useCallback((message: string) => {
    setImportResult({ success: true, message })
  }, [])

  const handleError = useCallback((message: string) => {
    setImportResult({ success: false, message })
  }, [])

  const isImporting =
    importMagnet.isPending || importTorrent.isPending || analyzeMagnet.isPending || analyzeTorrent.isPending
  const isNZBImporting = importNZBFile.isPending || importNZBUrl.isPending
  const importTabTriggerClass =
    'shrink-0 sm:flex-1 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
          <div className="p-2 rounded-xl bg-gradient-to-br from-blue-500 to-cyan-600 shadow-lg shadow-blue-500/20">
            <FileInput className="h-5 w-5 text-white" />
          </div>
          Content Import
        </h1>
        <p className="text-muted-foreground mt-1">Import torrents and playlists to expand your content library</p>
      </div>

      {/* Import Result Banner */}
      {importResult && <ImportResultBanner result={importResult} onDismiss={() => setImportResult(null)} />}

      {/* Import Tabs */}
      <Tabs value={activeTab} onValueChange={handleTabChange} className="space-y-6">
        <ScrollArea className="-mx-1 px-1 pb-1">
          <TabsList className="h-auto w-max min-w-full flex-nowrap gap-1 rounded-xl bg-muted/50 p-1">
            <TabsTrigger value="debrid" className={importTabTriggerClass}>
              <HardDrive className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
              <span className="hidden sm:inline">Debrid</span>
            </TabsTrigger>
            {isTabEnabled('magnet') && (
              <TabsTrigger value="magnet" className={importTabTriggerClass}>
                <Magnet className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
                <span className="hidden sm:inline">Magnet</span>
              </TabsTrigger>
            )}
            {isTabEnabled('torrent') && (
              <TabsTrigger value="torrent" className={importTabTriggerClass}>
                <Upload className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
                <span className="hidden sm:inline">Torrent</span>
              </TabsTrigger>
            )}
            {isTabEnabled('nzb') && (
              <TabsTrigger value="nzb" className={importTabTriggerClass}>
                <Newspaper className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
                <span className="hidden sm:inline">NZB</span>
              </TabsTrigger>
            )}
            {isTabEnabled('m3u') && (
              <TabsTrigger value="m3u" className={importTabTriggerClass}>
                <FileVideo className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
                <span className="hidden sm:inline">M3U</span>
              </TabsTrigger>
            )}
            {isTabEnabled('xtream') && (
              <TabsTrigger value="xtream" className={importTabTriggerClass}>
                <Tv className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
                <span className="hidden sm:inline">Xtream</span>
              </TabsTrigger>
            )}
            {isTabEnabled('youtube') && (
              <TabsTrigger value="youtube" className={importTabTriggerClass}>
                <Youtube className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4 text-red-500" />
                <span className="hidden sm:inline">YouTube</span>
              </TabsTrigger>
            )}
            {isTabEnabled('http') && (
              <TabsTrigger value="http" className={importTabTriggerClass}>
                <Globe className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
                <span className="hidden sm:inline">HTTP</span>
              </TabsTrigger>
            )}
            {isTabEnabled('acestream') && (
              <TabsTrigger value="acestream" className={importTabTriggerClass}>
                <Radio className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4 text-green-500" />
                <span className="hidden sm:inline">AceStream</span>
              </TabsTrigger>
            )}
            {isTabEnabled('telegram') && (
              <TabsTrigger value="telegram" className={importTabTriggerClass}>
                <Send className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4 text-blue-500" />
                <span className="hidden sm:inline">Telegram</span>
              </TabsTrigger>
            )}
          </TabsList>
          <ScrollBar orientation="horizontal" />
        </ScrollArea>
        <p className="px-1 text-[11px] text-muted-foreground sm:hidden">Swipe left/right to view all import tabs</p>

        {/* Magnet Link Tab */}
        {isTabEnabled('magnet') && (
          <TabsContent value="magnet" className="space-y-6">
            {/* Content Type Selector */}
            <Card className="glass border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Content Type</CardTitle>
                <CardDescription className="text-sm">Select the type of content you&apos;re importing</CardDescription>
              </CardHeader>
              <CardContent>
                <ContentTypeSelector
                  value={selectedContentType}
                  importMode={importMode}
                  onChange={(newType) => {
                    setSelectedContentType(newType)
                    // Reset import mode when content type changes
                    setImportMode('single')
                  }}
                  onImportModeChange={setImportMode}
                  showImportMode={selectedContentType !== 'sports'}
                  excludeTypes={['tv']}
                />
              </CardContent>
            </Card>

            <MagnetTab
              onAnalysisComplete={handleMagnetAnalysis}
              onError={handleError}
              contentType={selectedContentType}
              initialMagnet={locationState?.prefillMagnet}
              autoAnalyze={!!locationState?.prefillMagnet}
            />
          </TabsContent>
        )}

        {/* Torrent File Tab */}
        {isTabEnabled('torrent') && (
          <TabsContent value="torrent" className="space-y-6">
            {/* Content Type Selector */}
            <Card className="glass border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Content Type</CardTitle>
                <CardDescription className="text-sm">Select the type of content you&apos;re importing</CardDescription>
              </CardHeader>
              <CardContent>
                <ContentTypeSelector
                  value={selectedContentType}
                  importMode={importMode}
                  onChange={(newType) => {
                    setSelectedContentType(newType)
                    // Reset import mode when content type changes
                    setImportMode('single')
                  }}
                  onImportModeChange={setImportMode}
                  showImportMode={selectedContentType !== 'sports'}
                  excludeTypes={['tv']}
                />
              </CardContent>
            </Card>

            <TorrentTab
              onAnalysisComplete={handleTorrentAnalysis}
              onError={handleError}
              contentType={selectedContentType}
            />
          </TabsContent>
        )}

        {/* NZB Tab */}
        {isTabEnabled('nzb') && (
          <TabsContent value="nzb" className="space-y-6">
            {/* Content Type Selector */}
            <Card className="glass border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Content Type</CardTitle>
                <CardDescription className="text-sm">Select the type of content you&apos;re importing</CardDescription>
              </CardHeader>
              <CardContent>
                <ContentTypeSelector
                  value={selectedContentType}
                  importMode={importMode}
                  onChange={(newType) => {
                    setSelectedContentType(newType)
                    setImportMode('single')
                  }}
                  onImportModeChange={setImportMode}
                  showImportMode={selectedContentType !== 'sports'}
                  excludeTypes={['tv']}
                />
              </CardContent>
            </Card>

            <NZBTab
              onAnalysisComplete={handleNZBAnalysis}
              onError={handleError}
              contentType={selectedContentType}
              fileImportEnabled={appConfig?.nzb_file_import_enabled ?? false}
            />
          </TabsContent>
        )}

        {/* M3U Playlist Tab */}
        {isTabEnabled('m3u') && (
          <TabsContent value="m3u" className="space-y-6">
            {iptvSettings?.enabled === false ? (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>IPTV import feature is disabled on this server.</AlertDescription>
              </Alert>
            ) : (
              <M3UTab onSuccess={handleSuccess} onError={handleError} iptvSettings={iptvSettings} />
            )}
          </TabsContent>
        )}

        {/* Xtream Codes Tab */}
        {isTabEnabled('xtream') && (
          <TabsContent value="xtream" className="space-y-6">
            {iptvSettings?.enabled === false ? (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>IPTV import feature is disabled on this server.</AlertDescription>
              </Alert>
            ) : (
              <XtreamTab onSuccess={handleSuccess} onError={handleError} iptvSettings={iptvSettings} />
            )}
          </TabsContent>
        )}

        {/* YouTube Tab */}
        {isTabEnabled('youtube') && (
          <TabsContent value="youtube" className="space-y-6">
            {/* Content Type Selector */}
            <Card className="glass border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Content Type</CardTitle>
                <CardDescription className="text-sm">
                  Select the type of content this YouTube video belongs to
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ContentTypeSelector
                  value={selectedContentType}
                  importMode="single"
                  onChange={setSelectedContentType}
                  showImportMode={false}
                  excludeTypes={['tv']}
                />
              </CardContent>
            </Card>

            <YouTubeTab
              onAnalysisComplete={handleYouTubeAnalysis}
              onError={handleError}
              contentType={selectedContentType}
            />
          </TabsContent>
        )}

        {/* HTTP Tab */}
        {isTabEnabled('http') && (
          <TabsContent value="http" className="space-y-6">
            {/* Content Type Selector */}
            <Card className="glass border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Content Type</CardTitle>
                <CardDescription className="text-sm">Select the type of content for this HTTP stream</CardDescription>
              </CardHeader>
              <CardContent>
                <ContentTypeSelector
                  value={selectedContentType}
                  importMode="single"
                  onChange={setSelectedContentType}
                  showImportMode={false}
                  excludeTypes={['tv']}
                />
              </CardContent>
            </Card>

            <HTTPTab onSuccess={handleSuccess} onError={handleError} contentType={selectedContentType} />
          </TabsContent>
        )}

        {/* AceStream Tab */}
        {isTabEnabled('acestream') && (
          <TabsContent value="acestream" className="space-y-6">
            <AceStreamTab onSuccess={handleSuccess} onError={handleError} />
          </TabsContent>
        )}

        {/* Telegram Bot Tab */}
        {isTabEnabled('telegram') && (
          <TabsContent value="telegram" className="space-y-6">
            <TelegramTab telegram={appConfig?.telegram} />
          </TabsContent>
        )}

        {/* Debrid Import Tab */}
        <TabsContent value="debrid" className="space-y-6">
          <DebridTab />
        </TabsContent>
      </Tabs>

      {/* Enhanced Torrent Import Dialog */}
      <TorrentImportDialog
        open={torrentDialogOpen}
        onOpenChange={handleTorrentDialogOpenChange}
        analysis={torrentAnalysis}
        magnetLink={magnetLink || undefined}
        torrentFile={selectedFile}
        currentIndex={magnetLink ? undefined : currentTorrentQueueIndex}
        totalItems={magnetLink ? undefined : torrentQueue.length}
        queueItems={magnetLink ? undefined : queueItems}
        prefillData={queuePrefillData}
        onImport={handleTorrentImport}
        onReanalyze={handleReanalyze}
        isImporting={isImporting}
        initialContentType={selectedContentType}
        importMode={importMode}
        onImportModeChange={setImportMode}
      />

      {/* NZB Import Dialog */}
      <NZBImportDialog
        open={nzbDialogOpen}
        onOpenChange={setNzbDialogOpen}
        analysis={nzbAnalysis}
        nzbSource={nzbSource || undefined}
        onImport={handleNZBImport}
        isImporting={isNZBImporting}
        initialContentType={selectedContentType}
      />

      {/* YouTube Import Dialog */}
      <YouTubeImportDialog
        open={youtubeDialogOpen}
        onOpenChange={setYoutubeDialogOpen}
        analysis={youtubeAnalysis}
        youtubeUrl={youtubeUrl}
        onImport={handleYouTubeImport}
        isImporting={youtubeImporting}
        initialContentType={selectedContentType}
      />
    </div>
  )
}
