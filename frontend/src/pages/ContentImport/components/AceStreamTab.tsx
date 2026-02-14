import { useState, useCallback, useMemo } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { 
  Radio, Loader2, ArrowRight, Info, CheckCircle, AlertCircle, Image, ChevronDown
} from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { contentImportApi, type AceStreamAnalyzeResponse, type ImportResponse } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'

interface AceStreamTabProps {
  onSuccess: (message: string) => void
  onError: (message: string) => void
}

// AceStream content_id and info_hash are both 40-character hex strings
const HEX_40_PATTERN = /^[a-fA-F0-9]{40}$/
const ACESTREAM_URL_PATTERN = /^acestream:\/\/([a-fA-F0-9]{40})$/

function extractAceStreamId(input: string): string | null {
  // Check if it's an acestream:// URL
  const match = input.match(ACESTREAM_URL_PATTERN)
  if (match) return match[1].toLowerCase()
  
  // Check if it's already a valid hex ID
  if (HEX_40_PATTERN.test(input)) return input.toLowerCase()
  
  return null
}

function isValidHex40(value: string | null | undefined): boolean {
  if (!value) return false
  return HEX_40_PATTERN.test(value)
}

// AceStream is primarily used for live streaming, so we always use 'tv' as the content type
const ACESTREAM_CONTENT_TYPE = 'tv' as const

