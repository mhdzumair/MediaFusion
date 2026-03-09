import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  RefreshCw,
  ArrowRightLeft,
  Loader2,
  AlertCircle,
  Search,
  Film,
  Calendar,
  ChevronDown,
  Database,
  Link2,
  Check,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  metadataApi,
  type ExternalSearchResult,
  getCanonicalExternalId,
  type ExternalIds,
  type MetadataProvider,
  type ExternalProvider,
} from '@/lib/api'
import { useToast } from '@/hooks/use-toast'
import { catalogKeys } from '@/hooks/useCatalog'

// Available metadata providers with their details
const PROVIDERS: {
  id: ExternalProvider
  name: string
  icon: string
  description: string
  idFormat: string
  idPlaceholder: string
  color: string
}[] = [
  {
    id: 'imdb',
    name: 'IMDb',
    icon: '🎬',
    description: 'Internet Movie Database',
    idFormat: 'tt1234567',
    idPlaceholder: 'tt1234567',
    color: 'yellow',
  },
  {
    id: 'tmdb',
    name: 'TMDB',
    icon: '🎞️',
    description: 'The Movie Database',
    idFormat: '12345',
    idPlaceholder: '12345',
    color: 'blue',
  },
  {
    id: 'tvdb',
    name: 'TVDB',
    icon: '📺',
    description: 'TheTVDB (best for TV series)',
    idFormat: '12345',
    idPlaceholder: '12345',
    color: 'green',
  },
  {
    id: 'mal',
    name: 'MAL',
    icon: '🎌',
    description: 'MyAnimeList (anime)',
    idFormat: '12345',
    idPlaceholder: '12345',
    color: 'blue',
  },
  {
    id: 'kitsu',
    name: 'Kitsu',
    icon: '🦊',
    description: 'Kitsu (anime)',
    idFormat: '12345',
    idPlaceholder: '12345',
    color: 'orange',
  },
]

// Extract available IDs from a search result
function getAvailableIds(result: ExternalSearchResult): { provider: ExternalProvider; id: string }[] {
  const ids: { provider: ExternalProvider; id: string }[] = []
  if (result.imdb_id) ids.push({ provider: 'imdb', id: result.imdb_id })
  if (result.tmdb_id) ids.push({ provider: 'tmdb', id: String(result.tmdb_id) })
  if (result.tvdb_id) ids.push({ provider: 'tvdb', id: String(result.tvdb_id) })
  if (result.mal_id) ids.push({ provider: 'mal', id: String(result.mal_id) })
  if (result.kitsu_id) ids.push({ provider: 'kitsu', id: String(result.kitsu_id) })
  return ids
}

interface RefreshMetadataButtonProps {
  mediaId: number
  externalIds?: ExternalIds
  mediaType: 'movie' | 'series'
  title?: string
  year?: number
  className?: string
}

/**
 * RefreshMetadataButton - Provides refresh and migrate functionality for content metadata
 *
 * - Refresh: Updates metadata from external sources (IMDB/TMDB)
 * - Migrate: Converts internal mf... IDs to proper IMDB IDs
 */
