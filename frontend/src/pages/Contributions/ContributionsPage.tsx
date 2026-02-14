import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  GitPullRequest,
  Filter,
  Clock,
  CheckCircle,
  XCircle,
  FileEdit,
  Magnet,
  Tag,
  Eye,
  Trash2,
  MoreVertical,
  Library,
  ArrowRight,
  Film,
  Zap,
  ArrowUpDown,
  ExternalLink,
  Copy,
  Check,
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
import {
  useContributions,
  useContributionStats,
  useDeleteContribution,
  useMyStreamSuggestions,
  useDeleteStreamSuggestion,
} from '@/hooks'
import type { ContributionStatus, ContributionType, StreamSuggestion, StreamSuggestionStatus } from '@/lib/api'

const statusConfig: Record<ContributionStatus, { label: string; icon: typeof Clock; color: string }> = {
  pending: { label: 'Pending', icon: Clock, color: 'text-primary' },
  approved: { label: 'Approved', icon: CheckCircle, color: 'text-emerald-500' },
  rejected: { label: 'Rejected', icon: XCircle, color: 'text-red-500' },
}

const streamStatusConfig: Record<StreamSuggestionStatus, { label: string; icon: typeof Clock; color: string }> = {
  pending: { label: 'Pending', icon: Clock, color: 'text-primary' },
  approved: { label: 'Approved', icon: CheckCircle, color: 'text-emerald-500' },
  auto_approved: { label: 'Auto-Approved', icon: Zap, color: 'text-blue-500' },
  rejected: { label: 'Rejected', icon: XCircle, color: 'text-red-500' },
}

const typeConfig: Record<ContributionType, { label: string; icon: typeof FileEdit; color: string }> = {
  metadata: { label: 'Metadata Fix', icon: FileEdit, color: 'text-primary bg-primary/10' },
  stream: { label: 'New Stream', icon: Magnet, color: 'text-blue-500 bg-blue-500/10' },
  torrent: { label: 'Torrent Imports', icon: Tag, color: 'text-orange-500 bg-orange-500/10' },
}

