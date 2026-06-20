import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams, useLocation } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Film, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, ShieldAlert, Trash2 } from 'lucide-react'
import {
  ContentCard,
  ContentGrid,
  ContentList,
  ContentFilters,
  type ContentCardData,
  type ViewMode,
  type SearchMode,
} from '@/components/content'
import {
  useCatalogList,
  useAvailableCatalogs,
  useGenres,
  catalogKeys,
  type CatalogType,
  type SortOption,
  type SortDirection,
} from '@/hooks'
import { adminApi } from '@/lib/api/admin'
import { useRole } from '@/hooks/useRole'
import { useToast } from '@/hooks/use-toast'
import { saveContentDetailReturnUrl } from '../browseNavigation'

// Storage key for persisting browse state
const BROWSE_STATE_KEY = 'browse_tab_state'
const BROWSE_SELECTED_ITEM_KEY = 'browse_selected_item'

type PageSize = 25 | 50 | 100

interface BrowseState {
  catalogType: CatalogType
  selectedCatalog: string
  sort: SortOption
  sortDir: SortDirection
  viewMode: ViewMode
  pageSize: PageSize
  page: number
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

// ---------------------------------------------------------------------------
// Pagination component
// ---------------------------------------------------------------------------

interface BrowsePaginationProps {
  currentPage: number
  totalPages: number
  totalItems: number
  pageSize: number
  onPageChange: (page: number) => void
}

function BrowsePagination({ currentPage, totalPages, totalItems, pageSize, onPageChange }: BrowsePaginationProps) {
  if (totalPages <= 1) return null

  const startItem = (currentPage - 1) * pageSize + 1
  const endItem = Math.min(currentPage * pageSize, totalItems)

  // Build page numbers array with ellipsis markers
  const pages: (number | '...')[] = []
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i)
  } else if (currentPage <= 4) {
    for (let i = 1; i <= 5; i++) pages.push(i)
    pages.push('...')
    pages.push(totalPages)
  } else if (currentPage >= totalPages - 3) {
    pages.push(1)
    pages.push('...')
    for (let i = totalPages - 4; i <= totalPages; i++) pages.push(i)
  } else {
    pages.push(1)
    pages.push('...')
    for (let i = currentPage - 1; i <= currentPage + 1; i++) pages.push(i)
    pages.push('...')
    pages.push(totalPages)
  }

  const btnBase =
    'inline-flex items-center justify-center h-8 min-w-[2rem] px-2 text-sm rounded border transition-colors select-none'
  const btnActive = 'bg-primary text-primary-foreground border-primary font-medium'
  const btnInactive = 'bg-transparent text-foreground border-border hover:bg-accent cursor-pointer'
  const btnDisabled = 'opacity-40 cursor-not-allowed bg-transparent border-border text-muted-foreground'

  return (
    <div className="flex items-center justify-between text-sm text-muted-foreground py-1">
      <span className="hidden sm:block">
        Showing {startItem.toLocaleString()}–{endItem.toLocaleString()} of {totalItems.toLocaleString()}
      </span>
      <div className="flex items-center gap-1 mx-auto sm:mx-0">
        {/* First */}
        <button
          className={`${btnBase} ${currentPage === 1 ? btnDisabled : btnInactive}`}
          disabled={currentPage === 1}
          onClick={() => onPageChange(1)}
          title="First page"
        >
          <ChevronsLeft className="h-3.5 w-3.5" />
        </button>
        {/* Prev */}
        <button
          className={`${btnBase} ${currentPage === 1 ? btnDisabled : btnInactive}`}
          disabled={currentPage === 1}
          onClick={() => onPageChange(currentPage - 1)}
          title="Previous page"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>

        {pages.map((p, i) =>
          p === '...' ? (
            <span key={`ellipsis-${i}`} className="px-1 text-muted-foreground">
              …
            </span>
          ) : (
            <button
              key={p}
              className={`${btnBase} ${p === currentPage ? btnActive : btnInactive}`}
              onClick={() => onPageChange(p as number)}
            >
              {p}
            </button>
          ),
        )}

        {/* Next */}
        <button
          className={`${btnBase} ${currentPage === totalPages ? btnDisabled : btnInactive}`}
          disabled={currentPage === totalPages}
          onClick={() => onPageChange(currentPage + 1)}
          title="Next page"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        {/* Last */}
        <button
          className={`${btnBase} ${currentPage === totalPages ? btnDisabled : btnInactive}`}
          disabled={currentPage === totalPages}
          onClick={() => onPageChange(totalPages)}
          title="Last page"
        >
          <ChevronsRight className="h-3.5 w-3.5" />
        </button>
      </div>
      <span className="hidden sm:block text-right opacity-0 pointer-events-none">
        Showing {startItem}–{endItem} of {totalItems}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BrowseTab
// ---------------------------------------------------------------------------

export function BrowseTab() {
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const { isAdmin, isModerator } = useRole()
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [blockTarget, setBlockTarget] = useState<ContentCardData | null>(null)
  const [blockReason, setBlockReason] = useState('')

  // Bulk state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [bulkMode, setBulkMode] = useState(false)
  const [bulkBlockReason, setBulkBlockReason] = useState('')
  const [bulkActionDialog, setBulkActionDialog] = useState<'block' | 'delete' | null>(null)

  const blockMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      adminApi.blockMedia(id, { reason: reason || undefined }),
    onSuccess: (data) => {
      toast({ title: 'Content blocked', description: data.message })
      setBlockTarget(null)
      setBlockReason('')
      queryClient.invalidateQueries({ queryKey: catalogKeys.all })
    },
    onError: (error: Error) => {
      toast({ variant: 'destructive', title: 'Block failed', description: error.message })
    },
  })

  const bulkBlockMutation = useMutation({
    mutationFn: ({ ids, reason }: { ids: number[]; reason: string }) =>
      adminApi.bulkBlockMedia(ids, reason || undefined),
    onSuccess: (data) => {
      toast({ title: 'Bulk block complete', description: data.message })
      setBulkActionDialog(null)
      setSelectedIds(new Set())
      setBulkMode(false)
      setBulkBlockReason('')
      queryClient.invalidateQueries({ queryKey: catalogKeys.all })
    },
    onError: (e: Error) => toast({ variant: 'destructive', title: 'Bulk block failed', description: e.message }),
  })

  const bulkDeleteMutation = useMutation({
    mutationFn: (ids: number[]) => adminApi.bulkDeleteMedia(ids),
    onSuccess: (data) => {
      toast({ title: 'Bulk delete complete', description: data.message })
      setBulkActionDialog(null)
      setSelectedIds(new Set())
      setBulkMode(false)
      queryClient.invalidateQueries({ queryKey: catalogKeys.all })
    },
    onError: (e: Error) => toast({ variant: 'destructive', title: 'Bulk delete failed', description: e.message }),
  })

  // ---------------------------------------------------------------------------
  // All URL-synced state is derived directly from searchParams.
  // There is no useState / useEffect for these — the URL IS the state.
  // ---------------------------------------------------------------------------
  const storedState = getStoredState()

  const catalogType: CatalogType = (searchParams.get('type') as CatalogType) || storedState.catalogType || 'movie'
  const selectedGenre = searchParams.get('genre') || ''
  const urlSearchMode = searchParams.get('search_mode') as SearchMode | null
  const urlExternalId = searchParams.get('external_id') || ''
  const urlSearch = searchParams.get('search') || ''
  const searchMode: SearchMode = urlSearchMode ?? (urlExternalId ? 'external_id' : 'title')
  const search = urlExternalId || urlSearch
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
  const hasRestoredBrowsePage = useRef(false)

  // ---------------------------------------------------------------------------
  // Single URL mutator — all filter/pagination writes go through here.
  // Using the functional form of setSearchParams guarantees we always build
  // on top of the latest URL state, never clobbering params we didn't intend.
  // ---------------------------------------------------------------------------
  const updateUrl = useCallback(
    (
      updates: Partial<{
        type: CatalogType
        genre: string
        search: string
        searchMode: SearchMode
        page: number
      }>,
      opts: { resetPage?: boolean } = {},
    ) => {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev)

          if (updates.type !== undefined) params.set('type', updates.type)

          if (updates.genre !== undefined) {
            if (updates.genre) {
              params.set('genre', updates.genre)
            } else {
              params.delete('genre')
            }
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
            if (updates.page > 1) {
              params.set('page', String(updates.page))
            } else {
              params.delete('page')
            }
          } else if (opts.resetPage) {
            params.delete('page')
          }

          return params
        },
        { replace: true },
      )
    },
    [searchMode, search, setSearchParams],
  )

  // One-shot URL hydration so a stored catalogType materialises in URL on bare /library visit
  useEffect(() => {
    if (!searchParams.get('type') && storedState.catalogType) {
      updateUrl({ type: storedState.catalogType })
    }
    // Strip legacy scroll_mode param from bookmarks
    if (searchParams.get('scroll_mode')) {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev)
          params.delete('scroll_mode')
          return params
        },
        { replace: true },
      )
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

  const { data: pagedData, isLoading } = useCatalogList(catalogType, { ...commonParams, page: browsePage })
  const hasData = !!pagedData

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
        pageSize,
        page: browsePage,
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
    pageSize,
    browsePage,
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
          pageSize,
          page: browsePage,
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
  }, [catalogType, selectedCatalog, sort, sortDir, viewMode, pageSize, browsePage, workingOnly, myChannels])

  // Restore browse page when returning from detail (URL may omit ?page=)
  useEffect(() => {
    if (!selectedItemId || hasRestoredBrowsePage.current) return
    const storedPage = storedState.page ?? 1
    if (storedPage > 1 && browsePage !== storedPage) {
      hasRestoredBrowsePage.current = true
      updateUrl({ page: storedPage })
      setRestoredScroll(true)
    }
  }, [selectedItemId, browsePage, storedState.page, updateUrl])

  // Restore scroll position after data loads (skip when highlighting a returned item)
  useEffect(() => {
    if (!isLoading && hasData && !restoredScroll && !selectedItemId && storedState.scrollPosition !== undefined) {
      const timer = setTimeout(() => {
        window.scrollTo(0, storedState.scrollPosition!)
        setRestoredScroll(true)
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [isLoading, hasData, storedState.scrollPosition, restoredScroll, selectedItemId])

  const items = pagedData?.items.filter(Boolean) ?? []

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

  const itemExists = contentItems.some((item) => item.id === selectedItemId)

  // Clear selection only after the stored page is loaded and the item is still missing
  useEffect(() => {
    if (!isLoading && hasData && selectedItemId && !itemExists) {
      const storedPage = storedState.page ?? 1
      if (browsePage === storedPage) {
        setSelectedItemId(null)
      }
    }
  }, [isLoading, hasData, selectedItemId, itemExists, browsePage, storedState.page])

  useEffect(() => {
    if (selectedItemId === null) {
      sessionStorage.removeItem(BROWSE_SELECTED_ITEM_KEY)
    }
  }, [selectedItemId])

  // Scroll to selected item after returning from detail view
  useEffect(() => {
    if (!isLoading && hasData && selectedItemId && itemExists && !hasScrolledToSelected.current) {
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
  }, [isLoading, hasData, selectedItemId, itemExists, contentItems])

  useEffect(() => {
    hasScrolledToSelected.current = false
    hasRestoredBrowsePage.current = false
  }, [catalogType, selectedCatalog, selectedGenre, search, searchMode, sort, sortDir])

  // Clear bulk selection when page changes
  useEffect(() => {
    setSelectedIds(new Set())
  }, [browsePage, catalogType, selectedCatalog, selectedGenre, search, searchMode, sort, sortDir, pageSize])

  const handleCardClick = (item: ContentCardData) => {
    saveContentDetailReturnUrl(location.pathname, location.search)
    saveState({
      catalogType,
      selectedCatalog,
      sort,
      sortDir,
      viewMode,
      pageSize,
      page: browsePage,
      scrollPosition: window.scrollY,
      workingOnly,
      myChannels,
    })
    sessionStorage.setItem(BROWSE_SELECTED_ITEM_KEY, item.id.toString())
    setSelectedItemId(item.id)
  }

  const toggleItemSelection = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const allPageSelected = contentItems.length > 0 && contentItems.every((item) => selectedIds.has(item.id))

  const toggleSelectAll = () => {
    if (allPageSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(contentItems.map((item) => item.id)))
    }
  }

  const totalPages = pagedData ? Math.ceil(pagedData.total / pageSize) : 1

  const handlePageChange = (page: number) => {
    updateUrl({ page })
    window.scrollTo(0, 0)
  }

  const canUseBulk = isAdmin || isModerator

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

      {/* Bulk toolbar */}
      {canUseBulk && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant={bulkMode ? 'secondary' : 'outline'}
            size="sm"
            onClick={() => {
              setBulkMode((prev) => !prev)
              setSelectedIds(new Set())
            }}
          >
            {bulkMode ? 'Exit Select' : 'Select'}
          </Button>

          {bulkMode && (
            <>
              <div className="flex items-center gap-2">
                <Checkbox id="select-all-page" checked={allPageSelected} onCheckedChange={toggleSelectAll} />
                <label htmlFor="select-all-page" className="text-sm cursor-pointer select-none">
                  Select all on page
                </label>
              </div>

              {selectedIds.size > 0 && (
                <span className="text-sm text-muted-foreground">{selectedIds.size} selected</span>
              )}

              <Button
                variant="outline"
                size="sm"
                disabled={selectedIds.size === 0}
                onClick={() => setBulkActionDialog('block')}
                className="gap-1"
              >
                <ShieldAlert className="h-4 w-4" />
                Block
              </Button>

              {isAdmin && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={selectedIds.size === 0}
                  onClick={() => setBulkActionDialog('delete')}
                  className="gap-1 text-destructive hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                  Delete
                </Button>
              )}

              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setBulkMode(false)
                  setSelectedIds(new Set())
                }}
              >
                Cancel
              </Button>
            </>
          )}
        </div>
      )}

      {/* Top pagination */}
      {pagedData && pagedData.total > pageSize && (
        <BrowsePagination
          currentPage={browsePage}
          totalPages={totalPages}
          totalItems={pagedData.total}
          pageSize={pageSize}
          onPageChange={handlePageChange}
        />
      )}

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
                const isBulkSelected = selectedIds.has(item.id)
                return bulkMode ? (
                  <div
                    key={item.id}
                    className={`relative rounded-xl transition-all ${isBulkSelected ? 'ring-2 ring-primary ring-offset-1 ring-offset-background' : ''}`}
                  >
                    {/* Full-area click capture — sits above card, below checkbox */}
                    <div
                      className="absolute inset-0 z-10 cursor-pointer"
                      onClick={() => toggleItemSelection(item.id)}
                    />
                    <div className="absolute top-2 left-2 z-20">
                      <Checkbox
                        checked={isBulkSelected}
                        onCheckedChange={() => toggleItemSelection(item.id)}
                        className="bg-background/80 backdrop-blur-sm border-white/60"
                      />
                    </div>
                    <ContentCard
                      item={item}
                      variant="grid"
                      showEdit={false}
                      onNavigate={undefined}
                      isSelected={false}
                    />
                  </div>
                ) : (
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
                const isBulkSelected = selectedIds.has(item.id)
                return bulkMode ? (
                  <div
                    key={item.id}
                    className={`relative rounded-xl transition-all ${isBulkSelected ? 'ring-2 ring-primary ring-offset-1 ring-offset-background' : ''}`}
                  >
                    {/* Full-area click capture — sits above card, below checkbox */}
                    <div
                      className="absolute inset-0 z-10 cursor-pointer"
                      onClick={() => toggleItemSelection(item.id)}
                    />
                    <div className="absolute top-1/2 left-3 z-20 -translate-y-1/2">
                      <Checkbox
                        checked={isBulkSelected}
                        onCheckedChange={() => toggleItemSelection(item.id)}
                        className="bg-background/80 backdrop-blur-sm border-white/60"
                      />
                    </div>
                    <div className="pl-10">
                      <ContentCard
                        item={item}
                        variant="list"
                        showEdit={false}
                        onNavigate={undefined}
                        isSelected={false}
                      />
                    </div>
                  </div>
                ) : (
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

          {/* Bottom pagination */}
          {pagedData && pagedData.total > pageSize && (
            <BrowsePagination
              currentPage={browsePage}
              totalPages={totalPages}
              totalItems={pagedData.total}
              pageSize={pageSize}
              onPageChange={handlePageChange}
            />
          )}
        </>
      )}

      {/* Admin single-block dialog */}
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

      {/* Bulk block dialog */}
      <AlertDialog open={bulkActionDialog === 'block'} onOpenChange={(open) => !open && setBulkActionDialog(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Block {selectedIds.size} item{selectedIds.size !== 1 ? 's' : ''}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              These items will be hidden from all regular users. You can unblock them later from the Blocked Content
              view.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="bulk-block-reason">Reason (optional)</Label>
            <Input
              id="bulk-block-reason"
              placeholder="e.g. Copyright violation, inappropriate content..."
              value={bulkBlockReason}
              onChange={(e) => setBulkBlockReason(e.target.value)}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setBulkBlockReason('')}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={bulkBlockMutation.isPending}
              onClick={() => bulkBlockMutation.mutate({ ids: Array.from(selectedIds), reason: bulkBlockReason })}
            >
              {bulkBlockMutation.isPending ? 'Blocking...' : 'Confirm Block'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Bulk delete dialog */}
      <AlertDialog open={bulkActionDialog === 'delete'} onOpenChange={(open) => !open && setBulkActionDialog(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {selectedIds.size} item{selectedIds.size !== 1 ? 's' : ''}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. These items and all their associated data will be permanently deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={bulkDeleteMutation.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => bulkDeleteMutation.mutate(Array.from(selectedIds))}
            >
              {bulkDeleteMutation.isPending ? 'Deleting...' : 'Confirm Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