export function AceStreamTab({ 
  onSuccess, 
  onError, 
}: AceStreamTabProps) {
  const { user } = useAuth()
  const [contentIdInput, setContentIdInput] = useState('')
  const [infoHashInput, setInfoHashInput] = useState('')
  const [metaId, setMetaId] = useState('')
  const [title, setTitle] = useState('')
  const [languages, setLanguages] = useState('')
  const [resolution, setResolution] = useState('')
  const [quality, setQuality] = useState('')
  const [codec, setCodec] = useState('')
  const [poster, setPoster] = useState('')
  const [background, setBackground] = useState('')
  const [logo, setLogo] = useState('')
  const [isAnonymous, setIsAnonymous] = useState(user?.contribute_anonymously ?? false)
  const [imagesOpen, setImagesOpen] = useState(false)
  
  const [analysis, setAnalysis] = useState<AceStreamAnalyzeResponse | null>(null)

  // Extract and normalize content_id
  const normalizedContentId = useMemo(() => 
    extractAceStreamId(contentIdInput.trim()) || contentIdInput.trim().toLowerCase(),
    [contentIdInput]
  )
  
  // Normalize info_hash
  const normalizedInfoHash = useMemo(() => 
    infoHashInput.trim().toLowerCase(),
    [infoHashInput]
  )

  // Validation
  const contentIdValid = contentIdInput ? isValidHex40(normalizedContentId) : true
  const infoHashValid = infoHashInput ? isValidHex40(normalizedInfoHash) : true
  const hasAtLeastOneId = !!(contentIdInput || infoHashInput)
  const hasTitle = title.trim().length > 0
  const isInputValid = hasAtLeastOneId && contentIdValid && infoHashValid

  // Analyze mutation
  const analyzeMutation = useMutation({
    mutationFn: () => contentImportApi.analyzeAceStream({ 
      content_id: contentIdInput ? normalizedContentId : undefined,
      info_hash: infoHashInput ? normalizedInfoHash : undefined,
      meta_type: ACESTREAM_CONTENT_TYPE 
    }),
    onSuccess: (result) => {
      if (result.status === 'success') {
        setAnalysis(result)
        if (result.already_exists) {
          onError('This AceStream content already exists in the database')
        }
      } else {
        onError(result.error || 'Failed to analyze AceStream content')
      }
    },
    onError: () => {
      onError('Failed to analyze AceStream content')
    },
  })

  // Import mutation
  const importMutation = useMutation({
    mutationFn: () => contentImportApi.importAceStream({
      content_id: contentIdInput ? normalizedContentId : undefined,
      info_hash: infoHashInput ? normalizedInfoHash : undefined,
      meta_type: ACESTREAM_CONTENT_TYPE,
      title: title.trim(),
      meta_id: metaId || undefined,
      languages: languages || undefined,
      resolution: resolution || undefined,
      quality: quality || undefined,
      codec: codec || undefined,
      poster: poster || undefined,
      background: background || undefined,
      logo: logo || undefined,
      is_anonymous: isAnonymous,
    }),
    onSuccess: (result: ImportResponse) => {
      if (result.status === 'success') {
        onSuccess(result.message || 'AceStream content imported successfully!')
        // Reset form
        setContentIdInput('')
        setInfoHashInput('')
        setMetaId('')
        setTitle('')
        setLanguages('')
        setResolution('')
        setQuality('')
        setCodec('')
        setPoster('')
        setBackground('')
        setLogo('')
        setAnalysis(null)
        setImagesOpen(false)
      } else if (result.status === 'warning') {
        onSuccess(result.message)
      } else {
        onError(result.message || 'Failed to import AceStream content')
      }
    },
    onError: () => {
      onError('Failed to import AceStream content')
    },
  })

  const handleAnalyze = useCallback(() => {
    if (!isInputValid) return
    analyzeMutation.mutate()
  }, [isInputValid, analyzeMutation])

  const handleImport = useCallback(() => {
    if (!isInputValid || !hasTitle) return
    importMutation.mutate()
  }, [isInputValid, hasTitle, importMutation])

  const isLoading = analyzeMutation.isPending || importMutation.isPending

  return (
    <Card className="glass border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Radio className="h-5 w-5 text-green-500" />
          Import AceStream Content
        </CardTitle>
        <CardDescription>
          Import AceStream content using content ID or info hash for MediaFlow proxy playback
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Content ID Input */}
        <div className="space-y-2">
          <Label htmlFor="content-id">Content ID</Label>
          <Input
            id="content-id"
            placeholder="acestream://... or 40-character hex ID"
            value={contentIdInput}
            onChange={(e) => {
              setContentIdInput(e.target.value)
              setAnalysis(null)
            }}
            className={`font-mono text-sm rounded-xl ${contentIdInput && !contentIdValid ? 'border-destructive' : ''}`}
          />
          {contentIdInput && !contentIdValid && (
            <p className="text-sm text-destructive flex items-center gap-1">
              <AlertCircle className="h-3 w-3" />
              Invalid content ID. Must be 40-character hex or acestream:// URL
            </p>
          )}
        </div>

        {/* Info Hash Input */}
        <div className="space-y-2">
          <Label htmlFor="info-hash">Info Hash (Optional)</Label>
          <Input
            id="info-hash"
            placeholder="40-character hex torrent info hash"
            value={infoHashInput}
            onChange={(e) => {
              setInfoHashInput(e.target.value)
              setAnalysis(null)
            }}
            className={`font-mono text-sm rounded-xl ${infoHashInput && !infoHashValid ? 'border-destructive' : ''}`}
          />
          {infoHashInput && !infoHashValid && (
            <p className="text-sm text-destructive flex items-center gap-1">
              <AlertCircle className="h-3 w-3" />
              Invalid info hash. Must be 40-character hexadecimal
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Providing both content_id and info_hash increases compatibility with MediaFlow proxy
          </p>
        </div>

        {/* Analyze Button */}
        <Button 
          onClick={handleAnalyze}
          disabled={!isInputValid || isLoading}
          variant="outline"
          className="w-full rounded-xl"
        >
          {analyzeMutation.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <ArrowRight className="mr-2 h-4 w-4" />
          )}
          Validate & Check Existence
        </Button>

        {/* Analysis Results */}
        {analysis && (
          <div className={`p-4 rounded-xl space-y-2 ${analysis.already_exists ? 'bg-amber-500/10 border border-amber-500/20' : 'bg-green-500/10 border border-green-500/20'}`}>
            <div className="flex flex-wrap gap-2">
              {analysis.content_id_valid && (
                <Badge variant="secondary" className="bg-green-500/20">
                  <CheckCircle className="h-3 w-3 mr-1" />
                  Content ID Valid
                </Badge>
              )}
              {analysis.info_hash_valid && (
                <Badge variant="secondary" className="bg-green-500/20">
                  <CheckCircle className="h-3 w-3 mr-1" />
                  Info Hash Valid
                </Badge>
              )}
              {analysis.already_exists && (
                <Badge variant="outline" className="border-amber-500 text-amber-600">
                  <AlertCircle className="h-3 w-3 mr-1" />
                  Already Exists
                </Badge>
              )}
            </div>
            {analysis.already_exists && (
              <p className="text-sm text-amber-600">
                This content already exists in the database. Use force import if you want to add it anyway.
              </p>
            )}
          </div>
        )}

        {/* MediaFlow Playback Info */}
        <div className="p-4 rounded-xl bg-muted/50 space-y-2">
          <Label className="text-sm font-medium">MediaFlow Proxy URLs</Label>
          <div className="text-xs text-muted-foreground space-y-1 font-mono">
            {contentIdInput && contentIdValid && (
              <p>/proxy/acestream/stream?id={normalizedContentId.slice(0, 8)}...</p>
            )}
            {infoHashInput && infoHashValid && (
              <p>/proxy/acestream/stream?infohash={normalizedInfoHash.slice(0, 8)}...</p>
            )}
            {!contentIdInput && !infoHashInput && (
              <p className="italic">Enter a content ID or info hash to see proxy URLs</p>
            )}
          </div>
        </div>

        {/* Metadata Input */}
        <div className="space-y-4 pt-4 border-t">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="title">Title *</Label>
              <Input
                id="title"
                placeholder="BBC One HD"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className={`rounded-xl ${!hasTitle && title !== '' ? 'border-destructive' : ''}`}
              />
              <p className="text-xs text-muted-foreground">
                The name of the channel or stream. If a channel with this name already exists, the stream will be linked to it.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="meta-id">existing media ID (Optional)</Label>
              <Input
                id="meta-id"
                placeholder="mf:1234"
                value={metaId}
                onChange={(e) => setMetaId(e.target.value)}
                className="rounded-xl"
              />
              <p className="text-xs text-muted-foreground">
                Optional existing media ID to link to (e.g. mf:1234).
              </p>
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

          {/* Images Section (Collapsible) */}
          <Collapsible open={imagesOpen} onOpenChange={setImagesOpen}>
            <CollapsibleTrigger asChild>
              <Button variant="ghost" className="w-full justify-between rounded-xl px-3 py-2 h-auto">
                <span className="flex items-center gap-2 text-sm font-medium">
                  <Image className="h-4 w-4" />
                  Images (Optional)
                  {(poster || background || logo) && (
                    <Badge variant="secondary" className="text-xs">
                      {[poster, background, logo].filter(Boolean).length} set
                    </Badge>
                  )}
                </span>
                <ChevronDown className={`h-4 w-4 transition-transform ${imagesOpen ? 'rotate-180' : ''}`} />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="space-y-3 pt-2">
              <p className="text-xs text-muted-foreground">
                Provide image URLs for the media entry. These are only used when creating a new media entry
                (not when linking to an existing one).
              </p>
              <div className="space-y-2">
                <Label htmlFor="poster">Poster URL</Label>
                <Input
                  id="poster"
                  placeholder="https://example.com/poster.jpg"
                  value={poster}
                  onChange={(e) => setPoster(e.target.value)}
                  className="rounded-xl text-sm"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="background">Background URL</Label>
                <Input
                  id="background"
                  placeholder="https://example.com/background.jpg"
                  value={background}
                  onChange={(e) => setBackground(e.target.value)}
                  className="rounded-xl text-sm"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="logo">Logo URL</Label>
                <Input
                  id="logo"
                  placeholder="https://example.com/logo.png"
                  value={logo}
                  onChange={(e) => setLogo(e.target.value)}
                  className="rounded-xl text-sm"
                />
              </div>
              {/* Image Previews */}
              {(poster || background || logo) && (
                <div className="grid gap-3 md:grid-cols-3 pt-2">
                  {poster && (
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">Poster Preview</Label>
                      <img 
                        src={poster} 
                        alt="Poster preview" 
                        className="rounded-lg w-full h-32 object-cover border border-border/50"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                    </div>
                  )}
                  {background && (
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">Background Preview</Label>
                      <img 
                        src={background} 
                        alt="Background preview" 
                        className="rounded-lg w-full h-32 object-cover border border-border/50"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                    </div>
                  )}
                  {logo && (
                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">Logo Preview</Label>
                      <img 
                        src={logo} 
                        alt="Logo preview" 
                        className="rounded-lg w-full h-32 object-contain border border-border/50 bg-black/20"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                    </div>
                  )}
                </div>
              )}
            </CollapsibleContent>
          </Collapsible>
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
          disabled={!isInputValid || !hasTitle || isLoading}
          className="w-full rounded-xl bg-gradient-to-r from-green-500 to-green-600"
        >
          {importMutation.isPending ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Importing...
            </>
          ) : (
            <>
              <CheckCircle className="mr-2 h-4 w-4" />
              Import AceStream Content
            </>
          )}
        </Button>

        {/* Info */}
        <div className="flex items-start gap-2 p-3 rounded-xl bg-muted/50">
          <Info className="h-4 w-4 text-muted-foreground mt-0.5" />
          <p className="text-sm text-muted-foreground">
            AceStream is commonly used for live TV channels. Provide the content ID (acestream:// URL or 40-char hex) 
            and the channel title. If a channel with the same title already exists, the stream will be added to it. 
            Playback is handled via MediaFlow Proxy with automatic transcoding for browser compatibility.
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
