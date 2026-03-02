import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowRightLeft, ExternalLink, Loader2, Search, ShieldAlert } from 'lucide-react'

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
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useDebounce } from '@/hooks/useDebounce'
import { useToast } from '@/hooks/use-toast'
import { adminApi, scrapersApi, type MetadataItem, type MigrateMediaResponse } from '@/lib/api'

import { ModeratorMediaPoster } from './ModeratorMediaPoster'

const SEARCH_MIN_LENGTH = 2

function getCanonicalExternalId(item: MetadataItem): string {
  if (item.external_ids?.imdb) return item.external_ids.imdb
  if (item.external_ids?.tmdb) return `tmdb:${item.external_ids.tmdb}`
  if (item.external_ids?.tvdb) return `tvdb:${item.external_ids.tvdb}`
  if (item.external_ids?.mal) return `mal:${item.external_ids.mal}`
  return `mf:${item.id}`
}

function getMediaTypeLabel(type: MetadataItem['type']): string {
  if (type === 'movie') return 'Movie'
  if (type === 'series') return 'Series'
  return 'TV'
}

function renderMediaSummary(item: MetadataItem): string {
  const parts = [
    getMediaTypeLabel(item.type),
    item.year ? String(item.year) : null,
    `#${item.id}`,
    `${item.total_streams} streams`,
  ].filter((part): part is string => Boolean(part))
  return parts.join(' • ')
}

function getMediaDetailPath(item: MetadataItem): string {
  return `/app/dashboard/content/${item.type}/${item.id}`
}

async function searchMetadata(query: string): Promise<MetadataItem[]> {
  const trimmedQuery = query.trim()
  if (!trimmedQuery) return []

  // Support direct ID search via #1234 or raw 1234.
  const normalizedId = trimmedQuery.startsWith('#') ? trimmedQuery.slice(1) : trimmedQuery
  if (/^\d+$/.test(normalizedId)) {
    const mediaId = Number(normalizedId)
    if (Number.isInteger(mediaId) && mediaId > 0) {
      try {
        const item = await adminApi.getMetadata(mediaId)
        return [item]
      } catch {
        return []
      }
    }
  }

  const response = await adminApi.listMetadata({
    page: 1,
    per_page: 8,
    search: trimmedQuery,
  })
  return response.items
}

interface SearchColumnProps {
  title: string
  inputId: string
  query: string
  onQueryChange: (value: string) => void
  selected: MetadataItem | null
  onSelect: (item: MetadataItem) => void
  results: MetadataItem[]
  isLoading: boolean
  allowDuplicateSelection?: boolean
}

