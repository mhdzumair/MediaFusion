import { useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { 
  Globe, Loader2, ArrowRight, Info, CheckCircle, Plus, Trash2, 
  ChevronDown, ChevronUp, Shield, Settings2 
} from 'lucide-react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { contentImportApi, type HTTPAnalyzeResponse, type ImportResponse } from '@/lib/api'
import type { ContentType } from '@/lib/constants'
import { useAuth } from '@/contexts/AuthContext'

interface HTTPTabProps {
  onSuccess: (message: string) => void
  onError: (message: string) => void
  contentType?: ContentType
}

interface HeaderEntry {
  key: string
  value: string
}

function isValidUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

function HeadersEditor({ 
  headers, 
  onChange, 
  label 
}: { 
  headers: HeaderEntry[]
  onChange: (headers: HeaderEntry[]) => void
  label: string 
}) {
  const addHeader = () => {
    onChange([...headers, { key: '', value: '' }])
  }

  const removeHeader = (index: number) => {
    onChange(headers.filter((_, i) => i !== index))
  }

  const updateHeader = (index: number, field: 'key' | 'value', value: string) => {
    const newHeaders = [...headers]
    newHeaders[index][field] = value
    onChange(newHeaders)
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-sm">{label}</Label>
        <Button variant="ghost" size="sm" onClick={addHeader} className="h-7 text-xs">
          <Plus className="h-3 w-3 mr-1" />
          Add Header
        </Button>
      </div>
      {headers.length === 0 ? (
        <p className="text-xs text-muted-foreground">No headers configured</p>
      ) : (
        <div className="space-y-2">
          {headers.map((header, index) => (
            <div key={index} className="flex gap-2 items-center">
              <Input
                placeholder="Header name"
                value={header.key}
                onChange={(e) => updateHeader(index, 'key', e.target.value)}
                className="flex-1 h-8 text-sm rounded-lg"
              />
              <Input
                placeholder="Value"
                value={header.value}
                onChange={(e) => updateHeader(index, 'value', e.target.value)}
                className="flex-1 h-8 text-sm rounded-lg"
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => removeHeader(index)}
                className="h-8 w-8 p-0 text-destructive"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function HTTPTab({ 
  onSuccess, 
  onError, 
  contentType = 'movie',
}: HTTPTabProps) {
  const { user } = useAuth()
  const [url, setUrl] = useState('')
  const [metaId, setMetaId] = useState('')
  const [title, setTitle] = useState('')
  const [languages, setLanguages] = useState('')
  const [resolution, setResolution] = useState('')
  const [quality, setQuality] = useState('')
  const [codec, setCodec] = useState('')
  const [isAnonymous, setIsAnonymous] = useState(user?.contribute_anonymously ?? false)
  
  // MediaFlow extractor
  const [useExtractor, setUseExtractor] = useState(false)
  const [extractorName, setExtractorName] = useState('')
  
  // Headers
  const [requestHeaders, setRequestHeaders] = useState<HeaderEntry[]>([])
  const [responseHeaders, setResponseHeaders] = useState<HeaderEntry[]>([])
  const [headersOpen, setHeadersOpen] = useState(false)
  
  // DRM
  const [drmOpen, setDrmOpen] = useState(false)
  const [drmKeyId, setDrmKeyId] = useState('')
  const [drmKey, setDrmKey] = useState('')
  
  const [analysis, setAnalysis] = useState<HTTPAnalyzeResponse | null>(null)

  // Fetch available extractors
  const { data: extractorsData } = useQuery({
    queryKey: ['mediaflow-extractors'],
    queryFn: () => contentImportApi.getMediaFlowExtractors(),
    staleTime: 1000 * 60 * 60, // 1 hour
  })

  const extractors = extractorsData?.extractors || []

  // Check URL validity
  const urlValid = isValidUrl(url)

  // Analyze mutation
  const analyzeMutation = useMutation({
    mutationFn: () => contentImportApi.analyzeHTTP({ 
      url, 
      meta_type: contentType 
    }),
    onSuccess: (result) => {
      if (result.status === 'success') {
        setAnalysis(result)
        // Auto-detect extractor
        if (result.detected_extractor && !extractorName) {
          setExtractorName(result.detected_extractor)
          setUseExtractor(true)
        }
      } else {
        onError(result.error || 'Failed to analyze URL')
      }
    },
    onError: () => {
      onError('Failed to analyze URL')
    },
  })

  // Import mutation
  const importMutation = useMutation({
    mutationFn: () => {
      // Convert headers to Record<string, string>
      const reqHeaders = requestHeaders.reduce((acc, h) => {
        if (h.key && h.value) acc[h.key] = h.value
        return acc
      }, {} as Record<string, string>)
      
      const resHeaders = responseHeaders.reduce((acc, h) => {
        if (h.key && h.value) acc[h.key] = h.value
        return acc
      }, {} as Record<string, string>)

      return contentImportApi.importHTTP({
        url,
        meta_type: contentType,
        meta_id: metaId || undefined,
        title: title || undefined,
        extractor_name: useExtractor ? extractorName : undefined,
        request_headers: Object.keys(reqHeaders).length > 0 ? reqHeaders : undefined,
        response_headers: Object.keys(resHeaders).length > 0 ? resHeaders : undefined,
        drm_key_id: drmKeyId || undefined,
        drm_key: drmKey || undefined,
        resolution: resolution || undefined,
        quality: quality || undefined,
        codec: codec || undefined,
        languages: languages || undefined,
        is_anonymous: isAnonymous,
      })
    },
    onSuccess: (result: ImportResponse) => {
      if (result.status === 'success') {
        onSuccess(result.message || 'HTTP stream imported successfully!')
        // Reset form
        setUrl('')
        setMetaId('')
        setTitle('')
        setLanguages('')
        setResolution('')
        setQuality('')
        setCodec('')
        setRequestHeaders([])
        setResponseHeaders([])
        setDrmKeyId('')
        setDrmKey('')
        setAnalysis(null)
        setExtractorName('')
        setUseExtractor(false)
      } else if (result.status === 'warning') {
        onSuccess(result.message)
      } else {
        onError(result.message || 'Failed to import HTTP stream')
      }
    },
    onError: () => {
      onError('Failed to import HTTP stream')
    },
  })

  const handleAnalyze = useCallback(() => {
    if (!urlValid) return
    analyzeMutation.mutate()
  }, [urlValid, analyzeMutation])

  const handleImport = useCallback(() => {
    if (!urlValid || !metaId) return
    importMutation.mutate()
  }, [urlValid, metaId, importMutation])

  const isLoading = analyzeMutation.isPending || importMutation.isPending

  return (
    <Card className="glass border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe className="h-5 w-5 text-primary" />
          Import HTTP Stream
        </CardTitle>
        <CardDescription>
          Import direct HTTP URLs, HLS/DASH streams, or MediaFlow extractor URLs
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* URL Input */}
        <div className="space-y-2">
          <Label htmlFor="http-url">Stream URL</Label>
          <div className="flex gap-2">
            <Input
              id="http-url"
              placeholder="https://example.com/video.mp4 or .m3u8/.mpd URL"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value)
                setAnalysis(null)
              }}
              className="font-mono text-sm rounded-xl"
            />
            <Button 
              onClick={handleAnalyze}
              disabled={!urlValid || isLoading}
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
          {url && !urlValid && (
            <p className="text-sm text-destructive">Invalid URL. Must be http:// or https://</p>
          )}
        </div>

        {/* Analysis Results */}
        {analysis && analysis.is_valid && (
          <div className="p-4 rounded-xl bg-muted/50 space-y-2">
            <div className="flex flex-wrap gap-2">
              {analysis.detected_format && (
                <Badge variant="secondary">Format: {analysis.detected_format.toUpperCase()}</Badge>
              )}
              {analysis.detected_extractor && (
                <Badge variant="outline">Extractor: {analysis.detected_extractor}</Badge>
              )}
            </div>
          </div>
        )}

        {/* MediaFlow Extractor */}
        <div className="space-y-3 p-4 rounded-xl bg-muted/30">
          <div className="flex items-center justify-between">
            <div>
              <Label className="text-sm font-medium">MediaFlow Extractor</Label>
              <p className="text-xs text-muted-foreground">
                Enable if this URL requires a MediaFlow extractor to play
              </p>
            </div>
            <Switch checked={useExtractor} onCheckedChange={setUseExtractor} />
          </div>
          
          {useExtractor && (
            <Select value={extractorName} onValueChange={setExtractorName}>
              <SelectTrigger className="rounded-lg">
                <SelectValue placeholder="Select extractor..." />
              </SelectTrigger>
              <SelectContent>
                {extractors.map((ext) => (
                  <SelectItem key={ext} value={ext}>
                    {ext.charAt(0).toUpperCase() + ext.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        {/* Headers Section */}
        <Collapsible open={headersOpen} onOpenChange={setHeadersOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="outline" className="w-full justify-between rounded-xl">
              <span className="flex items-center gap-2">
                <Settings2 className="h-4 w-4" />
                Request/Response Headers
              </span>
              {headersOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-4 pt-4">
            <HeadersEditor
              headers={requestHeaders}
              onChange={setRequestHeaders}
              label="Request Headers (sent when fetching stream)"
            />
            <HeadersEditor
              headers={responseHeaders}
              onChange={setResponseHeaders}
              label="Response Headers (for proxying)"
            />
          </CollapsibleContent>
        </Collapsible>

        {/* DRM Section */}
        <Collapsible open={drmOpen} onOpenChange={setDrmOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="outline" className="w-full justify-between rounded-xl">
              <span className="flex items-center gap-2">
                <Shield className="h-4 w-4" />
                DRM Settings (for MPD streams)
              </span>
              {drmOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-4 pt-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="drm-key-id">DRM Key ID</Label>
                <Input
                  id="drm-key-id"
                  placeholder="Key ID (hex)"
                  value={drmKeyId}
                  onChange={(e) => setDrmKeyId(e.target.value)}
                  className="font-mono text-sm rounded-lg"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="drm-key">DRM Key</Label>
                <Input
                  id="drm-key"
                  placeholder="Decryption key (hex)"
                  value={drmKey}
                  onChange={(e) => setDrmKey(e.target.value)}
                  className="font-mono text-sm rounded-lg"
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Required for Widevine/PlayReady protected MPD streams
            </p>
          </CollapsibleContent>
        </Collapsible>

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
              {contentType === 'tv' && (
                <p className="text-xs text-muted-foreground">
                  A unique identifier for the TV channel (e.g., mf_tv_bbc_one).
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="title">Title (Optional)</Label>
              <Input
                id="title"
                placeholder="Stream title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="rounded-xl"
              />
            </div>
          </div>
          
          {/* Quality Info */}
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-2">
              <Label htmlFor="resolution">Resolution</Label>
              <Select value={resolution} onValueChange={setResolution}>
                <SelectTrigger className="rounded-lg">
                  <SelectValue placeholder="Select..." />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="4k">4K</SelectItem>
                  <SelectItem value="1080p">1080p</SelectItem>
                  <SelectItem value="720p">720p</SelectItem>
                  <SelectItem value="480p">480p</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="quality">Quality</Label>
              <Select value={quality} onValueChange={setQuality}>
                <SelectTrigger className="rounded-lg">
                  <SelectValue placeholder="Select..." />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="web-dl">WEB-DL</SelectItem>
                  <SelectItem value="webrip">WEBRip</SelectItem>
                  <SelectItem value="bluray">BluRay</SelectItem>
                  <SelectItem value="hdtv">HDTV</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="codec">Codec</Label>
              <Select value={codec} onValueChange={setCodec}>
                <SelectTrigger className="rounded-lg">
                  <SelectValue placeholder="Select..." />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="x264">x264</SelectItem>
                  <SelectItem value="x265">x265/HEVC</SelectItem>
                  <SelectItem value="av1">AV1</SelectItem>
                </SelectContent>
              </Select>
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
          </div>
        </div>

        {/* Options */}
        <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30">
          <div>
            <span className="text-sm font-medium">Anonymous contribution</span>
            <p className="text-xs text-muted-foreground">
              {isAnonymous 
                ? 'Uploader will show as "Anonymous"' 
                : 'Your username will be linked to this contribution'}
            </p>
          </div>
          <Switch
            checked={isAnonymous}
            onCheckedChange={setIsAnonymous}
          />
        </div>

        {/* Import Button */}
        <Button 
          onClick={handleImport}
          disabled={!urlValid || !metaId || isLoading}
          className="w-full rounded-xl bg-gradient-to-r from-primary to-primary/80"
        >
          {importMutation.isPending ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Importing...
            </>
          ) : (
            <>
              <CheckCircle className="mr-2 h-4 w-4" />
              Import HTTP Stream
            </>
          )}
        </Button>

        {/* Info */}
        <div className="flex items-start gap-2 p-3 rounded-xl bg-muted/50">
          <Info className="h-4 w-4 text-muted-foreground mt-0.5" />
          <p className="text-sm text-muted-foreground">
            {contentType === 'tv'
              ? 'Perfect for live TV streams. Supports HLS (.m3u8), DASH (.mpd), and direct stream URLs with optional headers for authentication.'
              : 'Supports direct video URLs (.mp4, .mkv), HLS streams (.m3u8), DASH streams (.mpd), and MediaFlow extractor URLs from services like Doodstream, FileMoon, Voe, etc.'}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
