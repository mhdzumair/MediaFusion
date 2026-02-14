import { useState, useEffect, useRef, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { 
  Film, 
  Tv, 
  Radio,
  Search,
  SortAsc,
  Plus,
  Heart,
  Bookmark,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import { 
  ContentCard, 
  ContentGrid,
  type ContentCardData,
} from '@/components/content'
import { useLibrary, useLibraryStats, useRemoveFromLibrary } from '@/hooks'
import type { CatalogType } from '@/hooks'

// Storage key for persisting selected item
const LIBRARY_SELECTED_ITEM_KEY = 'my_library_selected_item'

export function MyLibraryTab() {
  const [catalogType, setCatalogType] = useState<CatalogType | ''>('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<'added' | 'title'>('added')
  const [page, setPage] = useState(1)

  // Track selected item ID for highlighting
  const [selectedItemId, setSelectedItemId] = useState<number | null>(() => {
    try {
      const stored = sessionStorage.getItem(LIBRARY_SELECTED_ITEM_KEY)
      return stored ? parseInt(stored, 10) : null
    } catch {
      return null
    }
  })

  const selectedCardRef = useRef<HTMLDivElement>(null)
  const hasScrolledToSelected = useRef(false)

  const { data, isLoading } = useLibrary({
    catalog_type: catalogType || undefined,
    search: search || undefined,
    sort,
    page,
    page_size: 24,
  })
  const { data: stats } = useLibraryStats()
  const removeFromLibrary = useRemoveFromLibrary()

  // Transform library items to ContentCardData format and create ID mapping
  const { contentItems, libraryItemIdMap } = useMemo(() => {
    const idMap = new Map<number, number>() // media_id -> library_item_id
    const items: ContentCardData[] = (data?.items || []).map(item => {
      idMap.set(item.media_id, item.id)
      return {
        id: item.media_id, // Use media_id for navigation
        external_ids: item.external_ids,
        title: item.title,
        type: item.catalog_type,
        poster: item.poster,
      }
    })
    return { contentItems: items, libraryItemIdMap: idMap }
  }, [data?.items])

  const handleRemove = async (item: ContentCardData) => {
    const libraryItemId = libraryItemIdMap.get(item.id)
    if (libraryItemId) {
      await removeFromLibrary.mutateAsync(libraryItemId)
    }
  }

  // Store selected item when clicking on a card
  const handleCardClick = (item: ContentCardData) => {
    sessionStorage.setItem(LIBRARY_SELECTED_ITEM_KEY, item.id.toString())
    setSelectedItemId(item.id)
  }

  // Scroll to selected item and highlight after data loads
  useEffect(() => {
    if (!isLoading && data && selectedItemId && !hasScrolledToSelected.current) {
      const itemExists = contentItems.some(item => item.id === selectedItemId)
      if (!itemExists) {
        setSelectedItemId(null)
        sessionStorage.removeItem(LIBRARY_SELECTED_ITEM_KEY)
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
        setTimeout(() => {
          setSelectedItemId(null)
          sessionStorage.removeItem(LIBRARY_SELECTED_ITEM_KEY)
        }, 5000)
      }, 200)
      return () => clearTimeout(timer)
    }
  }, [isLoading, data, selectedItemId, contentItems])

  // Reset scroll flag when filters change
  useEffect(() => {
    hasScrolledToSelected.current = false
  }, [catalogType, search, sort, page])

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <Bookmark className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stats?.total_items ?? 0}</p>
                <p className="text-xs text-muted-foreground">Total Saved</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-blue-500/10">
                <Film className="h-4 w-4 text-blue-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stats?.movies ?? 0}</p>
                <p className="text-xs text-muted-foreground">Movies</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-emerald-500/10">
                <Tv className="h-4 w-4 text-emerald-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stats?.series ?? 0}</p>
                <p className="text-xs text-muted-foreground">Series</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-orange-500/10">
                <Radio className="h-4 w-4 text-orange-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{stats?.tv ?? 0}</p>
                <p className="text-xs text-muted-foreground">TV Channels</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search your library..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9 rounded-xl"
          />
        </div>
        
        <Select 
          value={catalogType || 'all'} 
          onValueChange={(v) => setCatalogType(v === 'all' ? '' : v as CatalogType)}
        >
          <SelectTrigger className="w-[130px] rounded-xl">
            <SelectValue placeholder="All Types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="movie">Movies</SelectItem>
            <SelectItem value="series">Series</SelectItem>
            <SelectItem value="tv">TV</SelectItem>
          </SelectContent>
        </Select>

        <Select value={sort} onValueChange={(v) => setSort(v as 'added' | 'title')}>
          <SelectTrigger className="w-[130px] rounded-xl">
            <SortAsc className="mr-2 h-4 w-4" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="added">Date Added</SelectItem>
            <SelectItem value="title">Title</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Library Items */}
      {isLoading ? (
        <ContentGrid>
          {[...Array(12)].map((_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="aspect-[2/3] rounded-xl" />
              <Skeleton className="h-4 w-3/4" />
            </div>
          ))}
        </ContentGrid>
      ) : !data?.items.length ? (
        <div className="text-center py-12">
          <Heart className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-muted-foreground">Your library is empty</p>
          <p className="text-sm text-muted-foreground mt-2">
            Browse content and add items to your library
          </p>
          <Button className="mt-4 rounded-xl" asChild>
            <Link to="/dashboard/library">
              <Plus className="mr-2 h-4 w-4" />
              Browse Content
            </Link>
          </Button>
        </div>
      ) : (
        <>
          <ContentGrid>
            {contentItems.map(item => {
              const isSelected = selectedItemId === item.id
              return (
                <ContentCard
                  key={item.id}
                  item={item}
                  variant="grid"
                  showType={true}
                  showEdit
                  onRemove={handleRemove}
                  onNavigate={handleCardClick}
                  isSelected={isSelected}
                  cardRef={isSelected ? selectedCardRef : undefined}
                />
              )
            })}
          </ContentGrid>

          {/* Pagination */}
          {data.total > 24 && (
            <div className="flex justify-center items-center gap-2 pt-4">
              <Button
                variant="outline"
                size="icon"
                disabled={page === 1}
                onClick={() => setPage(p => p - 1)}
                className="rounded-xl"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="px-4 text-sm text-muted-foreground">
                Page {page} of {Math.ceil(data.total / 24)}
              </span>
              <Button
                variant="outline"
                size="icon"
                disabled={!data.has_more}
                onClick={() => setPage(p => p + 1)}
                className="rounded-xl"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

