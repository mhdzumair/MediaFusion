import { useState } from 'react'
import { Ban, CheckCircle2, Loader2, Plus, RefreshCw, Search, ShieldCheck, Trash2, XCircle } from 'lucide-react'

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
  useKeywordWhitelist,
  useReloadKeywordCache,
  useToggleKeyword,
  useUpdateKeywordScope,
} from '@/hooks'

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
        <Button variant="outline" size="sm" onClick={() => reloadCache.mutate()} disabled={reloadCache.isPending}>
          {reloadCache.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          <span className="ml-1.5">Reload Cache</span>
        </Button>
      </div>

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
                <span className="font-medium text-cyan-400">stream</span> — torrent/stream titles only.{' '}
                <span className="font-medium text-orange-400">media</span> — media title/description only.
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
