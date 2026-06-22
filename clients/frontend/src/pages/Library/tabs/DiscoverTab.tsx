import { useState, useRef } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { Sparkles, ChevronLeft, ChevronRight, Settings2, Loader2, Search, X } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { Poster } from '@/components/ui/poster'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useProfiles } from '@/hooks/useProfiles'
import { useRpdb } from '@/contexts/RpdbContext'
import { useWatchProviders, useDiscoverSource, type DiscoverSource } from '@/hooks/useDiscover'
import { userMetadataApi } from '@/lib/api/user-metadata'
import { metadataApi } from '@/lib/api/metadata'
import type { DiscoverItem } from '@/lib/api/discover'
import { discoverDbKey } from '@/lib/api/discover'
import type { MDBListItem } from '@/pages/Configure/components/types'
import type { ImportProvider } from '@/lib/api/user-metadata'
import { useDebounce } from '@/hooks/useDebounce'
import { saveContentDetailReturnUrl } from '../browseNavigation'

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

const LANG_ANY = '__any__'

function DiscoverRowPagination({
  page,
  totalPages,
  onPageChange,
  isFetching,
}: {
  page: number
  totalPages: number
  onPageChange: (page: number) => void
  isFetching?: boolean
}) {
  if (totalPages <= 1) return null

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        disabled={page <= 1 || isFetching}
        onClick={() => onPageChange(page - 1)}
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>
      <span className="text-xs text-muted-foreground min-w-[4.5rem] text-center">
        {page} / {totalPages}
      </span>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        disabled={page >= totalPages || isFetching}
        onClick={() => onPageChange(page + 1)}
      >
        <ChevronRight className="h-4 w-4" />
      </Button>
    </div>
  )
}

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

// ─── Horizontal Row ─────────────────────────────────────────────────────────

interface DiscoverRowProps {
  title: string
  source: DiscoverSource
  onImport: (item: DiscoverItem) => void
  loadingKey?: string | null
  rpdbApiKey?: string | null
  onNavigate: (mediaId: number, mediaType: string) => void
}

function DiscoverRow(props: DiscoverRowProps) {
  return <DiscoverRowInner key={JSON.stringify(props.source)} {...props} />
}

function DiscoverRowInner({ title, source, onImport, loadingKey, rpdbApiKey, onNavigate }: DiscoverRowProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [page, setPage] = useState(1)

  const query = useDiscoverSource(source, page)
  const items = query.data?.items ?? []
  const db_index = query.data?.db_index ?? {}
  const totalPages = query.data?.total_pages ?? 1

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
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold text-sm">{title}</h3>
        <div className="flex items-center gap-1">
          <DiscoverRowPagination
            page={page}
            totalPages={totalPages}
            onPageChange={setPage}
            isFetching={query.isFetching}
          />
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

function MDBListRow(props: MDBListRowProps) {
  const catalogType = props.list.ct === 'movie' ? 'movie' : 'series'
  const sourceKey = `${props.list.i}:${catalogType}:${props.enabled}`
  return <MDBListRowInner key={sourceKey} {...props} />
}

function MDBListRowInner({ list, enabled, rpdbApiKey, onImport, loadingKey, onNavigate }: MDBListRowProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const catalogType = list.ct === 'movie' ? 'movie' : ('series' as const)
  const [page, setPage] = useState(1)
  const source: DiscoverSource = { kind: 'mdblist', listId: list.i, catalogType, enabled }

  const query = useDiscoverSource(source, page)
  const items = query.data?.items ?? []
  const db_index = query.data?.db_index ?? {}
  const totalPages = query.data?.total_pages ?? 1

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
        <div className="flex items-center gap-1">
          <DiscoverRowPagination
            page={page}
            totalPages={totalPages}
            onPageChange={setPage}
            isFetching={query.isFetching}
          />
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
      </div>
    </section>
  )
}

// ─── Search Results ───────────────────────────────────────────────────────────

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

function SearchResults(props: SearchResultsProps) {
  const sourceKey = JSON.stringify({
    query: props.query,
    mediaType: props.mediaType,
    language: props.language,
    enabled: props.enabled,
  })
  return <SearchResultsInner key={sourceKey} {...props} />
}

