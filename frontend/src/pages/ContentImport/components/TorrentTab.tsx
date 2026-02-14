import { useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Upload, FileInput, Loader2 } from 'lucide-react'
import { useDropzone } from 'react-dropzone'
import { useAnalyzeTorrent } from '@/hooks'
import type { TorrentAnalyzeResponse, TorrentMetaType } from '@/lib/api'
import type { ContentType } from '@/lib/constants'

// Helper to convert ContentType to TorrentMetaType (defaults to 'movie' for unsupported types like 'tv')
function toTorrentMetaType(contentType: ContentType): TorrentMetaType {
  if (contentType === 'tv') return 'movie'
  return contentType
}

interface TorrentTabProps {
  onAnalysisComplete: (analysis: TorrentAnalyzeResponse, file: File) => void
  onError: (message: string) => void
  contentType?: ContentType
}

export function TorrentTab({ onAnalysisComplete, onError, contentType = 'movie' }: TorrentTabProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const analyzeTorrent = useAnalyzeTorrent()

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      if (acceptedFiles.length > 0) {
        const file = acceptedFiles[0]
        setSelectedFile(file)

        try {
          const result = await analyzeTorrent.mutateAsync({ file, metaType: toTorrentMetaType(contentType) })
          if (result.status === 'success' || result.matches) {
            onAnalysisComplete(result, file)
          } else {
            onError(result.error || 'Failed to analyze torrent')
          }
        } catch {
          onError('Failed to analyze torrent file')
        }
      }
    },
    [analyzeTorrent, onAnalysisComplete, onError, contentType],
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/x-bittorrent': ['.torrent'],
    },
    maxFiles: 1,
  })

  const isAnalyzing = analyzeTorrent.isPending

  return (
    <Card className="glass border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Upload className="h-5 w-5 text-primary" />
          Upload Torrent File
        </CardTitle>
        <CardDescription>Upload a .torrent file to analyze and import</CardDescription>
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
              <p className="text-primary font-medium">Drop the torrent file here...</p>
            ) : (
              <>
                <div className="space-y-1">
                  <p className="font-medium">Drag and drop a torrent file here, or click to browse</p>
                  <p className="text-sm text-muted-foreground">Only .torrent files are accepted</p>
                </div>
                <Button variant="outline" className="rounded-xl">
                  Browse Files
                </Button>
              </>
            )}
          </div>
        </div>
        {selectedFile && !isAnalyzing && (
          <div className="mt-4 p-4 rounded-xl bg-muted/50 flex items-center gap-3">
            <FileInput className="h-5 w-5 text-primary" />
            <span className="font-medium truncate">{selectedFile.name}</span>
            <Badge variant="secondary" className="ml-auto">
              {(selectedFile.size / 1024).toFixed(1)} KB
            </Badge>
          </div>
        )}
        {isAnalyzing && (
          <div className="mt-4 p-4 rounded-xl bg-muted/50 flex items-center justify-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            <span className="text-muted-foreground">Analyzing torrent...</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
