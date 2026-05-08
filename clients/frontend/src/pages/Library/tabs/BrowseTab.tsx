import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Film, Loader2, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  ContentCard,
  ContentGrid,
  ContentList,
  ContentFilters,
  VirtualContentGrid,
  type ContentCardData,
  type ViewMode,
  type SearchMode,
} from '@/components/content'
import {
  useInfiniteCatalog,
  useCatalogList,
  useAvailableCatalogs,
  useGenres,
  type CatalogType,
  type SortOption,
  type SortDirection,
} from '@/hooks'
import { adminApi } from '@/lib/api/admin'
import { useRole } from '@/hooks/useRole'
import { useToast } from '@/hooks/use-toast'

// Storage key for persisting browse state
const BROWSE_STATE_KEY = 'browse_tab_state'
const BROWSE_SELECTED_ITEM_KEY = 'browse_selected_item'

type ScrollMode = 'infinite' | 'paged'
type PageSize = 25 | 50 | 100

interface BrowseState {
  catalogType: CatalogType
  selectedCatalog: string
  sort: SortOption
  sortDir: SortDirection
  viewMode: ViewMode
  scrollMode: ScrollMode
  pageSize: PageSize
  scrollPosition: number
  workingOnly: boolean
  myChannels: boolean
}

const getStoredState = (): Partial<BrowseState> => {
  try {
    const stored = sessionStorage.getItem(BROWSE_STATE_KEY)
    return stored ? JSON.parse(stored) : {}
  } catch {
    return {}
  }
}

const saveState = (state: BrowseState) => {
  try {
    sessionStorage.setItem(BROWSE_STATE_KEY, JSON.stringify(state))
  } catch {
    // Ignore storage errors
  }
}

