import { useState, useRef, useMemo } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import {
  Sparkles,
  ChevronLeft,
  ChevronRight,
  Settings2,
  Loader2,
  Search,
  X,
  ChevronRight as LoadMoreIcon,
} from 'lucide-react'
import { useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { Poster } from '@/components/ui/poster'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useProfiles } from '@/hooks/useProfiles'
import { useRpdb } from '@/contexts/RpdbContext'
import {
  useDiscoverTrending,
  useDiscoverList,
  useWatchProviders,
  useDiscoverProviderFeed,
  useDiscoverAnime,
  useDiscoverSearch,
  useDiscoverTvdb,
  useDiscoverMdblist,
} from '@/hooks/useDiscover'
import { userMetadataApi } from '@/lib/api/user-metadata'
import { scrapersApi } from '@/lib/api/scrapers'
import { metadataApi } from '@/lib/api/metadata'
import type { DiscoverItem, DiscoverPage, DiscoverDbEntry } from '@/lib/api/discover'
import { discoverDbKey } from '@/lib/api/discover'
import type { MDBListItem } from '@/pages/Configure/components/types'
import type { ImportProvider } from '@/lib/api/user-metadata'
import { useDebounce } from '@/hooks/useDebounce'

// ─── Types ────────────────────────────────────────────────────────────────────

type InfiniteDiscoverResult = ReturnType<typeof useDiscoverTrending>

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getCurrentSeason(): { season: string; year: number } {
  const month = new Date().getMonth() + 1
  const year = new Date().getFullYear()
  let season = 'winter'
  if (month >= 4 && month <= 6) season = 'spring'
  else if (month >= 7 && month <= 9) season = 'summer'
  else if (month >= 10 && month <= 12) season = 'fall'
  return { season, year }
}

/** Flatten all pages from an infinite query into a single items + merged db_index */
function flattenPages(data?: InfiniteData<DiscoverPage>) {
  if (!data) return { items: [], db_index: {} as Record<string, DiscoverDbEntry> }
  return {
    items: data.pages.flatMap((p) => p.items),
    db_index: data.pages.reduce((acc, p) => ({ ...acc, ...p.db_index }), {} as Record<string, DiscoverDbEntry>),
  }
}

const LANG_ANY = '__any__'

const LANGUAGES = [
  { code: LANG_ANY, label: 'Any Language' },
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Spanish' },
  { code: 'fr', label: 'French' },
  { code: 'de', label: 'German' },
  { code: 'ja', label: 'Japanese' },
  { code: 'ko', label: 'Korean' },
  { code: 'zh', label: 'Chinese (Mandarin)' },
  { code: 'hi', label: 'Hindi' },
  { code: 'pt', label: 'Portuguese' },
  { code: 'it', label: 'Italian' },
  { code: 'ar', label: 'Arabic' },
  { code: 'ru', label: 'Russian' },
  { code: 'tr', label: 'Turkish' },
  { code: 'ta', label: 'Tamil' },
  { code: 'te', label: 'Telugu' },
  { code: 'ml', label: 'Malayalam' },
  { code: 'bn', label: 'Bengali' },
  { code: 'th', label: 'Thai' },
  { code: 'pl', label: 'Polish' },
  { code: 'nl', label: 'Dutch' },
  { code: 'sv', label: 'Swedish' },
  { code: 'da', label: 'Danish' },
  { code: 'fi', label: 'Finnish' },
  { code: 'no', label: 'Norwegian' },
  { code: 'id', label: 'Indonesian' },
  { code: 'ms', label: 'Malay' },
  { code: 'vi', label: 'Vietnamese' },
  { code: 'he', label: 'Hebrew' },
  { code: 'uk', label: 'Ukrainian' },
]

