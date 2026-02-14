import { useState, useMemo, useCallback, useRef } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import {
  ArrowLeft,
  Star,
  Clock,
  Calendar,
  Film,
  Heart,
  HeartOff,
  Play,
  Download,
  Copy,
  Check,
  Loader2,
  Info,
  Wifi,
  HardDrive,
  Ban,
  Trash2,
  Hash,
  Layers,
} from 'lucide-react'
import {
  useCatalogItem,
  useCatalogStreams,
  useLibraryCheck,
  useAddToLibrary,
  useRemoveFromLibraryByMediaId,
  useTrackStreamAction,
  useCreateStreamSuggestion,
  useProfiles,
  useDeleteEpisodeAdmin,
  useUpdateWatchProgress,
  type CatalogType,
} from '@/hooks'
import { useBlockTorrentStream, useDeleteTorrentStream } from '@/hooks/useAdmin'
import { useAuth } from '@/contexts/AuthContext'
import { useRpdb } from '@/contexts/RpdbContext'
import type { StreamingProviderInfo } from '@/lib/api'
import {
  StreamVoteButtons,
  StreamReport,
  StreamCard,
  StreamFilters,
  StreamGroupedList,
  ViewModeToggle,
  StreamEditSheet,
  FileAnnotationDialog,
  StreamRelinkButton,
  CollectionAnnotationDialog,
  defaultStreamFilters,
  type StreamFilterState,
  type ViewMode,
  type FileLink,
  type EditedFileLink,
} from '@/components/stream'
import {
  MetadataActions,
  MetadataEditSheet,
  ContentLikesBadge,
  RefreshMetadataButton,
  ScrapeContentButton,
  ExternalIdsDisplay,
  BlockContentButton,
} from '@/components/metadata'
import { RatingsDisplay, ContentGuidance, SeriesEpisodePicker, TrailerButton } from '@/components/content'
import { PlayerDialog, ExternalPlayerMenu } from '@/components/player'
import { Poster, Backdrop } from '@/components/ui/poster'
import type { CatalogStreamInfo } from '@/lib/api'

// Stream Action Dialog Component
interface StreamActionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stream: CatalogStreamInfo | null
  mediaId: number
  title: string
  catalogType: 'movie' | 'series' | 'tv'
  season?: number
  episode?: number
  selectedProvider?: string | null // Currently selected provider service name
  hasMediaflowProxy: boolean // Whether MediaFlow proxy is configured for in-browser playback
  onStreamDeleted?: () => void
  onWatch?: (stream: CatalogStreamInfo, streamUrl: string) => void // Callback to open player at page level
}