export function BrowseTab() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { isAdmin } = useRole()
  const { toast } = useToast()
  const [blockTarget, setBlockTarget] = useState<ContentCardData | null>(null)
  const [blockReason, setBlockReason] = useState('')

  const blockMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      adminApi.blockMedia(id, { reason: reason || undefined }),
    onSuccess: (data) => {
      toast({ title: 'Content blocked', description: data.message })
      setBlockTarget(null)
      setBlockReason('')
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Block failed', description: error.message })
    },
  })

  // ---------------------------------------------------------------------------
  // All URL-synced state is derived directly from searchParams.
  // There is no useState / useEffect for these — the URL IS the state.
  // ---------------------------------------------------------------------------
  const storedState = getStoredState()

  const catalogType: CatalogType =
    (searchParams.get('type') as CatalogType) || storedState.catalogType || 'movie'
  const selectedGenre = searchParams.get('genre') || ''
  const urlSearchMode = searchParams.get('search_mode') as SearchMode | null
  const urlExternalId = searchParams.get('external_id') || ''
  const urlSearch = searchParams.get('search') || ''
  const searchMode: SearchMode = urlSearchMode ?? (urlExternalId ? 'external_id' : 'title')
  const search = urlExternalId || urlSearch
  const scrollMode: ScrollMode = searchParams.get('scroll_mode') === 'paged' ? 'paged' : 'infinite'
  const browsePage = (() => {
    const n = parseInt(searchParams.get('page') ?? '1', 10)
    return Number.isFinite(n) && n > 0 ? n : 1
  })()

  // ---------------------------------------------------------------------------
  // State that is NOT in the URL — persisted to sessionStorage only.
  // ---------------------------------------------------------------------------
  const [selectedCatalog, setSelectedCatalog] = useState<string>(storedState.selectedCatalog || '')
  const [sort, setSort] = useState<SortOption>(storedState.sort || 'latest')
  const [sortDir, setSortDir] = useState<SortDirection>(storedState.sortDir || 'desc')
  const [viewMode, setViewMode] = useState<ViewMode>(storedState.viewMode || 'grid')
  const [pageSize, setPageSize] = useState<PageSize>(storedState.pageSize || 25)
  const [workingOnly, setWorkingOnly] = useState(storedState.workingOnly || false)
  const [myChannels, setMyChannels] = useState(storedState.myChannels || false)
  const [restoredScroll, setRestoredScroll] = useState(false)

  // Track selected item ID for highlighting
  const [selectedItemId, setSelectedItemId] = useState<number | null>(() => {
    try {
      const stored = sessionStorage.getItem(BROWSE_SELECTED_ITEM_KEY)
      return stored ? parseInt(stored, 10) : null
    } catch {
      return null
    }
  })

  // Refs
  const containerRef = useRef<HTMLDivElement>(null)
  const selectedCardRef = useRef<HTMLDivElement>(null)
  const hasScrolledToSelected = useRef(false)

  // ---------------------------------------------------------------------------
  // Single URL mutator — all filter/pagination writes go through here.
  // Using the functional form of setSearchParams guarantees we always build
  // on top of the latest URL state, never clobbering params we didn't intend.
  // ---------------------------------------------------------------------------
  function updateUrl(
    updates: Partial<{
      type: CatalogType
      genre: string
      search: string
      searchMode: SearchMode
      scrollMode: ScrollMode
      page: number
    }>,
    opts: { resetPage?: boolean } = {},
  ) {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)

        if (updates.type !== undefined) params.set('type', updates.type)

        if (updates.genre !== undefined) {
          updates.genre ? params.set('genre', updates.genre) : params.delete('genre')
        }

        if (updates.scrollMode !== undefined) {
          updates.scrollMode === 'paged'
            ? params.set('scroll_mode', 'paged')
            : params.delete('scroll_mode')
        }

        if (updates.searchMode !== undefined || updates.search !== undefined) {
          const nextMode = updates.searchMode ?? searchMode
          const nextSearch = updates.search ?? search
          params.delete('search')
          params.delete('external_id')
          params.delete('search_mode')
          if (nextMode === 'external_id') {
            params.set('search_mode', 'external_id')
            if (nextSearch) params.set('external_id', nextSearch)
          } else if (nextSearch) {
            params.set('search', nextSearch)
          }
        }

        if (updates.page !== undefined) {
          updates.page > 1 ? params.set('page', String(updates.page)) : params.delete('page')
        } else if (opts.resetPage) {
          params.delete('page')
        }

        return params
      },
      { replace: true },
    )
  }

  // One-shot URL hydration so a stored catalogType materialises in URL on bare /library visit
  useEffect(() => {
    if (!searchParams.get('type') && storedState.catalogType) {
      updateUrl({ type: storedState.catalogType, scrollMode: storedState.scrollMode })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { data: availableCatalogs } = useAvailableCatalogs()
  const { data: genres } = useGenres(catalogType)

  const commonParams = {
    catalog: selectedCatalog || undefined,
    genre: selectedGenre || undefined,
    search: searchMode === 'title' ? search || undefined : undefined,
    external_id: searchMode === 'external_id' ? search || undefined : undefined,
    sort,
    sort_dir: sortDir,
    page_size: pageSize,
    ...(catalogType === 'tv' && {
      working_only: workingOnly || undefined,
      my_channels: myChannels || undefined,
    }),
  }

  const {
    data: infiniteData,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading: isInfiniteLoading,
  } = useInfiniteCatalog(catalogType, commonParams, { enabled: scrollMode === 'infinite' })

  const { data: pagedData, isLoading: isPagedLoading } = useCatalogList(
    catalogType,
    { ...commonParams, page: browsePage },
    { enabled: scrollMode === 'paged' },
  )

  const isLoading = scrollMode === 'infinite' ? isInfiniteLoading : isPagedLoading
  const hasData = scrollMode === 'infinite' ? !!infiniteData : !!pagedData

  // ---------------------------------------------------------------------------
  // Save non-URL state to sessionStorage (excludes URL-derived values)
  // ---------------------------------------------------------------------------
  const isRestoring = isLoading || !hasData
  useEffect(() => {
    if (!isRestoring) {
      saveState({
        catalogType,
        selectedCatalog,
        sort,
        sortDir,
        viewMode,
        scrollMode,
        pageSize,
        scrollPosition: window.scrollY,
        workingOnly,
        myChannels,
      })
    }
  }, [
    catalogType,
    selectedCatalog,
    sort,
    sortDir,
    viewMode,
    scrollMode,
    pageSize,
    isRestoring,
    workingOnly,
    myChannels,
  ])

  // Debounced scroll position save
  useEffect(() => {
    let timeoutId: ReturnType<typeof setTimeout>
    const handleScroll = () => {
      clearTimeout(timeoutId)
      timeoutId = setTimeout(() => {
        saveState({
          catalogType,
          selectedCatalog,
          sort,
          sortDir,
          viewMode,
          scrollMode,
          pageSize,
          scrollPosition: window.scrollY,
          workingOnly,
          myChannels,
        })
      }, 150)
    }
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', handleScroll)
      clearTimeout(timeoutId)
    }
  }, [
    catalogType,
    selectedCatalog,
    sort,
    sortDir,
    viewMode,
    scrollMode,
    pageSize,
    workingOnly,
    myChannels,
  ])

  // Restore scroll position after data loads
  useEffect(() => {
    if (!isLoading && hasData && !restoredScroll && storedState.scrollPosition !== undefined) {
      const timer = setTimeout(() => {
        window.scrollTo(0, storedState.scrollPosition!)
        setRestoredScroll(true)
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [isLoading, hasData, storedState.scrollPosition, restoredScroll])

  const items =
    scrollMode === 'infinite'
      ? (infiniteData?.pages.flatMap((page) => page.items ?? []).filter(Boolean) ?? [])
      : (pagedData?.items.filter(Boolean) ?? [])

  const catalogs =
    catalogType === 'movie'
      ? availableCatalogs?.movies
      : catalogType === 'series'
        ? availableCatalogs?.series
        : availableCatalogs?.tv

  const contentItems: ContentCardData[] = items.map((item) => ({
    id: item.id,
    external_ids: item.external_ids,
    title: item.title,
    type: item.type,
    year: item.year,
    poster: item.poster,
    runtime: item.runtime,
    imdb_rating: item.imdb_rating,
    ratings: item.ratings,
    genres: item.genres,
    likes_count: item.likes_count,
    certification: item.certification,
    nudity: item.nudity,
  }))

  // Clear selection if item not found
  const itemExists = contentItems.some((item) => item.id === selectedItemId)
  if (!isLoading && hasData && selectedItemId && !itemExists) {
    setSelectedItemId(null)
  }

  useEffect(() => {
    if (selectedItemId === null) {
      sessionStorage.removeItem(BROWSE_SELECTED_ITEM_KEY)
    }
  }, [selectedItemId])

  // Scroll to selected item in paged/list mode (virtual grid handles its own scrolling)
  useEffect(() => {
    const isVirtualMode = scrollMode === 'infinite' && viewMode === 'grid'
    if (!isVirtualMode && !isLoading && hasData && selectedItemId && itemExists && !hasScrolledToSelected.current) {
      const timer = setTimeout(() => {
        if (selectedCardRef.current) {
          selectedCardRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }
        hasScrolledToSelected.current = true
        setTimeout(() => {
          setSelectedItemId(null)
          sessionStorage.removeItem(BROWSE_SELECTED_ITEM_KEY)
        }, 5000)
      }, 200)
      return () => clearTimeout(timer)
    }
  }, [isLoading, hasData, selectedItemId, itemExists, scrollMode, viewMode, contentItems])

  useEffect(() => {
    hasScrolledToSelected.current = false
  }, [catalogType, selectedCatalog, selectedGenre, search, searchMode, sort, sortDir])

  const handleCardClick = (item: ContentCardData) => {
    sessionStorage.setItem(BROWSE_SELECTED_ITEM_KEY, item.id.toString())
    setSelectedItemId(item.id)
  }

  const selectedIndex =
    selectedItemId != null ? contentItems.findIndex((i) => i.id === selectedItemId) : undefined

  return (
    <div ref={containerRef} className="space-y-6">
      {/* Filters */}
      <ContentFilters
        catalogType={catalogType}
        onCatalogTypeChange={(newType) => {
          updateUrl({ type: newType, genre: '' }, { resetPage: true })
          setSelectedCatalog('')
          window.scrollTo(0, 0)
          setRestoredScroll(true)
        }}
        search={search}
        onSearchChange={(v) => updateUrl({ search: v }, { resetPage: true })}
        searchMode={searchMode}
        onSearchModeChange={(v) => updateUrl({ searchMode: v }, { resetPage: true })}
        showSearchMode
        searchPlaceholder={
          searchMode === 'external_id' ? 'Search by external ID (e.g., tt0133093, tmdb:603)...' : 'Search...'
        }
        selectedCatalog={selectedCatalog}
        catalogs={catalogs}
        onCatalogChange={(v) => {
          setSelectedCatalog(v)
          updateUrl({}, { resetPage: true })
        }}
        selectedGenre={selectedGenre}
        genres={genres}
        onGenreChange={(v) => updateUrl({ genre: v }, { resetPage: true })}
        sort={sort}
        onSortChange={(v) => {
          setSort(v as SortOption)
          updateUrl({}, { resetPage: true })
        }}
        sortDir={sortDir}
        onSortDirChange={(v) => {
          setSortDir(v)
          updateUrl({}, { resetPage: true })
        }}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        scrollMode={scrollMode}
        onScrollModeChange={(mode) => {
          updateUrl({ scrollMode: mode }, { resetPage: true })
          window.scrollTo(0, 0)
        }}
        pageSize={pageSize}
        onPageSizeChange={(size) => {
          setPageSize(size)
          updateUrl({}, { resetPage: true })
        }}
        workingOnly={workingOnly}
        onWorkingOnlyChange={(v) => {
          setWorkingOnly(v)
          updateUrl({}, { resetPage: true })
        }}
        myChannels={myChannels}
        onMyChannelsChange={(v) => {
          setMyChannels(v)
          updateUrl({}, { resetPage: true })
        }}
      />

      {/* Results */}
      {isLoading ? (
        viewMode === 'grid' ? (
          <ContentGrid>
            {[...Array(12)].map((_, i) => (
              <div key={i} className="space-y-2">
                <Skeleton className="aspect-[2/3] rounded-xl" />
                <Skeleton className="h-4 w-3/4" />
              </div>
            ))}
          </ContentGrid>
        ) : (
          <ContentList>
            {[...Array(6)].map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-xl" />
            ))}
          </ContentList>
        )
      ) : items.length === 0 ? (
        <div className="text-center py-12">
          <Film className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-muted-foreground">No items found</p>
          {search && <p className="text-sm text-muted-foreground mt-2">Try adjusting your search or filters</p>}
        </div>
      ) : (
        <>
          {/* Virtual grid for infinite scroll — bounds DOM size regardless of scroll depth */}
          {scrollMode === 'infinite' && viewMode === 'grid' ? (
            <>
              <VirtualContentGrid
                items={contentItems}
                renderItem={(item) => {
                  const isSelected = selectedItemId === item.id
                  return (
                    <ContentCard
                      key={item.id}
                      item={item}
                      variant="grid"
                      showEdit
                      onBlock={isAdmin ? setBlockTarget : undefined}
                      onNavigate={handleCardClick}
                      isSelected={isSelected}
                    />
                  )
                }}
                onLoadMore={fetchNextPage}
                hasMore={hasNextPage}
                loading={isFetchingNextPage}
                scrollTargetIndex={
                  selectedIndex !== undefined && selectedIndex >= 0 ? selectedIndex : undefined
                }
              />
              <div className="flex justify-center py-8">
                {isFetchingNextPage && (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Loader2 className="h-5 w-5 animate-spin" />
                    <span>Loading more...</span>
                  </div>
                )}
                {!hasNextPage && items.length > 0 && (
                  <p className="text-sm text-muted-foreground">You've reached the end</p>
                )}
              </div>
            </>
          ) : scrollMode === 'infinite' && viewMode === 'list' ? (
            <>
              <ContentList>
                {contentItems.map((item) => {
                  const isSelected = selectedItemId === item.id
                  return (
                    <ContentCard
                      key={item.id}
                      item={item}
                      variant="list"
                      showEdit
                      onBlock={isAdmin ? setBlockTarget : undefined}
                      onNavigate={handleCardClick}
                      isSelected={isSelected}
                      cardRef={isSelected ? selectedCardRef : undefined}
                    />
                  )
                })}
              </ContentList>
              {/* Infinite scroll sentinel for list view */}
              <InfiniteScrollSentinel
                hasNextPage={hasNextPage}
                isFetchingNextPage={isFetchingNextPage}
                fetchNextPage={fetchNextPage}
                itemCount={items.length}
              />
            </>
          ) : viewMode === 'grid' ? (
            <ContentGrid>
              {contentItems.map((item) => {
                const isSelected = selectedItemId === item.id
                return (
                  <ContentCard
                    key={item.id}
                    item={item}
                    variant="grid"
                    showEdit
                    onBlock={isAdmin ? setBlockTarget : undefined}
                    onNavigate={handleCardClick}
                    isSelected={isSelected}
                    cardRef={isSelected ? selectedCardRef : undefined}
                  />
                )
              })}
            </ContentGrid>
          ) : (
            <ContentList>
              {contentItems.map((item) => {
                const isSelected = selectedItemId === item.id
                return (
                  <ContentCard
                    key={item.id}
                    item={item}
                    variant="list"
                    showEdit
                    onBlock={isAdmin ? setBlockTarget : undefined}
                    onNavigate={handleCardClick}
                    isSelected={isSelected}
                    cardRef={isSelected ? selectedCardRef : undefined}
                  />
                )
              })}
            </ContentList>
          )}

          {/* Paged navigation */}
          {scrollMode === 'paged' && pagedData && pagedData.total > pageSize && (
            <div className="flex justify-center items-center gap-2 pt-4">
              <Button
                variant="outline"
                size="icon"
                disabled={browsePage === 1}
                onClick={() => {
                  updateUrl({ page: browsePage - 1 })
                  window.scrollTo(0, 0)
                }}
                className="rounded-xl"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="px-4 text-sm text-muted-foreground">
                Page {browsePage} of {Math.ceil(pagedData.total / pageSize)}
              </span>
              <Button
                variant="outline"
                size="icon"
                disabled={!pagedData.has_more}
                onClick={() => {
                  updateUrl({ page: browsePage + 1 })
                  window.scrollTo(0, 0)
                }}
                className="rounded-xl"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </>
      )}

      {/* Admin block dialog */}
      <Dialog
        open={!!blockTarget}
        onOpenChange={(open) => {
          if (!open) {
            setBlockTarget(null)
            setBlockReason('')
          }
        }}
      >
        <DialogContent onOpenAutoFocus={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>Block "{blockTarget?.title}"?</DialogTitle>
            <DialogDescription>
              This content will be hidden from all regular users. You can unblock it later from the Blocked Content
              view.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="browse-block-reason">Reason (optional)</Label>
            <Input
              id="browse-block-reason"
              placeholder="e.g. Copyright violation, inappropriate content..."
              value={blockReason}
              onChange={(e) => setBlockReason(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && blockTarget) {
                  blockMutation.mutate({ id: blockTarget.id, reason: blockReason })
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setBlockTarget(null)
                setBlockReason('')
              }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={blockMutation.isPending}
              onClick={() => blockTarget && blockMutation.mutate({ id: blockTarget.id, reason: blockReason })}
            >
              {blockMutation.isPending ? 'Blocking...' : 'Block Content'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// Infinite scroll sentinel for list view (grid view uses VirtualContentGrid)
function InfiniteScrollSentinel({
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  itemCount,
}: {
  hasNextPage: boolean | undefined
  isFetchingNextPage: boolean
  fetchNextPage: () => void
  itemCount: number
}) {
  const sentinelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { threshold: 0.1, rootMargin: '100px' },
    )
    const el = sentinelRef.current
    if (el) observer.observe(el)
    return () => { if (el) observer.unobserve(el) }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  return (
    <div ref={sentinelRef} className="flex justify-center py-8">
      {isFetchingNextPage && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Loading more...</span>
        </div>
      )}
      {!hasNextPage && itemCount > 0 && (
        <p className="text-sm text-muted-foreground">You've reached the end</p>
      )}
    </div>
  )
}
