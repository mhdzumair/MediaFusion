import { useState, useCallback } from 'react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Search, Loader2, CheckCircle, AlertTriangle } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { metadataApi } from '@/lib/api'
import { MatchResultsGrid, type ExtendedMatch } from './MatchResultsGrid'

interface MatchSearchSectionProps {
  initialMatches: ExtendedMatch[]
  selectedIndex: number | null
  selectedMatch: ExtendedMatch | null
  onSelectMatch: (match: ExtendedMatch, index: number) => void
  metaId: string
  onMetaIdChange: (id: string) => void
  contentType: 'movie' | 'series'
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
  gridClassName = 'h-[250px]',
}: MatchSearchSectionProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [customMatches, setCustomMatches] = useState<ExtendedMatch[] | null>(null)
  const [customSelectedIndex, setCustomSelectedIndex] = useState<number | null>(null)

  const isCustom = customMatches !== null
  const displayMatches = customMatches ?? initialMatches
  const hasMatches = displayMatches.length > 0
  const activeSelectedIndex = isCustom ? customSelectedIndex : selectedIndex

  const searchMutation = useMutation({
    mutationFn: (query: string) => metadataApi.searchExternal(query, contentType),
    onSuccess: (result) => {
      if (result.status === 'success' && result.results.length > 0) {
        const mapped: ExtendedMatch[] = result.results.map((r) => ({
          id: r.imdb_id || r.id,
          title: r.title,
          year: r.year,
          poster: r.poster,
          type: contentType,
          imdb_id: r.imdb_id,
          description: r.description,
        }))
        setCustomMatches(mapped)
        setCustomSelectedIndex(null)
      } else {
        setCustomMatches([])
        setCustomSelectedIndex(null)
      }
    },
  })

  const handleSearch = useCallback(() => {
    if (!searchQuery.trim()) return
    searchMutation.mutate(searchQuery.trim())
  }, [searchQuery, searchMutation])

  const handleReset = useCallback(() => {
    setCustomMatches(null)
    setCustomSelectedIndex(null)
    setSearchQuery('')
  }, [])

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
        <Label className="text-xs text-muted-foreground">Search by title</Label>
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

      {/* No Matches Fallback */}
      {!hasMatches && (
        <div className="p-4 rounded-xl bg-primary/10 border border-primary/20">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-primary mt-0.5" />
            <div className="space-y-2 flex-1">
              <p className="font-medium text-primary">No matches found</p>
              <p className="text-sm text-muted-foreground">
                Try searching with a different title above, or enter the IMDb ID manually.
              </p>
              <div className="flex gap-2">
                <Input
                  placeholder="tt1234567"
                  value={metaId}
                  onChange={(e) => onMetaIdChange(e.target.value)}
                  className="max-w-xs"
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
