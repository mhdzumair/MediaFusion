import { Badge } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
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
import { Loader2, CheckCircle } from 'lucide-react'
import type { TorrentAnalyzeResponse } from '@/lib/api'

interface TorrentPreviewDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  analysis: TorrentAnalyzeResponse | null
  onImport: () => void
  isImporting: boolean
}

export function TorrentPreviewDialog({
  open,
  onOpenChange,
  analysis,
  onImport,
  isImporting,
}: TorrentPreviewDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="glass border-border/50 sm:max-w-[600px]">
        <AlertDialogHeader>
          <AlertDialogTitle>Confirm Import</AlertDialogTitle>
          <AlertDialogDescription>Review the torrent details before importing</AlertDialogDescription>
        </AlertDialogHeader>

        {analysis && (
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label className="text-muted-foreground">Name</Label>
              <p className="font-medium">{analysis.torrent_name || analysis.parsed_title}</p>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <Label className="text-muted-foreground">Size</Label>
                <p className="font-medium">
                  {analysis.total_size_readable ||
                    (analysis.total_size ? `${(analysis.total_size / (1024 * 1024 * 1024)).toFixed(2)} GB` : 'Unknown')}
                </p>
              </div>
              <div className="space-y-1">
                <Label className="text-muted-foreground">Files</Label>
                <p className="font-medium">{analysis.file_count ?? 'Unknown'}</p>
              </div>
            </div>

            {analysis.info_hash && (
              <div className="space-y-1">
                <Label className="text-muted-foreground">Info Hash</Label>
                <p className="font-mono text-xs bg-muted/50 p-2 rounded-lg break-all">{analysis.info_hash}</p>
              </div>
            )}

            {(analysis.resolution || analysis.quality) && (
              <div className="space-y-1">
                <Label className="text-muted-foreground">Quality Info</Label>
                <div className="flex gap-2">
                  {analysis.resolution && <Badge variant="secondary">{analysis.resolution}</Badge>}
                  {analysis.quality && <Badge variant="outline">{analysis.quality}</Badge>}
                  {analysis.codec && <Badge variant="outline">{analysis.codec}</Badge>}
                </div>
              </div>
            )}
          </div>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isImporting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={onImport}
            disabled={isImporting}
            className="bg-gradient-to-r from-primary to-primary/80"
          >
            {isImporting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Importing...
              </>
            ) : (
              <>
                <CheckCircle className="mr-2 h-4 w-4" />
                Import
              </>
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
