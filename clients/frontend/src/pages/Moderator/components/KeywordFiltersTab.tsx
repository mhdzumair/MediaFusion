import { useState } from 'react'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useDebounce } from '@/hooks'
import {
  useAddKeyword,
  useAddWhitelistPhrase,
  useDeleteKeyword,
  useDeleteWhitelistPhrase,
  useKeywordFilters,
  useKeywordSyncStatus,
  useKeywordWhitelist,
  useReloadKeywordCache,
  useResetKeywordFilters,
  useToggleKeyword,
  useUpdateKeywordScope,
} from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import type {
  FileSyncStatus,
  KeywordSyncStatus,
  RecomputeJobStatus,
  RuntimeStreamKeywordsStatus,
} from '@/lib/api/keyword-filters'

const PAGE_SIZE = 50

type Scope = 'all' | 'stream' | 'media'

const SCOPE_OPTIONS: { value: Scope; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'stream', label: 'Stream' },
  { value: 'media', label: 'Media' },
]

function ScopeBadge({ scope }: { scope: string }) {
  if (scope === 'stream') {
    return (
      <Badge className="text-xs px-1.5 py-0 bg-cyan-500/15 text-cyan-400 border-cyan-500/30 hover:bg-cyan-500/20">
        stream
      </Badge>
    )
  }
  if (scope === 'media') {
    return (
      <Badge className="text-xs px-1.5 py-0 bg-orange-500/15 text-orange-400 border-orange-500/30 hover:bg-orange-500/20">
        media
      </Badge>
    )
  }
  return (
    <Badge className="text-xs px-1.5 py-0 bg-blue-500/15 text-blue-400 border-blue-500/30 hover:bg-blue-500/20">
      all
    </Badge>
  )
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) return 'Never'
  return new Date(value).toLocaleString()
}

function SyncStateBadge({ ok, okLabel, badLabel }: { ok: boolean; okLabel: string; badLabel: string }) {
  if (ok) {
    return (
      <Badge className="text-xs bg-emerald-500/15 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20">
        {okLabel}
      </Badge>
    )
  }
  return (
    <Badge className="text-xs bg-amber-500/15 text-amber-400 border-amber-500/30 hover:bg-amber-500/20">
      {badLabel}
    </Badge>
  )
}

function FileSyncRow({ label, status }: { label: string; status: FileSyncStatus }) {
  return (
    <div className="rounded-md border border-border/50 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        <SyncStateBadge ok={status.in_sync} okLabel="In sync" badLabel="Out of sync" />
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>Embedded keywords</span>
        <span className="text-right font-mono">{status.embedded_keyword_count}</span>
        <span>DB file keywords</span>
        <span className="text-right font-mono">{status.db_file_keyword_count}</span>
        {status.embedded_whitelist_count > 0 && (
          <>
            <span>Embedded whitelist</span>
            <span className="text-right font-mono">{status.embedded_whitelist_count}</span>
            <span>DB file whitelist</span>
            <span className="text-right font-mono">{status.db_file_whitelist_count}</span>
          </>
        )}
        <span>Last file sync</span>
        <span className="text-right">{formatTimestamp(status.synced_at)}</span>
      </div>
    </div>
  )
}

function RuntimeStreamRow({ status }: { status: RuntimeStreamKeywordsStatus }) {
  return (
    <div className="rounded-md border border-border/50 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">Stream keywords (runtime)</span>
        <Badge className="text-xs bg-cyan-500/15 text-cyan-400 border-cyan-500/30 hover:bg-cyan-500/20">
          Runtime only
        </Badge>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>Embedded keywords</span>
        <span className="text-right font-mono">{status.embedded_keyword_count}</span>
        <span>In-memory cache</span>
        <span className="text-right font-mono">{status.cache_keyword_count}</span>
        <span>Admin stream overrides</span>
        <span className="text-right font-mono">{status.admin_override_count}</span>
      </div>
      <p className="text-xs text-muted-foreground">
        Stream titles are filtered in memory when serving or scraping — no DB column or batch recompute.
      </p>
    </div>
  )
}

