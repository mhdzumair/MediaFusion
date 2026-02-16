import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Film, Tv, Cloud, CloudOff, Settings, Loader2, HardDrive, Download, Trash2 } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { ContentCard, ContentGrid, type ContentCardData } from '@/components/content'
import {
  useWatchlistProviders,
  useInfiniteWatchlist,
  useMissingTorrents,
  useProfiles,
  useRemoveTorrent,
  useClearAllTorrents,
} from '@/hooks'
import {
  getProviderDisplayName,
  DEBRID_SERVICE_DISPLAY_NAMES,
  type WatchlistProviderInfo,
  type WatchlistItem,
} from '@/lib/api'

// Providers that support import functionality (all providers with fetch_torrent_details)
const IMPORT_SUPPORTED_PROVIDERS = new Set([
  'realdebrid',
  'alldebrid',
  'torbox',
  'debridlink',
  'premiumize',
  'offcloud',
  'seedr',
  'pikpak',
])

export function WatchlistTab() {
  const { toast } = useToast()

  // Profile selection
  const { data: profiles } = useProfiles()
  const [selectedProfileId, setSelectedProfileId] = useState<number | undefined>()

  // Provider selection
  const [selectedProvider, setSelectedProvider] = useState<string | undefined>()

  // Filters
  const [mediaType, setMediaType] = useState<'movie' | 'series' | ''>('')

  // Infinite scroll ref
  const loadMoreRef = useRef<HTMLDivElement>(null)

  // Set default profile on load (during render, not in effect)
  // Only guard on selectedProfileId === undefined — prev reference guard fails when profiles are cached
  if (profiles && profiles.length > 0 && selectedProfileId === undefined) {
    const defaultProfile = profiles.find((p) => p.is_default) || profiles[0]
    setSelectedProfileId(defaultProfile.id)
  }

  // Fetch providers for the selected profile
  const { data: providersData, isLoading: providersLoading } = useWatchlistProviders(selectedProfileId, {
    enabled: selectedProfileId !== undefined,
  })

  // Set default provider when providers load (during render, not in effect)
  const [prevProviders, setPrevProviders] = useState(providersData?.providers)
  if (prevProviders !== providersData?.providers) {
    setPrevProviders(providersData?.providers)
    if (providersData?.providers && providersData.providers.length > 0) {
      setSelectedProvider(providersData.providers[0].service)
    } else {
      setSelectedProvider(undefined)
    }
  }

  // Fetch watchlist items with infinite scroll
  const {
    data: watchlistData,
    isLoading: watchlistLoading,
    isFetching,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteWatchlist(
    selectedProvider,
    {
      profileId: selectedProfileId,
      mediaType: mediaType || undefined,
      pageSize: 24,
    },
    {
      enabled: !!selectedProvider && selectedProfileId !== undefined,
    },
  )

  // Transform watchlist items to ContentCardData format (flatten pages)
  // Also keep track of info_hashes for each item
  const { contentItems, itemHashesMap } = useMemo(() => {
    if (!watchlistData?.pages)
      return { contentItems: [] as ContentCardData[], itemHashesMap: new Map<number, string[]>() }

    const hashesMap = new Map<number, string[]>()
    const items = watchlistData.pages.flatMap((page) =>
      page.items.map((item: WatchlistItem) => {
        hashesMap.set(item.id, item.info_hashes || [])
        return {
          id: item.id,
          external_ids: item.external_ids,
          title: item.title,
          type: item.type,
          year: item.year,
          poster: item.poster,
        } as ContentCardData
      }),
    )
    return { contentItems: items, itemHashesMap: hashesMap }
  }, [watchlistData])

  // Remove torrent mutation
  const removeTorrent = useRemoveTorrent()
  const clearAllTorrents = useClearAllTorrents()

  // Handle removing a content item from debrid
  const handleRemove = useCallback(
    async (item: ContentCardData) => {
      if (!selectedProvider || !selectedProfileId) return

      const infoHashes = itemHashesMap.get(item.id) || []
      if (infoHashes.length === 0) {
        toast({ title: 'Error', description: 'No torrent hashes found for this item', variant: 'destructive' })
        return
      }

      // Remove all info_hashes for this item
      let successCount = 0
      let failCount = 0

      for (const hash of infoHashes) {
        try {
          const result = await removeTorrent.mutateAsync({
            provider: selectedProvider,
            infoHash: hash,
            profileId: selectedProfileId,
          })
          if (result.success) {
            successCount++
          } else {
            failCount++
          }
        } catch {
          failCount++
        }
      }

      if (successCount > 0 && failCount === 0) {
        toast({
          title: 'Removed',
          description: `Removed "${item.title}" from ${DEBRID_SERVICE_DISPLAY_NAMES[selectedProvider] || selectedProvider}`,
        })
      } else if (successCount > 0) {
        toast({
          title: 'Partial Success',
          description: `Partially removed "${item.title}" (${successCount}/${infoHashes.length} torrents)`,
        })
      } else {
        toast({ title: 'Error', description: `Failed to remove "${item.title}"`, variant: 'destructive' })
      }
    },
    [selectedProvider, selectedProfileId, itemHashesMap, removeTorrent, toast],
  )

  // Handle clearing all torrents
  const handleClearAll = useCallback(async () => {
    if (!selectedProvider || !selectedProfileId) return

    try {
      const result = await clearAllTorrents.mutateAsync({
        provider: selectedProvider,
        profileId: selectedProfileId,
      })
      if (result.success) {
        toast({
          title: 'Cleared',
          description: `All torrents cleared from ${DEBRID_SERVICE_DISPLAY_NAMES[selectedProvider] || selectedProvider}`,
        })
      } else {
        toast({ title: 'Error', description: result.message || 'Failed to clear torrents', variant: 'destructive' })
      }
    } catch {
      toast({ title: 'Error', description: 'Failed to clear torrents', variant: 'destructive' })
    }
  }, [selectedProvider, selectedProfileId, clearAllTorrents, toast])

  // Get total count from first page
  const totalCount = watchlistData?.pages?.[0]?.total || 0
  const providerName = watchlistData?.pages?.[0]?.provider_name

  // Check for missing torrents (only for supported providers)
  const supportsImport = selectedProvider && IMPORT_SUPPORTED_PROVIDERS.has(selectedProvider)
  const { data: missingData } = useMissingTorrents(supportsImport ? selectedProvider : undefined, selectedProfileId, {
    enabled: !!supportsImport && selectedProfileId !== undefined,
  })
  const missingCount = missingData?.total || 0

  // Infinite scroll observer
  const handleObserver = useCallback(
    (entries: IntersectionObserverEntry[]) => {
      const [target] = entries
      if (target.isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage()
      }
    },
    [fetchNextPage, hasNextPage, isFetchingNextPage],
  )

  useEffect(() => {
    const element = loadMoreRef.current
    if (!element) return

    const observer = new IntersectionObserver(handleObserver, {
      root: null,
      rootMargin: '100px',
      threshold: 0,
    })

    observer.observe(element)
    return () => observer.disconnect()
  }, [handleObserver])

  // Get display name for provider tabs
  const getTabDisplayName = (provider: WatchlistProviderInfo): string => {
    const serviceName = DEBRID_SERVICE_DISPLAY_NAMES[provider.service] || provider.service
    if (provider.name && provider.name !== serviceName && provider.name !== provider.service) {
      return provider.name
    }
    return serviceName
  }

  const providers = providersData?.providers || []
  const hasProviders = providers.length > 0
  const isLoading = providersLoading || (watchlistLoading && !watchlistData)

  // No profile selected yet
  if (!selectedProfileId) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header with Profile Selector */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-primary/10 border border-primary/20">
            <Cloud className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">Debrid Watchlist</h2>
            <p className="text-sm text-muted-foreground">Content downloaded in your debrid accounts</p>
          </div>
        </div>

        {/* Profile Selector */}
        {profiles && profiles.length > 1 && (
          <Select
            value={selectedProfileId?.toString()}
            onValueChange={(value) => setSelectedProfileId(parseInt(value, 10))}
          >
            <SelectTrigger className="w-[180px] rounded-xl">
              <SelectValue placeholder="Select Profile" />
            </SelectTrigger>
            <SelectContent>
              {profiles.map((profile) => (
                <SelectItem key={profile.id} value={profile.id.toString()}>
                  <div className="flex items-center gap-2">
                    <span>{profile.name}</span>
                    {profile.is_default && (
                      <Badge variant="secondary" className="text-[10px] px-1 py-0">
                        Default
                      </Badge>
                    )}
                  </div>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* No Providers Configured */}
      {!providersLoading && !hasProviders && (
        <Card className="glass border-border/50">
          <CardContent className="py-12 text-center">
            <CloudOff className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
            <p className="mt-4 font-medium">No Debrid Providers Configured</p>
            <p className="text-sm text-muted-foreground mt-2 max-w-md mx-auto">
              Configure a debrid service in your profile to see your downloaded content here. Supported services include
              Real-Debrid, AllDebrid, TorBox, and more.
            </p>
            <Button className="mt-4 rounded-xl" asChild>
              <Link to="/dashboard/configure">
                <Settings className="mr-2 h-4 w-4" />
                Configure Profile
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Provider Tabs */}
      {hasProviders && (
        <>
          {providers.length > 1 ? (
            <Tabs value={selectedProvider} onValueChange={setSelectedProvider}>
              <TabsList className="h-auto flex-wrap gap-1 bg-transparent p-0">
                {providers.map((provider) => (
                  <TabsTrigger
                    key={provider.service}
                    value={provider.service}
                    className="rounded-xl data-[state=active]:bg-primary data-[state=active]:text-primary-foreground px-4 py-2"
                  >
                    <HardDrive className="mr-2 h-4 w-4" />
                    {getTabDisplayName(provider)}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          ) : (
            <div className="flex items-center gap-2">
              <Badge variant="secondary" className="rounded-xl px-3 py-1.5">
                <HardDrive className="mr-2 h-4 w-4" />
                {getProviderDisplayName(providers[0])}
              </Badge>
            </div>
          )}

          {/* Stats & Filters */}
          <div className="flex flex-wrap items-center justify-between gap-4">
            {/* Stats & Import Button */}
            <div className="flex items-center gap-4">
              {totalCount > 0 && (
                <p className="text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">{totalCount}</span> items found
                  {providerName && <span> in {providerName}</span>}
                </p>
              )}
              {isFetching && !watchlistLoading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}

              {/* Import Missing Link */}
              {supportsImport && (
                <Button variant="outline" size="sm" className="rounded-xl" asChild>
                  <Link to="/dashboard/content-import?tab=debrid">
                    <Download className="mr-2 h-4 w-4" />
                    Import Missing
                    {missingCount > 0 && (
                      <Badge variant="secondary" className="ml-2 px-1.5 py-0 text-xs">
                        {missingCount}
                      </Badge>
                    )}
                  </Link>
                </Button>
              )}

              {/* Clear All Button */}
              {totalCount > 0 && (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      className="rounded-xl text-destructive hover:text-destructive"
                      disabled={clearAllTorrents.isPending}
                    >
                      {clearAllTorrents.isPending ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="mr-2 h-4 w-4" />
                      )}
                      Clear All
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Clear All Torrents?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This will delete ALL torrents from your{' '}
                        {DEBRID_SERVICE_DISPLAY_NAMES[selectedProvider || ''] || selectedProvider} account. This action
                        cannot be undone.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={handleClearAll}
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      >
                        Clear All
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              )}
            </div>

            {/* Type Filter */}
            <Select
              value={mediaType || 'all'}
              onValueChange={(v) => {
                setMediaType(v === 'all' ? '' : (v as 'movie' | 'series'))
              }}
            >
              <SelectTrigger className="w-[130px] rounded-xl">
                <SelectValue placeholder="All Types" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Types</SelectItem>
                <SelectItem value="movie">
                  <div className="flex items-center gap-2">
                    <Film className="h-4 w-4" />
                    Movies
                  </div>
                </SelectItem>
                <SelectItem value="series">
                  <div className="flex items-center gap-2">
                    <Tv className="h-4 w-4" />
                    Series
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Content Grid */}
          {isLoading ? (
            <ContentGrid>
              {[...Array(12)].map((_, i) => (
                <div key={i} className="space-y-2">
                  <Skeleton className="aspect-[2/3] rounded-xl" />
                  <Skeleton className="h-4 w-3/4" />
                </div>
              ))}
            </ContentGrid>
          ) : !contentItems.length ? (
            <Card className="glass border-border/50">
              <CardContent className="py-12 text-center">
                <Cloud className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
                <p className="mt-4 font-medium">No Downloads Found</p>
                <p className="text-sm text-muted-foreground mt-2">
                  {mediaType
                    ? `No ${mediaType === 'movie' ? 'movies' : 'series'} found in your ${DEBRID_SERVICE_DISPLAY_NAMES[selectedProvider || ''] || selectedProvider} account`
                    : `Your ${DEBRID_SERVICE_DISPLAY_NAMES[selectedProvider || ''] || selectedProvider} watchlist is empty or the content isn't in our database yet`}
                </p>
              </CardContent>
            </Card>
          ) : (
            <>
              <ContentGrid>
                {contentItems.map((item) => (
                  <ContentCard key={item.id} item={item} variant="grid" showType={true} onRemove={handleRemove} />
                ))}
              </ContentGrid>

              {/* Infinite scroll trigger */}
              <div ref={loadMoreRef} className="h-4" />

              {/* Loading indicator for next page */}
              {isFetchingNextPage && (
                <div className="flex justify-center py-4">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
