import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Skeleton } from '@/components/ui/skeleton'
import { Film, Loader2 } from 'lucide-react'
import {
  ContentCard,
  ContentGrid,
  ContentList,
  ContentFilters,
  type ContentCardData,
  type ViewMode,
} from '@/components/content'
import {
  useInfiniteCatalog,
  useAvailableCatalogs,
  useGenres,
  type CatalogType,
  type SortOption,
  type SortDirection,
} from '@/hooks'

// Storage key for persisting browse state
const BROWSE_STATE_KEY = 'browse_tab_state'
const BROWSE_SELECTED_ITEM_KEY = 'browse_selected_item'

interface BrowseState {
  catalogType: CatalogType
  selectedCatalog: string
  selectedGenre: string
  search: string
  sort: SortOption
  sortDir: SortDirection
  viewMode: ViewMode
  scrollPosition: number
  // TV-specific filters
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
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  // Get initial state from URL params or session storage
  const storedState = getStoredState()
  const urlType = searchParams.get('type') as CatalogType | null
  const urlGenre = searchParams.get('genre')
  const urlSearch = searchParams.get('search')

  const [catalogType, setCatalogType] = useState<CatalogType>(urlType || storedState.catalogType || 'movie')
  const [selectedCatalog, setSelectedCatalog] = useState<string>(storedState.selectedCatalog || '')
  const [selectedGenre, setSelectedGenre] = useState<string>(urlGenre || storedState.selectedGenre || '')
  const [search, setSearch] = useState(urlSearch || storedState.search || '')
  const [sort, setSort] = useState<SortOption>(storedState.sort || 'latest')
  const [sortDir, setSortDir] = useState<SortDirection>(storedState.sortDir || 'desc')
  const [viewMode, setViewMode] = useState<ViewMode>(storedState.viewMode || 'grid')
  const [isRestoring, setIsRestoring] = useState(true)

  // TV-specific filters
  const [workingOnly, setWorkingOnly] = useState(storedState.workingOnly || false)
  const [myChannels, setMyChannels] = useState(storedState.myChannels || false)

  // Track selected item ID for highlighting
  const [selectedItemId, setSelectedItemId] = useState<number | null>(() => {
    try {
      const stored = sessionStorage.getItem(BROWSE_SELECTED_ITEM_KEY)
      return stored ? parseInt(stored, 10) : null
    } catch {
      return null
    }
  })

  // Track previous URL params to detect external navigation
  const prevUrlParamsRef = useRef({ type: urlType, genre: urlGenre, search: urlSearch })

  // Refs
  const loadMoreRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const restoredScroll = useRef(false)
  const selectedCardRef = useRef<HTMLDivElement>(null)
  const hasScrolledToSelected = useRef(false)

  // Sync state from URL params when navigating from another page
  // This ensures clicking a genre link clears the search and vice versa
  useEffect(() => {
    const prevParams = prevUrlParamsRef.current
    const newType = searchParams.get('type') as CatalogType | null
    const newGenre = searchParams.get('genre')
    const newSearch = searchParams.get('search')

    // Detect if this is an external navigation (params changed from outside)
    const isExternalNavigation =
      newType !== prevParams.type || newGenre !== prevParams.genre || newSearch !== prevParams.search

    if (isExternalNavigation) {
      // Update state to match URL params exactly
      // If a param is not in URL, clear it (don't use stored state)
      if (newType) setCatalogType(newType)
      setSelectedGenre(newGenre || '')
      setSearch(newSearch || '')

      // Reset scroll position for fresh navigation
      window.scrollTo(0, 0)
      restoredScroll.current = true
    }

    // Update ref for next comparison
    prevUrlParamsRef.current = { type: newType, genre: newGenre, search: newSearch }
  }, [searchParams])

