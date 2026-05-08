import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import {
  Film,
  Tv,
  Radio,
  Search,
  Grid3X3,
  List,
  SortAsc,
  SortDesc,
  CheckCircle,
  User,
  X,
  Infinity as InfinityIcon,
  LayoutList,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { CatalogType, SortOption, SortDirection, GenreResponse, CatalogInfo } from '@/lib/api'

// ============================================
// Types
// ============================================

export type ViewMode = 'grid' | 'list'
export type SearchMode = 'title' | 'external_id'

export interface ContentFiltersProps {
  // Type filter
  catalogType?: CatalogType
  onCatalogTypeChange?: (type: CatalogType) => void
  showTypeFilter?: boolean

  // Search
  search?: string
  onSearchChange?: (search: string) => void
  searchPlaceholder?: string
  searchMode?: SearchMode
  onSearchModeChange?: (mode: SearchMode) => void
  showSearchMode?: boolean

  // Catalog filter
  selectedCatalog?: string
  catalogs?: CatalogInfo[]
  onCatalogChange?: (catalog: string) => void
  showCatalogFilter?: boolean

  // Genre filter
  selectedGenre?: string
  genres?: GenreResponse[]
  onGenreChange?: (genre: string) => void
  showGenreFilter?: boolean

  // Sort
  sort?: SortOption | 'added' | 'title'
  onSortChange?: (sort: SortOption | 'added' | 'title') => void
  sortOptions?: Array<{ value: string; label: string }>
  showSort?: boolean

  // Sort direction
  sortDir?: SortDirection
  onSortDirChange?: (dir: SortDirection) => void
  showSortDir?: boolean

  // View mode
  viewMode?: ViewMode
  onViewModeChange?: (mode: ViewMode) => void
  showViewMode?: boolean

  // TV-specific filters
  workingOnly?: boolean
  onWorkingOnlyChange?: (value: boolean) => void
  myChannels?: boolean
  onMyChannelsChange?: (value: boolean) => void

  // Page size
  pageSize?: 25 | 50 | 100
  onPageSizeChange?: (size: 25 | 50 | 100) => void

  // Scroll mode
  scrollMode?: 'infinite' | 'paged'
  onScrollModeChange?: (mode: 'infinite' | 'paged') => void

  className?: string
}

// Default sort options for browse
const DEFAULT_SORT_OPTIONS = [
  { value: 'latest', label: 'Latest' },
  { value: 'popular', label: 'Popular' },
  { value: 'rating', label: 'Rating' },
  { value: 'year', label: 'Year' },
  { value: 'release_date', label: 'Release Date' },
  { value: 'title', label: 'Title' },
]

// Sort options for library
const LIBRARY_SORT_OPTIONS = [
  { value: 'added', label: 'Date Added' },
  { value: 'title', label: 'Title' },
]

// ============================================
// Main Component
// ============================================

export function ContentFilters({
  // Type filter
  catalogType,
  onCatalogTypeChange,
  showTypeFilter = true,

  // Search
  search = '',
  onSearchChange,
  searchPlaceholder = 'Search...',
  searchMode,
  onSearchModeChange,
  showSearchMode = false,

  // Catalog filter
  selectedCatalog,
  catalogs,
  onCatalogChange,
  showCatalogFilter = true,

  // Genre filter
  selectedGenre,
  genres,
  onGenreChange,
  showGenreFilter = true,

  // Sort
  sort,
  onSortChange,
  sortOptions = DEFAULT_SORT_OPTIONS,
  showSort = true,

  // Sort direction
  sortDir = 'desc',
  onSortDirChange,
  showSortDir = true,

  // View mode
  viewMode = 'grid',
  onViewModeChange,
  showViewMode = true,

  // TV-specific filters
  workingOnly = false,
  onWorkingOnlyChange,
  myChannels = false,
  onMyChannelsChange,

  // Page size
  pageSize,
  onPageSizeChange,

  // Scroll mode
  scrollMode,
  onScrollModeChange,

  className,
}: ContentFiltersProps) {
  const showTvFilters = catalogType === 'tv' && (onWorkingOnlyChange || onMyChannelsChange)
  const hasDisplayControls = (showViewMode && !!onViewModeChange) || !!onScrollModeChange || pageSize !== undefined
  const hasTypeRow = (showTypeFilter && !!catalogType && !!onCatalogTypeChange) || showTvFilters || hasDisplayControls

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {/* Row 1: Type tabs (left) + display controls (right)
          On mobile: stacked (flex-col). On sm+: side-by-side (flex-row justify-between). */}
      {hasTypeRow && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          {/* Left: type tabs + TV-specific filters */}
          <div className="flex flex-wrap items-center gap-1.5">
            {showTypeFilter && catalogType && onCatalogTypeChange && (
              <>
                <Button
                  variant={catalogType === 'movie' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => onCatalogTypeChange('movie')}
                  className="rounded-xl"
                >
                  <Film className="mr-2 h-4 w-4" />
                  Movies
                </Button>
                <Button
                  variant={catalogType === 'series' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => onCatalogTypeChange('series')}
                  className="rounded-xl"
                >
                  <Tv className="mr-2 h-4 w-4" />
                  Series
                </Button>
                <Button
                  variant={catalogType === 'tv' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => onCatalogTypeChange('tv')}
                  className="rounded-xl"
                >
                  <Radio className="mr-2 h-4 w-4" />
                  TV
                </Button>
              </>
            )}

            {showTvFilters && (
              <div className="flex items-center gap-4 px-4 py-2 rounded-xl bg-muted/30 border border-border/50">
                {onWorkingOnlyChange && (
                  <div className="flex items-center gap-2">
                    <Switch id="working-only" checked={workingOnly} onCheckedChange={onWorkingOnlyChange} />
                    <Label htmlFor="working-only" className="flex items-center gap-1.5 text-sm cursor-pointer">
                      <CheckCircle className="h-4 w-4 text-emerald-500" />
                      Working Only
                    </Label>
                  </div>
                )}
                {onMyChannelsChange && (
                  <div className="flex items-center gap-2">
                    <Switch id="my-channels" checked={myChannels} onCheckedChange={onMyChannelsChange} />
                    <Label htmlFor="my-channels" className="flex items-center gap-1.5 text-sm cursor-pointer">
                      <User className="h-4 w-4 text-primary" />
                      My Channels
                    </Label>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Right: display controls — view mode, scroll mode, page size */}
          {hasDisplayControls && (
            <div className="flex items-center gap-2 self-start sm:self-auto">
              {showViewMode && onViewModeChange && (
                <div className="flex items-center border rounded-xl overflow-hidden">
                  <Button
                    variant={viewMode === 'grid' ? 'secondary' : 'ghost'}
                    size="icon"
                    className="rounded-none h-9 w-9"
                    onClick={() => onViewModeChange('grid')}
                    title="Grid view"
                  >
                    <Grid3X3 className="h-4 w-4" />
                  </Button>
                  <Button
                    variant={viewMode === 'list' ? 'secondary' : 'ghost'}
                    size="icon"
                    className="rounded-none h-9 w-9"
                    onClick={() => onViewModeChange('list')}
                    title="List view"
                  >
                    <List className="h-4 w-4" />
                  </Button>
                </div>
              )}

              {scrollMode && onScrollModeChange && (
                <div className="flex items-center border rounded-xl overflow-hidden">
                  <Button
                    variant={scrollMode === 'infinite' ? 'secondary' : 'ghost'}
                    size="icon"
                    className="rounded-none h-9 w-9"
                    onClick={() => onScrollModeChange('infinite')}
                    title="Infinite scroll"
                  >
                    <InfinityIcon className="h-4 w-4" />
                  </Button>
                  <Button
                    variant={scrollMode === 'paged' ? 'secondary' : 'ghost'}
                    size="icon"
                    className="rounded-none h-9 w-9"
                    onClick={() => onScrollModeChange('paged')}
                    title="Page navigation"
                  >
                    <LayoutList className="h-4 w-4" />
                  </Button>
                </div>
              )}

              {pageSize !== undefined && onPageSizeChange && (
                <Select
                  value={pageSize.toString()}
                  onValueChange={(v) => onPageSizeChange(parseInt(v) as 25 | 50 | 100)}
                >
                  <SelectTrigger className="w-[75px] rounded-xl" title="Items per page">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="25">25</SelectItem>
                    <SelectItem value="50">50</SelectItem>
                    <SelectItem value="100">100</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </div>
          )}
        </div>
      )}

      {/* Row 2: Search + content filters + sort — wraps naturally at any width */}
      <div className="flex flex-wrap items-center gap-2">
        {onSearchChange && (
          <div className="relative flex-1 min-w-[160px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder={searchPlaceholder}
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
              className={cn('pl-9 rounded-xl', showSearchMode && searchMode && onSearchModeChange ? 'pr-28' : 'pr-8')}
            />
            <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
              {showSearchMode && searchMode && onSearchModeChange && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => onSearchModeChange(searchMode === 'title' ? 'external_id' : 'title')}
                  className="h-6 rounded-md px-2 text-[11px] leading-none"
                  title={searchMode === 'title' ? 'Switch to external ID search' : 'Switch to title search'}
                >
                  {searchMode === 'title' ? 'Title' : 'Ext ID'}
                </Button>
              )}
              {search && (
                <button
                  type="button"
                  onClick={() => onSearchChange('')}
                  className="p-1 rounded-full hover:bg-muted transition-colors"
                  title="Clear search"
                >
                  <X className="h-4 w-4 text-muted-foreground hover:text-foreground" />
                </button>
              )}
            </div>
          </div>
        )}

        {showCatalogFilter && catalogs && onCatalogChange && (
          <Select value={selectedCatalog || 'all'} onValueChange={(v) => onCatalogChange(v === 'all' ? '' : v)}>
            <SelectTrigger className="w-[140px] sm:w-[160px] rounded-xl">
              <SelectValue placeholder="All Catalogs" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Catalogs</SelectItem>
              {catalogs.map((cat) => (
                <SelectItem key={cat.name} value={cat.name}>
                  {cat.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {showGenreFilter && genres && onGenreChange && (
          <Select value={selectedGenre || 'all'} onValueChange={(v) => onGenreChange(v === 'all' ? '' : v)}>
            <SelectTrigger className="w-[110px] sm:w-[120px] rounded-xl">
              <SelectValue placeholder="All Genres" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Genres</SelectItem>
              {genres.map((g) => (
                <SelectItem key={g.id} value={g.name}>
                  {g.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {showSort && sort && onSortChange && (
          <div className="flex items-center gap-1">
            <Select value={sort} onValueChange={onSortChange}>
              <SelectTrigger className="w-[110px] sm:w-[130px] rounded-xl">
                {sortDir === 'desc' ? <SortDesc className="mr-2 h-4 w-4" /> : <SortAsc className="mr-2 h-4 w-4" />}
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {sortOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {showSortDir && onSortDirChange && (
              <Button
                variant="outline"
                size="icon"
                className="h-9 w-9 rounded-xl shrink-0"
                onClick={() => onSortDirChange(sortDir === 'desc' ? 'asc' : 'desc')}
                title={sortDir === 'desc' ? 'Descending (click for ascending)' : 'Ascending (click for descending)'}
              >
                {sortDir === 'desc' ? <SortDesc className="h-4 w-4" /> : <SortAsc className="h-4 w-4" />}
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// Re-export for convenience
export { DEFAULT_SORT_OPTIONS, LIBRARY_SORT_OPTIONS }
