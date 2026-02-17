import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Edit, Trash2, Ban, Loader2, MoreVertical, Flag, FileVideo } from 'lucide-react'
import { useState, useCallback } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { useBlockTorrentStream, useDeleteTorrentStream } from '@/hooks/useAdmin'
import { useCreateStreamSuggestion } from '@/hooks'
import type { CatalogStreamInfo } from '@/lib/api'
import { StreamEditSheet } from './StreamEditSheet'
import { StreamReport } from './StreamReport'
import { FileAnnotationDialog, type FileLink, type EditedFileLink } from './FileAnnotationDialog'
import { catalogApi } from '@/lib/api'

interface StreamCardProps {
  stream: CatalogStreamInfo
  onClick: () => void
  showActions?: boolean
  showModeratorActions?: boolean
  onDeleted?: () => void
  mediaType?: 'movie' | 'series'
  isLastPlayed?: boolean // Highlight this stream as the last played
}

// Helper to format HDR formats array as string
function getHdrFormatsString(stream: CatalogStreamInfo): string | undefined {
  return stream.hdr_formats || undefined
}

// Helper to format audio formats array as string
function getAudioFormatsString(stream: CatalogStreamInfo): string | undefined {
  return stream.audio_formats || undefined
}

export function StreamCard({
  stream,
  onClick,
  showActions = true,
  showModeratorActions = true,
  onDeleted,
  mediaType = 'movie',
  isLastPlayed = false,
}: StreamCardProps) {
  const hdrFormatsString = getHdrFormatsString(stream)
  const audioFormatsString = getAudioFormatsString(stream)
  const { hasMinimumRole, isAuthenticated } = useAuth()
  const isModerator = hasMinimumRole('moderator')

  // Dialog states for moderator actions
  const [blockDialogOpen, setBlockDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  // File annotation state
  const [annotationDialogOpen, setAnnotationDialogOpen] = useState(false)
  const [annotationFiles, setAnnotationFiles] = useState<FileLink[]>([])
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [isSavingAnnotation, setIsSavingAnnotation] = useState(false)

  // Moderator mutations
  const blockStream = useBlockTorrentStream()
  const deleteStream = useDeleteTorrentStream()
  const createSuggestion = useCreateStreamSuggestion()

  const isDeleting = blockStream.isPending || deleteStream.isPending

  const handleBlock = async () => {
    if (!stream.id) return
    await blockStream.mutateAsync(stream.id)
    setBlockDialogOpen(false)
    onDeleted?.()
  }

  const handleDelete = async () => {
    if (!stream.id) return
    await deleteStream.mutateAsync(stream.id)
    setDeleteDialogOpen(false)
    onDeleted?.()
  }

  // Handle opening file annotation dialog
  const handleOpenAnnotation = async () => {
    if (!stream.id) return

    setIsLoadingFiles(true)
    try {
      // Fetch stream files from the API (includes file sizes)
      const files = await catalogApi.getStreamFiles(stream.id)
      setAnnotationFiles(
        files.map((f) => ({
          file_id: f.file_id,
          file_name: f.file_name,
          size: f.size,
          season_number: f.season_number,
          episode_number: f.episode_number,
          episode_end: f.episode_end,
        })),
      )
      setAnnotationDialogOpen(true)
    } catch (error) {
      console.error('Failed to load stream files:', error)
      // If API fails, fall back to episode_links if available
      if (stream.episode_links && stream.episode_links.length > 0) {
        setAnnotationFiles(
          stream.episode_links.map((el) => ({
            file_id: el.file_id,
            file_name: el.file_name,
            size: null, // No size available in fallback
            season_number: el.season_number ?? null,
            episode_number: el.episode_number ?? null,
            episode_end: el.episode_end ?? null,
          })),
        )
        setAnnotationDialogOpen(true)
      }
    } finally {
      setIsLoadingFiles(false)
    }
  }

  // Handle saving file annotations
  const handleSaveAnnotation = useCallback(
    async (editedFiles: EditedFileLink[]) => {
      if (!stream.id) return

      setIsSavingAnnotation(true)
      try {
        // Submit each modified field as a suggestion
        for (const editedFile of editedFiles) {
          if (!editedFile.isModified) continue

          const originalFile = annotationFiles.find((f) => f.file_id === editedFile.file_id)
          if (!originalFile) continue

          // Check which fields changed and submit suggestions
          if (editedFile.season_number !== originalFile.season_number) {
            await createSuggestion.mutateAsync({
              streamId: stream.id,
              data: {
                suggestion_type: 'field_correction',
                field_name: `episode_link:${editedFile.file_id}:season_number`,
                current_value: String(originalFile.season_number ?? ''),
                suggested_value: String(editedFile.season_number ?? ''),
                reason: `Episode link fix for file: ${editedFile.file_name}`,
              },
            })
          }

          if (editedFile.episode_number !== originalFile.episode_number) {
            await createSuggestion.mutateAsync({
              streamId: stream.id,
              data: {
                suggestion_type: 'field_correction',
                field_name: `episode_link:${editedFile.file_id}:episode_number`,
                current_value: String(originalFile.episode_number ?? ''),
                suggested_value: String(editedFile.episode_number ?? ''),
                reason: `Episode link fix for file: ${editedFile.file_name}`,
              },
            })
          }

          if (editedFile.episode_end !== originalFile.episode_end) {
            await createSuggestion.mutateAsync({
              streamId: stream.id,
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

        // Refresh the stream data
        onDeleted?.()
      } finally {
        setIsSavingAnnotation(false)
      }
    },
    [stream.id, annotationFiles, createSuggestion, onDeleted],
  )

  // Check if we should show the annotate option (series with episode data)
  const hasEpisodeFiles = mediaType === 'series' && isAuthenticated

  return (
    <div
      className={`group relative flex items-start gap-3 p-3 rounded-xl border transition-all cursor-pointer ${
        isLastPlayed
          ? 'border-l-2 border-l-primary border-t-border/50 border-r-border/50 border-b-border/50 bg-primary/5 hover:bg-primary/10'
          : 'border-border/50 bg-card/50 hover:border-primary/50 hover:bg-card/80'
      }`}
      onClick={onClick}
    >
      {/* Main content */}
      <div className="flex-1 min-w-0 space-y-1 relative z-10">
        {/* Stream name (title) with Last Played badge */}
        <div className="flex items-center gap-2">
          <p className="font-medium text-sm leading-tight flex-1">{stream.name}</p>
          {isLastPlayed && (
            <span className="text-[10px] font-medium bg-primary/20 text-primary px-1.5 py-0.5 rounded shrink-0">
              Last Played
            </span>
          )}
        </div>

        {/* Pre-formatted description from API */}
        {stream.description && (
          <p className="text-xs text-muted-foreground whitespace-pre-line leading-relaxed">{stream.description}</p>
        )}
      </div>

      {/* Compact Actions Menu */}
      {showActions && stream.id && (
        <div className="relative z-10 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-foreground">
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <StreamEditSheet
                streamId={stream.id}
                streamName={stream.name}
                currentValues={{
                  name: stream.name,
                  resolution: stream.resolution,
                  quality: stream.quality,
                  codec: stream.codec,
                  bit_depth: stream.bit_depth,
                  audio_formats: audioFormatsString,
                  channels: stream.channels,
                  hdr_formats: hdrFormatsString,
                  source: stream.source,
                  languages: stream.languages,
                  size: stream.size,
                }}
                mediaType={mediaType}
                episodeLinks={stream.episode_links || []}
                trigger={
                  <DropdownMenuItem onSelect={(e) => e.preventDefault()}>
                    <Edit className="h-4 w-4 mr-2" />
                    Edit Stream
                  </DropdownMenuItem>
                }
              />

              {/* Annotate Files option for series */}
              {hasEpisodeFiles && (
                <DropdownMenuItem
                  onSelect={(e) => {
                    e.preventDefault()
                    handleOpenAnnotation()
                  }}
                  disabled={isLoadingFiles}
                >
                  {isLoadingFiles ? (
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <FileVideo className="h-4 w-4 mr-2" />
                  )}
                  Annotate Files
                </DropdownMenuItem>
              )}

              <StreamReport
                streamId={stream.id}
                streamName={stream.name}
                currentQuality={stream.quality || stream.resolution}
                currentLanguage={audioFormatsString}
                trigger={
                  <DropdownMenuItem onSelect={(e) => e.preventDefault()}>
                    <Flag className="h-4 w-4 mr-2" />
                    Report Issue
                  </DropdownMenuItem>
                }
              />

              {/* Moderator Actions */}
              {showModeratorActions && isModerator && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    className="text-primary"
                    onSelect={() => setBlockDialogOpen(true)}
                    disabled={isDeleting}
                  >
                    {blockStream.isPending ? (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    ) : (
                      <Ban className="h-4 w-4 mr-2" />
                    )}
                    Block Stream
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="text-red-600"
                    onSelect={() => setDeleteDialogOpen(true)}
                    disabled={isDeleting}
                  >
                    {deleteStream.isPending ? (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4 mr-2" />
                    )}
                    Delete Stream
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>

          {/* File Annotation Dialog */}
          <FileAnnotationDialog
            open={annotationDialogOpen}
            onOpenChange={setAnnotationDialogOpen}
            streamName={stream.name}
            initialFiles={annotationFiles}
            onSave={handleSaveAnnotation}
            isLoading={isSavingAnnotation}
          />

          {/* Moderator Dialogs */}
          <AlertDialog open={blockDialogOpen} onOpenChange={setBlockDialogOpen}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Block this stream?</AlertDialogTitle>
                <AlertDialogDescription>
                  This will block the stream from appearing in search results. The stream can be unblocked later.
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

          <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
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
      )}
    </div>
  )
}
