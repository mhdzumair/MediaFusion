import { useState, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { FileInput, Magnet, Upload, FileVideo, Tv, AlertTriangle, Newspaper, Youtube, Globe, Radio } from 'lucide-react'
import { useImportMagnet, useImportTorrent, useAnalyzeMagnet, useAnalyzeTorrent, useIPTVImportSettings } from '@/hooks'
import type { TorrentAnalyzeResponse, ImportResponse, TorrentMetaType } from '@/lib/api'
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
  M3UTab,
  XtreamTab,
  YouTubeTab,
  HTTPTab,
  AceStreamTab,
  TorrentImportDialog,
  ImportResultBanner,
  ContentTypeSelector,
  type ImportResult,
  type TorrentImportFormData,
} from './components'

interface LocationState {
  prefillMagnet?: string
  prefillType?: ContentType
}

export function ContentImportPage() {
  const location = useLocation()
  const locationState = location.state as LocationState | null

  const [activeTab, setActiveTab] = useState('magnet')
  const [importResult, setImportResult] = useState<ImportResult | null>(null)

  // Content type for initial analysis
  const [selectedContentType, setSelectedContentType] = useState<ContentType>(locationState?.prefillType || 'movie')

  // Import mode for multi-content support
  const [importMode, setImportMode] = useState<ImportMode>('single')

  // Torrent import state
  const [torrentAnalysis, setTorrentAnalysis] = useState<TorrentAnalyzeResponse | null>(null)
  const [torrentDialogOpen, setTorrentDialogOpen] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [magnetLink, setMagnetLink] = useState('')

  const importMagnet = useImportMagnet()
  const importTorrent = useImportTorrent()
  const analyzeMagnet = useAnalyzeMagnet()

  // Fetch IPTV import settings from server
  const { data: iptvSettings } = useIPTVImportSettings()

  // Handle magnet analysis completion
  const handleMagnetAnalysis = useCallback((analysis: TorrentAnalyzeResponse, magnet: string) => {
    setTorrentAnalysis(analysis)
    setMagnetLink(magnet)
    setSelectedFile(null)
    setTorrentDialogOpen(true)
  }, [])

  // Handle torrent analysis completion
  const handleTorrentAnalysis = useCallback((analysis: TorrentAnalyzeResponse, file: File) => {
    setTorrentAnalysis(analysis)
    setSelectedFile(file)
    setMagnetLink('')
    setTorrentDialogOpen(true)
  }, [])

  // Hook for torrent file analysis
  const analyzeTorrent = useAnalyzeTorrent()

  // Handle re-analyze with different content type
  const handleReanalyze = useCallback(
    async (contentType: ContentType) => {
      const metaType = toTorrentMetaType(contentType)
      if (magnetLink) {
        try {
          const result = await analyzeMagnet.mutateAsync({
            magnet_link: magnetLink,
            meta_type: metaType,
          })
          setTorrentAnalysis(result)
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
          setTorrentAnalysis(result)
        } catch {
          setImportResult({ success: false, message: 'Re-analysis failed' })
        }
      }
    },
    [magnetLink, selectedFile, analyzeMagnet, analyzeTorrent],
  )

  // Handle torrent import from dialog
  const handleTorrentImport = useCallback(
    async (formData: TorrentImportFormData): Promise<ImportResponse> => {
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

        if (result.status === 'success') {
          setImportResult({ success: true, message: result.message || 'Import successful!' })
          setTorrentDialogOpen(false)
          setTorrentAnalysis(null)
          setSelectedFile(null)
          setMagnetLink('')
        } else if (result.status === 'warning') {
          setImportResult({ success: true, message: result.message })
          setTorrentDialogOpen(false)
          setTorrentAnalysis(null)
        }

        return result
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : 'Import failed'
        setImportResult({ success: false, message: errorMessage })
        return { status: 'error', message: errorMessage }
      }
    },
    [magnetLink, selectedFile, importMagnet, importTorrent],
  )

  const handleSuccess = useCallback((message: string) => {
    setImportResult({ success: true, message })
  }, [])

  const handleError = useCallback((message: string) => {
    setImportResult({ success: false, message })
  }, [])

  const isImporting =
    importMagnet.isPending || importTorrent.isPending || analyzeMagnet.isPending || analyzeTorrent.isPending

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
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
        <TabsList className="grid w-full grid-cols-4 md:grid-cols-8 p-1 bg-muted/50 rounded-xl gap-1">
          <TabsTrigger
            value="magnet"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm"
          >
            <Magnet className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">Magnet</span>
          </TabsTrigger>
          <TabsTrigger
            value="torrent"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm"
          >
            <Upload className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">Torrent</span>
          </TabsTrigger>
          <TabsTrigger
            value="nzb"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm"
          >
            <Newspaper className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">NZB</span>
          </TabsTrigger>
          <TabsTrigger
            value="m3u"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm"
          >
            <FileVideo className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">M3U</span>
          </TabsTrigger>
          <TabsTrigger
            value="xtream"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm"
          >
            <Tv className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">Xtream</span>
          </TabsTrigger>
          <TabsTrigger
            value="youtube"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm"
          >
            <Youtube className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4 text-red-500" />
            <span className="hidden sm:inline">YouTube</span>
          </TabsTrigger>
          <TabsTrigger
            value="http"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm"
          >
            <Globe className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">HTTP</span>
          </TabsTrigger>
          <TabsTrigger
            value="acestream"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm text-xs md:text-sm"
          >
            <Radio className="mr-1.5 h-3.5 w-3.5 md:h-4 md:w-4 text-green-500" />
            <span className="hidden sm:inline">AceStream</span>
          </TabsTrigger>
        </TabsList>

        {/* Magnet Link Tab */}
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

        {/* Torrent File Tab */}
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

        {/* NZB Tab */}
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
            onAnalysisComplete={(analysis) => {
              // For now, show a success message - full import dialog would be similar to TorrentImportDialog
              if (analysis.matches && analysis.matches.length > 0) {
                handleSuccess(
                  `NZB analyzed: ${analysis.nzb_title || 'Unknown'} - ${analysis.matches.length} matches found`,
                )
              } else {
                handleError('No metadata matches found for this NZB')
              }
            }}
            onError={handleError}
            contentType={selectedContentType}
          />
        </TabsContent>

        {/* M3U Playlist Tab */}
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

        {/* Xtream Codes Tab */}
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

        {/* YouTube Tab */}
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

          <YouTubeTab onSuccess={handleSuccess} onError={handleError} contentType={selectedContentType} />
        </TabsContent>

        {/* HTTP Tab */}
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

        {/* AceStream Tab */}
        <TabsContent value="acestream" className="space-y-6">
          <AceStreamTab onSuccess={handleSuccess} onError={handleError} />
        </TabsContent>
      </Tabs>

      {/* Enhanced Torrent Import Dialog */}
      <TorrentImportDialog
        open={torrentDialogOpen}
        onOpenChange={setTorrentDialogOpen}
        analysis={torrentAnalysis}
        magnetLink={magnetLink || undefined}
        torrentFile={selectedFile}
        onImport={handleTorrentImport}
        onReanalyze={handleReanalyze}
        isImporting={isImporting}
        initialContentType={selectedContentType}
        importMode={importMode}
        onImportModeChange={setImportMode}
      />
    </div>
  )
}