function RecomputeRow({ label, status }: { label: string; status: RecomputeJobStatus }) {
  const stateLabel = status.in_progress ? 'Running' : status.up_to_date ? 'Complete' : 'Pending'
  const stateOk = status.up_to_date && !status.in_progress

  return (
    <div className="rounded-md border border-border/50 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        <div className="flex items-center gap-2">
          {status.in_progress && <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400" />}
          <SyncStateBadge ok={stateOk} okLabel={stateLabel} badLabel={stateLabel} />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>Target version</span>
        <span className="text-right font-mono truncate">{status.target_version}</span>
        <span>Recorded version</span>
        <span className="text-right font-mono truncate">{status.recorded_version ?? '—'}</span>
        {status.in_progress && (
          <>
            <span>Lease holder</span>
            <span className="text-right truncate">{status.lease_owner ?? '—'}</span>
            <span>Lease renewed</span>
            <span className="text-right">{formatTimestamp(status.lease_synced_at)}</span>
          </>
        )}
      </div>
    </div>
  )
}

function KeywordSyncStatusPanel({ status, isLoading }: { status?: KeywordSyncStatus; isLoading: boolean }) {
  if (isLoading) {
    return (
      <Card className="glass border-border/50">
        <CardContent className="pt-6">
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    )
  }
  if (!status) return null

  const hasOverrides = status.admin_overrides.keywords > 0 || status.admin_overrides.whitelist > 0
  const needsAttention = !status.file_sync.media.in_sync || !status.recompute.up_to_date || status.recompute.in_progress

  return (
    <Card className="glass border-border/50">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <RefreshCw className="h-4 w-4 text-blue-400" />
          Keyword Sync Jobs
          {needsAttention ? (
            <Badge className="ml-auto text-xs bg-amber-500/15 text-amber-400 border-amber-500/30">
              Attention needed
            </Badge>
          ) : (
            <Badge className="ml-auto text-xs bg-emerald-500/15 text-emerald-400 border-emerald-500/30">Healthy</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Media keywords sync into the database and refresh the media blocked-flag column. Stream keywords load from the
          embedded file at runtime (plus admin stream overrides) and are applied when streams are served.
          {hasOverrides &&
            ` Admin overrides: ${status.admin_overrides.keywords} keywords, ${status.admin_overrides.whitelist} whitelist phrases.`}
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <FileSyncRow label="Media file sync" status={status.file_sync.media} />
          <RuntimeStreamRow status={status.file_sync.stream} />
          <RecomputeRow label="Media blocked-flag recompute" status={status.recompute} />
        </div>

        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
          <span>
            In-memory cache:{' '}
            <span className="font-mono text-foreground">
              {status.cache.media_keywords} media / {status.cache.stream_keywords} stream / {status.cache.whitelist}{' '}
              whitelist
            </span>
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

export function KeywordFiltersTab() {
  // ── Blocked keywords state ────────────────────────────────────────────────
  const [keywordPage, setKeywordPage] = useState(1)
  const [keywordSearch, setKeywordSearch] = useState('')
  const debouncedSearch = useDebounce(keywordSearch, 300)
  const [newKeyword, setNewKeyword] = useState('')
  const [newScope, setNewScope] = useState<Scope>('all')
  const [scopeFilter, setScopeFilter] = useState<string>('')

  const { data: keywordsData, isLoading: keywordsLoading } = useKeywordFilters({
    page: keywordPage,
    page_size: PAGE_SIZE,
    search: debouncedSearch || undefined,
    scope: scopeFilter || undefined,
  })
  const addKeyword = useAddKeyword()
  const toggleKeyword = useToggleKeyword()
  const updateScope = useUpdateKeywordScope()
  const deleteKeyword = useDeleteKeyword()
  const reloadCache = useReloadKeywordCache()
  const resetKeywords = useResetKeywordFilters()
  const { data: syncStatus, isLoading: syncStatusLoading } = useKeywordSyncStatus()
  const { toast } = useToast()

  // ── Whitelist state ───────────────────────────────────────────────────────
  const [whitelistPage, setWhitelistPage] = useState(1)
  const [newPhrase, setNewPhrase] = useState('')
  const [newReason, setNewReason] = useState('')

  const { data: whitelistData, isLoading: whitelistLoading } = useKeywordWhitelist({
    page: whitelistPage,
    page_size: PAGE_SIZE,
  })
  const addPhrase = useAddWhitelistPhrase()
  const deletePhrase = useDeleteWhitelistPhrase()

  // ── Handlers ─────────────────────────────────────────────────────────────
  const handleAddKeyword = () => {
    const kw = newKeyword.trim()
    if (!kw) return
    addKeyword.mutate({ keyword: kw, scope: newScope }, { onSuccess: () => setNewKeyword('') })
  }

  const handleAddPhrase = () => {
    const ph = newPhrase.trim()
    if (!ph) return
    addPhrase.mutate(
      { phrase: ph, reason: newReason.trim() || undefined },
      {
        onSuccess: () => {
          setNewPhrase('')
          setNewReason('')
        },
      },
    )
  }

  const handleReset = async () => {
    try {
      await resetKeywords.mutateAsync()
      toast({
        title: 'Keywords reset',
        description: 'Bundled default keywords were restored and background recompute jobs were scheduled.',
      })
    } catch (error) {
      toast({
        title: 'Reset failed',
        description: error instanceof Error ? error.message : 'Failed to reset keyword filters.',
        variant: 'destructive',
      })
    }
  }

  const keywordTotal = keywordsData?.total ?? 0
  const keywordPages = Math.ceil(keywordTotal / PAGE_SIZE)
  const whitelistTotal = whitelistData?.total ?? 0
  const whitelistPages = Math.ceil(whitelistTotal / PAGE_SIZE)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Ban className="h-5 w-5 text-red-500" />
            Keyword Filters
          </h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Block contributions containing these keywords. Whitelist phrases bypass all keyword checks.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" size="sm" disabled={resetKeywords.isPending}>
                {resetKeywords.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="h-4 w-4" />
                )}
                <span className="ml-1.5">Reset to Defaults</span>
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle className="flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5 text-amber-500" />
                  Reset Keyword Filters
                </AlertDialogTitle>
                <AlertDialogDescription>
                  This removes all admin-added keywords and whitelist phrases, then re-imports the bundled default lists
                  compiled into the running server. Disabled or deleted default keywords are restored. Background
                  recompute jobs will refresh blocked flags afterward.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={handleReset} disabled={resetKeywords.isPending}>
                  Reset keywords
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <Button variant="outline" size="sm" onClick={() => reloadCache.mutate()} disabled={reloadCache.isPending}>
            {reloadCache.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            <span className="ml-1.5">Reload Cache</span>
          </Button>
        </div>
      </div>

      <KeywordSyncStatusPanel status={syncStatus} isLoading={syncStatusLoading} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* ── Blocked Keywords ─────────────────────────────────────────── */}
        <Card className="glass border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <XCircle className="h-4 w-4 text-red-500" />
              Blocked Keywords
              {keywordTotal > 0 && (
                <Badge variant="secondary" className="ml-auto text-xs">
                  {keywordTotal}
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* Add keyword */}
            <div className="space-y-2">
              <div className="flex gap-2">
                <Input
                  placeholder="e.g. brazzers"
                  value={newKeyword}
                  onChange={(e) => setNewKeyword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleAddKeyword()}
                  className="h-8 text-sm"
                />
                <Select value={newScope} onValueChange={(v) => setNewScope(v as Scope)}>
                  <SelectTrigger className="h-8 w-28 text-xs shrink-0">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SCOPE_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value} className="text-xs">
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  onClick={handleAddKeyword}
                  disabled={addKeyword.isPending || !newKeyword.trim()}
                  className="h-8 shrink-0"
                >
                  {addKeyword.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                <span className="font-medium text-blue-400">all</span> — blocks streams &amp; media.{' '}
                <span className="font-medium text-cyan-400">stream</span> — admin stream override (runtime).{' '}
                <span className="font-medium text-orange-400">media</span> — media file only (not in bundled stream
                list).
              </p>
            </div>

            {/* Search + scope filter */}
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  placeholder="Search keywords…"
                  value={keywordSearch}
                  onChange={(e) => {
                    setKeywordSearch(e.target.value)
                    setKeywordPage(1)
                  }}
                  className="h-8 pl-8 text-sm"
                />
              </div>
              <Select
                value={scopeFilter || 'all-scopes'}
                onValueChange={(v) => {
                  setScopeFilter(v === 'all-scopes' ? '' : v)
                  setKeywordPage(1)
                }}
              >
                <SelectTrigger className="h-8 w-28 text-xs shrink-0">
                  <SelectValue placeholder="Scope" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all-scopes" className="text-xs">
                    All scopes
                  </SelectItem>
                  {SCOPE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value} className="text-xs">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* List */}
            <div className="space-y-1 max-h-80 overflow-y-auto pr-1">
              {keywordsLoading ? (
                Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)
              ) : keywordsData?.items.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">No keywords found.</p>
              ) : (
                keywordsData?.items.map((kw) => (
                  <div key={kw.id} className="flex items-center gap-2 px-2 py-1 rounded-md hover:bg-muted/50 group">
                    <span
                      className={`flex-1 text-sm font-mono truncate ${!kw.is_active ? 'line-through text-muted-foreground' : ''}`}
                    >
                      {kw.keyword}
                    </span>
                    <ScopeBadge scope={kw.scope} />
                    <Select value={kw.scope} onValueChange={(v) => updateScope.mutate({ id: kw.id, scope: v })}>
                      <SelectTrigger className="h-6 w-20 text-xs shrink-0 opacity-0 group-hover:opacity-100 border-0 bg-transparent px-1">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {SCOPE_OPTIONS.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value} className="text-xs">
                            {opt.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 opacity-0 group-hover:opacity-100 shrink-0"
                      title={kw.is_active ? 'Disable' : 'Enable'}
                      onClick={() => toggleKeyword.mutate({ id: kw.id, is_active: !kw.is_active })}
                      disabled={toggleKeyword.isPending}
                    >
                      {kw.is_active ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 opacity-0 group-hover:opacity-100 shrink-0 text-destructive hover:text-destructive"
                      onClick={() => deleteKeyword.mutate(kw.id)}
                      disabled={deleteKeyword.isPending}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))
              )}
            </div>

            {/* Pagination */}
            {keywordPages > 1 && (
              <div className="flex items-center justify-between pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={keywordPage <= 1}
                  onClick={() => setKeywordPage((p) => p - 1)}
                >
                  Previous
                </Button>
                <span className="text-xs text-muted-foreground">
                  {keywordPage} / {keywordPages}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={keywordPage >= keywordPages}
                  onClick={() => setKeywordPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ── Whitelist ─────────────────────────────────────────────────── */}
        <Card className="glass border-border/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-emerald-500" />
              Whitelist
              {whitelistTotal > 0 && (
                <Badge variant="secondary" className="ml-auto text-xs">
                  {whitelistTotal}
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* Add phrase */}
            <div className="space-y-2">
              <div className="flex gap-2">
                <Input
                  placeholder='e.g. "sex education"'
                  value={newPhrase}
                  onChange={(e) => setNewPhrase(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleAddPhrase()}
                  className="h-8 text-sm"
                />
                <Button
                  size="sm"
                  onClick={handleAddPhrase}
                  disabled={addPhrase.isPending || !newPhrase.trim()}
                  className="h-8 shrink-0"
                >
                  {addPhrase.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
                </Button>
              </div>
              <div>
                <Label className="text-xs text-muted-foreground">Reason (optional)</Label>
                <Input
                  placeholder="e.g. TV series, not adult content"
                  value={newReason}
                  onChange={(e) => setNewReason(e.target.value)}
                  className="h-8 text-sm mt-1"
                />
              </div>
            </div>

            <p className="text-xs text-muted-foreground">
              Titles containing a whitelisted phrase are allowed even if they contain a blocked keyword.
            </p>

            {/* List */}
            <div className="space-y-1 max-h-72 overflow-y-auto pr-1">
              {whitelistLoading ? (
                Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)
              ) : whitelistData?.items.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">No whitelist phrases.</p>
              ) : (
                whitelistData?.items.map((ph) => (
                  <div key={ph.id} className="flex items-start gap-2 px-2 py-1.5 rounded-md hover:bg-muted/50 group">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-mono truncate">{ph.phrase}</p>
                      {ph.reason && <p className="text-xs text-muted-foreground truncate">{ph.reason}</p>}
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 opacity-0 group-hover:opacity-100 shrink-0 text-destructive hover:text-destructive mt-0.5"
                      onClick={() => deletePhrase.mutate(ph.id)}
                      disabled={deletePhrase.isPending}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))
              )}
            </div>

            {/* Pagination */}
            {whitelistPages > 1 && (
              <div className="flex items-center justify-between pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={whitelistPage <= 1}
                  onClick={() => setWhitelistPage((p) => p - 1)}
                >
                  Previous
                </Button>
                <span className="text-xs text-muted-foreground">
                  {whitelistPage} / {whitelistPages}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={whitelistPage >= whitelistPages}
                  onClick={() => setWhitelistPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
