import { useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Upload, FileInput, Loader2 } from 'lucide-react'
import { useDropzone } from 'react-dropzone'
import { useAnalyzeTorrent } from '@/hooks'
import type { TorrentMetaType } from '@/lib/api'
import type { ContentType } from '@/lib/constants'
import type { TorrentBatchAnalysisItem } from './types'

// Helper to convert ContentType to TorrentMetaType (defaults to 'movie' for unsupported types like 'tv')
function toTorrentMetaType(contentType: ContentType): TorrentMetaType {
  if (contentType === 'tv') return 'movie'
  return contentType
}

interface TorrentTabProps {
  onAnalysisComplete: (items: TorrentBatchAnalysisItem[]) => void
  onError: (message: string) => void
  contentType?: ContentType
}

export function TorrentTab({ onAnalysisComplete, onError, contentType = 'movie' }: TorrentTabProps) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [analysisProgress, setAnalysisProgress] = useState<{ current: number; total: number } | null>(null)
  const analyzeTorrent = useAnalyzeTorrent()

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      if (acceptedFiles.length === 0) return

      setSelectedFiles(acceptedFiles)

      const successfulAnalyses: TorrentBatchAnalysisItem[] = []
      const failedFiles: string[] = []
      const metaType = toTorrentMetaType(contentType)

      for (let index = 0; index < acceptedFiles.length; index += 1) {
        const file = acceptedFiles[index]
        setAnalysisProgress({ current: index + 1, total: acceptedFiles.length })

        try {
          const result = await analyzeTorrent.mutateAsync({ file, metaType })
          if (result.status === 'success' || result.matches) {
            successfulAnalyses.push({ file, analysis: result })
          } else {
            failedFiles.push(file.name)
          }
        } catch {
          failedFiles.push(file.name)
        }
      }

      setAnalysisProgress(null)

      if (successfulAnalyses.length > 0) {
        onAnalysisComplete(successfulAnalyses)
      }

      if (failedFiles.length > 0) {
        const preview = failedFiles.slice(0, 3).join(', ')
        const suffix = failedFiles.length > 3 ? ` and ${failedFiles.length - 3} more` : ''
        onError(`Failed to analyze ${failedFiles.length} torrent file(s): ${preview}${suffix}`)
      }
    },
    [analyzeTorrent, onAnalysisComplete, onError, contentType],
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/x-bittorrent': ['.torrent'],
    },
  })

  const isAnalyzing = analyzeTorrent.isPending || analysisProgress !== null

  return (
    <Card className="glass border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Upload className="h-5 w-5 text-primary" />
          Upload Torrent Files
        </CardTitle>
        <CardDescription>Upload one or more .torrent files to analyze and import</CardDescription>
      </CardHeader>
      <CardContent>
        <div
          {...getRootProps()}
          className={`
            border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-colors
            ${
              isDragActive
                ? 'border-primary bg-primary/10'
                : 'border-border/50 hover:border-primary/50 hover:bg-muted/30'
            }
          `}
        >
          <input {...getInputProps()} />
          <div className="flex flex-col items-center gap-4">
            <div className="p-4 rounded-2xl bg-primary/10">
              <Upload className="h-8 w-8 text-primary" />
            </div>
            {isDragActive ? (
              <p className="text-primary font-medium">Drop the torrent files here...</p>
            ) : (
              <>
                <div className="space-y-1">
                  <p className="font-medium">Drag and drop torrent files here, or click to browse</p>
                  <p className="text-sm text-muted-foreground">Only .torrent files are accepted</p>
                </div>
                <Button variant="outline" className="rounded-xl">
                  Browse Files
                </Button>
              </>
            )}
          </div>
        </div>
        {selectedFiles.length > 0 && !isAnalyzing && (
          <div className="mt-4 p-4 rounded-xl bg-muted/50 space-y-2">
            <div className="flex items-center gap-3">
              <FileInput className="h-5 w-5 text-primary" />
              <span className="font-medium">{selectedFiles.length} file(s) selected</span>
            </div>
            <div className="space-y-1 max-h-36 overflow-auto pr-1">
              {selectedFiles.map((file) => (
                <div key={`${file.name}-${file.size}`} className="flex items-center gap-3 text-sm">
                  <span className="truncate">{file.name}</span>
                  <Badge variant="secondary" className="ml-auto">
                    {(file.size / 1024).toFixed(1)} KB
                  </Badge>
                </div>
              ))}
            </div>
          </div>
        )}
        {isAnalyzing && (
          <div className="mt-4 p-4 rounded-xl bg-muted/50 flex items-center justify-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            <span className="text-muted-foreground">
              {analysisProgress
                ? `Analyzing torrent ${analysisProgress.current} of ${analysisProgress.total}...`
                : 'Analyzing torrents...'}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
