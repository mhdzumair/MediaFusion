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
import { apiClient } from '@/lib/api/client'
import type { ContentType } from '@/lib/constants'

interface NZBAnalyzeResponse {
  status: string
  nzb_guid?: string
  nzb_title?: string
  total_size?: number
  total_size_readable?: string
  file_count?: number
  files?: Array<{
    filename: string
    size: number
    index: number
  }>
  parsed_title?: string
  year?: number
  resolution?: string
  quality?: string
  codec?: string
  audio?: string[]
  matches?: Array<{
    id: string
    title: string
    year?: number
    poster?: string
    type: string
    source: string
    confidence?: number
  }>
  error?: string
  indexer?: string
  group_name?: string
  poster?: string
  posted_at?: string
  is_passworded?: boolean
}

interface NZBTabProps {
  onAnalysisComplete: (analysis: NZBAnalyzeResponse, source: { type: 'file' | 'url'; file?: File; url?: string }) => void
  onError: (message: string) => void
  contentType?: ContentType
}

// API functions
async function analyzeNZBFile(file: File, metaType: string): Promise<NZBAnalyzeResponse> {
  const formData = new FormData()
  formData.append('nzb_file', file)
  formData.append('meta_type', metaType)
  
  return apiClient.upload<NZBAnalyzeResponse>('/import/nzb/analyze/file', formData)
}

async function analyzeNZBUrl(url: string, metaType: string): Promise<NZBAnalyzeResponse> {
  return apiClient.post<NZBAnalyzeResponse>('/import/nzb/analyze/url', {
    nzb_url: url,
    meta_type: metaType
  })
}

export function NZBTab({ onAnalysisComplete, onError, contentType = 'movie' }: NZBTabProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [nzbUrl, setNzbUrl] = useState('')
  const [activeMethod, setActiveMethod] = useState<'file' | 'url'>('file')
  
  // File analysis mutation
  const analyzeFile = useMutation({
    mutationFn: (file: File) => analyzeNZBFile(file, contentType),
    onSuccess: (result, file) => {
      if (result.status === 'success' || result.matches) {
        onAnalysisComplete(result, { type: 'file', file })
      } else {
        onError(result.error || 'Failed to analyze NZB file')
      }
    },
    onError: () => {
      onError('Failed to analyze NZB file')
    }
  })
  
  // URL analysis mutation
  const analyzeUrl = useMutation({
    mutationFn: (url: string) => analyzeNZBUrl(url, contentType),
    onSuccess: (result) => {
      if (result.status === 'success' || result.matches) {
        onAnalysisComplete(result, { type: 'url', url: nzbUrl })
      } else {
        onError(result.error || 'Failed to analyze NZB URL')
      }
    },
    onError: () => {
      onError('Failed to analyze NZB URL')
    }
  })

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (acceptedFiles.length > 0) {
      const file = acceptedFiles[0]
      setSelectedFile(file)
      analyzeFile.mutate(file)
    }
  }, [analyzeFile])

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
          Upload an NZB file or provide a URL to analyze and import Usenet content
        </CardDescription>
      </CardHeader>
      <CardContent>
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
                ${isDragActive 
                  ? 'border-primary bg-primary/10' 
                  : 'border-border/50 hover:border-primary/50 hover:bg-muted/30'}
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
                      <p className="font-medium">
                        Drag and drop an NZB file here, or click to browse
                      </p>
                      <p className="text-sm text-muted-foreground">
                        Only .nzb files are accepted
                      </p>
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
              <Button 
                type="submit" 
                disabled={!nzbUrl.trim() || isAnalyzing}
                className="w-full"
              >
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