// Helper to format bytes
function formatBytes(bytes: number | string): string {
  const size = typeof bytes === 'string' ? parseFloat(bytes) : bytes
  if (isNaN(size) || size === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(size) / Math.log(k))
  return parseFloat((size / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// Parse contribution data into displayable fields
function parseContributionData(
  data: Record<string, unknown>,
  type: ContributionType,
): { label: string; value: string; type: 'text' | 'link' | 'badge' | 'size' | 'code' }[] {
  const fields: { label: string; value: string; type: 'text' | 'link' | 'badge' | 'size' | 'code' }[] = []

  if (type === 'torrent' || type === 'stream') {
    if (data.name) fields.push({ label: 'Torrent Name', value: String(data.name), type: 'text' })
    if (data.title) fields.push({ label: 'Title', value: String(data.title), type: 'text' })
    if (data.meta_type) fields.push({ label: 'Content Type', value: String(data.meta_type), type: 'badge' })
    if (data.meta_id) fields.push({ label: 'Media ID', value: String(data.meta_id), type: 'link' })
    if (data.info_hash) fields.push({ label: 'Info Hash', value: String(data.info_hash), type: 'code' })
    if (data.resolution) fields.push({ label: 'Resolution', value: String(data.resolution), type: 'badge' })
    if (data.quality) fields.push({ label: 'Quality', value: String(data.quality), type: 'badge' })
    if (data.codec) fields.push({ label: 'Codec', value: String(data.codec), type: 'badge' })
    if (data.total_size) fields.push({ label: 'Size', value: String(data.total_size), type: 'size' })
    if (data.file_count) fields.push({ label: 'Files', value: String(data.file_count), type: 'text' })
    if (data.languages && Array.isArray(data.languages) && data.languages.length > 0) {
      fields.push({ label: 'Languages', value: (data.languages as string[]).join(', '), type: 'text' })
    }
    if (data.catalogs && Array.isArray(data.catalogs) && data.catalogs.length > 0) {
      fields.push({ label: 'Catalogs', value: (data.catalogs as string[]).join(', '), type: 'text' })
    }
    // Show contribution visibility
    fields.push({
      label: 'Uploader',
      value: data.is_anonymous === true ? 'Anonymous' : 'Linked to profile',
      type: 'badge',
    })
  } else if (type === 'metadata') {
    // For metadata contributions, show what was changed
    Object.entries(data).forEach(([key, value]) => {
      if (value !== null && value !== undefined) {
        fields.push({
          label: key.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase()),
          value: typeof value === 'object' ? JSON.stringify(value) : String(value),
          type: 'text',
        })
      }
    })
  }

  return fields
}

// Format stream suggestion type for display
function formatSuggestionType(type: string): string {
  const typeMap: Record<string, string> = {
    report_broken: 'Report Broken',
    field_correction: 'Field Correction',
    language_add: 'Add Language',
    language_remove: 'Remove Language',
    mark_duplicate: 'Mark Duplicate',
    other: 'Other',
  }
  return typeMap[type] || type
}

// Format field name for display
function formatFieldName(fieldName: string | null): string {
  if (!fieldName) return ''

  // Handle episode link fields (episode_link:123:season_number)
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

// Contribution details content component for cleaner display
function ContributionDetailsContent({
  contribution,
}: {
  contribution: {
    id: string
    contribution_type: ContributionType
    status: ContributionStatus
    target_id?: string
    data: Record<string, unknown>
    created_at: string
    reviewed_at?: string
    review_notes?: string
  }
}) {
  const [copied, setCopied] = useState(false)
  const parsedFields = parseContributionData(contribution.data, contribution.contribution_type)
  const torrentData = contribution.data as Record<string, unknown>

  // Extract values for TypeScript
  const torrentName = torrentData.name ? String(torrentData.name) : null
  const torrentTitle = torrentData.title ? String(torrentData.title) : null
  const displayName = torrentName || torrentTitle
  const magnetLink = torrentData.magnet_link ? String(torrentData.magnet_link) : null

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="space-y-5 py-2">
      {/* Status and Type Badges */}
      <div className="flex items-center gap-3 flex-wrap">
        <Badge variant="secondary" className={`${statusConfig[contribution.status]?.color ?? ''} bg-opacity-10`}>
          {(() => {
            const StatusIcon = statusConfig[contribution.status]?.icon ?? Clock
            return <StatusIcon className="mr-1.5 h-3.5 w-3.5" />
          })()}
          {statusConfig[contribution.status]?.label ?? 'Unknown'}
        </Badge>
        <Badge variant="outline" className="capitalize">
          {typeConfig[contribution.contribution_type]?.label ?? 'Unknown'}
        </Badge>
        {contribution.target_id && (
          <Badge variant="outline" className="font-mono text-xs">
            {contribution.target_id}
          </Badge>
        )}
      </div>

      {/* Main Content - Torrent Name */}
      {displayName && (
        <div className="p-4 rounded-md hero-gradient border border-primary/20">
          <p className="text-xs text-muted-foreground mb-1.5">
            {contribution.contribution_type === 'torrent' ? 'Torrent Name' : 'Title'}
          </p>
          <p className="font-medium text-lg break-all font-display">{displayName}</p>
        </div>
      )}

      {/* Structured Data Fields */}
      {parsedFields.length > 0 && (
        <div className="space-y-3">
          <h4 className="text-sm font-medium text-muted-foreground">Details</h4>
          <div className="grid grid-cols-2 gap-3">
            {parsedFields
              .filter((f) => f.label !== 'Torrent Name' && f.label !== 'Title')
              .map((field, idx) => (
                <div
                  key={idx}
                  className={`p-3 rounded-md bg-muted/40 border border-border/50 ${
                    field.type === 'code' || (field.type === 'text' && String(field.value).length > 40)
                      ? 'col-span-2'
                      : ''
                  }`}
                >
                  <p className="text-xs text-muted-foreground mb-1.5">{field.label}</p>
                  {field.type === 'badge' ? (
                    <Badge variant="secondary" className="text-sm">
                      {field.value}
                    </Badge>
                  ) : field.type === 'link' ? (
                    <a
                      href={field.value.startsWith('tt') ? `https://www.imdb.com/title/${field.value}` : `#`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-medium text-primary hover:underline inline-flex items-center gap-1.5"
                    >
                      {field.value}
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  ) : field.type === 'size' ? (
                    <p className="text-sm font-medium">{formatBytes(field.value)}</p>
                  ) : field.type === 'code' ? (
                    <div className="flex items-center gap-2">
                      <code className="text-xs font-mono bg-background/50 px-2 py-1 rounded flex-1 break-all">
                        {field.value}
                      </code>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 flex-shrink-0"
                        onClick={() => copyToClipboard(field.value)}
                      >
                        {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                      </Button>
                    </div>
                  ) : (
                    <p className="text-sm font-medium break-all">{field.value}</p>
                  )}
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Magnet Link */}
      {magnetLink && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-muted-foreground">Magnet Link</h4>
          <div className="p-3 rounded-md bg-muted/40 border border-border/50">
            <div className="flex items-start gap-2">
              <code className="text-xs font-mono break-all line-clamp-3 flex-1">{magnetLink}</code>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 flex-shrink-0"
                onClick={() => magnetLink && copyToClipboard(magnetLink)}
              >
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Timeline */}
      <div className="flex items-center justify-between text-xs text-muted-foreground pt-2 border-t border-border/50">
        <span>Submitted: {new Date(contribution.created_at).toLocaleString()}</span>
        {contribution.reviewed_at && <span>Reviewed: {new Date(contribution.reviewed_at).toLocaleString()}</span>}
      </div>

      {/* Reviewer Notes */}
      {contribution.review_notes && (
        <div className="p-4 rounded-md bg-primary/5 border border-primary/20">
          <p className="text-xs font-medium text-primary dark:text-primary mb-1.5">Reviewer Notes</p>
          <p className="text-sm">{contribution.review_notes}</p>
        </div>
      )}
    </div>
  )
}

export function ContributionsPage() {
  const [activeTab, setActiveTab] = useState<'metadata' | 'streams'>('streams')
  const [statusFilter, setStatusFilter] = useState<ContributionStatus | undefined>()
  const [streamStatusFilter, setStreamStatusFilter] = useState<StreamSuggestionStatus | undefined>()
  const [typeFilter, setTypeFilter] = useState<ContributionType | undefined>()
  const [page, setPage] = useState(1)
  const [streamPage, setStreamPage] = useState(1)
  const [detailsDialogOpen, setDetailsDialogOpen] = useState<string | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [deleteStreamId, setDeleteStreamId] = useState<string | null>(null)
  const [streamDetailsOpen, setStreamDetailsOpen] = useState<StreamSuggestion | null>(null)

  // Metadata contributions
  const { data: contributions, isLoading } = useContributions({
    contribution_status: statusFilter,
    contribution_type: typeFilter,
    page,
    page_size: 20,
  })

  // Stream suggestions
  const { data: streamSuggestions, isLoading: streamLoading } = useMyStreamSuggestions({
    status: streamStatusFilter,
    page: streamPage,
    page_size: 20,
  })

  const { data: stats, isLoading: statsLoading } = useContributionStats()
  const deleteContribution = useDeleteContribution()
  const deleteStreamSuggestion = useDeleteStreamSuggestion()

  const handleDelete = async (id: string) => {
    await deleteContribution.mutateAsync(id)
    setDeleteId(null)
  }

  const handleDeleteStreamSuggestion = async (id: string) => {
    await deleteStreamSuggestion.mutateAsync(id)
    setDeleteStreamId(null)
  }

  const selectedContribution = contributions?.items.find((c) => c.id === detailsDialogOpen)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="font-display text-3xl font-semibold tracking-tight flex items-center gap-3">
          <div className="p-2 rounded-md bg-primary/10 border border-primary/20">
            <GitPullRequest className="h-5 w-5 text-primary" />
          </div>
          My Contributions
        </h1>
        <p className="text-muted-foreground mt-1">Track the status of your metadata and stream corrections</p>
      </div>

      {/* Info Banner */}
      <Card className="border-primary/30 hero-gradient">
        <CardContent className="p-4">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Library className="h-5 w-5 text-primary" />
              <div>
                <p className="font-medium">Want to contribute?</p>
                <p className="text-sm text-muted-foreground">
                  Browse content in the Library and use the "Edit Metadata" button to suggest corrections.
                </p>
              </div>
            </div>
            <Button asChild variant="outline" className="shrink-0">
              <Link to="/dashboard/library">
                Go to Library
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-primary/10">
                <GitPullRequest className="h-4 w-4 text-primary" />
              </div>
              <div>
                {statsLoading ? (
                  <Skeleton className="h-7 w-12" />
                ) : (
                  <p className="text-2xl font-bold">{stats?.total_contributions ?? 0}</p>
                )}
                <p className="text-xs text-muted-foreground">Total Contributions</p>
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
                {statsLoading ? (
                  <Skeleton className="h-7 w-12" />
                ) : (
                  <p className="text-2xl font-bold">{stats?.pending ?? 0}</p>
                )}
                <p className="text-xs text-muted-foreground">Pending Review</p>
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
                {statsLoading ? (
                  <Skeleton className="h-7 w-12" />
                ) : (
                  <p className="text-2xl font-bold">{stats?.approved ?? 0}</p>
                )}
                <p className="text-xs text-muted-foreground">Approved</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-red-500/10">
                <XCircle className="h-4 w-4 text-red-500" />
              </div>
              <div>
                {statsLoading ? (
                  <Skeleton className="h-7 w-12" />
                ) : (
                  <p className="text-2xl font-bold">{stats?.rejected ?? 0}</p>
                )}
                <p className="text-xs text-muted-foreground">Rejected</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Contributions Tabs */}
      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'metadata' | 'streams')} className="space-y-4">
        <div className="flex items-center justify-between">
          <TabsList>
            <TabsTrigger value="streams" className="gap-2">
              <Film className="h-4 w-4" />
              Stream Edits
              {streamSuggestions?.total ? (
                <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-xs">
                  {streamSuggestions.total}
                </Badge>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value="metadata" className="gap-2">
              <FileEdit className="h-4 w-4" />
              Metadata & Torrents
              {contributions?.total ? (
                <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-xs">
                  {contributions.total}
                </Badge>
              ) : null}
            </TabsTrigger>
          </TabsList>

          {/* Tab-specific filters */}
          {activeTab === 'streams' && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <Filter className="mr-2 h-4 w-4" />
                  {streamStatusFilter ? streamStatusConfig[streamStatusFilter].label : 'All Status'}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setStreamStatusFilter(undefined)}>All Status</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setStreamStatusFilter('pending')}>Pending</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setStreamStatusFilter('approved')}>Approved</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setStreamStatusFilter('auto_approved')}>
                  Auto-Approved
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setStreamStatusFilter('rejected')}>Rejected</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          {activeTab === 'metadata' && (
            <div className="flex items-center gap-2">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm">
                    <Filter className="mr-2 h-4 w-4" />
                    {statusFilter ? statusConfig[statusFilter].label : 'All Status'}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setStatusFilter(undefined)}>All Status</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setStatusFilter('pending')}>Pending</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setStatusFilter('approved')}>Approved</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setStatusFilter('rejected')}>Rejected</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm">
                    <ArrowUpDown className="mr-2 h-4 w-4" />
                    {typeFilter ? typeConfig[typeFilter].label : 'All Types'}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setTypeFilter(undefined)}>All Types</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setTypeFilter('metadata')}>Metadata Fix</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setTypeFilter('stream')}>New Stream</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setTypeFilter('torrent')}>Torrent Imports</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
        </div>

        {/* Stream Suggestions Tab */}
        <TabsContent value="streams" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="font-display">Your Stream Edit Suggestions</CardTitle>
              <CardDescription>Corrections to stream metadata (quality, language, episode info, etc.)</CardDescription>
            </CardHeader>
            <CardContent>
              {streamLoading ? (
                <div className="space-y-4">
                  {[...Array(5)].map((_, i) => (
                    <div key={i} className="flex items-center gap-4 p-4 rounded-xl border border-border/50">
                      <Skeleton className="h-10 w-10 rounded-lg" />
                      <div className="flex-1 space-y-2">
                        <Skeleton className="h-4 w-3/4" />
                        <Skeleton className="h-3 w-1/2" />
                      </div>
                      <Skeleton className="h-6 w-20" />
                    </div>
                  ))}
                </div>
              ) : !streamSuggestions?.suggestions.length ? (
                <div className="text-center py-12 text-muted-foreground">
                  <Film className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>No stream edit suggestions yet.</p>
                  <p className="text-sm mt-2">Browse content and click on streams to suggest corrections.</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {streamSuggestions.suggestions.map((suggestion) => {
                    const status = streamStatusConfig[suggestion.status]
                    const StatusIcon = status?.icon ?? Clock

                    return (
                      <div
                        key={suggestion.id}
                        className="flex items-start gap-4 p-4 rounded-md border border-border/50 hover:border-primary/30 transition-colors"
                      >
                        {/* Type Icon */}
                        <div className="p-2 rounded-lg bg-blue-500/10 flex-shrink-0">
                          <Film className="h-5 w-5 text-blue-500" />
                        </div>

                        {/* Info */}
                        <div className="flex-1 min-w-0 space-y-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <p className="font-medium">{formatSuggestionType(suggestion.suggestion_type)}</p>
                            {suggestion.field_name && (
                              <Badge variant="outline" className="text-xs">
                                {formatFieldName(suggestion.field_name)}
                              </Badge>
                            )}
                          </div>

                          {suggestion.stream_name && (
                            <p className="text-sm text-muted-foreground truncate" title={suggestion.stream_name}>
                              Stream: {suggestion.stream_name}
                            </p>
                          )}

                          {/* Show value changes */}
                          {suggestion.field_name && (suggestion.current_value || suggestion.suggested_value) && (
                            <div className="flex items-center gap-2 text-sm">
                              <span
                                className="text-red-400 line-through truncate max-w-[150px]"
                                title={suggestion.current_value || ''}
                              >
                                {suggestion.current_value || '(empty)'}
                              </span>
                              <ArrowRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                              <span
                                className="text-emerald-400 truncate max-w-[150px]"
                                title={suggestion.suggested_value || ''}
                              >
                                {suggestion.suggested_value || '(empty)'}
                              </span>
                            </div>
                          )}

                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <span>{new Date(suggestion.created_at).toLocaleDateString()}</span>
                            {suggestion.was_auto_approved && (
                              <>
                                <span>•</span>
                                <Badge variant="outline" className="text-xs h-5 px-1.5">
                                  <Zap className="h-3 w-3 mr-1" />
                                  Auto
                                </Badge>
                              </>
                            )}
                            {suggestion.reviewed_at && (
                              <>
                                <span>•</span>
                                <span>Reviewed {new Date(suggestion.reviewed_at).toLocaleDateString()}</span>
                              </>
                            )}
                          </div>
                        </div>

                        {/* Status */}
                        <Badge variant="secondary" className={`${status?.color} bg-opacity-10 flex-shrink-0`}>
                          <StatusIcon className="mr-1 h-3 w-3" />
                          {status?.label}
                        </Badge>

                        {/* Actions */}
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8 flex-shrink-0">
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => setStreamDetailsOpen(suggestion)}>
                              <Eye className="mr-2 h-4 w-4" />
                              View Details
                            </DropdownMenuItem>
                            {suggestion.status === 'pending' && (
                              <DropdownMenuItem
                                className="text-destructive"
                                onClick={() => setDeleteStreamId(suggestion.id)}
                              >
                                <Trash2 className="mr-2 h-4 w-4" />
                                Withdraw
                              </DropdownMenuItem>
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    )
                  })}

                  {/* Pagination */}
                  {streamSuggestions && streamSuggestions.total > 20 && (
                    <div className="flex justify-center gap-2 pt-4">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={streamPage === 1}
                        onClick={() => setStreamPage((p) => p - 1)}
                      >
                        Previous
                      </Button>
                      <span className="flex items-center px-4 text-sm text-muted-foreground">
                        Page {streamPage} of {Math.ceil(streamSuggestions.total / 20)}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!streamSuggestions.has_more}
                        onClick={() => setStreamPage((p) => p + 1)}
                      >
                        Next
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Metadata Contributions Tab */}
        <TabsContent value="metadata" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="font-display">Your Metadata & Torrent Contributions</CardTitle>
              <CardDescription>Metadata corrections and torrent imports</CardDescription>
            </CardHeader>
            <CardContent>
              {isLoading ? (
                <div className="space-y-4">
                  {[...Array(5)].map((_, i) => (
                    <div key={i} className="flex items-center gap-4 p-4 rounded-xl border border-border/50">
                      <Skeleton className="h-10 w-10 rounded-lg" />
                      <div className="flex-1 space-y-2">
                        <Skeleton className="h-4 w-3/4" />
                        <Skeleton className="h-3 w-1/2" />
                      </div>
                      <Skeleton className="h-6 w-20" />
                    </div>
                  ))}
                </div>
              ) : contributions?.items.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground">
                  <FileEdit className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>No metadata contributions yet.</p>
                  <p className="text-sm mt-2">
                    Browse content in the Library and use the "Edit Metadata" button to suggest corrections.
                  </p>
                  <Button asChild className="mt-4">
                    <Link to="/dashboard/library">
                      <Library className="mr-2 h-4 w-4" />
                      Browse Library
                    </Link>
                  </Button>
                </div>
              ) : (
                <div className="space-y-3">
                  {contributions?.items.map((item) => {
                    const status = statusConfig[item.status]
                    const type = typeConfig[item.contribution_type]
                    const StatusIcon = status?.icon ?? Clock
                    const TypeIcon = type?.icon ?? FileEdit

                    return (
                      <div
                        key={item.id}
                        className="flex items-center gap-4 p-4 rounded-md border border-border/50 hover:border-primary/30 transition-colors"
                      >
                        {/* Type Icon */}
                        <div className="p-2 rounded-md bg-primary/10 flex-shrink-0">
                          <TypeIcon className="h-5 w-5 text-primary" />
                        </div>

                        {/* Info */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <p className="font-medium truncate">{type?.label}</p>
                            {item.target_id && (
                              <Badge variant="outline" className="text-xs font-mono">
                                {item.target_id}
                              </Badge>
                            )}
                          </div>
                          <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <span>{new Date(item.created_at).toLocaleDateString()}</span>
                            {item.reviewed_at && (
                              <>
                                <span>•</span>
                                <span>Reviewed {new Date(item.reviewed_at).toLocaleDateString()}</span>
                              </>
                            )}
                          </div>
                        </div>

                        {/* Status */}
                        <Badge variant="secondary" className={`${status?.color} bg-opacity-10`}>
                          <StatusIcon className="mr-1 h-3 w-3" />
                          {status?.label}
                        </Badge>

                        {/* Actions */}
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8">
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => setDetailsDialogOpen(item.id)}>
                              <Eye className="mr-2 h-4 w-4" />
                              View Details
                            </DropdownMenuItem>
                            {item.status === 'pending' && (
                              <DropdownMenuItem className="text-destructive" onClick={() => setDeleteId(item.id)}>
                                <Trash2 className="mr-2 h-4 w-4" />
                                Withdraw
                              </DropdownMenuItem>
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    )
                  })}

                  {/* Pagination */}
                  {contributions && contributions.total > 20 && (
                    <div className="flex justify-center gap-2 pt-4">
                      <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
                        Previous
                      </Button>
                      <span className="flex items-center px-4 text-sm text-muted-foreground">
                        Page {page} of {Math.ceil(contributions.total / 20)}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!contributions.has_more}
                        onClick={() => setPage((p) => p + 1)}
                      >
                        Next
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Details Dialog */}
      <Dialog open={!!detailsDialogOpen} onOpenChange={() => setDetailsDialogOpen(null)}>
        <DialogContent className="sm:max-w-[700px] max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {selectedContribution &&
                (() => {
                  const TypeIcon = typeConfig[selectedContribution.contribution_type]?.icon ?? FileEdit
                  const typeColor =
                    typeConfig[selectedContribution.contribution_type]?.color ?? 'text-primary bg-primary/10'
                  return (
                    <div className={`p-2 rounded-lg ${typeColor.split(' ')[1]}`}>
                      <TypeIcon className={`h-4 w-4 ${typeColor.split(' ')[0]}`} />
                    </div>
                  )
                })()}
              Contribution Details
            </DialogTitle>
          </DialogHeader>
          {selectedContribution && <ContributionDetailsContent contribution={selectedContribution} />}
        </DialogContent>
      </Dialog>

      {/* Delete/Withdraw Dialog */}
      <AlertDialog open={!!deleteId} onOpenChange={() => setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Withdraw contribution?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently withdraw your contribution. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => deleteId && handleDelete(deleteId)}
            >
              Withdraw
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Stream Suggestion Details Dialog */}
      <Dialog open={!!streamDetailsOpen} onOpenChange={() => setStreamDetailsOpen(null)}>
        <DialogContent className="sm:max-w-[600px]">
          <DialogHeader>
            <DialogTitle>Stream Edit Details</DialogTitle>
          </DialogHeader>
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

              {streamDetailsOpen.media_id && (
                <div>
                  <Label className="text-muted-foreground">Media ID</Label>
                  <p className="font-mono text-sm">{streamDetailsOpen.media_id}</p>
                </div>
              )}

              {/* Value changes */}
              {(streamDetailsOpen.current_value || streamDetailsOpen.suggested_value) && (
                <div className="p-4 rounded-md bg-muted/50 space-y-2">
                  <Label className="text-muted-foreground">Change</Label>
                  <div className="grid grid-cols-[1fr_auto_1fr] gap-2 items-start">
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">Current</p>
                      <p className="text-sm text-red-400 break-all">{streamDetailsOpen.current_value || '(empty)'}</p>
                    </div>
                    <ArrowRight className="h-4 w-4 text-muted-foreground mt-5" />
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

              <div className="flex justify-between text-sm text-muted-foreground">
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
        </DialogContent>
      </Dialog>

      {/* Delete Stream Suggestion Dialog */}
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
