/**
 * Shared metadata search popover.
 *
 * Single entry-point for all "find and select a media title" flows:
 *   - Title + year text search (via /metadata/search/matches)
 *   - Manual ID entry: IMDB / TMDB / TVDB / MAL / Kitsu (resolves via /matches)
 *   - MediaFusion internal DB id (direct — no network call)
 *
 * Props:
 *   metaType       — initial type filter ('all' | 'movie' | 'series').  User can
 *                    change it inside the popover via the type selector row.
 *   requireInternal — when true, external (not-in-library) search results are shown
 *                     but disabled with a tooltip.  Use for flows that need a
 *                     media_id (e.g. admin stream-link).  Default false.
 */

import { useState, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { AlertCircle, Film, Loader2, Link2, Search, Tv, X } from 'lucide-react'
import { useCombinedMetadataSearch, type CombinedSearchResult } from '@/hooks'
import { useDebounce } from '@/hooks/useDebounce'
import { type ImportProvider } from '@/lib/api'
import { fetchCombinedMatchByProviderId } from '@/pages/ContentImport/utils/importMetaLookup'

// ─── Provider options ─────────────────────────────────────────────────────────

type AllProvider = ImportProvider | 'mediafusion'

interface ProviderOption {
  value: AllProvider
  label: string
  placeholder: string
  example: string
  description?: string
}

const PROVIDER_OPTIONS: ProviderOption[] = [
  { value: 'imdb', label: 'IMDB', placeholder: 'tt1234567', example: 'tt0111161' },
  { value: 'tmdb', label: 'TMDB', placeholder: '278', example: '278' },
  { value: 'tvdb', label: 'TVDB', placeholder: '81189', example: '81189' },
  { value: 'mal', label: 'MAL', placeholder: '5114', example: '5114' },
  { value: 'kitsu', label: 'Kitsu', placeholder: '1555', example: '1555' },
  {
    value: 'mediafusion',
    label: 'MediaFusion',
    placeholder: '123  or  mf:123',
    example: 'mf:123',
    description: 'Link directly by internal DB id',
  },
]

const TYPE_OPTIONS: { value: 'all' | 'movie' | 'series'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'movie', label: 'Movie' },
  { value: 'series', label: 'Series' },
]

// ─── Types ────────────────────────────────────────────────────────────────────