const REGIONS = [
  { code: 'US', name: 'United States' },
  { code: 'GB', name: 'United Kingdom' },
  { code: 'CA', name: 'Canada' },
  { code: 'AU', name: 'Australia' },
  { code: 'IN', name: 'India' },
  { code: 'DE', name: 'Germany' },
  { code: 'FR', name: 'France' },
  { code: 'JP', name: 'Japan' },
  { code: 'KR', name: 'South Korea' },
  { code: 'BR', name: 'Brazil' },
  { code: 'MX', name: 'Mexico' },
  { code: 'ES', name: 'Spain' },
  { code: 'IT', name: 'Italy' },
  { code: 'NL', name: 'Netherlands' },
  { code: 'SE', name: 'Sweden' },
  { code: 'NO', name: 'Norway' },
  { code: 'DK', name: 'Denmark' },
  { code: 'FI', name: 'Finland' },
  { code: 'PL', name: 'Poland' },
  { code: 'AT', name: 'Austria' },
  { code: 'CH', name: 'Switzerland' },
  { code: 'BE', name: 'Belgium' },
  { code: 'PT', name: 'Portugal' },
  { code: 'AR', name: 'Argentina' },
  { code: 'CO', name: 'Colombia' },
  { code: 'CL', name: 'Chile' },
  { code: 'ZA', name: 'South Africa' },
  { code: 'NG', name: 'Nigeria' },
  { code: 'TR', name: 'Turkey' },
  { code: 'SA', name: 'Saudi Arabia' },
  { code: 'AE', name: 'United Arab Emirates' },
  { code: 'SG', name: 'Singapore' },
  { code: 'PH', name: 'Philippines' },
  { code: 'ID', name: 'Indonesia' },
  { code: 'TH', name: 'Thailand' },
  { code: 'TW', name: 'Taiwan' },
  { code: 'HK', name: 'Hong Kong' },
  { code: 'NZ', name: 'New Zealand' },
  { code: 'IE', name: 'Ireland' },
  { code: 'RU', name: 'Russia' },
]

// ─── Card ─────────────────────────────────────────────────────────────────────

interface DiscoverCardProps {
  title: string
  year?: string | number | null
  poster?: string | null
  metaId: string
  catalogType: 'movie' | 'series'
  rpdbApiKey?: string | null
  isLoading?: boolean
  isInDb?: boolean
  onClick: () => void
}

function DiscoverCard({
  title,
  year,
  poster,
  metaId,
  catalogType,
  rpdbApiKey,
  isLoading,
  isInDb,
  onClick,
}: DiscoverCardProps) {
  // For items in DB: normal Poster chain (RPDB → poster → MF endpoint).
  // For items NOT in DB: use overridePoster so MF's /poster endpoint is never hit
  // (the item hasn't been imported yet — it would always 404).
  // RPDB still works when metaId is a tt* id (in-DB items with imdb_id).
  const overridePoster = !isInDb ? (poster ?? undefined) : undefined

  return (
    <div
      className="relative flex-shrink-0 w-[140px] group cursor-pointer select-none"
      onClick={isLoading ? undefined : onClick}
    >
      <div className="relative rounded-lg overflow-hidden">
        <Poster
          metaId={metaId}
          catalogType={catalogType}
          poster={poster}
          rpdbApiKey={rpdbApiKey}
          title={title}
          overridePoster={overridePoster}
          className="transition-transform duration-200 group-hover:scale-105"
        />
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/60 rounded-lg z-10">
            <Loader2 className="h-7 w-7 animate-spin text-white" />
          </div>
        )}
        {!isLoading && !isInDb && (
          <div className="absolute bottom-1.5 right-1.5">
            <Badge className="text-[10px] px-1.5 py-0 bg-primary/90 shadow">
              <Sparkles className="h-2.5 w-2.5 mr-0.5" /> Add
            </Badge>
          </div>
        )}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/25 transition-colors rounded-lg" />
      </div>
      <p className="mt-1.5 text-xs font-medium line-clamp-2 leading-tight">{title}</p>
      {year && <p className="text-[11px] text-muted-foreground">{year}</p>}
    </div>
  )
}

// ─── Horizontal Row (infinite) ────────────────────────────────────────────────

interface DiscoverRowProps {
  title: string
  query: InfiniteDiscoverResult
  onImport: (item: DiscoverItem) => void
  loadingKey?: string | null
  rpdbApiKey?: string | null
  onNavigate: (mediaId: number, mediaType: string) => void
}

