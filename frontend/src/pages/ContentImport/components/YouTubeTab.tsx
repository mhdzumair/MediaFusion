import { useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Youtube, Loader2, ArrowRight, Info, CheckCircle, ExternalLink } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { contentImportApi, type YouTubeAnalyzeResponse, type ImportResponse } from '@/lib/api'
import type { ContentType } from '@/lib/constants'
import { useAuth } from '@/contexts/AuthContext'

interface YouTubeTabProps {
  onSuccess: (message: string) => void
  onError: (message: string) => void
  contentType?: ContentType
}

// YouTube URL patterns for validation
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
  // Check if it's already a video ID
  if (/^[a-zA-Z0-9_-]{11}$/.test(url)) return url
  return null
}

export function YouTubeTab({ onSuccess, onError, contentType = 'movie' }: YouTubeTabProps) {
  const { user } = useAuth()
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [metaId, setMetaId] = useState('')
  const [title, setTitle] = useState('')
  const [languages, setLanguages] = useState('')
  const [isAnonymous, setIsAnonymous] = useState(user?.contribute_anonymously ?? false)
  const [analysis, setAnalysis] = useState<YouTubeAnalyzeResponse | null>(null)

  // Extract video ID from current input
  const videoId = extractVideoId(youtubeUrl)
  const isValidUrl = !!videoId

  // Analyze mutation
  const analyzeMutation = useMutation({
    mutationFn: () =>
      contentImportApi.analyzeYouTube({
        youtube_url: youtubeUrl,
        meta_type: contentType,
      }),
    onSuccess: (result) => {
      if (result.status === 'success') {
        setAnalysis(result)
        if (result.title && !title) {
          setTitle(result.title)
        }
      } else {
        onError(result.error || 'Failed to analyze YouTube URL')
      }
    },
    onError: () => {
      onError('Failed to analyze YouTube URL')
    },
  })

  // Import mutation
  const importMutation = useMutation({
    mutationFn: () =>
      contentImportApi.importYouTube({
        youtube_url: youtubeUrl,
        meta_type: contentType,
        meta_id: metaId || undefined,
        title: title || undefined,
        languages: languages || undefined,
        is_anonymous: isAnonymous,
      }),
    onSuccess: (result: ImportResponse) => {
      if (result.status === 'success') {
        onSuccess(result.message || 'YouTube video imported successfully!')
        // Reset form
        setYoutubeUrl('')
        setMetaId('')
        setTitle('')
        setLanguages('')
        setAnalysis(null)
      } else if (result.status === 'warning') {
        onSuccess(result.message)
      } else {
        onError(result.message || 'Failed to import YouTube video')
      }
    },
    onError: () => {
      onError('Failed to import YouTube video')
    },
  })

  const handleAnalyze = useCallback(() => {
    if (!isValidUrl) return
    analyzeMutation.mutate()
  }, [isValidUrl, analyzeMutation])

  const handleImport = useCallback(() => {
    if (!isValidUrl || !metaId) return
    importMutation.mutate()
  }, [isValidUrl, metaId, importMutation])

  const isLoading = analyzeMutation.isPending || importMutation.isPending

  return (
    <Card className="glass border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Youtube className="h-5 w-5 text-red-500" />
          Import YouTube Video
        </CardTitle>
        <CardDescription>Import a YouTube video as a stream for your content</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* YouTube URL Input */}
        <div className="space-y-2">
          <Label htmlFor="youtube-url">YouTube URL</Label>
          <div className="flex gap-2">
            <Input
              id="youtube-url"
              placeholder="https://www.youtube.com/watch?v=... or youtu.be/..."
              value={youtubeUrl}
              onChange={(e) => {
                setYoutubeUrl(e.target.value)
                setAnalysis(null)
              }}
              className="font-mono text-sm rounded-xl"
            />
            <Button
              onClick={handleAnalyze}
              disabled={!isValidUrl || isLoading}
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

        {/* Video Preview */}
        {videoId && (
          <div className="p-4 rounded-xl bg-muted/50 space-y-3">
            <div className="flex gap-4">
              <img
                src={`https://img.youtube.com/vi/${videoId}/mqdefault.jpg`}
                alt="Video thumbnail"
                className="w-40 h-auto rounded-lg object-cover"
              />
              <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">Video ID: {videoId}</Badge>
                  {analysis?.is_live && <Badge variant="destructive">Live</Badge>}
                </div>
                {analysis?.title && <p className="font-medium">{analysis.title}</p>}
                {analysis?.channel_name && <p className="text-sm text-muted-foreground">{analysis.channel_name}</p>}
                <a
                  href={`https://www.youtube.com/watch?v=${videoId}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                >
                  <ExternalLink className="h-3 w-3" />
                  View on YouTube
                </a>
              </div>
            </div>
          </div>
        )}

        {/* Metadata Input */}
        <div className="space-y-4 pt-4 border-t">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="meta-id">{contentType === 'tv' ? 'Channel ID *' : 'IMDb ID / Meta ID *'}</Label>
              <Input
                id="meta-id"
                placeholder={contentType === 'tv' ? 'mf_tv_channel_name' : 'tt1234567'}
                value={metaId}
                onChange={(e) => setMetaId(e.target.value)}
                className="rounded-xl"
              />
              <p className="text-xs text-muted-foreground">
                {contentType === 'tv'
                  ? 'Required. A unique identifier for the TV channel (e.g., mf_tv_bbc_one).'
                  : 'Required. The IMDb ID of the movie/series this video belongs to.'}
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="title">Title (Optional)</Label>
              <Input
                id="title"
                placeholder="Video title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="rounded-xl"
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="languages">Languages (Optional)</Label>
            <Input
              id="languages"
              placeholder="English, Spanish"
              value={languages}
              onChange={(e) => setLanguages(e.target.value)}
              className="rounded-xl"
            />
            <p className="text-xs text-muted-foreground">Comma-separated list of languages</p>
          </div>
        </div>

        {/* Options */}
        <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30">
          <div>
            <span className="text-sm font-medium">Anonymous contribution</span>
            <p className="text-xs text-muted-foreground">
              {isAnonymous ? 'Uploader will show as "Anonymous"' : 'Your username will be linked to this contribution'}
            </p>
          </div>
          <Switch checked={isAnonymous} onCheckedChange={setIsAnonymous} />
        </div>

        {/* Import Button */}
        <Button
          onClick={handleImport}
          disabled={!isValidUrl || !metaId || isLoading}
          className="w-full rounded-xl bg-gradient-to-r from-red-500 to-red-600"
        >
          {importMutation.isPending ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Importing...
            </>
          ) : (
            <>
              <CheckCircle className="mr-2 h-4 w-4" />
              Import YouTube Video
            </>
          )}
        </Button>

        {/* Info */}
        <div className="flex items-start gap-2 p-3 rounded-xl bg-muted/50">
          <Info className="h-4 w-4 text-muted-foreground mt-0.5" />
          <p className="text-sm text-muted-foreground">
            {contentType === 'tv'
              ? 'YouTube live streams can be used for TV channels. Provide the channel title as the title field.'
              : 'YouTube videos are linked to media content via external URL. Make sure to provide the correct IMDb ID so the video appears with the right movie or series.'}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
