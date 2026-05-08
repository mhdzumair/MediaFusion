import { useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Youtube, Loader2, ArrowRight } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { contentImportApi, type YouTubeAnalyzeResponse } from '@/lib/api'
import type { ContentType } from '@/lib/constants'

interface YouTubeTabProps {
  onAnalysisComplete: (analysis: YouTubeAnalyzeResponse, youtubeUrl: string) => void
  onError: (message: string) => void
  contentType?: ContentType
}

const YOUTUBE_URL_PATTERNS = [
  /(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})/,
  /(?:https?:\/\/)?youtu\.be\/([a-zA-Z0-9_-]{11})/,
  /(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})/,
  /(?:https?:\/\/)?(?:www\.)?youtube\.com\/v\/([a-zA-Z0-9_-]{11})/,
  /(?:https?:\/\/)?(?:www\.)?youtube\.com\/shorts\/([a-zA-Z0-9_-]{11})/,
]

function extractVideoId(url: string): string | null {
  for (const pattern of YOUTUBE_URL_PATTERNS) {
    const match = url.match(pattern)
    if (match) return match[1]
  }
  if (/^[a-zA-Z0-9_-]{11}$/.test(url)) return url
  return null
}

export function YouTubeTab({ onAnalysisComplete, onError, contentType = 'movie' }: YouTubeTabProps) {
  const [youtubeUrl, setYoutubeUrl] = useState('')

  const videoId = extractVideoId(youtubeUrl)
  const isValidUrl = !!videoId

  const analyzeMutation = useMutation({
    mutationFn: () =>
      contentImportApi.analyzeYouTube({
        youtube_url: youtubeUrl,
        meta_type: contentType,
      }),
    onSuccess: (result) => {
      if (result.status === 'success') {
        onAnalysisComplete(result, youtubeUrl)
        setYoutubeUrl('')
      } else {
        onError(result.error || 'Failed to analyze YouTube URL')
      }
    },
    onError: () => {
      onError('Failed to analyze YouTube URL')
    },
  })

  const handleAnalyze = useCallback(() => {
    if (!isValidUrl) return
    analyzeMutation.mutate()
  }, [isValidUrl, analyzeMutation])

  return (
    <Card className="glass border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Youtube className="h-5 w-5 text-red-500" />
          Import YouTube Video
        </CardTitle>
        <CardDescription>
          Paste a YouTube URL and click Analyze to fetch video metadata and find matching content
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="youtube-url">YouTube URL</Label>
          <div className="flex gap-2">
            <Input
              id="youtube-url"
              placeholder="https://www.youtube.com/watch?v=... or youtu.be/..."
              value={youtubeUrl}
              onChange={(e) => setYoutubeUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && isValidUrl) handleAnalyze()
              }}
              className="font-mono text-sm rounded-xl"
            />
            <Button
              onClick={handleAnalyze}
              disabled={!isValidUrl || analyzeMutation.isPending}
              variant="outline"
              className="rounded-xl"
            >
              {analyzeMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <ArrowRight className="mr-2 h-4 w-4" />
              )}
              Analyze
            </Button>
          </div>
          {youtubeUrl && !isValidUrl && <p className="text-sm text-destructive">Invalid YouTube URL</p>}
        </div>
      </CardContent>
    </Card>
  )
}