export function RefreshMetadataButton({
  mediaId,
  externalIds,
  mediaType,
  title = '',
  year,
  className,
}: RefreshMetadataButtonProps) {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [linkDialogOpen, setLinkDialogOpen] = useState(false)
  const [newExternalId, setNewExternalId] = useState('')
  const [selectedProvider, setSelectedProvider] = useState<ExternalProvider>('imdb')
  const [searchQuery, setSearchQuery] = useState(title)
  const [searchYear, setSearchYear] = useState(year ? String(year) : '')
  const [searchResults, setSearchResults] = useState<ExternalSearchResult[]>([])
  const [selectedResult, setSelectedResult] = useState<ExternalSearchResult | null>(null)
  const [failedPosterKeys, setFailedPosterKeys] = useState<Record<string, boolean>>({})
  const [selectedProviders, setSelectedProviders] = useState<MetadataProvider[]>([])
  const [providerDropdownOpen, setProviderDropdownOpen] = useState(false)
  const [fetchMetadataOnLink, setFetchMetadataOnLink] = useState(true)
  // For multi-link: which IDs from selected result to link
  const [idsToLink, setIdsToLink] = useState<{ provider: ExternalProvider; id: string }[]>([])
  const [linkMode, setLinkMode] = useState<'search' | 'manual'>('search')
  const [includeAnimeProviders, setIncludeAnimeProviders] = useState(true)
  const [animeSourceOrder, setAnimeSourceOrder] = useState<'kitsu_first' | 'anilist_first'>('kitsu_first')

  // Get canonical external ID for display
  const canonicalExternalId = externalIds ? getCanonicalExternalId(externalIds, mediaId) : `mf:${mediaId}`
  const isInternalId = canonicalExternalId.startsWith('mf:')

  // Check if we have any external ID linked (for enabling refresh)
  const hasAnyExternalId =
    externalIds && (externalIds.imdb || externalIds.tmdb || externalIds.tvdb || externalIds.mal || externalIds.kitsu)

  // Refresh mutation
  const refreshMutation = useMutation({
    mutationFn: () =>
      metadataApi.refreshMetadata(mediaId, mediaType, selectedProviders.length > 0 ? selectedProviders : undefined),
    onSuccess: (data) => {
      const providersText = data.refreshed_providers?.join(', ') || 'external sources'
      toast({
        title: 'Metadata refreshed',
        description: `Updated from: ${providersText}`,
      })
      // Invalidate queries to refresh the page data
      queryClient.invalidateQueries({ queryKey: catalogKeys.item(mediaType, mediaId.toString()) })
      setSelectedProviders([])
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Refresh failed',
        description: error.message,
      })
    },
  })

  // Search mutation
  const searchMutation = useMutation({
    mutationFn: () => {
      const trimmedSearchYear = searchYear.trim()
      const parsedSearchYear = trimmedSearchYear ? Number(trimmedSearchYear) : undefined
      const validSearchYear = Number.isFinite(parsedSearchYear) ? parsedSearchYear : undefined
      return metadataApi.searchExternal(searchQuery, mediaType, validSearchYear, {
        includeAnime: includeAnimeProviders,
        animeSources: animeSourceOrder === 'kitsu_first' ? ['kitsu', 'anilist'] : ['anilist', 'kitsu'],
      })
    },
    onSuccess: (data) => {
      setFailedPosterKeys({})
      setSearchResults(data.results)
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Search failed',
        description: error.message,
      })
    },
  })

  // Link external ID mutation (single)
  const linkMutation = useMutation({
    mutationFn: () =>
      metadataApi.linkExternalId(mediaId, selectedProvider, newExternalId, mediaType, fetchMetadataOnLink),
    onSuccess: (data) => {
      toast({
        title: 'External ID linked',
        description: data.message,
      })
      setLinkDialogOpen(false)
      // Invalidate queries to refresh the page data
      queryClient.invalidateQueries({ queryKey: catalogKeys.item(mediaType, mediaId.toString()) })
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Linking failed',
        description: error.message,
      })
    },
  })

  // Link multiple external IDs mutation
  const linkMultipleMutation = useMutation({
    mutationFn: () => {
      const ids: Record<string, string | undefined> = {}
      for (const item of idsToLink) {
        if (item.provider === 'imdb') ids.imdb_id = item.id
        else if (item.provider === 'tmdb') ids.tmdb_id = item.id
        else if (item.provider === 'tvdb') ids.tvdb_id = item.id
        else if (item.provider === 'mal') ids.mal_id = item.id
        else if (item.provider === 'kitsu') ids.kitsu_id = item.id
      }
      return metadataApi.linkMultipleExternalIds(mediaId, ids, mediaType, fetchMetadataOnLink)
    },
    onSuccess: (data) => {
      toast({
        title: 'External IDs linked',
        description: `Successfully linked ${data.linked_providers.length} provider(s): ${data.linked_providers.join(', ')}`,
      })
      if (data.failed_providers.length > 0) {
        toast({
          variant: 'destructive',
          title: 'Some providers failed',
          description: `Failed to link: ${data.failed_providers.join(', ')}`,
        })
      }
      setLinkDialogOpen(false)
      // Invalidate queries to refresh the page data
      queryClient.invalidateQueries({ queryKey: catalogKeys.item(mediaType, mediaId.toString()) })
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Linking failed',
        description: error.message,
      })
    },
  })

  const handleRefresh = () => {
    refreshMutation.mutate()
    setProviderDropdownOpen(false)
  }

  const toggleProvider = (providerId: MetadataProvider) => {
    setSelectedProviders((prev) =>
      prev.includes(providerId) ? prev.filter((p) => p !== providerId) : [...prev, providerId],
    )
  }

  const handleOpenLinkDialog = () => {
    setSearchQuery(title)
    setSearchYear(year ? String(year) : '')
    setSearchResults([])
    setFailedPosterKeys({})
    setSelectedResult(null)
    setNewExternalId('')
    setSelectedProvider('imdb')
    setFetchMetadataOnLink(true)
    setIdsToLink([])
    setLinkMode('search')
    setIncludeAnimeProviders(true)
    setAnimeSourceOrder('kitsu_first')
    setLinkDialogOpen(true)
  }

  const handleSearch = () => {
    if (searchQuery.trim()) {
      searchMutation.mutate()
    }
  }

  const handleSelectResult = (result: ExternalSearchResult) => {
    setSelectedResult(result)
    // Extract all available IDs from the result
    // Pre-select new IDs, but also allow selecting already-linked IDs to update them
    const availableIds = getAvailableIds(result)
    // Pre-select only new IDs by default, user can manually enable already-linked ones
    const newIds = availableIds.filter((item) => !externalIds?.[item.provider as keyof ExternalIds])
    setIdsToLink(newIds)
  }

  const toggleIdToLink = (provider: ExternalProvider, id: string) => {
    setIdsToLink((prev) => {
      const exists = prev.some((item) => item.provider === provider)
      if (exists) {
        return prev.filter((item) => item.provider !== provider)
      } else {
        return [...prev, { provider, id }]
      }
    })
  }

  const handleLink = () => {
    if (linkMode === 'manual' && newExternalId.trim()) {
      linkMutation.mutate()
    }
  }

  const handleLinkMultiple = () => {
    if (idsToLink.length > 0) {
      linkMultipleMutation.mutate()
    }
  }

  // Get the current provider config
  const currentProviderConfig = PROVIDERS.find((p) => p.id === selectedProvider)

  return (
    <TooltipProvider>
      <div className={cn('flex items-center gap-2', className)}>
        {/* Refresh button with provider dropdown */}
        <div className="flex items-center">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-1.5 rounded-l-xl rounded-r-none border-r-0"
                onClick={handleRefresh}
                disabled={refreshMutation.isPending || !hasAnyExternalId}
              >
                {refreshMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                <span className="hidden sm:inline">
                  {selectedProviders.length > 0 ? `Refresh (${selectedProviders.length})` : 'Refresh All'}
                </span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>
                {!hasAnyExternalId
                  ? 'No external IDs linked - add an external ID first'
                  : selectedProviders.length > 0
                    ? `Refresh from: ${selectedProviders.join(', ')}`
                    : 'Refresh metadata from all configured providers'}
              </p>
            </TooltipContent>
          </Tooltip>

          <DropdownMenu open={providerDropdownOpen} onOpenChange={setProviderDropdownOpen}>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8 px-2 rounded-l-none rounded-r-xl"
                disabled={refreshMutation.isPending || !hasAnyExternalId}
              >
                <ChevronDown className="h-3 w-3" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <div className="p-2">
                <div className="flex items-center gap-2 mb-2 px-2 text-sm font-medium text-muted-foreground">
                  <Database className="h-4 w-4" />
                  Select providers to refresh
                </div>
                <div className="space-y-1">
                  {PROVIDERS.map((provider) => {
                    const hasId = externalIds?.[provider.id as keyof ExternalIds]
                    return (
                      <label
                        key={provider.id}
                        className={cn(
                          'flex items-center gap-3 p-2 rounded-lg cursor-pointer transition-colors',
                          hasId ? 'hover:bg-muted' : 'opacity-50 cursor-not-allowed',
                          selectedProviders.includes(provider.id) && 'bg-primary/10',
                        )}
                      >
                        <Checkbox
                          checked={selectedProviders.includes(provider.id)}
                          onCheckedChange={() => hasId && toggleProvider(provider.id)}
                          disabled={!hasId}
                        />
                        <span className="text-lg">{provider.icon}</span>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium">{provider.name}</p>
                          <p className="text-xs text-muted-foreground truncate">
                            {hasId ? provider.description : 'No ID available'}
                          </p>
                        </div>
                      </label>
                    )
                  })}
                </div>
                {selectedProviders.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full mt-2 text-xs"
                    onClick={() => setSelectedProviders([])}
                  >
                    Clear selection (refresh all)
                  </Button>
                )}
              </div>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {/* Link External ID button */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 rounded-xl border-emerald-500/50 text-emerald-600 hover:bg-emerald-500/10"
              onClick={handleOpenLinkDialog}
            >
              <ArrowRightLeft className="h-4 w-4" />
              <span className="hidden sm:inline">Link Provider</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Link an external provider ID (IMDb, TMDB, TVDB, etc.)</p>
          </TooltipContent>
        </Tooltip>

        {/* Link External ID Dialog */}
        <Dialog open={linkDialogOpen} onOpenChange={setLinkDialogOpen}>
          <DialogContent
            scrollMode="contained"
            className="sm:max-w-[700px] max-h-[90vh] flex flex-col overflow-hidden min-h-0"
          >
            <DialogHeader className="shrink-0">
              <DialogTitle className="flex items-center gap-2">
                <ArrowRightLeft className="h-5 w-5 text-emerald-500" />
                Link External Provider
              </DialogTitle>
              <DialogDescription>
                Link an external provider ID to this content. This allows fetching metadata from multiple sources.
              </DialogDescription>
            </DialogHeader>

            <ScrollArea className="flex-1 min-h-0 pr-1">
              <div className="space-y-4 py-4">
                {/* Current IDs */}
                <div className="p-3 rounded-xl bg-muted/50">
                  <Label className="text-xs text-muted-foreground">Current External IDs</Label>
                  <div className="flex flex-wrap gap-2 mt-2">
                    {externalIds?.imdb && (
                      <Badge variant="outline" className="bg-primary/10 text-primary border-primary/30">
                        🎬 IMDb: {externalIds.imdb}
                      </Badge>
                    )}
                    {externalIds?.tmdb && (
                      <Badge variant="outline" className="bg-blue-500/10 text-blue-600 border-blue-500/30">
                        🎞️ TMDB: {externalIds.tmdb}
                      </Badge>
                    )}
                    {externalIds?.tvdb && (
                      <Badge variant="outline" className="bg-green-500/10 text-green-600 border-green-500/30">
                        📺 TVDB: {externalIds.tvdb}
                      </Badge>
                    )}
                    {externalIds?.mal && (
                      <Badge variant="outline" className="bg-primary/10 text-primary border-primary/30">
                        🎌 MAL: {externalIds.mal}
                      </Badge>
                    )}
                    {externalIds?.kitsu && (
                      <Badge variant="outline" className="bg-orange-500/10 text-orange-600 border-orange-500/30">
                        🦊 Kitsu: {externalIds.kitsu}
                      </Badge>
                    )}
                    {isInternalId && (
                      <Badge variant="outline" className="bg-primary/10 text-primary border-primary/30">
                        Internal: {canonicalExternalId}
                      </Badge>
                    )}
                  </div>
                </div>

                {/* Search section */}
                <div className="space-y-3">
                  <Label className="text-sm font-medium">Search External Providers</Label>
                  <div className="grid grid-cols-[1fr_132px_auto] gap-2">
                    <Input
                      placeholder="Search by title..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                      className="rounded-xl min-w-0"
                    />
                    <Input
                      type="number"
                      inputMode="numeric"
                      min={1878}
                      max={9999}
                      step={1}
                      placeholder="Year (optional)"
                      value={searchYear}
                      onChange={(e) => setSearchYear(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                      className="w-[132px] rounded-xl"
                    />
                    <Button
                      onClick={handleSearch}
                      disabled={searchMutation.isPending || !searchQuery.trim()}
                      className="rounded-xl shrink-0"
                    >
                      {searchMutation.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Search className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border/60 bg-muted/20 p-2.5">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <Checkbox
                        checked={includeAnimeProviders}
                        onCheckedChange={(checked) => setIncludeAnimeProviders(Boolean(checked))}
                      />
                      <span className="text-xs text-muted-foreground">Include anime providers (MAL/Kitsu)</span>
                    </label>
                    <div className="flex items-center gap-2 ml-auto">
                      <span className="text-xs text-muted-foreground">Anime order</span>
                      <Select
                        value={animeSourceOrder}
                        onValueChange={(value) => setAnimeSourceOrder(value as 'kitsu_first' | 'anilist_first')}
                        disabled={!includeAnimeProviders}
                      >
                        <SelectTrigger className="h-8 w-[180px] rounded-lg">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="kitsu_first">Kitsu → AniList</SelectItem>
                          <SelectItem value="anilist_first">AniList → Kitsu</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                </div>

                {/* Search Results */}
                {searchResults.length > 0 && (
                  <div className="space-y-2">
                    <Label className="text-xs text-muted-foreground">Results from all providers</Label>
                    <ScrollArea className="h-52 rounded-xl border">
                      <div className="p-2 space-y-2">
                        {searchResults.map((result, idx) => {
                          const providerConfig = PROVIDERS.find((p) => p.id === result.provider)
                          const availableIds = getAvailableIds(result)
                          const newIdsCount = availableIds.filter(
                            (item) => !externalIds?.[item.provider as keyof ExternalIds],
                          ).length
                          const posterKey = `${result.provider}-${result.id}-${result.poster || 'no-poster'}`
                          const canShowPoster = !!result.poster && !failedPosterKeys[posterKey]
                          return (
                            <div
                              key={`${result.id}-${idx}`}
                              className={cn(
                                'flex gap-3 p-2 rounded-lg cursor-pointer transition-colors',
                                selectedResult?.id === result.id
                                  ? 'bg-primary/20 border border-primary/50'
                                  : 'hover:bg-muted',
                              )}
                              onClick={() => handleSelectResult(result)}
                            >
                              {canShowPoster ? (
                                <img
                                  src={result.poster}
                                  alt=""
                                  className="w-12 h-[72px] object-cover rounded shrink-0"
                                  loading="lazy"
                                  onError={() =>
                                    setFailedPosterKeys((prev) => ({
                                      ...prev,
                                      [posterKey]: true,
                                    }))
                                  }
                                />
                              ) : (
                                <div className="w-12 h-[72px] bg-muted rounded flex items-center justify-center shrink-0">
                                  <Film className="h-6 w-6 text-muted-foreground" />
                                </div>
                              )}
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 min-w-0">
                                  <p className="font-medium text-sm truncate min-w-0 flex-1">{result.title}</p>
                                  {providerConfig && (
                                    <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">
                                      {providerConfig.icon} {providerConfig.name}
                                    </Badge>
                                  )}
                                  {newIdsCount > 0 && (
                                    <Badge
                                      variant="outline"
                                      className="text-[10px] px-1.5 py-0 bg-emerald-500/10 text-emerald-600 border-emerald-500/30 shrink-0"
                                    >
                                      +{newIdsCount} new
                                    </Badge>
                                  )}
                                </div>
                                <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1">
                                  {result.year && (
                                    <span className="flex items-center gap-1">
                                      <Calendar className="h-3 w-3" />
                                      {result.year}
                                    </span>
                                  )}
                                </div>
                                <div className="flex flex-wrap gap-1 mt-1">
                                  {result.imdb_id && (
                                    <code
                                      className={cn(
                                        'text-[10px] px-1 rounded',
                                        externalIds?.imdb === result.imdb_id
                                          ? 'text-emerald-600 bg-emerald-500/10'
                                          : externalIds?.imdb
                                            ? 'text-primary bg-primary/10'
                                            : 'text-muted-foreground bg-muted',
                                      )}
                                    >
                                      IMDb: {result.imdb_id}
                                      {externalIds?.imdb === result.imdb_id && ' ✓'}
                                    </code>
                                  )}
                                  {result.tmdb_id && (
                                    <code
                                      className={cn(
                                        'text-[10px] px-1 rounded',
                                        String(externalIds?.tmdb) === String(result.tmdb_id)
                                          ? 'text-emerald-600 bg-emerald-500/10'
                                          : externalIds?.tmdb
                                            ? 'text-primary bg-primary/10'
                                            : 'text-muted-foreground bg-muted',
                                      )}
                                    >
                                      TMDB: {result.tmdb_id}
                                      {String(externalIds?.tmdb) === String(result.tmdb_id) && ' ✓'}
                                    </code>
                                  )}
                                  {result.tvdb_id && (
                                    <code
                                      className={cn(
                                        'text-[10px] px-1 rounded',
                                        String(externalIds?.tvdb) === String(result.tvdb_id)
                                          ? 'text-emerald-600 bg-emerald-500/10'
                                          : externalIds?.tvdb
                                            ? 'text-primary bg-primary/10'
                                            : 'text-muted-foreground bg-muted',
                                      )}
                                    >
                                      TVDB: {result.tvdb_id}
                                      {String(externalIds?.tvdb) === String(result.tvdb_id) && ' ✓'}
                                    </code>
                                  )}
                                  {result.mal_id && (
                                    <code
                                      className={cn(
                                        'text-[10px] px-1 rounded',
                                        String(externalIds?.mal) === String(result.mal_id)
                                          ? 'text-emerald-600 bg-emerald-500/10'
                                          : externalIds?.mal
                                            ? 'text-primary bg-primary/10'
                                            : 'text-muted-foreground bg-muted',
                                      )}
                                    >
                                      MAL: {result.mal_id}
                                      {String(externalIds?.mal) === String(result.mal_id) && ' ✓'}
                                    </code>
                                  )}
                                  {result.kitsu_id && (
                                    <code
                                      className={cn(
                                        'text-[10px] px-1 rounded',
                                        String(externalIds?.kitsu) === String(result.kitsu_id)
                                          ? 'text-emerald-600 bg-emerald-500/10'
                                          : externalIds?.kitsu
                                            ? 'text-primary bg-primary/10'
                                            : 'text-muted-foreground bg-muted',
                                      )}
                                    >
                                      Kitsu: {result.kitsu_id}
                                      {String(externalIds?.kitsu) === String(result.kitsu_id) && ' ✓'}
                                    </code>
                                  )}
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </ScrollArea>
                  </div>
                )}

                {/* Selected result - IDs to link */}
                {selectedResult && (
                  <div className="space-y-3 p-4 rounded-xl bg-primary/5 border border-primary/20">
                    <div className="flex items-center justify-between">
                      <Label className="text-sm font-medium flex items-center gap-2">
                        <Link2 className="h-4 w-4 text-primary" />
                        IDs to link from "{selectedResult.title}"
                      </Label>
                      <span className="text-xs text-muted-foreground">{idsToLink.length} selected</span>
                    </div>
                    <div className="space-y-2">
                      {getAvailableIds(selectedResult).map(({ provider, id }) => {
                        const providerConfig = PROVIDERS.find((p) => p.id === provider)
                        const currentLinkedId = externalIds?.[provider as keyof ExternalIds]
                        const alreadyLinked = !!currentLinkedId
                        const isSameId = alreadyLinked && String(currentLinkedId) === String(id)
                        const isSelected = idsToLink.some((item) => item.provider === provider)

                        return (
                          <label
                            key={provider}
                            className={cn(
                              'flex items-center gap-3 p-2 rounded-lg transition-colors cursor-pointer',
                              isSameId && !isSelected && 'bg-muted/30',
                              isSelected && 'bg-emerald-500/10 border border-emerald-500/30',
                              !isSelected && !isSameId && 'hover:bg-muted/50',
                            )}
                          >
                            <Checkbox checked={isSelected} onCheckedChange={() => toggleIdToLink(provider, id)} />
                            <span className="text-lg">{providerConfig?.icon}</span>
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium">{providerConfig?.name}</p>
                              <code className="text-xs text-muted-foreground">{id}</code>
                            </div>
                            {alreadyLinked &&
                              (isSameId ? (
                                <Badge variant="secondary" className="text-[10px] gap-1">
                                  <Check className="h-3 w-3" />
                                  Same ID
                                </Badge>
                              ) : (
                                <Badge
                                  variant="outline"
                                  className="text-[10px] gap-1 bg-primary/10 text-primary border-primary/30"
                                >
                                  Current: {currentLinkedId}
                                </Badge>
                              ))}
                          </label>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Manual entry section */}
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-px bg-border" />
                    <button
                      type="button"
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                      onClick={() => setLinkMode(linkMode === 'manual' ? 'search' : 'manual')}
                    >
                      {linkMode === 'manual' ? 'Use search results' : 'Or enter ID manually'}
                    </button>
                    <div className="flex-1 h-px bg-border" />
                  </div>

                  {linkMode === 'manual' && (
                    <>
                      <div className="grid grid-cols-[120px_1fr] gap-3">
                        <div className="space-y-2">
                          <Label className="text-sm">Provider</Label>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="outline" className="w-full justify-between rounded-xl">
                                <span className="flex items-center gap-2">
                                  <span>{currentProviderConfig?.icon}</span>
                                  <span>{currentProviderConfig?.name}</span>
                                </span>
                                <ChevronDown className="h-3 w-3" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="start" className="w-48">
                              {PROVIDERS.map((provider) => (
                                <div
                                  key={provider.id}
                                  className={cn(
                                    'flex items-center gap-2 p-2 cursor-pointer hover:bg-muted rounded-lg',
                                    selectedProvider === provider.id && 'bg-primary/10',
                                  )}
                                  onClick={() => {
                                    setSelectedProvider(provider.id)
                                    setNewExternalId('')
                                  }}
                                >
                                  <span>{provider.icon}</span>
                                  <span className="text-sm">{provider.name}</span>
                                </div>
                              ))}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </div>
                        <div className="space-y-2">
                          <Label className="text-sm">External ID</Label>
                          <Input
                            placeholder={currentProviderConfig?.idPlaceholder || 'Enter ID'}
                            value={newExternalId}
                            onChange={(e) => setNewExternalId(e.target.value)}
                            className="rounded-xl font-mono"
                          />
                        </div>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {selectedProvider === 'imdb'
                          ? 'IMDb ID starts with "tt" (e.g., tt1234567)'
                          : `Enter the ${currentProviderConfig?.name} ID (e.g., ${currentProviderConfig?.idFormat})`}
                      </p>
                    </>
                  )}
                </div>

                {/* Fetch metadata option */}
                <label className="flex items-center gap-3 p-3 rounded-xl bg-muted/30 cursor-pointer">
                  <Checkbox
                    checked={fetchMetadataOnLink}
                    onCheckedChange={(checked) => setFetchMetadataOnLink(checked as boolean)}
                  />
                  <div>
                    <p className="text-sm font-medium">Fetch metadata from provider(s)</p>
                    <p className="text-xs text-muted-foreground">
                      Update title, description, poster, and other details
                    </p>
                  </div>
                </label>

                {/* Status messages */}
                {(linkMutation.isError || linkMultipleMutation.isError) && (
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>
                      {(linkMutation.error as Error)?.message ||
                        (linkMultipleMutation.error as Error)?.message ||
                        'Linking failed'}
                    </AlertDescription>
                  </Alert>
                )}
              </div>
            </ScrollArea>

            <DialogFooter className="flex-col sm:flex-row gap-2 shrink-0">
              <Button variant="outline" onClick={() => setLinkDialogOpen(false)} className="rounded-xl">
                Cancel
              </Button>

              {linkMode === 'manual' ? (
                <Button
                  onClick={handleLink}
                  disabled={
                    linkMutation.isPending ||
                    !newExternalId.trim() ||
                    (selectedProvider === 'imdb' && !newExternalId.startsWith('tt'))
                  }
                  className="rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-600 hover:to-teal-700"
                >
                  {linkMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Linking...
                    </>
                  ) : (
                    <>
                      <ArrowRightLeft className="h-4 w-4 mr-2" />
                      Link {currentProviderConfig?.name}
                    </>
                  )}
                </Button>
              ) : (
                <Button
                  onClick={handleLinkMultiple}
                  disabled={linkMultipleMutation.isPending || idsToLink.length === 0}
                  className="rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-600 hover:to-teal-700"
                >
                  {linkMultipleMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Linking {idsToLink.length} ID(s)...
                    </>
                  ) : (
                    <>
                      <Link2 className="h-4 w-4 mr-2" />
                      Link {idsToLink.length} ID(s)
                    </>
                  )}
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
  )
}