  const { data: availableCatalogs } = useAvailableCatalogs()
  const { data: genres } = useGenres(catalogType)
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } = useInfiniteCatalog(catalogType, {
    catalog: selectedCatalog || undefined,
    genre: selectedGenre || undefined,
    search: search || undefined,
    sort,
    sort_dir: sortDir,
    page_size: 24,
    // TV-specific filters (only applied when catalogType is 'tv')
    ...(catalogType === 'tv' && {
      working_only: workingOnly || undefined,
      my_channels: myChannels || undefined,
    }),
  })

  // Save state whenever it changes
  useEffect(() => {
    if (!isRestoring) {
      const scrollPosition = window.scrollY
      saveState({
        catalogType,
        selectedCatalog,
        selectedGenre,
        search,
        sort,
        sortDir,
        viewMode,
        scrollPosition,
        workingOnly,
        myChannels,
      })
    }
  }, [
    catalogType,
    selectedCatalog,
    selectedGenre,
    search,
    sort,
    sortDir,
    viewMode,
    isRestoring,
    workingOnly,
    myChannels,
  ])

  // Update URL params when filters change
  useEffect(() => {
    const params: Record<string, string> = { type: catalogType }
    if (selectedGenre) params.genre = selectedGenre
    if (search) params.search = search

    // Only update if params actually changed
    const currentType = searchParams.get('type')
    const currentGenre = searchParams.get('genre')
    const currentSearch = searchParams.get('search')

    if (currentType !== catalogType || currentGenre !== (selectedGenre || null) || currentSearch !== (search || null)) {
      setSearchParams(params, { replace: true })
    }
  }, [catalogType, selectedGenre, search, searchParams, setSearchParams])

  // Restore scroll position after data loads
  useEffect(() => {
    if (!isLoading && data && !restoredScroll.current && storedState.scrollPosition !== undefined) {
      // Delay to ensure DOM is rendered
      const timer = setTimeout(() => {
        window.scrollTo(0, storedState.scrollPosition!)
        restoredScroll.current = true
        setIsRestoring(false)
      }, 100)
      return () => clearTimeout(timer)
    } else if (!isLoading && data) {
      setIsRestoring(false)
    }
  }, [isLoading, data, storedState.scrollPosition])

  // Save scroll position on scroll (debounced)
  useEffect(() => {
    let timeoutId: ReturnType<typeof setTimeout>
    const handleScroll = () => {
      clearTimeout(timeoutId)
      timeoutId = setTimeout(() => {
        saveState({
          catalogType,
          selectedCatalog,
          selectedGenre,
          search,
          sort,
          sortDir,
          viewMode,
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
  }, [catalogType, selectedCatalog, selectedGenre, search, sort, sortDir, viewMode, workingOnly, myChannels])

  // Infinite scroll with IntersectionObserver
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const first = entries[0]
        if (first.isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { threshold: 0.1, rootMargin: '100px' },
    )

    const currentRef = loadMoreRef.current
    if (currentRef) {
      observer.observe(currentRef)
    }

    return () => {
      if (currentRef) {
        observer.unobserve(currentRef)
      }
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  // Handle catalog type change - reset filters but preserve in storage
  const handleCatalogTypeChange = useCallback((newType: CatalogType) => {
    setCatalogType(newType)
    setSelectedGenre('')
    setSelectedCatalog('')
    // Reset scroll position when changing type
    window.scrollTo(0, 0)
    restoredScroll.current = true
  }, [])

  const items = data?.pages.flatMap((page) => page.items) ?? []

  const catalogs =
    catalogType === 'movie'
      ? availableCatalogs?.movies
      : catalogType === 'series'
        ? availableCatalogs?.series
        : availableCatalogs?.tv

  // Transform items to ContentCardData format
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

  // Scroll to selected item and highlight after data loads
  useEffect(() => {
    if (!isLoading && data && selectedItemId && !hasScrolledToSelected.current) {
      // Check if selected item exists in current data
      const itemExists = contentItems.some((item) => item.id === selectedItemId)
      if (!itemExists) {
        // Item not found, clear selection
        setSelectedItemId(null)
        sessionStorage.removeItem(BROWSE_SELECTED_ITEM_KEY)
        return
      }

      const timer = setTimeout(() => {
        if (selectedCardRef.current) {
          selectedCardRef.current.scrollIntoView({
            behavior: 'smooth',
            block: 'center',
          })
        }
        hasScrolledToSelected.current = true
        // Clear selection after 5 seconds
        setTimeout(() => {
          setSelectedItemId(null)
          sessionStorage.removeItem(BROWSE_SELECTED_ITEM_KEY)
        }, 5000)
      }, 200)
      return () => clearTimeout(timer)
    }
  }, [isLoading, data, selectedItemId, contentItems])

  // Reset scroll flag when filters change
  useEffect(() => {
    hasScrolledToSelected.current = false
  }, [catalogType, selectedCatalog, selectedGenre, search, sort, sortDir])

  // Store selected item when clicking on a card
  const handleCardClick = (item: ContentCardData) => {
    sessionStorage.setItem(BROWSE_SELECTED_ITEM_KEY, item.id.toString())
    setSelectedItemId(item.id)
  }

  const handlePlay = (item: ContentCardData) => {
    // Save selected item and scroll position before navigating
    sessionStorage.setItem(BROWSE_SELECTED_ITEM_KEY, item.id.toString())
    saveState({
      catalogType,
      selectedCatalog,
      selectedGenre,
      search,
      sort,
      sortDir,
      viewMode,
      scrollPosition: window.scrollY,
      workingOnly,
      myChannels,
    })
    navigate(`/dashboard/content/${item.type}/${item.id}`)
  }

  return (
    <div ref={containerRef} className="space-y-6">
      {/* Filters */}
      <ContentFilters
        catalogType={catalogType}
        onCatalogTypeChange={handleCatalogTypeChange}
        search={search}
        onSearchChange={setSearch}
        selectedCatalog={selectedCatalog}
        catalogs={catalogs}
        onCatalogChange={setSelectedCatalog}
        selectedGenre={selectedGenre}
        genres={genres}
        onGenreChange={setSelectedGenre}
        sort={sort}
        onSortChange={(v) => setSort(v as SortOption)}
        sortDir={sortDir}
        onSortDirChange={setSortDir}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        // TV-specific filters
        workingOnly={workingOnly}
        onWorkingOnlyChange={setWorkingOnly}
        myChannels={myChannels}
        onMyChannelsChange={setMyChannels}
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
          {viewMode === 'grid' ? (
            <ContentGrid>
              {contentItems.map((item) => {
                const isSelected = selectedItemId === item.id
                return (
                  <ContentCard
                    key={item.id}
                    item={item}
                    variant="grid"
                    showEdit
                    onPlay={handlePlay}
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
                    onPlay={handlePlay}
                    onNavigate={handleCardClick}
                    isSelected={isSelected}
                    cardRef={isSelected ? selectedCardRef : undefined}
                  />
                )
              })}
            </ContentList>
          )}

          {/* Infinite scroll sentinel */}
          <div ref={loadMoreRef} className="flex justify-center py-8">
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
      )}
    </div>
  )
}