function SearchColumn({
  title,
  inputId,
  query,
  onQueryChange,
  selected,
  onSelect,
  results,
  isLoading,
  allowDuplicateSelection = true,
}: SearchColumnProps) {
  const trimmedQuery = query.trim()

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label htmlFor={inputId}>{title}</Label>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id={inputId}
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search title, external ID, or #media_id"
            className="pl-9"
          />
        </div>
      </div>

      <Card className="border-border/60">
        <CardContent className="p-3 space-y-2">
          {trimmedQuery.length < SEARCH_MIN_LENGTH ? (
            <p className="text-xs text-muted-foreground">
              Type at least {SEARCH_MIN_LENGTH} characters to search metadata.
            </p>
          ) : isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Searching...
            </div>
          ) : results.length === 0 ? (
            <p className="text-xs text-muted-foreground">No matching metadata found.</p>
          ) : (
            <ScrollArea className="h-44 pr-1">
              <div className="space-y-2">
                {results.map((item) => {
                  const isSelected = selected?.id === item.id
                  const isDisabled = !allowDuplicateSelection && selected?.id !== item.id
                  return (
                    <div
                      key={item.id}
                      className={`rounded-lg border px-3 py-2 transition ${
                        isSelected
                          ? 'border-primary bg-primary/10'
                          : 'border-border/60 hover:border-primary/40 hover:bg-muted/40'
                      } ${isDisabled ? 'opacity-60 cursor-not-allowed' : ''}`}
                    >
                      <div className="flex items-center gap-3">
                        <button
                          type="button"
                          disabled={isDisabled}
                          onClick={() => onSelect(item)}
                          className="flex-1 min-w-0 text-left"
                        >
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-14 rounded overflow-hidden border border-border/50 bg-muted/20 shrink-0">
                              <ModeratorMediaPoster
                                mediaType={item.type}
                                mediaId={item.id}
                                imdbId={item.external_ids?.imdb}
                                posterUrl={item.poster}
                                title={item.title}
                                fallbackIconSizeClassName="h-4 w-4"
                              />
                            </div>
                            <div className="min-w-0">
                              <p className="text-sm font-medium truncate">{item.title}</p>
                              <p className="text-xs text-muted-foreground mt-0.5">{renderMediaSummary(item)}</p>
                            </div>
                          </div>
                        </button>
                        <a
                          href={getMediaDetailPath(item)}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-primary hover:underline inline-flex items-center gap-1 shrink-0"
                          onClick={(event) => event.stopPropagation()}
                        >
                          Open
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      </div>
                    </div>
                  )
                })}
              </div>
            </ScrollArea>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function SelectedPreview({ item }: { item: MetadataItem | null }) {
  if (!item) {
    return <p className="text-xs text-muted-foreground">No media selected.</p>
  }

  return (
    <div className="rounded-lg border border-border/60 p-3 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className="w-16 h-24 rounded overflow-hidden border border-border/50 bg-muted/20 shrink-0">
            <ModeratorMediaPoster
              mediaType={item.type}
              mediaId={item.id}
              imdbId={item.external_ids?.imdb}
              posterUrl={item.poster}
              title={item.title}
              fallbackIconSizeClassName="h-5 w-5"
            />
          </div>
          <div className="min-w-0">
            <p className="font-medium leading-tight truncate">{item.title}</p>
            <p className="text-xs text-muted-foreground mt-1">{renderMediaSummary(item)}</p>
          </div>
        </div>
        <Badge variant="outline">{getMediaTypeLabel(item.type)}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        Canonical ID: <span className="text-foreground">{getCanonicalExternalId(item)}</span>
      </p>
      <a
        href={getMediaDetailPath(item)}
        target="_blank"
        rel="noreferrer"
        className="text-xs text-primary hover:underline inline-flex items-center gap-1"
      >
        Open item page
        <ExternalLink className="h-3.5 w-3.5" />
      </a>
      {item.is_user_created ? (
        <p className="text-xs text-amber-600">User-created metadata cannot be used in duplicate migration.</p>
      ) : null}
      {item.total_streams === 0 ? (
        <p className="text-xs text-muted-foreground">
          No streams are currently linked. Migration will effectively delete this duplicate if selected as source.
        </p>
      ) : null}
    </div>
  )
}

export function MediaMigrationTab() {
  const { toast } = useToast()
  const [fromQuery, setFromQuery] = useState('')
  const [toQuery, setToQuery] = useState('')
  const [fromMedia, setFromMedia] = useState<MetadataItem | null>(null)
  const [toMedia, setToMedia] = useState<MetadataItem | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [lastResult, setLastResult] = useState<MigrateMediaResponse | null>(null)

  const debouncedFromQuery = useDebounce(fromQuery, 300)
  const debouncedToQuery = useDebounce(toQuery, 300)

  const { data: fromResults = [], isFetching: isSearchingFrom } = useQuery({
    queryKey: ['moderator', 'migration', 'search', 'from', debouncedFromQuery],
    queryFn: () => searchMetadata(debouncedFromQuery),
    enabled: debouncedFromQuery.trim().length >= SEARCH_MIN_LENGTH,
    staleTime: 10_000,
  })

  const { data: toResults = [], isFetching: isSearchingTo } = useQuery({
    queryKey: ['moderator', 'migration', 'search', 'to', debouncedToQuery],
    queryFn: () => searchMetadata(debouncedToQuery),
    enabled: debouncedToQuery.trim().length >= SEARCH_MIN_LENGTH,
    staleTime: 10_000,
  })

  const validationError = useMemo(() => {
    if (!fromMedia || !toMedia) return 'Select both source and target metadata items.'
    if (fromMedia.id === toMedia.id) return 'Source and target media IDs must be different.'
    if (fromMedia.type !== toMedia.type) return 'Source and target media must have the same media type.'
    if (fromMedia.is_user_created || toMedia.is_user_created) {
      return 'Only non-user-created media can be migrated.'
    }
    return null
  }, [fromMedia, toMedia])

  const migrateMutation = useMutation({
    mutationFn: async () => {
      if (!fromMedia || !toMedia) {
        throw new Error('Select source and target metadata before migration.')
      }

      return scrapersApi.migrateMedia({
        from_media_id: fromMedia.id,
        to_media_id: toMedia.id,
      })
    },
    onSuccess: (result) => {
      setLastResult(result)
      setConfirmOpen(false)
      toast({
        title: 'Media migrated',
        description: `Moved ${result.stream_links_migrated} stream links and ${result.file_links_migrated} file links.`,
      })
      setFromMedia(null)
      setFromQuery('')
    },
    onError: (error: Error) => {
      toast({
        variant: 'destructive',
        title: 'Migration failed',
        description: error.message,
      })
    },
  })

  const handleSwap = () => {
    const nextFrom = toMedia
    const nextTo = fromMedia
    setFromMedia(nextFrom)
    setToMedia(nextTo)
    setFromQuery(nextFrom ? `${nextFrom.title} #${nextFrom.id}` : '')
    setToQuery(nextTo ? `${nextTo.title} #${nextTo.id}` : '')
  }

  const canMigrate = !!fromMedia && !!toMedia && !validationError && !migrateMutation.isPending

  return (
    <div className="space-y-5">
      <Card className="glass border-border/50">
        <CardContent className="p-5 space-y-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="font-semibold flex items-center gap-2">
                <ArrowRightLeft className="h-4 w-4 text-primary" />
                Duplicate Media Migration
              </h3>
              <p className="text-sm text-muted-foreground mt-1">
                Search metadata, preview both items, then migrate links from source to target. Source metadata is
                removed after migration.
              </p>
            </div>
            <div className="text-xs px-2 py-1 rounded-md bg-amber-500/10 text-amber-600 flex items-center gap-1">
              <ShieldAlert className="h-3.5 w-3.5" />
              Moderator/Admin
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr] gap-4 items-start">
            <SearchColumn
              title="Source (migrate from)"
              inputId="fromMediaSearch"
              query={fromQuery}
              onQueryChange={setFromQuery}
              selected={fromMedia}
              onSelect={setFromMedia}
              results={fromResults}
              isLoading={isSearchingFrom}
            />

            <div className="flex lg:h-full items-center justify-center">
              <Button
                variant="outline"
                type="button"
                onClick={handleSwap}
                disabled={!fromMedia && !toMedia}
                className="rounded-xl"
              >
                <ArrowRightLeft className="h-4 w-4 mr-2" />
                Swap
              </Button>
            </div>

            <SearchColumn
              title="Target (migrate to)"
              inputId="toMediaSearch"
              query={toQuery}
              onQueryChange={setToQuery}
              selected={toMedia}
              onSelect={setToMedia}
              results={toResults}
              isLoading={isSearchingTo}
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Source Preview</Label>
              <SelectedPreview item={fromMedia} />
            </div>
            <div className="space-y-2">
              <Label>Target Preview</Label>
              <SelectedPreview item={toMedia} />
            </div>
          </div>

          {validationError ? <p className="text-sm text-destructive">{validationError}</p> : null}

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-end gap-3">
            <Button type="button" onClick={() => setConfirmOpen(true)} disabled={!canMigrate} className="rounded-xl">
              {migrateMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <ArrowRightLeft className="h-4 w-4 mr-2" />
              )}
              Confirm Migration
            </Button>
          </div>

          {lastResult ? (
            <div className="text-sm text-muted-foreground border border-border/50 rounded-lg p-3">
              <p>
                Migrated <span className="font-medium text-foreground">#{lastResult.from_media_id}</span> to{' '}
                <span className="font-medium text-foreground">#{lastResult.to_media_id}</span>.
              </p>
              <p className="mt-1">
                Stream links moved:{' '}
                <span className="font-medium text-foreground">{lastResult.stream_links_migrated}</span> | File links
                moved: <span className="font-medium text-foreground"> {lastResult.file_links_migrated}</span>
              </p>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Duplicate Migration</AlertDialogTitle>
            <AlertDialogDescription>
              {fromMedia && toMedia
                ? `Move links from "${fromMedia.title}" (#${fromMedia.id}) to "${toMedia.title}" (#${toMedia.id}) and remove the source metadata.`
                : 'Select source and target metadata first.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={migrateMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                type="button"
                onClick={() => migrateMutation.mutate()}
                disabled={!canMigrate}
                className="rounded-xl"
              >
                {migrateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                Proceed
              </Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