function SearchResultsInner({
  query,
  mediaType,
  language,
  enabled,
  onImport,
  loadingKey,
  rpdbApiKey,
  onNavigate,
}: SearchResultsProps) {
  const [page, setPage] = useState(1)
  const source: DiscoverSource = {
    kind: 'search',
    query,
    mediaType,
    language,
    enabled,
  }

  const result = useDiscoverSource(source, page)
  const items = result.data?.items ?? []
  const db_index = result.data?.db_index ?? {}
  const totalPages = result.data?.total_pages ?? 1

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
      {totalPages > 1 && (
        <div className="flex justify-center items-center gap-2 pt-2">
          <Button
            variant="outline"
            size="icon"
            disabled={page === 1 || result.isFetching}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-xl"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-4 text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="icon"
            disabled={page >= totalPages || result.isFetching}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-xl"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  )
}

// ─── Main Tab ─────────────────────────────────────────────────────────────────

export function DiscoverTab() {
  const navigate = useNavigate()
  const location = useLocation()
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

  useWatchProviders('movie', region, tmdbEnabled)

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
      return { id: imported.id, mediaType: item.media_type }
    },
    onMutate: (item) => setLoadingKey(discoverDbKey(item)),
    onSettled: () => setLoadingKey(null),
    onSuccess: ({ id, mediaType }) => {
      navigate(`/dashboard/content/${mediaType}/${id}?scraping=1`)
    },
  })

  const handleNavigate = (mediaId: number, mediaType: string) => {
    saveContentDetailReturnUrl(location.pathname, location.search, 'Discover')
    navigate(`/dashboard/content/${mediaType}/${mediaId}`)
  }

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
              {showAll && (
                <DiscoverRow
                  title="Trending This Week"
                  source={{
                    kind: 'trending',
                    mediaType: 'all',
                    window: 'week',
                    language: lang,
                    enabled: tmdbEnabled && showAll && !isSearching,
                  }}
                  {...rowProps}
                />
              )}
              {showMovies && !showAll && (
                <DiscoverRow
                  title="Trending Movies"
                  source={{
                    kind: 'trending',
                    mediaType: 'movie',
                    window: 'week',
                    language: lang,
                    enabled: tmdbEnabled && showMovies && !showAll && !isSearching,
                  }}
                  {...rowProps}
                />
              )}
              {showSeries && !showAll && (
                <DiscoverRow
                  title="Trending Series"
                  source={{
                    kind: 'trending',
                    mediaType: 'tv',
                    window: 'week',
                    language: lang,
                    enabled: tmdbEnabled && showSeries && !showAll && !isSearching,
                  }}
                  {...rowProps}
                />
              )}
              {showMovies && (
                <>
                  <DiscoverRow
                    title="Now Playing"
                    source={{
                      kind: 'list',
                      listKind: 'now_playing',
                      mediaType: 'movie',
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showMovies && !isSearching,
                    }}
                    {...rowProps}
                  />
                  <DiscoverRow
                    title="Upcoming Movies"
                    source={{
                      kind: 'list',
                      listKind: 'upcoming',
                      mediaType: 'movie',
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showMovies && !isSearching,
                    }}
                    {...rowProps}
                  />
                  <DiscoverRow
                    title="Popular Movies"
                    source={{
                      kind: 'list',
                      listKind: 'popular',
                      mediaType: 'movie',
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showMovies && !isSearching,
                    }}
                    {...rowProps}
                  />
                  <DiscoverRow
                    title="Top Rated Movies"
                    source={{
                      kind: 'list',
                      listKind: 'top_rated',
                      mediaType: 'movie',
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showMovies && !isSearching,
                    }}
                    {...rowProps}
                  />
                  <DiscoverRow
                    title="New on Netflix (Movies)"
                    source={{
                      kind: 'providerFeed',
                      mediaType: 'movie',
                      providerId: 8,
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showMovies && !isSearching,
                    }}
                    {...rowProps}
                  />
                  <DiscoverRow
                    title="New on Prime Video (Movies)"
                    source={{
                      kind: 'providerFeed',
                      mediaType: 'movie',
                      providerId: 9,
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showMovies && !isSearching,
                    }}
                    {...rowProps}
                  />
                </>
              )}
              {showSeries && (
                <>
                  <DiscoverRow
                    title="Popular Series"
                    source={{
                      kind: 'list',
                      listKind: 'popular',
                      mediaType: 'tv',
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showSeries && !isSearching,
                    }}
                    {...rowProps}
                  />
                  <DiscoverRow
                    title="Top Rated Series"
                    source={{
                      kind: 'list',
                      listKind: 'top_rated',
                      mediaType: 'tv',
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showSeries && !isSearching,
                    }}
                    {...rowProps}
                  />
                  <DiscoverRow
                    title="New on Netflix (Series)"
                    source={{
                      kind: 'providerFeed',
                      mediaType: 'tv',
                      providerId: 8,
                      region,
                      language: lang,
                      enabled: tmdbEnabled && showSeries && !isSearching,
                    }}
                    {...rowProps}
                  />
                </>
              )}
            </>
          )}

          {/* Anime rows — no external API key required */}
          {showSeries && !isSearching && (
            <>
              <DiscoverRow
                title="Trending Anime"
                source={{
                  kind: 'anime',
                  animeKind: 'trending',
                  source: 'anilist',
                  enabled: showSeries && !isSearching,
                }}
                {...rowProps}
              />
              <DiscoverRow
                title={`This Season — ${season.charAt(0).toUpperCase() + season.slice(1)} ${year}`}
                source={{
                  kind: 'anime',
                  animeKind: 'seasonal',
                  season,
                  year,
                  source: 'anilist',
                  enabled: showSeries && !isSearching,
                }}
                {...rowProps}
              />
            </>
          )}

          {/* TVDB rows — only when user has their own TVDB key */}
          {hasTvdbKey && (
            <>
              {showSeries && (
                <DiscoverRow
                  title="Popular Series (TVDB)"
                  source={{ kind: 'tvdb', mediaType: 'tv', enabled: hasTvdbKey && showSeries && !isSearching }}
                  {...rowProps}
                />
              )}
              {showMovies && (
                <DiscoverRow
                  title="Popular Movies (TVDB)"
                  source={{ kind: 'tvdb', mediaType: 'movie', enabled: hasTvdbKey && showMovies && !isSearching }}
                  {...rowProps}
                />
              )}
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
