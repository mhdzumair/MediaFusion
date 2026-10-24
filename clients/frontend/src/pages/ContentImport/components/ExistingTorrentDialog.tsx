import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { CollectionAnnotationDialog } from '@/components/stream/CollectionAnnotationDialog'
import { StreamResolutionEditor } from '@/components/stream/StreamResolutionEditor'
import type { TorrentAnalyzeResponse } from '@/lib/api'

export function ExistingTorrentDialog({
  open,
  onOpenChange,
  analysis,
  onDone,
  doneLabel = 'Done',
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  analysis: TorrentAnalyzeResponse
  onDone: () => void
  doneLabel?: string
}) {
  const [annotating, setAnnotating] = useState(false)
  const files = useMemo(
    () =>
      (analysis.files || [])
        .filter((file) => file.file_id != null && file.index >= 0)
        .map((file) => ({
          file_id: file.file_id!,
          file_name: file.path || file.filename,
          file_index: file.index,
          size: file.size,
          linked_media_id: file.linked_media_id,
          linked_media_title: file.linked_media_title,
          linked_media_type: file.linked_media_type,
        })),
    [analysis.files],
  )
  const streamId = analysis.existing_stream_id
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent scrollMode="contained" className="sm:max-w-[750px] max-h-[85vh] min-h-0 flex flex-col">
          <DialogHeader>
            <DialogTitle>This torrent is already in MediaFusion</DialogTitle>
            <DialogDescription>
              Review the existing entry or add missing information. There is no need to import it again.
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm font-medium break-all">{analysis.torrent_name || analysis.info_hash}</p>
          {streamId != null && (
            <StreamResolutionEditor
              key={`${streamId}:${analysis.resolution ?? ''}`}
              streamId={streamId}
              currentResolution={analysis.resolution}
            />
          )}
          <ScrollArea className="flex-1 min-h-0">
            <div className="space-y-4 pr-4">
              <div className="space-y-2">
                <p className="text-sm font-medium">Linked titles ({analysis.attached_media_count ?? 0})</p>
                {analysis.attached_media
                  ?.filter((media) => ['movie', 'series', 'tv'].includes(media.type))
                  .map((media) => (
                    <Link
                      key={media.media_id}
                      className="block text-sm text-primary underline"
                      to={`/dashboard/content/${media.type}/${media.media_id}`}
                    >
                      {media.title}
                      {media.year ? ` (${media.year})` : ''} — review metadata and stream links
                    </Link>
                  ))}
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium">Stored files ({files.length})</p>
                {files.map((file) => (
                  <div key={file.file_id} className="rounded border p-2 text-sm">
                    <p className="break-all">{file.file_name}</p>
                    <p className="text-xs text-muted-foreground">{file.linked_media_title || 'No media link yet'}</p>
                  </div>
                ))}
              </div>
            </div>
          </ScrollArea>
          <div className="flex justify-end gap-2 pt-3">
            {streamId != null && files.length > 0 && (
              <Button variant="outline" onClick={() => setAnnotating(true)}>
                Add missing file links
              </Button>
            )}
            <Button onClick={onDone}>{doneLabel}</Button>
          </div>
        </DialogContent>
      </Dialog>
      {streamId != null && (
        <CollectionAnnotationDialog
          open={annotating}
          onOpenChange={setAnnotating}
          streamId={streamId}
          streamName={analysis.torrent_name || analysis.info_hash || 'Existing torrent'}
          currentResolution={analysis.resolution}
          files={files}
        />
      )}
    </>
  )
}
