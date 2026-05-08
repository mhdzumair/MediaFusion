import { useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Upload, FileInput, Loader2, Link, Newspaper } from 'lucide-react'
import { useDropzone } from 'react-dropzone'
import { useMutation } from '@tanstack/react-query'
import { contentImportApi, type NZBAnalyzeResponse } from '@/lib/api'
import type { ContentType } from '@/lib/constants'

export type NZBSource = { type: 'file' | 'url'; file?: File; url?: string }

interface NZBTabProps {
  onAnalysisComplete: (analysis: NZBAnalyzeResponse, source: NZBSource) => void
  onError: (message: string) => void
  contentType?: ContentType
  fileImportEnabled?: boolean
}

export function NZBTab({ onAnalysisComplete, onError, contentType = 'movie', fileImportEnabled = false }: NZBTabProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [nzbUrl, setNzbUrl] = useState('')
  const [activeMethod, setActiveMethod] = useState<'file' | 'url'>(fileImportEnabled ? 'file' : 'url')

  const analyzeFile = useMutation({
    mutationFn: (file: File) => contentImportApi.analyzeNZBFile(file, contentType === 'movie' ? 'movie' : 'series'),
    onSuccess: (result, file) => {
      if (result.status === 'success' || result.matches) {
        onAnalysisComplete(result, { type: 'file', file })
      } else {
        onError(result.error || 'Failed to analyze NZB file')
      }
    },
    onError: () => {
      onError('Failed to analyze NZB file')
    },
  })

  const analyzeUrl = useMutation({
    mutationFn: (url: string) => contentImportApi.analyzeNZBUrl(url, contentType === 'movie' ? 'movie' : 'series'),
    onSuccess: (result) => {
      if (result.status === 'success' || result.matches) {
        onAnalysisComplete(result, { type: 'url', url: nzbUrl })
      } else {
        onError(result.error || 'Failed to analyze NZB URL')
      }
    },
    onError: () => {
      onError('Failed to analyze NZB URL')
    },
  })

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      if (acceptedFiles.length > 0) {
        const file = acceptedFiles[0]
        setSelectedFile(file)
        analyzeFile.mutate(file)
      }
    },
    [analyzeFile],
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/x-nzb': ['.nzb'],
      'application/xml': ['.nzb'],
      'text/xml': ['.nzb'],
    },
    maxFiles: 1,
  })

  const handleUrlSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (nzbUrl.trim()) {
      analyzeUrl.mutate(nzbUrl.trim())
    }
  }

  const isAnalyzing = analyzeFile.isPending || analyzeUrl.isPending

  return (
    <Card className="glass border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Newspaper className="h-5 w-5 text-primary" />
          Import NZB
        </CardTitle>
        <CardDescription>
          {fileImportEnabled
            ? 'Upload an NZB file or provide a URL to analyze and import Usenet content'
            : 'Provide an NZB URL to analyze and import Usenet content'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {fileImportEnabled ? (
          <Tabs value={activeMethod} onValueChange={(v) => setActiveMethod(v as 'file' | 'url')}>
            <TabsList className="grid w-full grid-cols-2 mb-4">
              <TabsTrigger value="file" className="flex items-center gap-2">
                <Upload className="h-4 w-4" />
                Upload File
              </TabsTrigger>
              <TabsTrigger value="url" className="flex items-center gap-2">
                <Link className="h-4 w-4" />
                NZB URL
              </TabsTrigger>
            </TabsList>

            <TabsContent value="file">
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
                    <Newspaper className="h-8 w-8 text-primary" />
                  </div>
                  {isDragActive ? (
                    <p className="text-primary font-medium">Drop the NZB file here...</p>
                  ) : (
                    <>
                      <div className="space-y-1">
                        <p className="font-medium">Drag and drop an NZB file here, or click to browse</p>
                        <p className="text-sm text-muted-foreground">Only .nzb files are accepted</p>
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
            </TabsContent>

            <TabsContent value="url">
              <form onSubmit={handleUrlSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="nzb-url">NZB URL</Label>
                  <Input
                    id="nzb-url"
                    type="url"
                    placeholder="https://example.com/download.nzb"
                    value={nzbUrl}
                    onChange={(e) => setNzbUrl(e.target.value)}
                    disabled={isAnalyzing}
                  />
                  <p className="text-xs text-muted-foreground">
                    Enter a direct URL to an NZB file or an indexer download link
                  </p>
                </div>
                <Button type="submit" disabled={!nzbUrl.trim() || isAnalyzing} className="w-full">
                  {isAnalyzing ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Analyzing...
                    </>
                  ) : (
                    <>
                      <Newspaper className="h-4 w-4 mr-2" />
                      Analyze NZB
                    </>
                  )}
                </Button>
              </form>
            </TabsContent>
          </Tabs>
        ) : (
          <form onSubmit={handleUrlSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="nzb-url">NZB URL</Label>
              <Input
                id="nzb-url"
                type="url"
                placeholder="https://example.com/download.nzb"
                value={nzbUrl}
                onChange={(e) => setNzbUrl(e.target.value)}
                disabled={isAnalyzing}
              />
              <p className="text-xs text-muted-foreground">
                Enter a direct URL to an NZB file or an indexer download link
              </p>
            </div>
            <Button type="submit" disabled={!nzbUrl.trim() || isAnalyzing} className="w-full">
              {isAnalyzing ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Analyzing...
                </>
              ) : (
                <>
                  <Newspaper className="h-4 w-4 mr-2" />
                  Analyze NZB
                </>
              )}
            </Button>
          </form>
        )}

        {isAnalyzing && (
          <div className="mt-4 p-4 rounded-xl bg-muted/50 flex items-center justify-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            <span className="text-muted-foreground">Analyzing NZB...</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