function DiscoverRow({ title, query, onImport, loadingKey, rpdbApiKey, onNavigate }: DiscoverRowProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const { items, db_index } = useMemo(() => flattenPages(query.data), [query.data])

  const scroll = (dir: 'left' | 'right') => {
    scrollRef.current?.scrollBy({ left: dir === 'right' ? 600 : -600, behavior: 'smooth' })
  }

  if (query.isLoading) {
    return (
      <section className="space-y-2">
        <h3 className="font-semibold text-sm">{title}</h3>
        <div className="flex gap-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} className="flex-shrink-0 w-[140px] aspect-[2/3] rounded-lg" />
          ))}
        </div>
      </section>
    )
  }

  if (!items.length) return null

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm">{title}</h3>
        <div className="flex gap-1">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => scroll('left')}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => scroll('right')}>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div
        ref={scrollRef}
        className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1"
        style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
      >
        {items.map((item, i) => {
          const key = discoverDbKey(item)
          const entry = db_index[key]
          const metaId = entry?.imdb_id || `${item.provider}:${item.external_id}`

          return (
            <DiscoverCard
              key={`${key}:${i}`}
              title={item.title}
              year={item.year}
              poster={item.poster}
              metaId={metaId}
              catalogType={item.media_type === 'movie' ? 'movie' : 'series'}
              rpdbApiKey={entry?.imdb_id ? rpdbApiKey : null}
              isLoading={loadingKey === key}
              isInDb={!!entry}
              onClick={() => (entry ? onNavigate(entry.id, item.media_type) : onImport(item))}
            />
          )
        })}

        {/* Load more card */}
        {query.hasNextPage && (
          <div
            className="relative flex-shrink-0 w-[140px] group cursor-pointer select-none"
            onClick={() => !query.isFetchingNextPage && query.fetchNextPage()}
          >
            <div className="aspect-[2/3] rounded-lg bg-muted/50 border border-border/50 flex flex-col items-center justify-center gap-2 hover:bg-muted/80 transition-colors">
              {query.isFetchingNextPage ? (
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              ) : (
                <>
                  <LoadMoreIcon className="h-6 w-6 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground font-medium">Load more</span>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

// ─── MDBList Row ──────────────────────────────────────────────────────────────

interface MDBListRowProps {
  list: MDBListItem
  enabled: boolean
  rpdbApiKey?: string | null
  onImport: (item: DiscoverItem) => void
  loadingKey?: string | null
  onNavigate: (mediaId: number, mediaType: string) => void
}

function MDBListRow({ list, enabled, rpdbApiKey, onImport, loadingKey, onNavigate }: MDBListRowProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const catalogType = list.ct === 'movie' ? 'movie' : ('series' as const)
  const query = useDiscoverMdblist(list.i, catalogType, enabled)
  const { items, db_index } = useMemo(() => flattenPages(query.data), [query.data])

  const scroll = (dir: 'left' | 'right') => {
    scrollRef.current?.scrollBy({ left: dir === 'right' ? 600 : -600, behavior: 'smooth' })
  }

  if (query.isLoading) {
    return (
      <section className="space-y-2">
        <h3 className="font-semibold text-sm">{list.t}</h3>
        <div className="flex gap-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} className="flex-shrink-0 w-[140px] aspect-[2/3] rounded-lg" />
          ))}
        </div>
      </section>
    )
  }

  if (!items.length) return null

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold text-sm">{list.t}</h3>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
            MDBList
          </Badge>
        </div>
        <div className="flex gap-1">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => scroll('left')}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => scroll('right')}>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div
        ref={scrollRef}
        className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1"
        style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
      >
        {items.map((item, i) => {
          const key = discoverDbKey(item)
          const entry = db_index[key]
          const metaId = entry?.imdb_id || item.external_id
          return (
            <DiscoverCard
              key={`${key}:${i}`}
              title={item.title}
              year={item.year}
              poster={item.poster}
              metaId={metaId}
              catalogType={item.media_type === 'movie' ? 'movie' : 'series'}
              rpdbApiKey={rpdbApiKey}
              isLoading={loadingKey === key}
              isInDb={!!entry}
              onClick={() => (entry ? onNavigate(entry.id, item.media_type) : onImport(item))}
            />
          )
        })}

        {query.hasNextPage && (
          <div
            className="relative flex-shrink-0 w-[140px] group cursor-pointer select-none"
            onClick={() => !query.isFetchingNextPage && query.fetchNextPage()}
          >
            <div className="aspect-[2/3] rounded-lg bg-muted/50 border border-border/50 flex flex-col items-center justify-center gap-2 hover:bg-muted/80 transition-colors">
              {query.isFetchingNextPage ? (
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              ) : (
                <>
                  <LoadMoreIcon className="h-6 w-6 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground font-medium">Load more</span>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

// ─── Search Results (infinite grid) ──────────────────────────────────────────

interface SearchResultsProps {
  query: string
  mediaType: 'movie' | 'tv' | 'all'
  language?: string
  enabled: boolean
  onImport: (item: DiscoverItem) => void
  loadingKey?: string | null
  rpdbApiKey?: string | null
  onNavigate: (mediaId: number, mediaType: string) => void
}

function SearchResults({
  query,
  mediaType,
  language,
  enabled,
  onImport,
  loadingKey,
  rpdbApiKey,
  onNavigate,
}: SearchResultsProps) {
  const result = useDiscoverSearch(query, mediaType, enabled, language)
  const { items, db_index } = useMemo(() => flattenPages(result.data), [result.data])

  if (result.isLoading) {
    return (
      <div className="grid grid-cols-3 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8 gap-3 pt-2">
        {Array.from({ length: 16 }).map((_, i) => (
          <Skeleton key={i} className="w-full aspect-[2/3] rounded-lg" />
        ))}
      </div>
    )
  }

  if (!items.length) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <Search className="h-10 w-10 mx-auto mb-3 opacity-30" />
        <p>No results for &quot;{query}&quot;</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8 gap-3">
        {items.map((item, i) => {
          const key = discoverDbKey(item)
          const entry = db_index[key]
          const metaId = entry?.imdb_id || `${item.provider}:${item.external_id}`
          return (
            <DiscoverCard
              key={`${key}:${i}`}
              title={item.title}
              year={item.year}
              poster={item.poster}
              metaId={metaId}
              catalogType={item.media_type === 'movie' ? 'movie' : 'series'}
              rpdbApiKey={entry?.imdb_id ? rpdbApiKey : null}
              isLoading={loadingKey === key}
              isInDb={!!entry}
              onClick={() => (entry ? onNavigate(entry.id, item.media_type) : onImport(item))}
            />
          )
        })}
      </div>
      {result.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => result.fetchNextPage()} disabled={result.isFetchingNextPage}>
            {result.isFetchingNextPage ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Load more results
          </Button>
        </div>
      )}
    </div>
  )
}

// ─── Main Tab ─────────────────────────────────────────────────────────────────

export function DiscoverTab() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { rpdbApiKey } = useRpdb()

  const { data: profiles } = useProfiles()
  const defaultProfile = profiles?.find((p) => p.is_default) ?? profiles?.[0]
  const config = (
    defaultProfile as
      | { config?: { tmdb?: { ak?: string }; tvdb?: { ak?: string }; mdb?: { ak?: string; l?: MDBListItem[] } } }
      | undefined
  )?.config

  // Discover auto-enables based on which keys the user has configured — no separate toggle.
  const hasKey = !!config?.tmdb?.ak
  const hasTvdbKey = !!config?.tvdb?.ak
  const mdbLists = config?.mdb?.l ?? []
  const hasMdbKey = !!config?.mdb?.ak
  const hasAnySource = hasKey || hasTvdbKey || hasMdbKey || mdbLists.length > 0

  const [region, setRegion] = useState('US')
  const [mediaFilter, setMediaFilter] = useState<'all' | 'movie' | 'tv'>('all')
  const [language, setLanguage] = useState(LANG_ANY)
  const [searchInput, setSearchInput] = useState('')
  const [loadingKey, setLoadingKey] = useState<string | null>(null)

  const debouncedSearch = useDebounce(searchInput, 400)
  const isSearching = debouncedSearch.trim().length > 0

  const { season, year } = getCurrentSeason()

  const showMovies = mediaFilter !== 'tv'
  const showSeries = mediaFilter !== 'movie'
  const showAll = mediaFilter === 'all'

  // Per-source enabled flags — each source activates only when its key is present
  const tmdbEnabled = hasKey
  const lang = language === LANG_ANY ? undefined : language || undefined

  const trendingAll = useDiscoverTrending('all', 'week', tmdbEnabled && showAll && !isSearching, lang)
  const trendingMovies = useDiscoverTrending(
    'movie',
    'week',
    tmdbEnabled && showMovies && !showAll && !isSearching,
    lang,
  )
  const trendingSeries = useDiscoverTrending('tv', 'week', tmdbEnabled && showSeries && !showAll && !isSearching, lang)
  const popularMovies = useDiscoverList('popular', 'movie', region, tmdbEnabled && showMovies && !isSearching, lang)
  const popularSeries = useDiscoverList('popular', 'tv', region, tmdbEnabled && showSeries && !isSearching, lang)
  const topMovies = useDiscoverList('top_rated', 'movie', region, tmdbEnabled && showMovies && !isSearching, lang)
  const topSeries = useDiscoverList('top_rated', 'tv', region, tmdbEnabled && showSeries && !isSearching, lang)
  const nowPlaying = useDiscoverList('now_playing', 'movie', region, tmdbEnabled && showMovies && !isSearching, lang)
  const upcoming = useDiscoverList('upcoming', 'movie', region, tmdbEnabled && showMovies && !isSearching, lang)
  useWatchProviders('movie', region, tmdbEnabled)
  const netflixMovies = useDiscoverProviderFeed('movie', 8, region, tmdbEnabled && showMovies && !isSearching, lang)
  const netflixSeries = useDiscoverProviderFeed('tv', 8, region, tmdbEnabled && showSeries && !isSearching, lang)
  const primeMovies = useDiscoverProviderFeed('movie', 9, region, tmdbEnabled && showMovies && !isSearching, lang)
  // Anime (AniList/Kitsu) needs no external API key — available to all authenticated users
  const animeTrending = useDiscoverAnime('trending', undefined, undefined, 'anilist', showSeries && !isSearching)
  const animeSeasonal = useDiscoverAnime('seasonal', season, year, 'anilist', showSeries && !isSearching)

  // TVDB rows — only when user has their own TVDB key configured
  const tvdbSeries = useDiscoverTvdb('tv', hasTvdbKey && showSeries && !isSearching)
  const tvdbMovies = useDiscoverTvdb('movie', hasTvdbKey && showMovies && !isSearching)

  const importMutation = useMutation({
    mutationFn: async (item: DiscoverItem) => {
      const imported = await userMetadataApi.importFromExternal({
        provider: item.provider as ImportProvider,
        external_id: item.external_id,
        media_type: item.media_type === 'series' ? 'series' : 'movie',
        is_public: true,
      })
      try {
        await metadataApi.refreshMetadata(imported.id, item.media_type === 'series' ? 'series' : 'movie')
      } catch {
        /* non-blocking */
      }
      scrapersApi
        .triggerScrape(imported.id, {
          media_type: item.media_type === 'series' ? 'series' : 'movie',
          ...(item.media_type === 'series' ? { season: 1, episode: 1 } : {}),
        })
        .catch(() => {})
      return { id: imported.id, mediaType: item.media_type }
    },
    onMutate: (item) => setLoadingKey(discoverDbKey(item)),
    onSettled: () => setLoadingKey(null),
    onSuccess: ({ id, mediaType }) => {
      queryClient.invalidateQueries({ queryKey: ['discover'] })
      navigate(`/dashboard/content/${mediaType}/${id}?scraping=1`)
    },
  })

  const handleNavigate = (mediaId: number, mediaType: string) => navigate(`/dashboard/content/${mediaType}/${mediaId}`)

  const rowProps = {
    onImport: (item: DiscoverItem) => importMutation.mutate(item),
    loadingKey,
    rpdbApiKey,
    onNavigate: handleNavigate,
  }

  if (!hasAnySource) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-20 text-center">
        <Sparkles className="h-12 w-12 text-muted-foreground/40" />
        <h2 className="text-xl font-semibold">Configure a Source to Discover</h2>
        <p className="text-muted-foreground max-w-sm">
          Add a TMDB, TVDB, or MDBList API key in Settings → External Services to browse trending movies, new releases,
          and more.
        </p>
        <Button asChild>
          <Link to="/dashboard/configure">
            <Settings2 className="mr-2 h-4 w-4" />
            Open Settings
          </Link>
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="space-y-2">
        {/* Search — full width */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search movies, series…"
            className="pl-8 pr-8"
          />
          {searchInput && (
            <button
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={() => setSearchInput('')}
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Filter pills — horizontal scroll on mobile */}
        <div className="flex gap-2 overflow-x-auto pb-1" style={{ scrollbarWidth: 'none' }}>
          <Select value={mediaFilter} onValueChange={(v) => setMediaFilter(v as typeof mediaFilter)}>
            <SelectTrigger className="shrink-0 w-[110px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="movie">Movies</SelectItem>
              <SelectItem value="tv">Series</SelectItem>
            </SelectContent>
          </Select>

          {hasKey && !isSearching && (
            <Select value={region} onValueChange={setRegion}>
              <SelectTrigger className="shrink-0 w-[190px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="max-h-72">
                {REGIONS.map((r) => (
                  <SelectItem key={r.code} value={r.code}>
                    {r.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          {hasKey && (
            <Select value={language} onValueChange={setLanguage}>
              <SelectTrigger className="shrink-0 w-[200px]">
                <SelectValue placeholder="Any Language" />
              </SelectTrigger>
              <SelectContent className="max-h-72">
                {LANGUAGES.map((l) => (
                  <SelectItem key={l.code} value={l.code}>
                    {l.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      </div>

      {/* Search results */}
      {isSearching && hasKey ? (
        <div className="space-y-3">
          <h3 className="font-semibold text-sm">Search results for &quot;{debouncedSearch}&quot;</h3>
          <SearchResults
            query={debouncedSearch}
            mediaType={mediaFilter === 'tv' ? 'tv' : mediaFilter === 'movie' ? 'movie' : 'all'}
            language={lang}
            enabled={tmdbEnabled}
            {...rowProps}
          />
        </div>
      ) : (
        <div className="space-y-8">
          {hasKey && (
            <>
              {showAll && <DiscoverRow title="Trending This Week" query={trendingAll} {...rowProps} />}
              {showMovies && !showAll && <DiscoverRow title="Trending Movies" query={trendingMovies} {...rowProps} />}
              {showSeries && !showAll && <DiscoverRow title="Trending Series" query={trendingSeries} {...rowProps} />}
              {showMovies && (
                <>
                  <DiscoverRow title="Now Playing" query={nowPlaying} {...rowProps} />
                  <DiscoverRow title="Upcoming Movies" query={upcoming} {...rowProps} />
                  <DiscoverRow title="Popular Movies" query={popularMovies} {...rowProps} />
                  <DiscoverRow title="Top Rated Movies" query={topMovies} {...rowProps} />
                  <DiscoverRow title="New on Netflix (Movies)" query={netflixMovies} {...rowProps} />
                  <DiscoverRow title="New on Prime Video (Movies)" query={primeMovies} {...rowProps} />
                </>
              )}
              {showSeries && (
                <>
                  <DiscoverRow title="Popular Series" query={popularSeries} {...rowProps} />
                  <DiscoverRow title="Top Rated Series" query={topSeries} {...rowProps} />
                  <DiscoverRow title="New on Netflix (Series)" query={netflixSeries} {...rowProps} />
                </>
              )}
            </>
          )}

          {/* Anime rows — no external API key required */}
          {showSeries && !isSearching && (
            <>
              <DiscoverRow title="Trending Anime" query={animeTrending} {...rowProps} />
              <DiscoverRow
                title={`This Season — ${season.charAt(0).toUpperCase() + season.slice(1)} ${year}`}
                query={animeSeasonal}
                {...rowProps}
              />
            </>
          )}

          {/* TVDB rows — only when user has their own TVDB key */}
          {hasTvdbKey && (
            <>
              {showSeries && <DiscoverRow title="Popular Series (TVDB)" query={tvdbSeries} {...rowProps} />}
              {showMovies && <DiscoverRow title="Popular Movies (TVDB)" query={tvdbMovies} {...rowProps} />}
            </>
          )}

          {/* MDBList rows — full discover: shows items not yet in DB */}
          {mdbLists
            .filter((list) =>
              mediaFilter === 'movie' ? list.ct === 'movie' : mediaFilter === 'tv' ? list.ct === 'series' : true,
            )
            .map((list) => (
              <MDBListRow
                key={`${list.ct}:${list.i}`}
                list={list}
                enabled={hasMdbKey}
                rpdbApiKey={rpdbApiKey}
                onImport={(item) => importMutation.mutate(item)}
                loadingKey={loadingKey}
                onNavigate={handleNavigate}
              />
            ))}
        </div>
      )}
    </div>
  )
}
