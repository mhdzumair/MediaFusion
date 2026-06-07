import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  GitPullRequest,
  Filter,
  Clock,
  CheckCircle,
  XCircle,
  Magnet,
  Library,
  ArrowRight,
  Film,
  Zap,
  ShieldOff,
  Search,
  Upload,
} from 'lucide-react'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
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
import { useMyStreamSuggestions, useDeleteStreamSuggestion, useMyStreams, useStreamSuggestionStats } from '@/hooks'
import { useDebounce } from '@/hooks/useDebounce'
import type { StreamSuggestion, StreamSuggestionStatus } from '@/lib/api'
import { MyStreamPosterCard } from './MyStreamPosterCard'
import { StreamEditSuggestionCard } from './StreamEditSuggestionCard'

type MyStreamStatusFilter = 'active' | 'blocked' | 'inactive' | undefined

const streamStatusConfig: Record<StreamSuggestionStatus, { label: string; icon: typeof Clock; color: string }> = {
  pending: { label: 'Pending', icon: Clock, color: 'text-primary' },
  approved: { label: 'Approved', icon: CheckCircle, color: 'text-emerald-500' },
  auto_approved: { label: 'Auto-Approved', icon: Zap, color: 'text-blue-500' },
  rejected: { label: 'Rejected', icon: XCircle, color: 'text-red-500' },
}

const STREAM_TYPE_OPTIONS = [
  { value: undefined, label: 'All Types' },
  { value: 'torrent', label: 'Torrent' },
  { value: 'http', label: 'HTTP' },
  { value: 'youtube', label: 'YouTube' },
  { value: 'usenet', label: 'Usenet' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'acestream', label: 'AceStream' },
] as const

const EDIT_TYPE_OPTIONS = [
  { value: undefined, label: 'All Types' },
  { value: 'field_correction', label: 'Field Correction' },
  { value: 'relink_media', label: 'Relink Media' },
  { value: 'add_media_link', label: 'Add Media Link' },
  { value: 'report_broken', label: 'Report Broken' },
  { value: 'other', label: 'Other' },
] as const

function formatSuggestionType(type: string): string {
  const typeMap: Record<string, string> = {
    report_broken: 'Report Broken',
    field_correction: 'Field Correction',
    language_add: 'Add Language',
    language_remove: 'Remove Language',
    mark_duplicate: 'Mark Duplicate',
    relink_media: 'Relink Media',
    add_media_link: 'Add Media Link',
    other: 'Other',
  }
  return typeMap[type] || type
}

function formatFieldName(fieldName: string | null): string {
  if (!fieldName) return ''
  if (fieldName.startsWith('episode_link:')) {
    const parts = fieldName.split(':')
    if (parts.length >= 3) {
      const field = parts[2]
      const fieldDisplay: Record<string, string> = {
        season_number: 'Season',
        episode_number: 'Episode',
        episode_end: 'Episode End',
      }
      return `Episode Link (${fieldDisplay[field] || field})`
    }
  }
  const nameMap: Record<string, string> = {
    name: 'Name',
    resolution: 'Resolution',
    codec: 'Codec',
    quality: 'Quality',
    bit_depth: 'Bit Depth',
    audio_formats: 'Audio',
    channels: 'Channels',
    hdr_formats: 'HDR',
    source: 'Source',
    languages: 'Languages',
  }
  return nameMap[fieldName] || fieldName
}

function PaginationBar({
  page,
  total,
  pageSize,
  hasMore,
  onPageChange,
}: {
  page: number
  total: number
  pageSize: number
  hasMore: boolean
  onPageChange: (page: number) => void
}) {
  if (total <= pageSize) return null
  return (
    <div className="flex justify-center gap-2 pt-4">
      <Button variant="outline" size="sm" disabled={page === 1} onClick={() => onPageChange(page - 1)}>
        Previous
      </Button>
      <span className="flex items-center px-4 text-sm text-muted-foreground">
        Page {page} of {Math.ceil(total / pageSize)}
      </span>
      <Button variant="outline" size="sm" disabled={!hasMore} onClick={() => onPageChange(page + 1)}>
        Next
      </Button>
    </div>
  )
}