function StreamActionDialog({
  open,
  onOpenChange,
  stream,
  mediaId,
  title,
  catalogType,
  season,
  episode,
  selectedProvider,
  hasMediaflowProxy,
  onStreamDeleted,
  onWatch,
}: StreamActionDialogProps) {
  // Check if selected provider is a debrid provider (not P2P)
  const isDebridProvider = selectedProvider && selectedProvider !== 'p2p'
  const [copied, setCopied] = useState(false)
  const [fileAnnotationOpen, setFileAnnotationOpen] = useState(false)
  const [collectionAnnotationOpen, setCollectionAnnotationOpen] = useState(false)
  const [fileLinks, setFileLinks] = useState<FileLink[]>([])
  const [isLoadingFileLinks, setIsLoadingFileLinks] = useState(false)
  const [isSavingFileLinks, setIsSavingFileLinks] = useState(false)
  const trackAction = useTrackStreamAction()
  const createStreamSuggestion = useCreateStreamSuggestion()
  const { hasMinimumRole } = useAuth()
  const isModerator = hasMinimumRole('moderator')

  // Moderator mutations
  const blockStream = useBlockTorrentStream()
  const deleteStream = useDeleteTorrentStream()
  const isDeleting = blockStream.isPending || deleteStream.isPending

  const handleBlock = async () => {
    if (!stream?.id) return
    await blockStream.mutateAsync(stream.id)
    onOpenChange(false)
    onStreamDeleted?.()
  }

  const handleDelete = async () => {
    if (!stream?.id) return
    await deleteStream.mutateAsync(stream.id)
    onOpenChange(false)
    onStreamDeleted?.()
  }

  // Stream URL is pre-resolved from the Stremio endpoint
  const streamUrl = stream?.url

  // Check if this is a torrent stream (has info_hash)
  const isTorrentStream = stream && !!stream.info_hash

  // Check if this is a Telegram stream (doesn't require debrid)
  const isTelegramStream = stream?.stream_type === 'telegram'

  // Check if this is an AceStream stream (doesn't require debrid, uses MediaFlow proxy)
  const isAceStreamStream = stream?.stream_type === 'acestream'

  // Streams that use MediaFlow proxy directly (no debrid needed)
  const isDirectProxyStream = isTelegramStream || isAceStreamStream

  const handleAction = async (action: 'download' | 'queue' | 'copy') => {
    if (!stream) return

    // Track the action (skip copy - not worth tracking)
    if (action !== 'copy') {
      await trackAction.mutateAsync({
        media_id: mediaId,
        title,
        catalog_type: catalogType,
        season,
        episode,
        action,
        stream_info: {
          name: stream.name,
          quality: stream.quality,
          size: stream.size,
          source: stream.source,
        },
      })
    }

    if (action === 'copy' && streamUrl) {
      await navigator.clipboard.writeText(streamUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } else if (action === 'download' && streamUrl) {
      window.open(streamUrl, '_blank')
      onOpenChange(false)
    }
  }

  const handleWatch = () => {
    if (stream && streamUrl && onWatch) {
      onOpenChange(false) // Close stream action dialog
      onWatch(stream, streamUrl) // Open player at page level
    }
  }

  const handleDownload = () => {
    if (streamUrl) {
      window.open(streamUrl, '_blank')
      handleAction('download')
    }
  }

  // File annotation handlers (for series episode link correction)
  const handleOpenFileAnnotation = async () => {
    if (!stream?.id) return

    // Need to look up the media ID from external_id
    // For now, we need to fetch file links
    setIsLoadingFileLinks(true)
    try {
      // The API needs stream_id (torrent ID) and media_id
      // We have metaId (external_id), need to convert or use a different approach
      // For now, use the episode_links from the stream if available
      if (stream.episode_links && stream.episode_links.length > 0) {
        setFileLinks(
          stream.episode_links.map((el) => ({
            file_id: el.file_id,
            file_name: el.file_name,
            season_number: el.season_number ?? null,
            episode_number: el.episode_number ?? null,
            episode_end: el.episode_end ?? null,
          })),
        )
        setFileAnnotationOpen(true)
      }
    } catch (error) {
      console.error('Failed to load file links:', error)
    } finally {
      setIsLoadingFileLinks(false)
    }
  }

  const handleSaveFileLinks = async (editedFiles: EditedFileLink[]) => {
    if (!stream?.id) return

    setIsSavingFileLinks(true)
    try {
      // Find the original files to calculate which fields changed
      const originalFiles = stream.episode_links || []

      // Submit each modified field as a suggestion
      for (const editedFile of editedFiles) {
        if (!editedFile.isModified) continue

        const originalFile = originalFiles.find((f) => f.file_id === editedFile.file_id)
        if (!originalFile) continue

        // Check which fields changed
        if (editedFile.season_number !== (originalFile.season_number ?? null)) {
          await createStreamSuggestion.mutateAsync({
            streamId: stream.id!,
            data: {
              suggestion_type: 'field_correction',
              field_name: `episode_link:${editedFile.file_id}:season_number`,
              current_value: String(originalFile.season_number ?? ''),
              suggested_value: String(editedFile.season_number ?? ''),
              reason: `Episode link fix for file: ${editedFile.file_name}`,
            },
          })
        }

        if (editedFile.episode_number !== (originalFile.episode_number ?? null)) {
          await createStreamSuggestion.mutateAsync({
            streamId: stream.id!,
            data: {
              suggestion_type: 'field_correction',
              field_name: `episode_link:${editedFile.file_id}:episode_number`,
              current_value: String(originalFile.episode_number ?? ''),
              suggested_value: String(editedFile.episode_number ?? ''),
              reason: `Episode link fix for file: ${editedFile.file_name}`,
            },
          })
        }

        if (editedFile.episode_end !== (originalFile.episode_end ?? null)) {
          await createStreamSuggestion.mutateAsync({
            streamId: stream.id!,
            data: {
              suggestion_type: 'field_correction',
              field_name: `episode_link:${editedFile.file_id}:episode_end`,
              current_value: String(originalFile.episode_end ?? ''),
              suggested_value: String(editedFile.episode_end ?? ''),
              reason: `Episode link fix for file: ${editedFile.file_name}`,
            },
          })
        }
      }
    } catch (error) {
      console.error('Failed to save file links:', error)
      throw error
    } finally {
      setIsSavingFileLinks(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="glass border-border/50 sm:max-w-[500px]" onOpenAutoFocus={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>Stream Actions</DialogTitle>
            <DialogDescription>Choose what to do with this stream</DialogDescription>
          </DialogHeader>

          {stream && (
            <div className="space-y-6 py-4">
              {/* Stream Info */}
              <div className="space-y-3">
                <p className="font-medium text-sm line-clamp-2">{stream.stream_name || stream.name}</p>
                <div className="flex flex-wrap gap-2">
                  {stream.quality && <Badge variant="secondary">{stream.quality}</Badge>}
                  {stream.resolution && <Badge variant="outline">{stream.resolution}</Badge>}
                  {stream.size && (
                    <Badge variant="outline">
                      <HardDrive className="mr-1 h-3 w-3" />
                      {stream.size}
                    </Badge>
                  )}
                  {stream.seeders !== undefined && stream.seeders > 0 && (
                    <Badge variant="outline" className="text-emerald-500">
                      <Wifi className="mr-1 h-3 w-3" />
                      {stream.seeders} seeders
                    </Badge>
                  )}
                  {stream.cached === true && (
                    <Badge variant="secondary" className="text-yellow-500 bg-yellow-500/10 border-yellow-500/30">
                      ⚡ Cached
                    </Badge>
                  )}
                  {stream.cached === false && stream.info_hash && (
                    <Badge variant="outline" className="text-muted-foreground">
                      Not Cached
                    </Badge>
                  )}
                </div>
              </div>

              {/* Stream Actions Section */}
              {stream.id && (
                <div className="space-y-3">
                  <div className="space-y-2">
                    <p className="text-xs font-medium text-muted-foreground">Rate this stream</p>
                    <StreamVoteButtons streamId={stream.id!} showCounts={true} />
                  </div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <StreamEditSheet
                      streamId={stream.id!}
                      streamName={stream.stream_name || stream.name}
                      currentValues={{
                        name: stream.stream_name || stream.name,
                        resolution: stream.resolution,
                        quality: stream.quality,
                        codec: stream.codec,
                        audio_formats: stream.audio_formats,
                        hdr_formats: stream.hdr_formats,
                        source: stream.source,
                        languages: stream.languages,
                        size: stream.size,
                      }}
                    />
                    <StreamReport
                      streamId={stream.id!}
                      streamName={stream.stream_name || stream.name}
                      currentQuality={stream.quality || stream.resolution}
                      currentLanguage={stream.audio_formats}
                    />
                    {/* File Annotation for Series */}
                    {catalogType === 'series' && stream.episode_links && stream.episode_links.length > 0 && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleOpenFileAnnotation}
                        disabled={isLoadingFileLinks}
                        className="gap-1.5"
                      >
                        {isLoadingFileLinks ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Hash className="h-4 w-4" />
                        )}
                        Annotate Files ({stream.episode_links.length})
                      </Button>
                    )}
                    {/* Collection Annotation for multi-file torrents */}
                    {stream.episode_links && stream.episode_links.length > 1 && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setCollectionAnnotationOpen(true)}
                        className="gap-1.5"
                      >
                        <Layers className="h-4 w-4" />
                        Collection ({stream.episode_links.length})
                      </Button>
                    )}
                    {/* Stream Re-linking */}
                    <StreamRelinkButton
                      streamId={stream.id!}
                      streamName={stream.stream_name || stream.name}
                      currentMediaId={mediaId}
                      currentMediaTitle={title}
                      variant="button"
                    />
                  </div>

                  {/* Moderator Actions */}
                  {isModerator && (
                    <div className="pt-2 mt-2 border-t border-border/50">
                      <p className="text-xs font-medium text-muted-foreground mb-2">Moderator Actions</p>
                      <div className="flex items-center gap-2">
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button
                              variant="outline"
                              size="sm"
                              className="text-primary hover:text-primary/80 hover:bg-primary/5 dark:hover:bg-primary/10"
                              disabled={isDeleting}
                            >
                              {blockStream.isPending ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              ) : (
                                <Ban className="mr-2 h-4 w-4" />
                              )}
                              Block Stream
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Block this stream?</AlertDialogTitle>
                              <AlertDialogDescription>
                                This will block the stream from appearing in search results. The stream can be unblocked
                                later from the admin panel.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction onClick={handleBlock} className="bg-primary hover:bg-primary/90">
                                Block Stream
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>

                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button
                              variant="outline"
                              size="sm"
                              className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-500/10"
                              disabled={isDeleting}
                            >
                              {deleteStream.isPending ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              ) : (
                                <Trash2 className="mr-2 h-4 w-4" />
                              )}
                              Delete Stream
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Delete this stream?</AlertDialogTitle>
                              <AlertDialogDescription>
                                This action cannot be undone. The stream will be permanently deleted from the database.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction onClick={handleDelete} className="bg-red-600 hover:bg-red-700">
                                Delete Stream
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Actions */}
              <div className="grid gap-3">
                {streamUrl && (isDebridProvider || isDirectProxyStream) ? (
                  <>
                    {/* In-browser playback - requires MediaFlow proxy AND debrid for all streams */}
                    {hasMediaflowProxy || isDirectProxyStream ? (
                      <>
                        <Button
                          onClick={handleWatch}
                          className="rounded-xl bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
                          disabled={trackAction.isPending}
                        >
                          <Play className="mr-2 h-4 w-4" />
                          Watch Now
                        </Button>

                        {/* External Players */}
                        <ExternalPlayerMenu streamUrl={streamUrl} className="rounded-xl justify-center w-full" />
                      </>
                    ) : (
                      <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-sm">
                        <p className="text-amber-600 dark:text-amber-400 font-medium mb-1">
                          Web Browser Playback Disabled
                        </p>
                        <p className="text-muted-foreground text-xs">
                          To play streams in your browser, go to Configure → External Services → MediaFlow and enable
                          "Enable Web Browser Playback". MediaFlow proxy is required for browser playback due to CORS
                          restrictions.
                        </p>
                      </div>
                    )}

                    <div className="grid grid-cols-2 gap-2">
                      <Button
                        variant={hasMediaflowProxy ? 'outline' : 'default'}
                        onClick={handleDownload}
                        className={
                          hasMediaflowProxy
                            ? 'rounded-xl'
                            : 'rounded-xl bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70'
                        }
                        disabled={trackAction.isPending}
                      >
                        <Download className="mr-2 h-4 w-4" />
                        Download
                      </Button>

                      <Button
                        variant="outline"
                        onClick={async () => {
                          // For torrent streams, copy info hash; for HTTP streams, copy URL
                          const textToCopy = isTorrentStream && stream.info_hash ? stream.info_hash : streamUrl
                          await navigator.clipboard.writeText(textToCopy)
                          setCopied(true)
                          setTimeout(() => setCopied(false), 2000)
                        }}
                        className="rounded-xl"
                        disabled={trackAction.isPending}
                      >
                        {copied ? (
                          <>
                            <Check className="mr-2 h-4 w-4 text-emerald-500" />
                            Copied
                          </>
                        ) : (
                          <>
                            <Copy className="mr-2 h-4 w-4" />
                            {isTorrentStream ? 'Copy Info Hash' : 'Copy Stream URL'}
                          </>
                        )}
                      </Button>
                    </div>

                    {/* External Players - show prominently when no MediaFlow */}
                    {!hasMediaflowProxy && (
                      <ExternalPlayerMenu streamUrl={streamUrl} className="rounded-xl justify-center w-full" />
                    )}
                  </>
                ) : (
                  <>
                    {/* Telegram/AceStream streams don't need debrid - show play button */}
                    {isDirectProxyStream && streamUrl ? (
                      <>
                        <Button
                          onClick={handleWatch}
                          className="rounded-xl bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
                          disabled={trackAction.isPending}
                        >
                          <Play className="mr-2 h-4 w-4" />
                          Watch Now
                        </Button>

                        {/* External Players */}
                        <ExternalPlayerMenu streamUrl={streamUrl} className="rounded-xl justify-center w-full" />

                        <div className="grid grid-cols-2 gap-2">
                          <Button
                            variant="outline"
                            onClick={handleDownload}
                            className="rounded-xl"
                            disabled={trackAction.isPending}
                          >
                            <Download className="mr-2 h-4 w-4" />
                            Download
                          </Button>

                          <Button
                            variant="outline"
                            onClick={async () => {
                              await navigator.clipboard.writeText(streamUrl)
                              setCopied(true)
                              setTimeout(() => setCopied(false), 2000)
                            }}
                            className="rounded-xl"
                            disabled={trackAction.isPending}
                          >
                            {copied ? (
                              <>
                                <Check className="mr-2 h-4 w-4 text-emerald-500" />
                                Copied
                              </>
                            ) : (
                              <>
                                <Copy className="mr-2 h-4 w-4" />
                                Copy URL
                              </>
                            )}
                          </Button>
                        </div>
                      </>
                    ) : isTorrentStream && stream.info_hash ? (
                      <>
                        {/* No debrid configured or no URL - show info hash for torrents */}
                        <Button
                          onClick={async () => {
                            await navigator.clipboard.writeText(stream.info_hash!)
                            setCopied(true)
                            setTimeout(() => setCopied(false), 2000)
                          }}
                          className="rounded-xl bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
                        >
                          {copied ? (
                            <>
                              <Check className="mr-2 h-4 w-4" />
                              Copied!
                            </>
                          ) : (
                            <>
                              <Copy className="mr-2 h-4 w-4" />
                              Copy Info Hash
                            </>
                          )}
                        </Button>
                        <p className="text-xs text-center text-muted-foreground">
                          {isDebridProvider
                            ? 'Stream is being processed. Copy the info hash to use with your debrid provider.'
                            : 'Configure a debrid provider to enable direct streaming and downloads.'}
                        </p>
                      </>
                    ) : streamUrl ? (
                      <>
                        {/* HTTP stream without debrid - show URL copy option */}
                        <Button
                          onClick={async () => {
                            await navigator.clipboard.writeText(streamUrl)
                            setCopied(true)
                            setTimeout(() => setCopied(false), 2000)
                          }}
                          className="rounded-xl bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
                        >
                          {copied ? (
                            <>
                              <Check className="mr-2 h-4 w-4" />
                              Copied!
                            </>
                          ) : (
                            <>
                              <Copy className="mr-2 h-4 w-4" />
                              Copy Stream URL
                            </>
                          )}
                        </Button>
                        <p className="text-xs text-center text-muted-foreground">
                          Configure a debrid provider with MediaFlow to enable in-browser playback.
                        </p>
                      </>
                    ) : (
                      <p className="text-xs text-center text-muted-foreground">
                        No stream URL available. Configure a debrid provider to access this stream.
                      </p>
                    )}
                  </>
                )}
              </div>

              {/* Info */}
              <div className="flex items-start gap-2 p-3 rounded-xl bg-muted/50 text-sm text-muted-foreground">
                <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <p>
                  {streamUrl && (isDebridProvider || isDirectProxyStream)
                    ? hasMediaflowProxy || isDirectProxyStream
                      ? isDirectProxyStream
                        ? isAceStreamStream
                          ? "Click 'Watch Now' to play via MediaFlow Proxy (AceStream)."
                          : "Click 'Watch Now' to play via MediaFlow Proxy (using your Telegram session)."
                        : "Click 'Watch Now' to play in browser, or 'Download' to open in a new tab."
                      : "Use 'Download' to open in a new tab or use an external player. Enable 'Web Browser Playback' in External Services for in-browser playback."
                    : isTorrentStream
                      ? isDebridProvider
                        ? 'The stream URL will be available once your debrid provider processes the torrent.'
                        : 'Configure a debrid provider to access this torrent stream.'
                      : 'Configure a debrid provider and enable Web Browser Playback to stream.'}
                </p>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* File Annotation Dialog for Series */}
      {stream && catalogType === 'series' && (
        <FileAnnotationDialog
          open={fileAnnotationOpen}
          onOpenChange={setFileAnnotationOpen}
          streamName={stream.stream_name || stream.name}
          initialFiles={fileLinks}
          onSave={handleSaveFileLinks}
          isLoading={isSavingFileLinks}
        />
      )}

      {/* Collection Annotation Dialog for multi-file torrents */}
      {stream && stream.episode_links && stream.episode_links.length > 1 && (
        <CollectionAnnotationDialog
          open={collectionAnnotationOpen}
          onOpenChange={setCollectionAnnotationOpen}
          streamId={stream.id!}
          streamName={stream.stream_name || stream.name}
          files={stream.episode_links.map((el) => ({
            file_id: el.file_id,
            file_name: el.file_name,
            file_index: el.file_id, // Use file_id as index if not provided
          }))}
        />
      )}
    </>
  )
}

// Service name display mapping
const SERVICE_DISPLAY_NAMES: Record<string, string> = {
  realdebrid: 'Real-Debrid',
  alldebrid: 'AllDebrid',
  premiumize: 'Premiumize',
  debridlink: 'Debrid-Link',
  torbox: 'TorBox',
  seedr: 'Seedr',
  offcloud: 'OffCloud',
  pikpak: 'PikPak',
  easydebrid: 'EasyDebrid',
  debrider: 'Debrider',
  qbittorrent: 'qBittorrent',
  stremthru: 'StremThru',
  p2p: 'P2P',
}

// Helper to get display name for a provider - shows both custom name and service
function getProviderDisplayName(provider: StreamingProviderInfo): string {
  const serviceName = SERVICE_DISPLAY_NAMES[provider.service] || provider.service

  // If user has a custom name, show both: "Custom Name (Service)"
  if (provider.name && provider.name !== serviceName) {
    return `${provider.name} (${serviceName})`
  }

  // Otherwise just show the service name
  return serviceName
}

// Main Content Detail Page
export function ContentDetailPage() {
  const { type, id } = useParams<{ type: string; id: string }>()
  const [searchParams] = useSearchParams()
  const catalogType = type as CatalogType
  const { isAuthenticated, hasMinimumRole } = useAuth()
  const { rpdbApiKey } = useRpdb()
  const mediaId = parseInt(id || '0', 10)
  const isModerator = hasMinimumRole('moderator')

  // Read initial season/episode from URL query params (for deep linking from history)
  const initialSeason = searchParams.get('season') ? parseInt(searchParams.get('season')!, 10) : undefined
  const initialEpisode = searchParams.get('episode') ? parseInt(searchParams.get('episode')!, 10) : undefined

  // For series: season and episode selection
  const [selectedSeason, setSelectedSeason] = useState<number | undefined>(initialSeason)
  const [selectedEpisode, setSelectedEpisode] = useState<number | undefined>(initialEpisode)
  const [streamDialogOpen, setStreamDialogOpen] = useState(false)
  const [selectedStream, setSelectedStream] = useState<CatalogStreamInfo | null>(null)

  // Stream filtering and view mode
  const [streamFilters, setStreamFilters] = useState<StreamFilterState>(defaultStreamFilters)
  const [viewMode, setViewMode] = useState<ViewMode>('list')

  // Player state (hoisted from StreamActionDialog for proper modal layering)
  const [playerOpen, setPlayerOpen] = useState(false)
  const [playerStream, setPlayerStream] = useState<CatalogStreamInfo | null>(null)
  const [playerStreamUrl, setPlayerStreamUrl] = useState<string | null>(null)

  // Watch history tracking for resume playback
  const [watchHistoryId, setWatchHistoryId] = useState<number | null>(null)
  const [startTime, setStartTime] = useState(0)
  const lastProgressUpdateRef = useRef<number>(0) // Track last update time for throttling
  const trackAction = useTrackStreamAction()
  const updateProgress = useUpdateWatchProgress()

  // Last played stream tracking
  const getLastPlayedKey = useCallback(() => {
    // Create a unique key based on media ID and optionally season/episode for series
    if (catalogType === 'series' && selectedSeason !== undefined && selectedEpisode !== undefined) {
      return `mf:lastPlayed:${mediaId}:${selectedSeason}:${selectedEpisode}`
    }
    return `mf:lastPlayed:${mediaId}`
  }, [mediaId, catalogType, selectedSeason, selectedEpisode])

  const [lastPlayedStreamId, setLastPlayedStreamId] = useState<string | null>(() => {
    // Initialize from localStorage
    if (typeof window === 'undefined') return null
    const key = `mf:lastPlayed:${mediaId}` // Initial key without season/episode
    return localStorage.getItem(key)
  })

  // Update lastPlayedStreamId when season/episode changes (during render, not in effect)
  const lastPlayedKey = getLastPlayedKey()
  const [prevLastPlayedKey, setPrevLastPlayedKey] = useState(lastPlayedKey)
  if (lastPlayedKey !== prevLastPlayedKey) {
    setPrevLastPlayedKey(lastPlayedKey)
    const stored = localStorage.getItem(lastPlayedKey)
    setLastPlayedStreamId(stored)
  }

  // Profile and provider selection for streams
  const [selectedProfileId, setSelectedProfileId] = useState<number | undefined>()
  const [selectedProvider, setSelectedProvider] = useState<string | undefined>()

  // Fetch user profiles
  const { data: profiles } = useProfiles()

  // Find default profile and set both profile ID and primary provider initially (during render, not in effect)
  const [prevProfiles, setPrevProfiles] = useState(profiles)
  if (profiles && profiles.length > 0 && selectedProfileId === undefined && prevProfiles !== profiles) {
    setPrevProfiles(profiles)
    const defaultProfile = profiles.find((p) => p.is_default) || profiles[0]
    setSelectedProfileId(defaultProfile.id)
    const primaryService =
      defaultProfile.streaming_providers?.primary_service || defaultProfile.streaming_providers?.providers?.[0]?.service
    if (primaryService) {
      setSelectedProvider(primaryService)
    }
  }

  // Fetch data
  const { data: item, isLoading } = useCatalogItem(catalogType, mediaId)
  const { data: libraryStatus } = useLibraryCheck(mediaId)

  // Use the Stremio catalog streams API (handles debrid, caching, user preferences)
  // Only fetch when we have BOTH profile ID and provider to make a single optimized request
  const {
    data: streamsData,
    isLoading: streamsLoading,
    refetch: refetchStreams,
  } = useCatalogStreams(catalogType, mediaId, selectedSeason, selectedEpisode, selectedProfileId, selectedProvider, {
    enabled:
      isAuthenticated &&
      selectedProfileId !== undefined && // Wait for profile to be selected
      selectedProvider !== undefined && // Wait for provider to be selected
      (catalogType === 'movie' ||
        catalogType === 'tv' ||
        (selectedSeason !== undefined && selectedEpisode !== undefined)),
  })

  // Get available providers from the streams response (memoized to avoid unstable deps)
  const availableProviders = useMemo(() => streamsData?.streaming_providers || [], [streamsData?.streaming_providers])

  // Update provider selection when providers list changes (during render, not in effect)
  const [prevProviders, setPrevProviders] = useState(availableProviders)
  if (availableProviders.length > 0 && selectedProvider && prevProviders !== availableProviders) {
    setPrevProviders(availableProviders)
    const stillValid = availableProviders.some((p) => p.service === selectedProvider)
    if (!stillValid) {
      setSelectedProvider(availableProviders[0].service)
    }
  }

  // Library mutations
  const addToLibrary = useAddToLibrary()
  const removeFromLibrary = useRemoveFromLibraryByMediaId()

  // Episode delete mutation (moderator only)
  const deleteEpisodeAdmin = useDeleteEpisodeAdmin()

  // Handle episode deletion (moderator only)
  // seasonNumber and episodeNumber are passed from SeriesEpisodePicker for potential toast messages
  const handleDeleteEpisode = async (episodeId: number, _seasonNumber: number, _episodeNumber: number) => {
    if (!item?.id) return
    try {
      await deleteEpisodeAdmin.mutateAsync({ mediaId: item.id, episodeId })
    } catch (error) {
      console.error('Failed to delete episode:', error)
      throw error
    }
  }

  // Calculate available seasons and episodes
  const seasons = useMemo(() => item?.seasons ?? [], [item?.seasons])

  const episodes = useMemo(() => {
    if (!selectedSeason || !seasons.length) return []
    const season = seasons.find((s) => s.season_number === selectedSeason)
    return season?.episodes ?? []
  }, [selectedSeason, seasons])

  // Sync URL params to state when they change (during render, not in effect)
  const [prevInitialSeason, setPrevInitialSeason] = useState(initialSeason)
  const [prevInitialEpisode, setPrevInitialEpisode] = useState(initialEpisode)
  if (initialSeason !== undefined && prevInitialSeason !== initialSeason) {
    setPrevInitialSeason(initialSeason)
    setSelectedSeason(initialSeason)
  }
  if (initialEpisode !== undefined && prevInitialEpisode !== initialEpisode) {
    setPrevInitialEpisode(initialEpisode)
    setSelectedEpisode(initialEpisode)
  }

  // Set default season when data loads (during render, not in effect)
  const [prevSeasons, setPrevSeasons] = useState(seasons)
  if (seasons.length > 0 && selectedSeason === undefined && prevSeasons !== seasons) {
    setPrevSeasons(seasons)
    setSelectedSeason(seasons[0].season_number)
  }

  // Set default episode when season changes (during render, not in effect)
  const [prevEpisodes, setPrevEpisodes] = useState(episodes)
  if (episodes.length > 0 && selectedEpisode === undefined && prevEpisodes !== episodes) {
    setPrevEpisodes(episodes)
    setSelectedEpisode(episodes[0].episode_number)
  }

  // Helper function for partial case-insensitive matching (for instant client-side filtering)
  const matchesFilter = (value: string | undefined, filters: string[]): boolean => {
    if (!value || filters.length === 0) return true
    const valueLower = value.toLowerCase()
    return filters.some((f) => {
      const filterLower = f.toLowerCase()
      return valueLower === filterLower || valueLower.includes(filterLower) || filterLower.includes(valueLower)
    })
  }

  // Client-side filtering for instant UI feedback
  // (Server-side filtering already applied via API params)
  const filteredStreams = useMemo(() => {
    if (!streamsData?.streams) return []

    let result = [...streamsData.streams]

    // Apply instant client-side filters (for UI responsiveness)
    const {
      qualityFilter,
      resolutionFilter,
      sourceFilter,
      codecFilter,
      cachedFilter,
      streamTypeFilter,
      sortBy,
      sortOrder,
    } = streamFilters

    if (qualityFilter.length > 0) {
      result = result.filter((s) => matchesFilter(s.quality, qualityFilter))
    }

    if (resolutionFilter.length > 0) {
      result = result.filter((s) => matchesFilter(s.resolution, resolutionFilter))
    }

    if (sourceFilter.length > 0) {
      result = result.filter((s) => matchesFilter(s.source, sourceFilter))
    }

    if (codecFilter.length > 0) {
      result = result.filter((s) => matchesFilter(s.codec, codecFilter))
    }

    // Apply stream type filter
    if (streamTypeFilter.length > 0) {
      result = result.filter((s) => {
        if (!s.stream_type) return false
        const streamType = s.stream_type.toLowerCase()
        return streamTypeFilter.some((filterType) => {
          // Handle various stream type naming conventions
          if (filterType === 'torrent') return streamType === 'torrent'
          if (filterType === 'usenet') return streamType === 'usenet'
          if (filterType === 'telegram') return streamType === 'telegram'
          if (filterType === 'http') return streamType === 'http' || streamType === 'web'
          if (filterType === 'direct') return streamType === 'direct' || streamType === 'ddl'
          return streamType === filterType
        })
      })
    }

    // Apply last played only filter
    if (streamFilters.lastPlayedOnly && lastPlayedStreamId) {
      result = result.filter((s) => s.id !== undefined && String(s.id) === lastPlayedStreamId)
    }

    // Apply cached filter
    if (cachedFilter === 'cached') {
      result = result.filter((s) => s.cached === true)
    } else if (cachedFilter === 'not_cached') {
      result = result.filter((s) => s.cached === false)
    }

    // Apply size filter
    const { minSizeGB, maxSizeGB } = streamFilters
    if (minSizeGB !== null || maxSizeGB !== null) {
      const parseSizeToGB = (sizeStr?: string) => {
        if (!sizeStr) return null
        const match = sizeStr.match(/([\d.]+)\s*(GB|MB|KB|TB)/i)
        if (!match) return null
        const [, num, unit] = match
        const multipliers: Record<string, number> = { KB: 1 / (1024 * 1024), MB: 1 / 1024, GB: 1, TB: 1024 }
        return parseFloat(num) * (multipliers[unit.toUpperCase()] || 1)
      }

      result = result.filter((s) => {
        const sizeGB = parseSizeToGB(s.size)
        if (sizeGB === null) return true // Keep streams without size info
        if (minSizeGB !== null && sizeGB < minSizeGB) return false
        if (maxSizeGB !== null && sizeGB > maxSizeGB) return false
        return true
      })
    }

    // Apply sorting
    result.sort((a, b) => {
      let comparison = 0

      switch (sortBy) {
        case 'quality': {
          // Sort by resolution quality tier (4K > 1080p > 720p > SD)
          const getQualityScore = (s: CatalogStreamInfo) => {
            const res = (s.resolution || '').toLowerCase()
            if (res.includes('4k') || res.includes('2160')) return 4
            if (res.includes('1080')) return 3
            if (res.includes('720')) return 2
            return 1
          }
          comparison = getQualityScore(a) - getQualityScore(b)
          break
        }
        case 'size': {
          // Parse size strings like "1.5 GB", "700 MB"
          const parseSize = (sizeStr?: string) => {
            if (!sizeStr) return 0
            const match = sizeStr.match(/([\d.]+)\s*(GB|MB|KB|TB)/i)
            if (!match) return 0
            const [, num, unit] = match
            const multipliers: Record<string, number> = { KB: 1, MB: 1024, GB: 1024 * 1024, TB: 1024 * 1024 * 1024 }
            return parseFloat(num) * (multipliers[unit.toUpperCase()] || 1)
          }
          comparison = parseSize(a.size) - parseSize(b.size)
          break
        }
        case 'seeders': {
          comparison = (a.seeders || 0) - (b.seeders || 0)
          break
        }
        case 'source': {
          comparison = (a.source || '').localeCompare(b.source || '')
          break
        }
      }

      return sortOrder === 'desc' ? -comparison : comparison
    })

    return result
  }, [streamsData, streamFilters, lastPlayedStreamId])

  // Derive available filter options from streams
  const availableSources = useMemo(() => {
    if (!streamsData?.streams) return []
    const sources = new Set(streamsData.streams.map((s) => s.source).filter(Boolean))
    return Array.from(sources).sort() as string[]
  }, [streamsData])

  const availableResolutions = useMemo(() => {
    if (!streamsData?.streams) return []
    const resolutions = new Set(streamsData.streams.map((s) => s.resolution).filter(Boolean))
    return Array.from(resolutions).sort() as string[]
  }, [streamsData])

  const availableQualities = useMemo(() => {
    if (!streamsData?.streams) return []
    const qualities = new Set(streamsData.streams.map((s) => s.quality).filter(Boolean))
    return Array.from(qualities).sort() as string[]
  }, [streamsData])

  const availableCodecs = useMemo(() => {
    if (!streamsData?.streams) return []
    const codecs = new Set(streamsData.streams.map((s) => s.codec).filter(Boolean))
    return Array.from(codecs).sort() as string[]
  }, [streamsData])

  const availableStreamTypes = useMemo(() => {
    if (!streamsData?.streams) return []
    const types = new Set(streamsData.streams.map((s) => s.stream_type).filter(Boolean))
    return Array.from(types) as ('torrent' | 'usenet' | 'http')[]
  }, [streamsData])

  const handleLibraryToggle = async () => {
    if (!id) return

    if (libraryStatus?.in_library) {
      await removeFromLibrary.mutateAsync(mediaId)
    } else {
      await addToLibrary.mutateAsync({
        media_id: mediaId,
        catalog_type: catalogType,
      })
    }
  }

  const handleStreamClick = (stream: CatalogStreamInfo) => {
    setSelectedStream(stream)
    setStreamDialogOpen(true)
  }

  // Handle watch action - opens player at page level (outside of StreamActionDialog)
  const handleWatchStream = useCallback(
    async (stream: CatalogStreamInfo, streamUrl: string) => {
      // For Telegram and AceStream streams, enable transcoding for browser playback.
      // This tells MediaFlow to convert MPEG-TS / unsupported codecs (DTS, AC3, EAC3)
      // into browser-compatible formats (HLS with AAC audio).
      let finalStreamUrl = streamUrl
      if (stream.stream_type === 'telegram' || stream.stream_type === 'acestream') {
        const separator = streamUrl.includes('?') ? '&' : '?'
        finalStreamUrl = `${streamUrl}${separator}transcode=true`
      }

      setPlayerStream(stream)
      setPlayerStreamUrl(finalStreamUrl)
      setWatchHistoryId(null) // Reset history ID
      setStartTime(0) // Reset start time
      lastProgressUpdateRef.current = 0 // Reset progress throttle

      // Save as last played stream
      if (stream.id) {
        const key = getLastPlayedKey()
        const streamIdStr = String(stream.id)
        localStorage.setItem(key, streamIdStr)
        setLastPlayedStreamId(streamIdStr)
      }

      // Track the watch action and get history entry for resume
      if (item) {
        try {
          const historyItem = await trackAction.mutateAsync({
            media_id: mediaId,
            title: item.title,
            catalog_type: catalogType === 'series' ? 'series' : catalogType === 'tv' ? 'tv' : 'movie',
            season: selectedSeason,
            episode: selectedEpisode,
            action: 'watch',
            stream_info: {
              name: stream.name,
              quality: stream.quality,
              size: stream.size,
              source: stream.source,
            },
          })

          // Store history ID for progress updates
          setWatchHistoryId(historyItem.id)
          // Resume from last position if available
          if (historyItem.progress > 0) {
            setStartTime(historyItem.progress)
          }
        } catch (err) {
          console.error('Failed to track watch action:', err)
          // Still open player even if tracking fails
        }
      }

      setPlayerOpen(true)
    },
    [item, mediaId, catalogType, selectedSeason, selectedEpisode, trackAction, getLastPlayedKey],
  )

  // Throttled progress update handler (every 10 seconds)
  const handleTimeUpdate = useCallback(
    (currentTime: number, duration: number) => {
      const now = Date.now()
      const timeSinceLastUpdate = now - lastProgressUpdateRef.current

      // Only update every 10 seconds and if we have a history ID
      if (watchHistoryId && timeSinceLastUpdate >= 10000) {
        lastProgressUpdateRef.current = now

        // Update progress in background (don't await)
        updateProgress.mutate({
          historyId: watchHistoryId,
          data: {
            progress: Math.floor(currentTime),
            duration: duration > 0 ? Math.floor(duration) : undefined,
          },
        })
      }
    },
    [watchHistoryId, updateProgress],
  )

  // Save final progress when player closes and clear player state to fully unmount
  const handlePlayerClose = useCallback((open: boolean) => {
    if (!open) {
      // Player is closing - clear player state so the component fully unmounts
      // This stops all network activity (HLS retries, video buffering)
      setPlayerOpen(false)
      // Use a microtask to clear stream state after dialog close animation
      setTimeout(() => {
        setPlayerStream(null)
        setPlayerStreamUrl(null)
      }, 300)
    } else {
      setPlayerOpen(open)
    }
  }, [])

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-32" />
        <div className="grid lg:grid-cols-[300px_1fr] gap-8">
          <Skeleton className="aspect-[2/3] rounded-2xl" />
          <div className="space-y-4">
            <Skeleton className="h-10 w-3/4" />
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-24 w-full" />
          </div>
        </div>
      </div>
    )
  }

  if (!item) {
    return (
      <div className="text-center py-12">
        <Film className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
        <p className="mt-4 text-lg">Content not found</p>
        <Button asChild className="mt-4 rounded-xl">
          <Link to="/dashboard/library">
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back to Library
          </Link>
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Back Button */}
      <Button variant="ghost" asChild className="rounded-xl">
        <Link to="/dashboard/library">
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Library
        </Link>
      </Button>

      {/* Hero Section */}
      <div className="relative overflow-hidden rounded-2xl">
        {/* Background with RPDB backdrop support */}
        <div className="absolute inset-0">
          <Backdrop
            metaId={item.external_ids?.imdb || `mf:${item.id}`}
            backdrop={item.background}
            rpdbApiKey={catalogType !== 'tv' ? rpdbApiKey : null}
            className="absolute inset-0 w-full h-full object-cover"
          />
          <div className="absolute inset-0 bg-gradient-to-r from-background via-background/95 to-background/80" />
        </div>

        <div className="relative grid lg:grid-cols-[280px_1fr] gap-8 p-6 lg:p-8">
          {/* Poster */}
          <div className="mx-auto lg:mx-0">
            <Poster
              metaId={item.external_ids?.imdb || `mf:${item.id}`}
              catalogType={catalogType === 'tv' ? 'tv' : catalogType}
              poster={item.poster}
              rpdbApiKey={catalogType !== 'tv' ? rpdbApiKey : null}
              title={item.title}
              className="w-[200px] lg:w-full rounded-2xl shadow-2xl"
            />
          </div>

          {/* Info */}
          <div className="space-y-4">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-xs">
                  {catalogType === 'movie' ? 'Movie' : catalogType === 'series' ? 'Series' : 'TV'}
                </Badge>
                {item.year && <span className="text-sm text-muted-foreground">{item.year}</span>}
              </div>
              <h1 className="text-3xl lg:text-4xl font-bold">{item.title}</h1>
            </div>

            {/* Meta Info */}
            <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
              {/* Multi-provider ratings */}
              {item.ratings?.external_ratings && item.ratings.external_ratings.length > 0 ? (
                <RatingsDisplay
                  ratings={item.ratings}
                  size="default"
                  maxExternalRatings={5}
                  showCommunity={false} // Community shown separately below
                />
              ) : (
                item.imdb_rating && (
                  <div className="flex items-center gap-1">
                    <Star className="h-4 w-4 fill-primary text-primary" />
                    <span className="font-medium text-foreground">{item.imdb_rating.toFixed(1)}</span>
                    <span>/ 10</span>
                  </div>
                )
              )}
              {/* Content Guidance (Certification & Nudity) */}
              <ContentGuidance certification={item.certification} nudity={item.nudity} size="default" />
              {item.runtime && (
                <div className="flex items-center gap-1">
                  <Clock className="h-4 w-4" />
                  <span>{item.runtime}</span>
                </div>
              )}
              {item.year && (
                <div className="flex items-center gap-1">
                  <Calendar className="h-4 w-4" />
                  <span>{item.year}</span>
                </div>
              )}
            </div>

            {/* Genres - clickable to filter */}
            {item.genres.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {item.genres.map((genre) => (
                  <Link key={genre} to={`/dashboard/library?tab=browse&genre=${encodeURIComponent(genre)}`}>
                    <Badge
                      variant="secondary"
                      className="rounded-lg hover:bg-primary/20 hover:text-primary cursor-pointer transition-colors"
                    >
                      {genre}
                    </Badge>
                  </Link>
                ))}
              </div>
            )}

            {/* Description */}
            {item.description && <p className="text-muted-foreground leading-relaxed max-w-2xl">{item.description}</p>}

            {/* Credits (Directors, Writers, Cast) */}
            {(item.directors?.length || item.writers?.length || item.cast?.length) && (
              <div className="grid gap-3 text-sm max-w-2xl">
                {item.directors && item.directors.length > 0 && (
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <span className="text-muted-foreground font-medium">
                      Director{item.directors.length > 1 ? 's' : ''}:
                    </span>
                    {item.directors.map((director, idx) => (
                      <Link
                        key={director}
                        to={`/dashboard/library?tab=browse&type=${catalogType}&search=${encodeURIComponent(director)}`}
                        className="hover:text-primary transition-colors"
                      >
                        {director}
                        {idx < item.directors!.length - 1 ? ',' : ''}
                      </Link>
                    ))}
                  </div>
                )}
                {item.writers && item.writers.length > 0 && (
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <span className="text-muted-foreground font-medium">
                      Writer{item.writers.length > 1 ? 's' : ''}:
                    </span>
                    {item.writers.slice(0, 5).map((writer, idx) => (
                      <Link
                        key={writer}
                        to={`/dashboard/library?tab=browse&type=${catalogType}&search=${encodeURIComponent(writer)}`}
                        className="hover:text-primary transition-colors"
                      >
                        {writer}
                        {idx < Math.min(item.writers!.length, 5) - 1 ? ',' : ''}
                      </Link>
                    ))}
                    {item.writers.length > 5 && (
                      <span className="text-muted-foreground">+{item.writers.length - 5} more</span>
                    )}
                  </div>
                )}
                {item.cast && item.cast.length > 0 && (
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <span className="text-muted-foreground font-medium">Cast:</span>
                    {item.cast.slice(0, 8).map((actor, idx) => (
                      <Link
                        key={actor}
                        to={`/dashboard/library?tab=browse&type=${catalogType}&search=${encodeURIComponent(actor)}`}
                        className="hover:text-primary transition-colors"
                      >
                        {actor}
                        {idx < Math.min(item.cast!.length, 8) - 1 ? ',' : ''}
                      </Link>
                    ))}
                    {item.cast.length > 8 && (
                      <span className="text-muted-foreground">+{item.cast.length - 8} more</span>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Actions */}
            <div className="flex flex-wrap gap-3 pt-2">
              {/* Trailer Button - shown if trailers available */}
              {item.trailers && item.trailers.length > 0 && (
                <TrailerButton trailers={item.trailers} title={item.title} />
              )}

              {isAuthenticated && (
                <Button
                  variant="outline"
                  onClick={handleLibraryToggle}
                  disabled={addToLibrary.isPending || removeFromLibrary.isPending}
                  className="rounded-xl"
                >
                  {addToLibrary.isPending || removeFromLibrary.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : libraryStatus?.in_library ? (
                    <HeartOff className="mr-2 h-4 w-4" />
                  ) : (
                    <Heart className="mr-2 h-4 w-4" />
                  )}
                  {libraryStatus?.in_library ? 'Remove from Library' : 'Add to Library'}
                </Button>
              )}
            </div>

            {/* Metadata Voting & Edit */}
            {isAuthenticated && mediaId && (
              <div className="pt-4 border-t border-border/30 mt-4">
                <div className="flex flex-wrap items-center gap-4">
                  <ContentLikesBadge mediaId={mediaId} />
                  <MetadataActions mediaId={mediaId} />
                  <MetadataEditSheet mediaId={mediaId} catalogType={catalogType} />
                  {/* RefreshMetadataButton only for movies/series - TV channels don't have IMDb IDs */}
                  {(catalogType === 'movie' || catalogType === 'series') && (
                    <RefreshMetadataButton
                      mediaId={mediaId}
                      externalIds={item.external_ids}
                      mediaType={catalogType}
                      title={item.title}
                      year={item.year}
                    />
                  )}
                  {/* ScrapeContentButton for movies/series - triggers stream scraping */}
                  {(catalogType === 'movie' || catalogType === 'series') && (
                    <ScrapeContentButton
                      mediaId={mediaId}
                      mediaType={catalogType}
                      title={item.title}
                      season={catalogType === 'series' ? selectedSeason : undefined}
                      episode={catalogType === 'series' ? selectedEpisode : undefined}
                    />
                  )}
                  {/* Block/Unblock button - moderators and admins only */}
                  {isModerator && (
                    <BlockContentButton
                      mediaId={mediaId}
                      mediaTitle={item.title}
                      mediaType={catalogType}
                      isBlocked={item.is_blocked || false}
                      blockReason={item.block_reason}
                    />
                  )}
                </div>
                {(item.last_refreshed_at || item.last_scraped_at) && (
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground mt-2">
                    {item.last_refreshed_at && (
                      <p>
                        Metadata refreshed:{' '}
                        {new Date(item.last_refreshed_at).toLocaleDateString(undefined, {
                          year: 'numeric',
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </p>
                    )}
                    {item.last_scraped_at && (
                      <p>
                        Streams scraped:{' '}
                        {new Date(item.last_scraped_at).toLocaleDateString(undefined, {
                          year: 'numeric',
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* External IDs */}
            {item.external_ids && (
              <div className="pt-4 border-t border-border/30">
                <p className="text-xs text-muted-foreground mb-2 font-medium">External IDs</p>
                <ExternalIdsDisplay externalIds={item.external_ids} mediaType={catalogType} />
              </div>
            )}

            {/* AKA Titles */}
            {item.aka_titles && item.aka_titles.length > 0 && (
              <div className="pt-4">
                <p className="text-xs text-muted-foreground">
                  Also known as: {item.aka_titles.slice(0, 3).join(', ')}
                  {item.aka_titles.length > 3 && ` +${item.aka_titles.length - 3} more`}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Series Season/Episode Selector */}
      {catalogType === 'series' && seasons.length > 0 && (
        <SeriesEpisodePicker
          seasons={seasons}
          selectedSeason={selectedSeason}
          selectedEpisode={selectedEpisode}
          onSeasonChange={(season) => {
            setSelectedSeason(season)
            setSelectedEpisode(undefined)
          }}
          onEpisodeChange={setSelectedEpisode}
          isAdmin={hasMinimumRole('admin')}
          onDeleteEpisode={handleDeleteEpisode}
          isDeletingEpisode={deleteEpisodeAdmin.isPending}
        />
      )}

      {/* Streams Section */}
      {isAuthenticated && (catalogType === 'movie' || catalogType === 'tv' || (selectedSeason && selectedEpisode)) && (
        <Card className="glass border-border/50">
          <CardHeader className="px-3 sm:px-6 py-4 sm:py-6">
            <div className="flex flex-col gap-3 sm:gap-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle className="text-base sm:text-lg flex items-center gap-2">
                    <Play className="h-4 w-4 sm:h-5 sm:w-5 text-primary shrink-0" />
                    Available Streams
                  </CardTitle>
                  <CardDescription className="text-xs sm:text-sm mt-0.5">
                    {catalogType === 'series'
                      ? `Season ${selectedSeason}, Episode ${selectedEpisode}`
                      : catalogType === 'tv'
                        ? 'Select a stream to watch this channel'
                        : 'Select a stream to watch or download'}
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  {/* Profile Selector - show when multiple profiles available */}
                  {profiles && profiles.length > 1 ? (
                    <Select
                      value={selectedProfileId?.toString() ?? ''}
                      onValueChange={(value) => {
                        const newProfileId = parseInt(value, 10)
                        const newProfile = profiles.find((p) => p.id === newProfileId)
                        setSelectedProfileId(newProfileId)
                        // Set the primary provider from the new profile
                        setSelectedProvider(newProfile?.streaming_providers?.primary_service || undefined)
                      }}
                    >
                      <SelectTrigger className="w-[130px] sm:w-[160px] h-9 rounded-xl text-xs sm:text-sm">
                        <SelectValue placeholder="Profile" />
                      </SelectTrigger>
                      <SelectContent>
                        {profiles.map((profile) => (
                          <SelectItem key={profile.id} value={profile.id.toString()}>
                            <div className="flex items-center gap-2">
                              <span>{profile.name}</span>
                              {profile.is_default && (
                                <Badge variant="secondary" className="text-[10px] px-1 py-0">
                                  Default
                                </Badge>
                              )}
                            </div>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : profiles && profiles.length === 1 ? (
                    // Show single profile name as a badge
                    <Badge
                      variant="outline"
                      className="rounded-lg px-2.5 sm:px-3 py-1.5 h-9 flex items-center text-xs sm:text-sm"
                    >
                      {profiles[0].name}
                    </Badge>
                  ) : null}
                  <ViewModeToggle mode={viewMode} onModeChange={setViewMode} />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => refetchStreams()}
                    disabled={streamsLoading}
                    className="rounded-xl h-9 text-xs sm:text-sm"
                  >
                    {streamsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Refresh'}
                  </Button>
                </div>
              </div>

              {/* Provider Tabs - show when providers available */}
              {availableProviders.length > 0 &&
                (availableProviders.length > 1 ? (
                  <Tabs
                    value={selectedProvider || availableProviders[0]?.service}
                    onValueChange={(value) => setSelectedProvider(value)}
                    className="w-full"
                  >
                    <TabsList className="w-full justify-start h-auto flex-wrap gap-1 bg-muted/50 p-1 rounded-xl">
                      {availableProviders.map((provider) => (
                        <TabsTrigger
                          key={provider.service}
                          value={provider.service}
                          className="rounded-lg data-[state=active]:bg-primary data-[state=active]:text-primary-foreground px-2 sm:px-3 py-1 sm:py-1.5 text-xs sm:text-sm"
                        >
                          {getProviderDisplayName(provider)}
                        </TabsTrigger>
                      ))}
                    </TabsList>
                  </Tabs>
                ) : (
                  // Show single provider as a badge
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">Provider:</span>
                    <Badge variant="secondary" className="rounded-lg px-3 py-1">
                      {getProviderDisplayName(availableProviders[0])}
                    </Badge>
                  </div>
                ))}

              {/* Filters and Sorting */}
              {streamsData?.streams && streamsData.streams.length > 0 && (
                <StreamFilters
                  filters={streamFilters}
                  onFiltersChange={setStreamFilters}
                  availableSources={availableSources}
                  availableResolutions={availableResolutions}
                  availableQualities={availableQualities}
                  availableCodecs={availableCodecs}
                  availableStreamTypes={availableStreamTypes}
                  totalStreams={streamsData?.streams?.length || 0}
                  filteredCount={filteredStreams.length}
                  showCachedFilter={availableProviders.length > 0}
                  hasLastPlayed={!!lastPlayedStreamId}
                />
              )}
            </div>
          </CardHeader>
          <CardContent className="px-3 sm:px-6">
            {streamsLoading ? (
              <div className="space-y-3">
                {[...Array(5)].map((_, i) => (
                  <Skeleton key={i} className="h-20 rounded-xl" />
                ))}
              </div>
            ) : !streamsData?.streams.length ? (
              <div className="text-center py-8">
                <Wifi className="h-12 w-12 mx-auto text-muted-foreground opacity-50" />
                <p className="mt-4 text-muted-foreground">No streams available</p>
                <p className="text-sm text-muted-foreground mt-2">Make sure you have a streaming provider configured</p>
              </div>
            ) : filteredStreams.length === 0 ? (
              <div className="text-center py-8">
                <Wifi className="h-12 w-12 mx-auto text-muted-foreground opacity-50" />
                <p className="mt-4 text-muted-foreground">No streams match your filters</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-4 rounded-xl"
                  onClick={() => setStreamFilters(defaultStreamFilters)}
                >
                  Clear Filters
                </Button>
              </div>
            ) : viewMode === 'grouped' ? (
              <StreamGroupedList
                streams={filteredStreams}
                groupBy="quality"
                renderStream={(stream, index) => (
                  <StreamCard
                    key={stream.id || index}
                    stream={stream}
                    onClick={() => handleStreamClick(stream as CatalogStreamInfo)}
                    mediaType={catalogType === 'series' ? 'series' : 'movie'}
                    isLastPlayed={stream.id !== undefined && String(stream.id) === lastPlayedStreamId}
                  />
                )}
              />
            ) : (
              <div className="space-y-3">
                {filteredStreams.map((stream, index) => (
                  <StreamCard
                    key={stream.id || index}
                    stream={stream}
                    onClick={() => handleStreamClick(stream as CatalogStreamInfo)}
                    mediaType={catalogType === 'series' ? 'series' : 'movie'}
                    isLastPlayed={stream.id !== undefined && String(stream.id) === lastPlayedStreamId}
                  />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Login prompt for anonymous users */}
      {!isAuthenticated && (
        <Card className="glass border-border/50">
          <CardContent className="py-8 text-center">
            <Play className="h-12 w-12 mx-auto text-muted-foreground opacity-50" />
            <p className="mt-4 font-medium">Sign in to view streams</p>
            <p className="text-sm text-muted-foreground mt-2">
              You need to be logged in with a configured streaming provider to access streams
            </p>
            <Button asChild className="mt-4 rounded-xl">
              <Link to="/login">Sign In</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Stream Action Dialog */}
      <StreamActionDialog
        open={streamDialogOpen}
        onOpenChange={setStreamDialogOpen}
        stream={selectedStream}
        mediaId={mediaId}
        title={item.title}
        catalogType={catalogType === 'series' ? 'series' : catalogType === 'tv' ? 'tv' : 'movie'}
        season={selectedSeason}
        episode={selectedEpisode}
        selectedProvider={streamsData?.selected_provider}
        hasMediaflowProxy={streamsData?.web_playback_enabled || false}
        onStreamDeleted={() => refetchStreams()}
        onWatch={handleWatchStream}
      />

      {/* Video Player Dialog (rendered at page level to avoid nested dialog issues) */}
      {playerStream && playerStreamUrl && (
        <PlayerDialog
          open={playerOpen}
          onOpenChange={handlePlayerClose}
          stream={{
            id: playerStream.id ? String(playerStream.id) : undefined,
            name: playerStream.name,
            title: playerStream.stream_name,
            url: playerStreamUrl,
            quality: playerStream.quality,
            resolution: playerStream.resolution,
            size: playerStream.size,
            source: playerStream.source,
            codec: playerStream.codec,
            audio: playerStream.audio_formats,
            behaviorHints: playerStream.behavior_hints,
          }}
          contentTitle={item.title}
          startTime={startTime}
          onTimeUpdate={handleTimeUpdate}
          onEnded={() => handlePlayerClose(false)}
        />
      )}
    </div>
  )
}
