import { useState, useCallback } from 'react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Search, Loader2, CheckCircle, AlertTriangle } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { metadataApi } from '@/lib/api'
import { MatchResultsGrid, type ExtendedMatch } from './MatchResultsGrid'
import type { ExternalSearchResult } from '@/lib/api'
import { fetchImportMatchByMetaId } from '../utils/importMetaLookup'

function mapExternalSearchResult(result: ExternalSearchResult, contentType: 'movie' | 'series'): ExtendedMatch {
  return {
    id: result.imdb_id || result.id,
    media_id: result.media_id,
    title: result.title,
    year: result.year,
    poster: result.poster,
    background: result.background,
    type: result.type ?? contentType,
    imdb_id: result.imdb_id,
    imdb_rating: result.imdb_rating,
    runtime: result.runtime,
    description: result.description ?? undefined,
    release_date: result.release_date,
  }
}

interface MatchSearchSectionProps {
  initialMatches: ExtendedMatch[]
  selectedIndex: number | null
  selectedMatch: ExtendedMatch | null
  onSelectMatch: (match: ExtendedMatch, index: number) => void
  metaId: string
  onMetaIdChange: (id: string) => void
  contentType: 'movie' | 'series'
  initialYear?: number
  gridClassName?: string
}

export function MatchSearchSection({
  initialMatches,
  selectedIndex,
  selectedMatch,
  onSelectMatch,
  metaId,
  onMetaIdChange,
  contentType,
  initialYear,
  gridClassName = 'h-[250px]',
}: MatchSearchSectionProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [searchYear, setSearchYear] = useState(initialYear ? String(initialYear) : '')
  const [customMatches, setCustomMatches] = useState<ExtendedMatch[] | null>(null)
  const [customSelectedIndex, setCustomSelectedIndex] = useState<number | null>(null)
  const [hasSearched, setHasSearched] = useState(false)
  const [manualLookupError, setManualLookupError] = useState<string | null>(null)
  const [isLookingUpMetaId, setIsLookingUpMetaId] = useState(false)

  const isCustom = customMatches !== null
  const displayMatches = customMatches ?? initialMatches
  const hasMatches = displayMatches.length > 0
  const activeSelectedIndex = isCustom ? customSelectedIndex : selectedIndex
  const showEmptySearchHint = hasSearched && !hasMatches

  const searchMutation = useMutation({
    mutationFn: ({ query, searchYear }: { query: string; searchYear?: number }) =>
      metadataApi.searchMatches({
        title: query,
        year: searchYear,
        media_type: contentType,
        include_user_content: false,
        include_catalog: true,
        include_external: true,
      }),
    onSuccess: (result) => {
      setHasSearched(true)
      const results = result.results ?? []
      if (results.length > 0) {
        setCustomMatches(results.map((entry) => mapExternalSearchResult(entry, contentType)))
        setCustomSelectedIndex(null)
      } else {
        setCustomMatches([])
        setCustomSelectedIndex(null)
      }
    },
    onError: () => {
      setHasSearched(true)
      setCustomMatches([])
      setCustomSelectedIndex(null)
    },
  })

  const handleSearch = useCallback(() => {
    if (!searchQuery.trim()) return
    const trimmedSearchYear = searchYear.trim()
    const parsedSearchYear = trimmedSearchYear ? Number(trimmedSearchYear) : undefined
    const validSearchYear = Number.isFinite(parsedSearchYear) ? parsedSearchYear : undefined
    searchMutation.mutate({ query: searchQuery.trim(), searchYear: validSearchYear })
  }, [searchQuery, searchYear, searchMutation])

  const handleReset = useCallback(() => {
    setCustomMatches(null)
    setCustomSelectedIndex(null)
    setHasSearched(false)
    setManualLookupError(null)
    setSearchQuery('')
    setSearchYear(initialYear ? String(initialYear) : '')
  }, [initialYear])

  const handleManualMetaLookup = useCallback(async () => {
    const trimmedMetaId = metaId.trim()
    if (!trimmedMetaId) return

    setIsLookingUpMetaId(true)
    setManualLookupError(null)

    try {
      const match = await fetchImportMatchByMetaId(trimmedMetaId, contentType)
      setCustomMatches([match])
      setCustomSelectedIndex(0)
      setHasSearched(true)
      onSelectMatch(match, 0)
    } catch (error) {
      setManualLookupError(error instanceof Error ? error.message : 'Failed to fetch metadata for that ID')
    } finally {
      setIsLookingUpMetaId(false)
    }
  }, [metaId, contentType, onSelectMatch])

  const handleSelectMatch = useCallback(
    (match: ExtendedMatch, index: number) => {
      if (isCustom) {
        setCustomSelectedIndex(index)
      }
      onSelectMatch(match, index)
    },
    [isCustom, onSelectMatch],
  )

  return (
    <div className="space-y-3">
      {/* Search Input */}
      <div className="space-y-2">
        <Label className="text-xs text-muted-foreground">Search by title (optional year filter)</Label>
        <div className="flex gap-2">
          <Input
            placeholder="Enter a title to search..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && searchQuery.trim()) handleSearch()
            }}
            className="rounded-lg text-sm"
          />
          <Input
            type="number"
            inputMode="numeric"
            min={1878}
            max={9999}
            step={1}
            placeholder="Year"
            value={searchYear}
            onChange={(e) => setSearchYear(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && searchQuery.trim()) handleSearch()
            }}
            className="rounded-lg text-sm w-24 shrink-0"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleSearch}
            disabled={!searchQuery.trim() || searchMutation.isPending}
            className="rounded-lg shrink-0 h-9"
          >
            {searchMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          </Button>
          {customMatches !== null && (
            <Button variant="ghost" size="sm" onClick={handleReset} className="rounded-lg shrink-0 h-9 text-xs">
              Reset
            </Button>
          )}
        </div>
      </div>

      {/* External ID lookup — always available */}
      <div className="space-y-2 rounded-xl border border-border/60 bg-muted/20 p-3">
        <Label className="text-xs text-muted-foreground">
          Or look up by external ID (IMDb, TMDB, TVDB, MAL, Kitsu)
        </Label>
        <div className="flex flex-wrap gap-2">
          <Input
            placeholder="tt1234567, tmdb:603, tvdb:81189"
            value={metaId}
            onChange={(e) => {
              onMetaIdChange(e.target.value)
              setManualLookupError(null)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && metaId.trim()) {
                e.preventDefault()
                void handleManualMetaLookup()
              }
            }}
            className="rounded-lg text-sm max-w-md flex-1 min-w-[12rem]"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleManualMetaLookup()}
            disabled={!metaId.trim() || isLookingUpMetaId}
            className="h-9 rounded-lg shrink-0"
          >
            {isLookingUpMetaId ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Look up ID'}
          </Button>
        </div>
        {manualLookupError && <p className="text-xs text-destructive">{manualLookupError}</p>}
      </div>

      {/* Match Results */}
      {hasMatches && (
        <div className="space-y-3 min-w-0">
          <div className="flex items-center justify-between gap-2 min-w-0">
            <Label className="text-sm font-medium shrink-0">
              {customMatches !== null ? 'Search Results' : 'Matched Content'} ({displayMatches.length})
            </Label>
            {selectedMatch && (
              <Badge variant="secondary" className="text-xs max-w-[50%] truncate">
                <CheckCircle className="h-3 w-3 mr-1 shrink-0" />
                <span className="truncate">{selectedMatch.title}</span>
              </Badge>
            )}
          </div>
          <MatchResultsGrid
            matches={displayMatches}
            selectedIndex={activeSelectedIndex}
            onSelectMatch={handleSelectMatch}
            className={gridClassName}
          />
        </div>
      )}

      {showEmptySearchHint && (
        <div className="flex items-start gap-3 rounded-xl border border-primary/20 bg-primary/10 p-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <div className="space-y-1">
            <p className="text-sm font-medium text-primary">No title matches found</p>
            <p className="text-xs text-muted-foreground">
              Try a different title or year above, or use the external ID lookup to fetch metadata directly.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