export interface MetadataSearchPopoverProps {
  value?: { id: string; title: string; poster?: string; type?: string }
  onSelect: (result: CombinedSearchResult) => void
  onClear: () => void
  disabled?: boolean
  /** Initial media-type filter.  User can change it inside the popover. */
  metaType?: 'movie' | 'series' | 'all'
  /**
   * When true, external (not-in-library) search results are shown disabled with
   * a tooltip.  Use when the caller needs an internal media_id to proceed.
   */
  requireInternal?: boolean
  placeholder?: string
  triggerClassName?: string
  popoverWidth?: string
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function parseMediaFusionId(raw: string): number | null {
  const match = raw.trim().match(/^(?:mf:|mediafusion:)?(\d+)$/i)
  if (!match) return null
  const n = Number.parseInt(match[1], 10)
  return Number.isFinite(n) && n > 0 ? n : null
}

// ─── Component ────────────────────────────────────────────────────────────────

export function MetadataSearchPopover({
  value,
  onSelect,
  onClear,
  disabled,
  metaType = 'all',
  requireInternal = false,
  placeholder = 'Search or enter an ID…',
  triggerClassName,
  popoverWidth = 'w-[calc(100vw-2rem)] sm:w-[360px]',
}: MetadataSearchPopoverProps) {
  const [open, setOpen] = useState(false)

  // Type filter — user can change inside the popover
  const [localType, setLocalType] = useState<'all' | 'movie' | 'series'>(metaType)

  // Search mode
  const [searchQuery, setSearchQuery] = useState('')
  const [searchYear, setSearchYear] = useState('')

  // Manual ID mode
  const [showManualId, setShowManualId] = useState(false)
  const [manualProvider, setManualProvider] = useState<AllProvider>('imdb')
  const [manualId, setManualId] = useState('')
  const [isLoadingManual, setIsLoadingManual] = useState(false)
  const [manualError, setManualError] = useState<string | null>(null)

  const debouncedQuery = useDebounce(searchQuery, 300)
  const validYear = /^\d{4}$/.test(searchYear.trim()) ? Number(searchYear.trim()) : undefined

  const {
    data: searchResults = [],
    isLoading,
    isFetching,
  } = useCombinedMetadataSearch(
    { query: debouncedQuery, type: localType, limit: 15, year: validYear },
    { enabled: debouncedQuery.length >= 2 && !showManualId && open },
  )

  const reset = useCallback(() => {
    setSearchQuery('')
    setSearchYear('')
    setShowManualId(false)
    setManualId('')
    setManualError(null)
  }, [])

  const handleSelect = useCallback(
    (result: CombinedSearchResult) => {
      onSelect(result)
      setOpen(false)
      reset()
    },
    [onSelect, reset],
  )

  const handleManualIdSubmit = useCallback(async () => {
    if (!manualId.trim()) return

    // MediaFusion internal ID — create result directly, no network call
    if (manualProvider === 'mediafusion') {
      const mfId = parseMediaFusionId(manualId)
      if (!mfId) {
        setManualError('Enter a numeric ID like 123 or mf:123')
        return
      }
      handleSelect({
        id: `manual-mf-${mfId}`,
        title: `MediaFusion media #${mfId}`,
        type: localType === 'all' ? 'movie' : localType,
        source: 'internal',
        internal_id: mfId,
        external_id: `mf:${mfId}`,
      })
      return
    }

    // External provider — resolve via /matches
    setIsLoadingManual(true)
    setManualError(null)
    try {
      const result = await fetchCombinedMatchByProviderId(manualProvider as ImportProvider, manualId.trim(), localType)
      handleSelect(result)
      setManualProvider('imdb')
    } catch (err) {
      setManualError(err instanceof Error ? err.message : 'Failed to fetch metadata')
    } finally {
      setIsLoadingManual(false)
    }
  }, [manualId, manualProvider, localType, handleSelect])

  const currentProvider = PROVIDER_OPTIONS.find((p) => p.value === manualProvider)

  // ── Selected-value chip ───────────────────────────────────────────────────

  if (value?.id) {
    return (
      <div className="flex items-center gap-1.5 p-1.5 rounded border border-primary/30 bg-primary/5">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-5 w-5 flex-shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10"
          onClick={onClear}
          disabled={disabled}
          title="Remove link"
        >
          <X className="h-3 w-3" />
        </Button>
        {value.poster ? (
          <img src={value.poster} alt="" className="w-6 h-8 rounded object-cover flex-shrink-0" />
        ) : (
          <div className="w-6 h-8 rounded bg-muted flex items-center justify-center flex-shrink-0">
            {value.type === 'series' ? (
              <Tv className="h-3 w-3 text-muted-foreground" />
            ) : (
              <Film className="h-3 w-3 text-muted-foreground" />
            )}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <span className="text-xs font-medium truncate block">{value.title}</span>
          {value.type && (
            <Badge variant="outline" className="text-[10px] px-1 py-0 mt-0.5">
              {value.type}
            </Badge>
          )}
        </div>
      </div>
    )
  }

  // ── Trigger ───────────────────────────────────────────────────────────────

  return (
    <Popover
      open={open}
      onOpenChange={(isOpen) => {
        setOpen(isOpen)
        if (!isOpen) reset()
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          className={triggerClassName ?? 'h-9 w-full justify-start text-sm text-muted-foreground'}
          disabled={disabled}
        >
          <Search className="h-4 w-4 mr-2 shrink-0" />
          {placeholder}
        </Button>
      </PopoverTrigger>

      <PopoverContent
        className={`${popoverWidth} p-0 overflow-hidden flex flex-col`}
        align="start"
        style={{ height: '420px', maxHeight: 'calc(var(--radix-popover-content-available-height) - 10px)' }}
      >
        {showManualId ? (
          // ── Manual ID mode ──────────────────────────────────────────────
          <div className="p-3 space-y-2.5 flex flex-col h-full">
            <div className="flex items-center justify-between shrink-0">
              <span className="text-xs font-medium">Enter External ID</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 text-[10px] px-2"
                onClick={() => {
                  setShowManualId(false)
                  setManualError(null)
                }}
                disabled={isLoadingManual}
              >
                ← Back
              </Button>
            </div>

            {/* Provider grid */}
            <div className="grid grid-cols-3 gap-1 shrink-0">
              {PROVIDER_OPTIONS.map((opt) => (
                <Button
                  key={opt.value}
                  type="button"
                  variant={manualProvider === opt.value ? 'default' : 'outline'}
                  size="sm"
                  className="h-7 text-[11px] px-1"
                  onClick={() => {
                    setManualProvider(opt.value)
                    setManualError(null)
                  }}
                >
                  {opt.label}
                </Button>
              ))}
            </div>

            <Input
              placeholder={currentProvider?.placeholder ?? 'Enter ID'}
              value={manualId}
              onChange={(e) => {
                setManualId(e.target.value)
                setManualError(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleManualIdSubmit()
              }}
              className="h-8 text-sm shrink-0"
              autoFocus
            />

            {currentProvider?.description ? (
              <p className="text-[10px] text-muted-foreground shrink-0">{currentProvider.description}</p>
            ) : (
              <p className="text-[10px] text-muted-foreground shrink-0">
                Example: <code className="bg-muted px-0.5 rounded">{currentProvider?.example}</code>
              </p>
            )}

            {manualError && (
              <div className="flex items-start gap-1.5 p-2 rounded bg-destructive/10 text-destructive text-[10px] shrink-0">
                <AlertCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
                <span>{manualError}</span>
              </div>
            )}

            <Button
              className="w-full h-8 text-xs shrink-0"
              onClick={handleManualIdSubmit}
              disabled={!manualId.trim() || isLoadingManual}
            >
              {isLoadingManual ? (
                <>
                  <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                  Fetching…
                </>
              ) : (
                <>
                  <Link2 className="h-3 w-3 mr-1.5" />
                  Fetch &amp; Use
                </>
              )}
            </Button>
          </div>
        ) : (
          // ── Search mode ─────────────────────────────────────────────────
          <>
            <div className="p-2 border-b space-y-1.5 shrink-0">
              {/* Type filter row */}
              <div className="flex gap-1">
                {TYPE_OPTIONS.map((t) => (
                  <Button
                    key={t.value}
                    type="button"
                    variant={localType === t.value ? 'default' : 'outline'}
                    size="sm"
                    className="h-6 text-[11px] flex-1 px-1"
                    onClick={() => setLocalType(t.value)}
                  >
                    {t.label}
                  </Button>
                ))}
              </div>

              {/* Title + year */}
              <div className="flex gap-1.5">
                <Input
                  placeholder="Search by title…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-8 text-sm"
                  autoFocus
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
                  className="h-8 w-20 text-sm shrink-0"
                />
              </div>

              <Button
                variant="ghost"
                size="sm"
                className="w-full h-6 text-[10px] text-muted-foreground hover:text-foreground"
                onClick={() => setShowManualId(true)}
              >
                Enter ID manually (IMDB / TMDB / TVDB / MediaFusion…)
              </Button>
            </div>

            <ScrollArea className="flex-1 min-h-0">
              {isLoading && searchResults.length === 0 && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              )}
              {!isLoading && !isFetching && searchQuery.length >= 2 && searchResults.length === 0 && (
                <div className="py-5 text-center space-y-2">
                  <p className="text-xs text-muted-foreground">No results found</p>
                  <Button variant="outline" size="sm" className="h-6 text-[10px]" onClick={() => setShowManualId(true)}>
                    Try entering an ID directly
                  </Button>
                </div>
              )}
              {!isLoading && searchQuery.length < 2 && (
                <div className="py-8 text-center text-xs text-muted-foreground">
                  Type at least 2 characters to search
                </div>
              )}
              {searchResults.length > 0 && (
                <div className="p-1">
                  {isFetching && (
                    <div className="flex items-center justify-center py-1 text-xs text-muted-foreground gap-1.5">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      <span>Updating…</span>
                    </div>
                  )}
                  <TooltipProvider>
                    {searchResults.map((result) => {
                      const isExternal = result.source === 'external'
                      const isDisabled = isExternal && requireInternal

                      const row = (
                        <button
                          key={result.id}
                          onClick={() => !isDisabled && handleSelect(result)}
                          disabled={isDisabled}
                          className={`w-full flex items-center gap-2 p-2 rounded-md text-left transition-colors ${
                            isDisabled ? 'opacity-50 cursor-not-allowed' : 'hover:bg-muted cursor-pointer'
                          }`}
                        >
                          {result.poster ? (
                            <img src={result.poster} alt="" className="w-8 h-12 rounded object-cover flex-shrink-0" />
                          ) : (
                            <div className="w-8 h-12 rounded bg-muted flex items-center justify-center flex-shrink-0">
                              {result.type === 'series' ? (
                                <Tv className="h-4 w-4 text-muted-foreground" />
                              ) : (
                                <Film className="h-4 w-4 text-muted-foreground" />
                              )}
                            </div>
                          )}
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium truncate">{result.title}</p>
                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground flex-wrap">
                              {result.year && <span>{result.year}</span>}
                              <Badge variant="outline" className="text-[10px] px-1 py-0">
                                {result.type}
                              </Badge>
                              {isExternal ? (
                                <Badge
                                  variant="secondary"
                                  className={`text-[10px] px-1 py-0 ${requireInternal ? 'bg-yellow-500/20 text-yellow-700' : 'bg-blue-500/20 text-blue-700'}`}
                                >
                                  {result.provider?.toUpperCase() ?? 'External'}
                                </Badge>
                              ) : (
                                <Badge
                                  variant="secondary"
                                  className="text-[10px] px-1 py-0 bg-green-500/20 text-green-700"
                                >
                                  In Library
                                </Badge>
                              )}
                            </div>
                          </div>
                        </button>
                      )

                      if (isDisabled) {
                        return (
                          <Tooltip key={result.id}>
                            <TooltipTrigger asChild>
                              <div>{row}</div>
                            </TooltipTrigger>
                            <TooltipContent side="left" className="text-xs max-w-[200px]">
                              Not in library yet — import this title first, then link it here.
                            </TooltipContent>
                          </Tooltip>
                        )
                      }
                      return row
                    })}
                  </TooltipProvider>
                </div>
              )}
            </ScrollArea>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}
