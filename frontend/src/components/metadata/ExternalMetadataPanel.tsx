import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Download,
  Search,
  ArrowRightLeft,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  Star,
  Calendar,
  Film,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMutation } from '@tanstack/react-query'
import { adminApi, type ExternalMetadataPreview } from '@/lib/api'

interface ExternalMetadataPanelProps {
  mediaId: number
  currentExternalId: string
  title: string
  year?: number
  mediaType: 'movie' | 'series' | 'tv'
  onMetadataApplied?: () => void
  onIdMigrated?: () => void
}

export function ExternalMetadataPanel({
  mediaId,
  currentExternalId,
  title,
  year,
  mediaType,
  onMetadataApplied,
  onIdMigrated,
}: ExternalMetadataPanelProps) {
  const [provider, setProvider] = useState<'imdb' | 'tmdb'>('imdb')
  const [searchQuery, setSearchQuery] = useState(title)
  const [searchYear, setSearchYear] = useState<string>(year?.toString() || '')
  const [searchResults, setSearchResults] = useState<ExternalMetadataPreview[]>([])
  const [selectedPreview, setSelectedPreview] = useState<ExternalMetadataPreview | null>(null)
  const [newExternalId, setNewExternalId] = useState('')
  const [migrateDialogOpen, setMigrateDialogOpen] = useState(false)

  const isInternalId = currentExternalId.startsWith('mf:')
  const isTmdbId = currentExternalId.startsWith('tmdb:')
  const canMigrate = isInternalId && mediaType !== 'tv'

  // Search mutation
  const searchMutation = useMutation({
    mutationFn: () =>
      adminApi.searchExternalMetadata({
        provider,
        title: searchQuery,
        year: searchYear ? parseInt(searchYear) : undefined,
        media_type: mediaType === 'tv' ? undefined : mediaType,
      }),
    onSuccess: (data) => {
      setSearchResults(data.results)
    },
  })

  // Fetch preview mutation
  const fetchPreviewMutation = useMutation({
    mutationFn: (externalId: string) =>
      adminApi.fetchExternalMetadata(mediaId, {
        provider,
        external_id: externalId,
      }),
    onSuccess: (data) => {
      setSelectedPreview(data)
    },
  })

  // Apply metadata mutation
  const applyMutation = useMutation({
    mutationFn: (externalId: string) =>
      adminApi.applyExternalMetadata(mediaId, {
        provider,
        external_id: externalId,
      }),
    onSuccess: () => {
      onMetadataApplied?.()
    },
  })

  // Migrate ID mutation
  const migrateMutation = useMutation({
    mutationFn: () =>
      adminApi.migrateMetadataId(mediaId, {
        new_external_id: newExternalId,
      }),
    onSuccess: () => {
      setMigrateDialogOpen(false)
      onIdMigrated?.()
    },
  })

  const handleSearch = () => {
    searchMutation.mutate()
  }

  const handleSelectResult = (result: ExternalMetadataPreview) => {
    setSelectedPreview(result)
  }

  const handleApply = () => {
    if (selectedPreview) {
      const externalId =
        provider === 'imdb'
          ? selectedPreview.imdb_id || selectedPreview.external_id
          : selectedPreview.tmdb_id || selectedPreview.external_id
      applyMutation.mutate(externalId)
    }
  }

  const handleMigrateId = () => {
    if (selectedPreview) {
      // Auto-fill from preview
      const suggestedId = provider === 'imdb' ? selectedPreview.imdb_id : `tmdb:${selectedPreview.tmdb_id}`
      setNewExternalId(suggestedId || '')
      setMigrateDialogOpen(true)
    }
  }

  return (
    <div className="space-y-4">
      {/* Current ID Status */}
      <div className="p-3 rounded-xl bg-muted/50">
        <div className="flex items-center justify-between">
          <div>
            <Label className="text-xs text-muted-foreground">Current External ID</Label>
            <code className="block text-sm font-mono mt-1">{currentExternalId}</code>
          </div>
          {isInternalId && (
            <Badge variant="outline" className="bg-primary/10 text-primary border-primary/30">
              {isTmdbId ? 'TMDB Internal' : 'MediaFusion Internal'}
            </Badge>
          )}
        </div>
        {canMigrate && (
          <p className="text-xs text-muted-foreground mt-2">
            This item uses an internal ID. You can migrate it to a proper IMDb or TMDB ID below.
          </p>
        )}
      </div>

      <Separator />

      {/* Provider Selection */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Label className="text-sm font-medium">Fetch from External Provider</Label>
          <Select value={provider} onValueChange={(v) => setProvider(v as 'imdb' | 'tmdb')}>
            <SelectTrigger className="w-[120px] rounded-xl">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="imdb">IMDb</SelectItem>
              <SelectItem value="tmdb">TMDB</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Search Form */}
        <div className="space-y-2">
          <div className="flex gap-2">
            <Input
              placeholder="Search title..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="flex-1 rounded-xl"
            />
            <Input
              placeholder="Year"
              value={searchYear}
              onChange={(e) => setSearchYear(e.target.value)}
              className="w-20 rounded-xl"
              type="number"
            />
            <Button
              onClick={handleSearch}
              disabled={searchMutation.isPending || !searchQuery.trim()}
              className="rounded-xl"
            >
              {searchMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        {/* Search Results */}
        {searchResults.length > 0 && (
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">Search Results</Label>
            <ScrollArea className="h-48 rounded-xl border">
              <div className="p-2 space-y-2">
                {searchResults.map((result, idx) => (
                  <div
                    key={idx}
                    className={cn(
                      'flex gap-3 p-2 rounded-lg cursor-pointer transition-colors',
                      selectedPreview?.external_id === result.external_id
                        ? 'bg-primary/20 border border-primary/50'
                        : 'hover:bg-muted',
                    )}
                    onClick={() => handleSelectResult(result)}
                  >
                    {result.poster ? (
                      <img src={result.poster} alt={result.title} className="w-12 h-18 object-cover rounded" />
                    ) : (
                      <div className="w-12 h-18 bg-muted rounded flex items-center justify-center">
                        <Film className="h-6 w-6 text-muted-foreground" />
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sm truncate">{result.title}</p>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1">
                        {result.year && (
                          <span className="flex items-center gap-1">
                            <Calendar className="h-3 w-3" />
                            {result.year}
                          </span>
                        )}
                        {result.imdb_rating && (
                          <span className="flex items-center gap-1">
                            <Star className="h-3 w-3 fill-primary text-primary" />
                            {result.imdb_rating.toFixed(1)}
                          </span>
                        )}
                      </div>
                      <code className="text-[10px] text-muted-foreground">{result.imdb_id || result.tmdb_id}</code>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </div>
        )}

        {/* Selected Preview */}
        {selectedPreview && (
          <div className="p-4 rounded-xl border bg-card space-y-3">
            <div className="flex gap-4">
              {selectedPreview.poster && (
                <img
                  src={selectedPreview.poster}
                  alt={selectedPreview.title}
                  className="w-20 h-30 object-cover rounded-lg"
                />
              )}
              <div className="flex-1 min-w-0">
                <h4 className="font-semibold">{selectedPreview.title}</h4>
                <p className="text-sm text-muted-foreground">{selectedPreview.year}</p>
                {selectedPreview.imdb_rating && (
                  <div className="flex items-center gap-1 text-sm mt-1">
                    <Star className="h-4 w-4 fill-primary text-primary" />
                    <span>{selectedPreview.imdb_rating.toFixed(1)}</span>
                  </div>
                )}
                {selectedPreview.genres.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {selectedPreview.genres.slice(0, 4).map((genre) => (
                      <Badge key={genre} variant="secondary" className="text-xs">
                        {genre}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {selectedPreview.description && (
              <p className="text-xs text-muted-foreground line-clamp-2">{selectedPreview.description}</p>
            )}

            <div className="flex gap-2">
              <Button
                onClick={handleApply}
                disabled={applyMutation.isPending}
                className="flex-1 rounded-xl bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
              >
                {applyMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Applying...
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4 mr-2" />
                    Apply Metadata
                  </>
                )}
              </Button>

              {canMigrate && (
                <Dialog open={migrateDialogOpen} onOpenChange={setMigrateDialogOpen}>
                  <DialogTrigger asChild>
                    <Button variant="outline" onClick={handleMigrateId} className="rounded-xl">
                      <ArrowRightLeft className="h-4 w-4 mr-2" />
                      Migrate ID
                    </Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Migrate External ID</DialogTitle>
                      <DialogDescription>
                        Replace the internal MediaFusion ID with a proper external ID. This will update all references
                        to this media item.
                      </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-4">
                      <div className="space-y-2">
                        <Label>Current ID</Label>
                        <code className="block p-2 bg-muted rounded text-sm">{currentExternalId}</code>
                      </div>

                      <div className="space-y-2">
                        <Label>New External ID</Label>
                        <Input
                          value={newExternalId}
                          onChange={(e) => setNewExternalId(e.target.value)}
                          placeholder="tt1234567 or tmdb:12345"
                          className="rounded-xl"
                        />
                        <p className="text-xs text-muted-foreground">
                          Use <code>tt1234567</code> for IMDb or <code>tmdb:12345</code> for TMDB
                        </p>
                      </div>
                    </div>

                    {migrateMutation.isError && (
                      <Alert variant="destructive">
                        <AlertCircle className="h-4 w-4" />
                        <AlertDescription>
                          {(migrateMutation.error as Error)?.message || 'Failed to migrate ID'}
                        </AlertDescription>
                      </Alert>
                    )}

                    <DialogFooter>
                      <Button variant="outline" onClick={() => setMigrateDialogOpen(false)} className="rounded-xl">
                        Cancel
                      </Button>
                      <Button
                        onClick={() => migrateMutation.mutate()}
                        disabled={migrateMutation.isPending || !newExternalId.trim()}
                        className="rounded-xl bg-gradient-to-r from-primary to-primary/80"
                      >
                        {migrateMutation.isPending ? (
                          <>
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                            Migrating...
                          </>
                        ) : (
                          'Migrate ID'
                        )}
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              )}
            </div>

            {applyMutation.isSuccess && (
              <Alert className="bg-emerald-500/10 border-emerald-500/30">
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                <AlertDescription className="text-emerald-500">Metadata applied successfully!</AlertDescription>
              </Alert>
            )}

            {applyMutation.isError && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  {(applyMutation.error as Error)?.message || 'Failed to apply metadata'}
                </AlertDescription>
              </Alert>
            )}
          </div>
        )}
      </div>

      {/* Quick Fetch by ID */}
      <Separator />

      <div className="space-y-3">
        <Label className="text-sm font-medium">Quick Fetch by ID</Label>
        <p className="text-xs text-muted-foreground">If you already know the IMDb or TMDB ID, enter it directly.</p>

        <div className="flex gap-2">
          <Input
            placeholder={provider === 'imdb' ? 'tt1234567' : '12345'}
            className="flex-1 rounded-xl"
            id="quick-fetch-id"
          />
          <Button
            onClick={() => {
              const input = document.getElementById('quick-fetch-id') as HTMLInputElement
              if (input?.value) {
                fetchPreviewMutation.mutate(input.value)
              }
            }}
            disabled={fetchPreviewMutation.isPending}
            variant="outline"
            className="rounded-xl"
          >
            {fetchPreviewMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                <ExternalLink className="h-4 w-4 mr-2" />
                Fetch
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
