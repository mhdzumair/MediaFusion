import { useState } from 'react'
import { Check, EyeOff, Eye, Loader2, Pencil, Plus, RefreshCw, Search, Tags, Trash2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useDebounce } from '@/hooks'
import {
  useAdminGenres,
  useCreateGenre,
  useDeleteGenre,
  useDeleteGenreType,
  useReloadGenresCache,
  useUpdateGenre,
} from '@/hooks'
import type { GenreDetail, MediaTypeWire } from '@/lib/api'

const PAGE_SIZE = 50

const MEDIA_TYPES: MediaTypeWire[] = ['movie', 'series', 'tv', 'events']

const MEDIA_TYPE_LABELS: Record<MediaTypeWire, string> = {
  movie: 'Movie',
  series: 'Series',
  tv: 'TV',
  events: 'Events',
}

const MEDIA_TYPE_COLORS: Record<MediaTypeWire, string> = {
  movie: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  series: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  tv: 'bg-green-500/15 text-green-400 border-green-500/30',
  events: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
}

// ─── Dialogs ──────────────────────────────────────────────────────────────────

interface CreateDialogProps {
  open: boolean
  onClose: () => void
}

function CreateGenreDialog({ open, onClose }: CreateDialogProps) {
  const [name, setName] = useState('')
  const [selected, setSelected] = useState<Set<MediaTypeWire>>(new Set())
  const create = useCreateGenre()

  const toggle = (mt: MediaTypeWire) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(mt)) {
        next.delete(mt)
      } else {
        next.add(mt)
      }
      return next
    })

  const handleSubmit = async () => {
    if (!name.trim()) return
    await create.mutateAsync({ name: name.trim(), media_types: [...selected] })
    setName('')
    setSelected(new Set())
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Genre</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div className="space-y-1">
            <Label htmlFor="genre-name">Genre name</Label>
            <Input
              id="genre-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Documentary"
              onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
            />
          </div>
          <div className="space-y-2">
            <Label>Supported media types</Label>
            <div className="flex flex-wrap gap-2">
              {MEDIA_TYPES.map((mt) => (
                <button
                  key={mt}
                  type="button"
                  onClick={() => toggle(mt)}
                  className={`px-3 py-1 rounded-full border text-sm font-medium transition-all ${
                    selected.has(mt) ? MEDIA_TYPE_COLORS[mt] : 'bg-muted/40 text-muted-foreground border-border'
                  }`}
                >
                  {selected.has(mt) && <Check className="inline h-3 w-3 mr-1" />}
                  {MEDIA_TYPE_LABELS[mt]}
                </button>
              ))}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!name.trim() || create.isPending}>
            {create.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Add Genre
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface EditDialogProps {
  genre: GenreDetail
  onClose: () => void
}

function EditGenreDialog({ genre, onClose }: EditDialogProps) {
  const [name, setName] = useState(genre.name)
  const [typeStates, setTypeStates] = useState<Record<MediaTypeWire, { present: boolean; is_hidden: boolean }>>(() => {
    const init: Record<string, { present: boolean; is_hidden: boolean }> = {}
    for (const mt of MEDIA_TYPES) {
      const existing = genre.types.find((t) => t.media_type === mt)
      init[mt] = existing ? { present: true, is_hidden: existing.is_hidden } : { present: false, is_hidden: false }
    }
    return init as Record<MediaTypeWire, { present: boolean; is_hidden: boolean }>
  })

  const update = useUpdateGenre()
  const deleteType = useDeleteGenreType()

  const handleSubmit = async () => {
    const typeUpdates = MEDIA_TYPES.filter((mt) => typeStates[mt].present).map((mt) => ({
      media_type: mt,
      is_hidden: typeStates[mt].is_hidden,
    }))

    // Compute which existing types were removed.
    const toRemove = genre.types.filter((t) => !typeStates[t.media_type as MediaTypeWire]?.present)
    for (const t of toRemove) {
      await deleteType.mutateAsync({ id: genre.id, mediaType: t.media_type })
    }

    await update.mutateAsync({
      id: genre.id,
      req: { name: name.trim() !== genre.name ? name.trim() : undefined, types: typeUpdates },
    })
    onClose()
  }

  const togglePresent = (mt: MediaTypeWire) =>
    setTypeStates((prev) => ({
      ...prev,
      [mt]: { present: !prev[mt].present, is_hidden: prev[mt].is_hidden },
    }))

  const toggleHidden = (mt: MediaTypeWire) =>
    setTypeStates((prev) => ({
      ...prev,
      [mt]: { ...prev[mt], is_hidden: !prev[mt].is_hidden },
    }))

  const isPending = update.isPending || deleteType.isPending

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Edit Genre</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div className="space-y-1">
            <Label htmlFor="edit-genre-name">Name</Label>
            <Input id="edit-genre-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>Media type pairings</Label>
            <p className="text-xs text-muted-foreground">
              Toggle a type on/off, or hide it from Stremio and search without removing it.
            </p>
            <div className="space-y-2">
              {MEDIA_TYPES.map((mt) => {
                const state = typeStates[mt]
                return (
                  <div
                    key={mt}
                    className={`flex items-center justify-between rounded-lg border p-2 transition-all ${
                      state.present ? 'bg-card' : 'opacity-40 bg-muted/20'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => togglePresent(mt)}
                        className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
                          state.present ? 'bg-primary border-primary' : 'border-muted-foreground'
                        }`}
                      >
                        {state.present && <Check className="h-3 w-3 text-primary-foreground" />}
                      </button>
                      <span className={`text-sm font-medium px-2 py-0.5 rounded-full border ${MEDIA_TYPE_COLORS[mt]}`}>
                        {MEDIA_TYPE_LABELS[mt]}
                      </span>
                    </div>
                    {state.present && (
                      <button
                        type="button"
                        onClick={() => toggleHidden(mt)}
                        className={`flex items-center gap-1 text-xs px-2 py-1 rounded border transition-colors ${
                          state.is_hidden
                            ? 'bg-red-500/15 text-red-400 border-red-500/30'
                            : 'bg-green-500/15 text-green-400 border-green-500/30'
                        }`}
                        title={state.is_hidden ? 'Hidden — click to show' : 'Visible — click to hide'}
                      >
                        {state.is_hidden ? (
                          <>
                            <EyeOff className="h-3 w-3" /> Hidden
                          </>
                        ) : (
                          <>
                            <Eye className="h-3 w-3" /> Visible
                          </>
                        )}
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!name.trim() || isPending}>
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ─── Genre row ────────────────────────────────────────────────────────────────

interface GenreRowProps {
  genre: GenreDetail
  onEdit: (genre: GenreDetail) => void
}

function GenreRow({ genre, onEdit }: GenreRowProps) {
  const update = useUpdateGenre()
  const deleteGenre = useDeleteGenre()
  const [confirmDelete, setConfirmDelete] = useState(false)

  const toggleHideForType = (mediaType: string, currentHidden: boolean) => {
    update.mutate({
      id: genre.id,
      req: { types: [{ media_type: mediaType as MediaTypeWire, is_hidden: !currentHidden }] },
    })
  }

  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-3 p-4 rounded-lg border bg-card hover:bg-card/80 transition-colors">
      {/* Name + usage */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm truncate">{genre.name}</span>
          <span className="text-xs text-muted-foreground shrink-0">{genre.usage_count.toLocaleString()} media</span>
        </div>
      </div>

      {/* Type badges with per-type hide toggle */}
      <div className="flex flex-wrap gap-1.5 items-center">
        {genre.types.length === 0 && <span className="text-xs text-muted-foreground italic">no types</span>}
        {genre.types.map((t) => (
          <button
            key={t.media_type}
            type="button"
            onClick={() => toggleHideForType(t.media_type, t.is_hidden)}
            disabled={update.isPending}
            title={
              t.is_hidden
                ? `Hidden for ${MEDIA_TYPE_LABELS[t.media_type as MediaTypeWire]} — click to show`
                : `Visible for ${MEDIA_TYPE_LABELS[t.media_type as MediaTypeWire]} — click to hide`
            }
            className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border transition-all ${
              t.is_hidden
                ? 'opacity-50 line-through bg-muted/30 text-muted-foreground border-border'
                : MEDIA_TYPE_COLORS[t.media_type as MediaTypeWire]
            }`}
          >
            {t.is_hidden ? <EyeOff className="h-2.5 w-2.5" /> : null}
            {MEDIA_TYPE_LABELS[t.media_type as MediaTypeWire] ?? t.media_type}
          </button>
        ))}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0">
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => onEdit(genre)} title="Edit genre">
          <Pencil className="h-3.5 w-3.5" />
        </Button>
        {confirmDelete ? (
          <div className="flex items-center gap-1">
            <span className="text-xs text-destructive">Delete?</span>
            <Button
              variant="destructive"
              size="icon"
              className="h-7 w-7"
              onClick={() => deleteGenre.mutate(genre.id)}
              disabled={deleteGenre.isPending}
            >
              {deleteGenre.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Check className="h-3.5 w-3.5" />
              )}
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setConfirmDelete(false)}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        ) : (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-destructive hover:text-destructive"
            onClick={() => setConfirmDelete(true)}
            title="Delete genre"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function GenreManagementPage() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const debouncedSearch = useDebounce(search, 300)
  const [typeFilter, setTypeFilter] = useState<MediaTypeWire | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [editing, setEditing] = useState<GenreDetail | null>(null)

  const { data, isLoading } = useAdminGenres({
    page,
    page_size: PAGE_SIZE,
    search: debouncedSearch || undefined,
    media_type: typeFilter ?? undefined,
  })
  const reloadCache = useReloadGenresCache()

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
          <div className="p-2 rounded-xl bg-gradient-to-br from-violet-500 to-violet-500/80 shadow-lg shadow-violet-500/20">
            <Tags className="h-5 w-5 text-white" />
          </div>
          Genre Management
        </h1>
        <p className="text-muted-foreground mt-1">
          Manage genres and their supported media types. Click a type badge on any genre to hide/show it in Stremio and
          catalog search.
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <CardTitle className="text-base">
              Genres
              {data && (
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  ({data.total.toLocaleString()} total)
                </span>
              )}
            </CardTitle>
            <div className="flex items-center gap-2 sm:ml-auto">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search genres…"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value)
                    setPage(1)
                  }}
                  className="pl-8 w-48"
                />
              </div>
              <Button
                variant="outline"
                size="icon"
                onClick={() => reloadCache.mutate()}
                disabled={reloadCache.isPending}
                title="Clear genre cache"
              >
                <RefreshCw className={`h-4 w-4 ${reloadCache.isPending ? 'animate-spin' : ''}`} />
              </Button>
              <Button onClick={() => setShowCreate(true)} size="sm">
                <Plus className="h-4 w-4 mr-1" />
                Add Genre
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {/* Type filter strip */}
          <div className="flex flex-wrap items-center gap-1.5 pb-2 border-b">
            <button
              type="button"
              onClick={() => {
                setTypeFilter(null)
                setPage(1)
              }}
              className={`px-3 py-1 rounded-full border text-xs font-medium transition-all ${
                typeFilter === null
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'bg-muted/40 text-muted-foreground border-border hover:border-muted-foreground'
              }`}
            >
              All
            </button>
            {MEDIA_TYPES.map((mt) => (
              <button
                key={mt}
                type="button"
                onClick={() => {
                  setTypeFilter(typeFilter === mt ? null : mt)
                  setPage(1)
                }}
                className={`px-3 py-1 rounded-full border text-xs font-medium transition-all ${
                  typeFilter === mt
                    ? MEDIA_TYPE_COLORS[mt]
                    : 'bg-muted/40 text-muted-foreground border-border hover:border-muted-foreground'
                }`}
              >
                {MEDIA_TYPE_LABELS[mt]}
              </button>
            ))}
            <span className="ml-auto text-xs text-muted-foreground">
              Click a type badge on any genre row to hide/show it. <EyeOff className="inline h-3 w-3" /> = hidden.
            </span>
          </div>

          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full" />
              ))}
            </div>
          ) : data?.items.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {debouncedSearch ? `No genres matching "${debouncedSearch}"` : 'No genres found.'}
            </p>
          ) : (
            <div className="space-y-1.5">
              {data?.items.map((genre) => (
                <GenreRow key={genre.id} genre={genre} onEdit={setEditing} />
              ))}
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-3">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                Previous
              </Button>
              <span className="text-sm text-muted-foreground">
                {page} / {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
              >
                Next
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Dialogs */}
      {showCreate && <CreateGenreDialog open onClose={() => setShowCreate(false)} />}
      {editing && <EditGenreDialog genre={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}
