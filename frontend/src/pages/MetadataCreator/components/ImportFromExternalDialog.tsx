import { useState, useCallback } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Loader2, Download, Search, Film, Tv, Radio, Globe, Lock, AlertCircle, CheckCircle } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { userMetadataApi } from '@/lib/api'
import type { ImportProvider, ImportPreviewResponse } from '@/lib/api'

interface ImportFromExternalDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}

const PROVIDERS: { value: ImportProvider; label: string; placeholder: string }[] = [
  { value: 'imdb', label: 'IMDb', placeholder: 'tt1234567' },
  { value: 'tmdb', label: 'TMDB', placeholder: '12345' },
  { value: 'tvdb', label: 'TVDB', placeholder: '12345' },
  { value: 'mal', label: 'MyAnimeList', placeholder: '12345' },
  { value: 'kitsu', label: 'Kitsu', placeholder: '12345' },
]

const MEDIA_TYPES = [
  { value: 'movie' as const, label: 'Movie', icon: Film },
  { value: 'series' as const, label: 'Series', icon: Tv },
  { value: 'tv' as const, label: 'TV', icon: Radio },
]

export function ImportFromExternalDialog({ open, onOpenChange, onSuccess }: ImportFromExternalDialogProps) {
  const [provider, setProvider] = useState<ImportProvider>('imdb')
  const [externalId, setExternalId] = useState('')
  const [mediaType, setMediaType] = useState<'movie' | 'series' | 'tv'>('movie')
  const [isPublic, setIsPublic] = useState(true)
  const [preview, setPreview] = useState<ImportPreviewResponse | null>(null)
  const [isPreviewing, setIsPreviewing] = useState(false)
  const [isImporting, setIsImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { toast } = useToast()

  const selectedProvider = PROVIDERS.find((p) => p.value === provider)

  const handlePreview = useCallback(async () => {
    if (!externalId.trim()) {
      setError('Please enter an external ID')
      return
    }

    setIsPreviewing(true)
    setError(null)
    setPreview(null)

    try {
      const result = await userMetadataApi.previewImport({
        provider,
        external_id: externalId.trim(),
        media_type: mediaType,
      })
      setPreview(result)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch metadata'
      setError(message)
    } finally {
      setIsPreviewing(false)
    }
  }, [provider, externalId, mediaType])

  const handleImport = useCallback(async () => {
    if (!preview) return

    setIsImporting(true)
    setError(null)

    try {
      await userMetadataApi.importFromExternal({
        provider,
        external_id: externalId.trim(),
        media_type: mediaType,
        is_public: isPublic,
      })

      toast({
        title: 'Success',
        description: `"${preview.title}" has been imported`,
      })

      // Reset state
      setPreview(null)
      setExternalId('')
      onOpenChange(false)
      onSuccess()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to import metadata'
      setError(message)
      toast({
        title: 'Error',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setIsImporting(false)
    }
  }, [preview, provider, externalId, mediaType, isPublic, toast, onOpenChange, onSuccess])

  const handleClose = useCallback(() => {
    setPreview(null)
    setExternalId('')
    setError(null)
    onOpenChange(false)
  }, [onOpenChange])

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Download className="h-5 w-5 text-primary" />
            Import from External ID
          </DialogTitle>
          <DialogDescription>
            Import metadata from IMDb, TMDB, TVDB, MyAnimeList, or Kitsu by providing an external ID.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Provider Selection */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Provider</Label>
              <Select
                value={provider}
                onValueChange={(v) => {
                  setProvider(v as ImportProvider)
                  setPreview(null)
                  setError(null)
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROVIDERS.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Media Type</Label>
              <Select
                value={mediaType}
                onValueChange={(v) => {
                  setMediaType(v as 'movie' | 'series' | 'tv')
                  setPreview(null)
                  setError(null)
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MEDIA_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      <span className="flex items-center gap-2">
                        <t.icon className="h-4 w-4" />
                        {t.label}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* External ID Input */}
          <div className="space-y-2">
            <Label htmlFor="external-id">External ID</Label>
            <div className="flex gap-2">
              <Input
                id="external-id"
                value={externalId}
                onChange={(e) => {
                  setExternalId(e.target.value)
                  setPreview(null)
                  setError(null)
                }}
                placeholder={selectedProvider?.placeholder}
                className="font-mono"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    handlePreview()
                  }
                }}
              />
              <Button type="button" onClick={handlePreview} disabled={isPreviewing || !externalId.trim()}>
                {isPreviewing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              </Button>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 text-sm text-red-500 bg-red-500/10 p-3 rounded-lg">
              <AlertCircle className="h-4 w-4 flex-shrink-0" />
              {error}
            </div>
          )}

          {/* Preview */}
          {preview && (
            <div className="border rounded-lg p-4 space-y-4 bg-muted/30">
              <div className="flex items-center gap-2 text-sm text-green-500">
                <CheckCircle className="h-4 w-4" />
                Metadata found!
              </div>

              <div className="flex gap-4">
                {/* Poster */}
                <div className="w-20 h-28 rounded bg-muted/50 flex-shrink-0 overflow-hidden">
                  {preview.poster ? (
                    <img src={preview.poster} alt={preview.title} className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center">
                      {mediaType === 'movie' ? (
                        <Film className="h-6 w-6 text-muted-foreground" />
                      ) : mediaType === 'tv' ? (
                        <Radio className="h-6 w-6 text-muted-foreground" />
                      ) : (
                        <Tv className="h-6 w-6 text-muted-foreground" />
                      )}
                    </div>
                  )}
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0 space-y-2">
                  <div>
                    <h4 className="font-semibold truncate">{preview.title}</h4>
                    {preview.year && <span className="text-sm text-muted-foreground">({preview.year})</span>}
                  </div>

                  {preview.genres.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {preview.genres.slice(0, 3).map((genre) => (
                        <Badge key={genre} variant="secondary" className="text-xs">
                          {genre}
                        </Badge>
                      ))}
                      {preview.genres.length > 3 && (
                        <Badge variant="outline" className="text-xs">
                          +{preview.genres.length - 3}
                        </Badge>
                      )}
                    </div>
                  )}

                  {preview.description && (
                    <p className="text-xs text-muted-foreground line-clamp-2">{preview.description}</p>
                  )}
                </div>
              </div>

              {/* External IDs */}
              <div className="flex flex-wrap gap-2 text-xs">
                {preview.imdb_id && <Badge variant="outline">IMDb: {preview.imdb_id}</Badge>}
                {preview.tmdb_id && <Badge variant="outline">TMDB: {preview.tmdb_id}</Badge>}
                {preview.tvdb_id && <Badge variant="outline">TVDB: {preview.tvdb_id}</Badge>}
                {preview.mal_id && <Badge variant="outline">MAL: {preview.mal_id}</Badge>}
                {preview.kitsu_id && <Badge variant="outline">Kitsu: {preview.kitsu_id}</Badge>}
              </div>

              {/* Visibility */}
              <div className="flex items-center justify-between pt-2 border-t">
                <div className="flex items-center gap-2">
                  {isPublic ? <Globe className="h-4 w-4 text-green-500" /> : <Lock className="h-4 w-4 text-primary" />}
                  <span className="text-sm">{isPublic ? 'Public' : 'Private'}</span>
                </div>
                <Switch checked={isPublic} onCheckedChange={setIsPublic} />
              </div>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={handleClose}>
            Cancel
          </Button>
          <Button
            onClick={handleImport}
            disabled={!preview || isImporting}
            className="bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
          >
            {isImporting ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Importing...
              </>
            ) : (
              <>
                <Download className="h-4 w-4 mr-2" />
                Import
              </>
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