export function ContributionsPage() {
  const [activeTab, setActiveTab] = useState<'my-streams' | 'edits'>('my-streams')

  const [myStreamsStatusFilter, setMyStreamsStatusFilter] = useState<MyStreamStatusFilter>()
  const [myStreamsTypeFilter, setMyStreamsTypeFilter] = useState<string | undefined>()
  const [myStreamsSearch, setMyStreamsSearch] = useState('')
  const [myStreamsPage, setMyStreamsPage] = useState(1)

  const [streamStatusFilter, setStreamStatusFilter] = useState<StreamSuggestionStatus | undefined>()
  const [editTypeFilter, setEditTypeFilter] = useState<string | undefined>()
  const [editsSearch, setEditsSearch] = useState('')
  const [streamPage, setStreamPage] = useState(1)

  const [deleteStreamId, setDeleteStreamId] = useState<string | null>(null)
  const [streamDetailsOpen, setStreamDetailsOpen] = useState<StreamSuggestion | null>(null)

  const debouncedMyStreamsSearch = useDebounce(myStreamsSearch, 300)
  const debouncedEditsSearch = useDebounce(editsSearch, 300)

  const {
    data: myStreams,
    isLoading: myStreamsLoading,
    refetch: refetchMyStreams,
  } = useMyStreams({
    status: myStreamsStatusFilter,
    stream_type: myStreamsTypeFilter,
    search: debouncedMyStreamsSearch || undefined,
    page: myStreamsPage,
    page_size: 12,
  })

  const { data: blockedStreams } = useMyStreams({
    status: 'blocked',
    page: 1,
    page_size: 1,
  })

  const { data: streamSuggestions, isLoading: streamLoading } = useMyStreamSuggestions({
    status: streamStatusFilter,
    suggestion_type: editTypeFilter,
    search: debouncedEditsSearch || undefined,
    page: streamPage,
    page_size: 12,
  })

  const { data: editStats, isLoading: editStatsLoading } = useStreamSuggestionStats()
  const deleteStreamSuggestion = useDeleteStreamSuggestion()

  const handleDeleteStreamSuggestion = async (id: string) => {
    await deleteStreamSuggestion.mutateAsync(id)
    setDeleteStreamId(null)
  }

  const approvedEdits = (editStats?.user_approved ?? 0) + (editStats?.user_auto_approved ?? 0)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl font-semibold tracking-tight flex items-center gap-3">
          <div className="p-2 rounded-md bg-primary/10 border border-primary/20">
            <GitPullRequest className="h-5 w-5 text-primary" />
          </div>
          My Contributions
        </h1>
        <p className="text-muted-foreground mt-1">
          Manage streams you uploaded and track edit suggestions awaiting review
        </p>
      </div>

      <Card className="border-primary/30 hero-gradient">
        <CardContent className="p-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <Upload className="h-5 w-5 text-primary mt-0.5 shrink-0" />
              <div>
                <p className="font-medium">Want to contribute?</p>
                <p className="text-sm text-muted-foreground">
                  Import torrents, NZBs, and other streams from Content Import, or suggest metadata fixes from the
                  Library.
                </p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 shrink-0">
              <Button asChild>
                <Link to="/dashboard/import">
                  <Upload className="mr-2 h-4 w-4" />
                  Import Content
                </Link>
              </Button>
              <Button asChild variant="outline">
                <Link to="/dashboard/library">
                  <Library className="mr-2 h-4 w-4" />
                  Browse Library
                </Link>
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-primary/10">
                <Magnet className="h-4 w-4 text-primary" />
              </div>
              <div>
                {myStreamsLoading ? (
                  <Skeleton className="h-7 w-12" />
                ) : (
                  <p className="text-2xl font-bold">{myStreams?.total ?? 0}</p>
                )}
                <p className="text-xs text-muted-foreground">My Streams</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-primary/10">
                <Clock className="h-4 w-4 text-primary" />
              </div>
              <div>
                {editStatsLoading ? (
                  <Skeleton className="h-7 w-12" />
                ) : (
                  <p className="text-2xl font-bold">{editStats?.user_pending ?? 0}</p>
                )}
                <p className="text-xs text-muted-foreground">Pending Edits</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-emerald-500/10">
                <CheckCircle className="h-4 w-4 text-emerald-500" />
              </div>
              <div>
                {editStatsLoading ? (
                  <Skeleton className="h-7 w-12" />
                ) : (
                  <p className="text-2xl font-bold">{approvedEdits}</p>
                )}
                <p className="text-xs text-muted-foreground">Approved Edits</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-red-500/10">
                <ShieldOff className="h-4 w-4 text-red-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{blockedStreams?.total ?? 0}</p>
                <p className="text-xs text-muted-foreground">Blocked Streams</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'my-streams' | 'edits')} className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <TabsList>
            <TabsTrigger value="my-streams" className="gap-2">
              <Magnet className="h-4 w-4" />
              My Streams
              {myStreams?.total ? (
                <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-xs">
                  {myStreams.total}
                </Badge>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value="edits" className="gap-2">
              <Film className="h-4 w-4" />
              Stream Edits
              {streamSuggestions?.total ? (
                <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-xs">
                  {streamSuggestions.total}
                </Badge>
              ) : null}
            </TabsTrigger>
          </TabsList>

          {activeTab === 'my-streams' && (
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative w-full sm:w-56">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search streams..."
                  value={myStreamsSearch}
                  onChange={(e) => {
                    setMyStreamsSearch(e.target.value)
                    setMyStreamsPage(1)
                  }}
                  className="pl-9 h-9"
                />
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm">
                    <Filter className="mr-2 h-4 w-4" />
                    {myStreamsStatusFilter
                      ? myStreamsStatusFilter.charAt(0).toUpperCase() + myStreamsStatusFilter.slice(1)
                      : 'All Status'}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onClick={() => {
                      setMyStreamsStatusFilter(undefined)
                      setMyStreamsPage(1)
                    }}
                  >
                    All Status
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      setMyStreamsStatusFilter('active')
                      setMyStreamsPage(1)
                    }}
                  >
                    Active
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      setMyStreamsStatusFilter('blocked')
                      setMyStreamsPage(1)
                    }}
                  >
                    Blocked
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      setMyStreamsStatusFilter('inactive')
                      setMyStreamsPage(1)
                    }}
                  >
                    Inactive
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm">
                    {STREAM_TYPE_OPTIONS.find((o) => o.value === myStreamsTypeFilter)?.label ?? 'All Types'}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {STREAM_TYPE_OPTIONS.map((opt) => (
                    <DropdownMenuItem
                      key={opt.label}
                      onClick={() => {
                        setMyStreamsTypeFilter(opt.value)
                        setMyStreamsPage(1)
                      }}
                    >
                      {opt.label}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}

          {activeTab === 'edits' && (
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative w-full sm:w-56">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search edits..."
                  value={editsSearch}
                  onChange={(e) => {
                    setEditsSearch(e.target.value)
                    setStreamPage(1)
                  }}
                  className="pl-9 h-9"
                />
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm">
                    <Filter className="mr-2 h-4 w-4" />
                    {streamStatusFilter ? streamStatusConfig[streamStatusFilter].label : 'All Status'}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onClick={() => {
                      setStreamStatusFilter(undefined)
                      setStreamPage(1)
                    }}
                  >
                    All Status
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      setStreamStatusFilter('pending')
                      setStreamPage(1)
                    }}
                  >
                    Pending
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      setStreamStatusFilter('approved')
                      setStreamPage(1)
                    }}
                  >
                    Approved
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      setStreamStatusFilter('auto_approved')
                      setStreamPage(1)
                    }}
                  >
                    Auto-Approved
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => {
                      setStreamStatusFilter('rejected')
                      setStreamPage(1)
                    }}
                  >
                    Rejected
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm">
                    {EDIT_TYPE_OPTIONS.find((o) => o.value === editTypeFilter)?.label ?? 'All Types'}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {EDIT_TYPE_OPTIONS.map((opt) => (
                    <DropdownMenuItem
                      key={opt.label}
                      onClick={() => {
                        setEditTypeFilter(opt.value)
                        setStreamPage(1)
                      }}
                    >
                      {opt.label}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
        </div>

        <TabsContent value="my-streams" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="font-display">Your Streams</CardTitle>
              <CardDescription>
                Only streams linked to your account (non-anonymous uploads). Edit, annotate, relink, block, or delete.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {myStreamsLoading ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  {[...Array(4)].map((_, i) => (
                    <Skeleton key={i} className="h-48 rounded-xl" />
                  ))}
                </div>
              ) : !myStreams?.items.length ? (
                <div className="text-center py-12 text-muted-foreground">
                  <Magnet className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>No streams found.</p>
                  <p className="text-sm mt-2">
                    {debouncedMyStreamsSearch || myStreamsStatusFilter || myStreamsTypeFilter
                      ? 'Try adjusting your search or filters.'
                      : 'Import content with your profile linked (non-anonymous) to manage streams here.'}
                  </p>
                  {!debouncedMyStreamsSearch && !myStreamsStatusFilter && (
                    <Button asChild className="mt-4">
                      <Link to="/dashboard/import">
                        <Upload className="mr-2 h-4 w-4" />
                        Import Content
                      </Link>
                    </Button>
                  )}
                </div>
              ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                  {myStreams.items.map((stream) => (
                    <MyStreamPosterCard key={stream.id} stream={stream} onUpdated={() => refetchMyStreams()} />
                  ))}
                  <div className="lg:col-span-2">
                    <PaginationBar
                      page={myStreamsPage}
                      total={myStreams.total}
                      pageSize={12}
                      hasMore={myStreams.has_more}
                      onPageChange={setMyStreamsPage}
                    />
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="edits" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="font-display">Your Stream Edit Suggestions</CardTitle>
              <CardDescription>
                Corrections and link changes you submitted on any stream — only your own suggestions are shown.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {streamLoading ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  {[...Array(4)].map((_, i) => (
                    <Skeleton key={i} className="h-36 rounded-xl" />
                  ))}
                </div>
              ) : !streamSuggestions?.suggestions.length ? (
                <div className="text-center py-12 text-muted-foreground">
                  <Film className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>No stream edit suggestions found.</p>
                  <p className="text-sm mt-2">
                    {debouncedEditsSearch || streamStatusFilter || editTypeFilter
                      ? 'Try adjusting your search or filters.'
                      : 'Browse content and suggest corrections from stream menus.'}
                  </p>
                </div>
              ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                  {streamSuggestions.suggestions.map((suggestion) => (
                    <StreamEditSuggestionCard
                      key={suggestion.id}
                      suggestion={suggestion}
                      onViewDetails={() => setStreamDetailsOpen(suggestion)}
                      onWithdraw={() => setDeleteStreamId(suggestion.id)}
                    />
                  ))}
                  <div className="lg:col-span-2">
                    <PaginationBar
                      page={streamPage}
                      total={streamSuggestions.total}
                      pageSize={12}
                      hasMore={streamSuggestions.has_more}
                      onPageChange={setStreamPage}
                    />
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Dialog open={!!streamDetailsOpen} onOpenChange={() => setStreamDetailsOpen(null)}>
        <DialogContent
          scrollMode="contained"
          className="sm:max-w-[600px] max-h-[90vh] flex flex-col overflow-hidden min-h-0"
        >
          <DialogHeader className="shrink-0">
            <DialogTitle>Stream Edit Details</DialogTitle>
          </DialogHeader>
          <ScrollArea className="flex-1 min-h-0 pr-1">
            {streamDetailsOpen && (
              <div className="space-y-4 py-4">
                <div className="flex items-center gap-4 flex-wrap">
                  <Badge
                    variant="secondary"
                    className={`${streamStatusConfig[streamDetailsOpen.status]?.color} bg-opacity-10`}
                  >
                    {streamStatusConfig[streamDetailsOpen.status]?.label}
                  </Badge>
                  <Badge variant="outline">{formatSuggestionType(streamDetailsOpen.suggestion_type)}</Badge>
                  {streamDetailsOpen.field_name && (
                    <Badge variant="outline">{formatFieldName(streamDetailsOpen.field_name)}</Badge>
                  )}
                  {streamDetailsOpen.was_auto_approved && (
                    <Badge variant="outline" className="text-blue-500">
                      <Zap className="h-3 w-3 mr-1" />
                      Auto-Approved
                    </Badge>
                  )}
                </div>

                {streamDetailsOpen.stream_name && (
                  <div>
                    <Label className="text-muted-foreground">Stream</Label>
                    <p className="text-sm font-mono break-all">{streamDetailsOpen.stream_name}</p>
                  </div>
                )}

                {streamDetailsOpen.source_media_title && (
                  <div>
                    <Label className="text-muted-foreground">Linked Content</Label>
                    <p className="text-sm">{streamDetailsOpen.source_media_title}</p>
                  </div>
                )}

                {(streamDetailsOpen.current_value || streamDetailsOpen.suggested_value) && (
                  <div className="p-4 rounded-md bg-muted/50 space-y-2">
                    <Label className="text-muted-foreground">Change</Label>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto_1fr] sm:gap-2 items-start">
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">Current</p>
                        <p className="text-sm text-red-400 break-all">{streamDetailsOpen.current_value || '(empty)'}</p>
                      </div>
                      <ArrowRight className="hidden h-4 w-4 text-muted-foreground mt-5 sm:block" />
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">Suggested</p>
                        <p className="text-sm text-emerald-400 break-all">
                          {streamDetailsOpen.suggested_value || '(empty)'}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {streamDetailsOpen.reason && (
                  <div>
                    <Label className="text-muted-foreground">Reason</Label>
                    <p className="text-sm mt-1">{streamDetailsOpen.reason}</p>
                  </div>
                )}

                <div className="flex flex-wrap justify-between gap-2 text-sm text-muted-foreground">
                  <span>Submitted: {new Date(streamDetailsOpen.created_at).toLocaleString()}</span>
                  {streamDetailsOpen.reviewed_at && (
                    <span>Reviewed: {new Date(streamDetailsOpen.reviewed_at).toLocaleString()}</span>
                  )}
                </div>

                {streamDetailsOpen.review_notes && (
                  <div className="p-4 rounded-md bg-muted/50">
                    <Label className="text-muted-foreground">Reviewer Notes</Label>
                    <p className="text-sm mt-1">{streamDetailsOpen.review_notes}</p>
                  </div>
                )}
              </div>
            )}
          </ScrollArea>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteStreamId} onOpenChange={() => setDeleteStreamId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Withdraw stream edit?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently withdraw your stream edit suggestion. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => deleteStreamId && handleDeleteStreamSuggestion(deleteStreamId)}
            >
              Withdraw
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
