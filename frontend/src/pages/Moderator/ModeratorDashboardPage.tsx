import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  Shield,
  CheckCircle2,
  XCircle,
  Clock,
  Eye,
  Filter,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Loader2,
  FileText,
  AlertTriangle,
  Inbox,
  ThumbsUp,
  ThumbsDown,
  Search,
} from 'lucide-react'
import {
  usePendingSuggestions,
  useReviewSuggestion,
  useSuggestions,
  usePendingStreamSuggestions,
  useReviewStreamSuggestion,
  useStreamSuggestionStats,
  useSuggestionStats,
  useContributionSettings,
  useUpdateContributionSettings,
  useResetContributionSettings,
  usePendingContributions,
  useReviewContribution,
  useStreamsNeedingAnnotation,
  useUpdateFileLinks,
} from '@/hooks'
import { useAuth } from '@/contexts/AuthContext'
import type { Suggestion, SuggestionStatus, StreamSuggestion, Contribution } from '@/lib/api'
import {
  Settings,
  Film,
  Zap,
  Save,
  RotateCcw,
  Magnet,
  Tag,
  FileText as FileIcon,
  ExternalLink,
  HardDrive,
  FileVideo,
  Tv,
  Hash,
} from 'lucide-react'
import { FileAnnotationDialog, type FileLink, type EditedFileLink } from '@/components/stream'
import { catalogApi } from '@/lib/api'

// Simple relative time formatter
function formatTimeAgo(dateString: string): string {
  const date = new Date(dateString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSecs = Math.floor(diffMs / 1000)
  const diffMins = Math.floor(diffSecs / 60)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffSecs < 60) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

const statusConfig: Record<SuggestionStatus, { label: string; color: string; icon: typeof Clock }> = {
  pending: { label: 'Pending', color: 'bg-primary/10 text-primary border-primary/30', icon: Clock },
  approved: {
    label: 'Approved',
    color: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30',
    icon: CheckCircle2,
  },
  auto_approved: {
    label: 'Auto-Approved',
    color: 'bg-blue-500/10 text-blue-500 border-blue-500/30',
    icon: CheckCircle2,
  },
  rejected: { label: 'Rejected', color: 'bg-red-500/10 text-red-500 border-red-500/30', icon: XCircle },
}

type ReviewDecision = 'approve' | 'reject'

interface ReviewDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  suggestion: Suggestion | null
  onReview: (decision: ReviewDecision, notes?: string) => Promise<void>
  isReviewing: boolean
}

function ReviewDialog({ open, onOpenChange, suggestion, onReview, isReviewing }: ReviewDialogProps) {
  const [notes, setNotes] = useState('')
  const [confirmReject, setConfirmReject] = useState(false)

  const handleApprove = async () => {
    await onReview('approve', notes || undefined)
    setNotes('')
    onOpenChange(false)
  }

  const handleReject = async () => {
    await onReview('reject', notes || undefined)
    setNotes('')
    setConfirmReject(false)
    onOpenChange(false)
  }

  if (!suggestion) return null

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[600px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Eye className="h-5 w-5 text-primary" />
              Review Suggestion
            </DialogTitle>
            <DialogDescription>Review and approve or reject this metadata correction suggestion.</DialogDescription>
          </DialogHeader>

          <div className="space-y-6 py-4">
            {/* Suggestion Details */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">Field</label>
                <p className="font-medium capitalize">{suggestion.field_name}</p>
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">Media ID</label>
                <p className="font-mono text-sm">{suggestion.media_id}</p>
              </div>
            </div>

            {/* Current vs Suggested Value */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">Current Value</label>
                <div className="p-3 rounded-lg bg-red-500/5 border border-red-500/20">
                  <p className="text-sm break-words">{suggestion.current_value || '(empty)'}</p>
                </div>
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">Suggested Value</label>
                <div className="p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
                  <p className="text-sm break-words">{suggestion.suggested_value}</p>
                </div>
              </div>
            </div>

            {/* Reason */}
            {suggestion.reason && (
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">User's Reason</label>
                <div className="p-3 rounded-lg bg-muted/50">
                  <p className="text-sm">{suggestion.reason}</p>
                </div>
              </div>
            )}

            {/* Submitted info */}
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span>Submitted by: {suggestion.username || suggestion.user_id}</span>
              <span>•</span>
              <span>{formatTimeAgo(suggestion.created_at)}</span>
            </div>

            {/* Review Notes */}
            <div className="space-y-2">
              <label className="text-sm font-medium">Review Notes (optional)</label>
              <Textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Add notes about your decision..."
                rows={3}
              />
            </div>
          </div>

          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isReviewing}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => setConfirmReject(true)} disabled={isReviewing}>
              <XCircle className="mr-2 h-4 w-4" />
              Reject
            </Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700" onClick={handleApprove} disabled={isReviewing}>
              {isReviewing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="mr-2 h-4 w-4" />
              )}
              Approve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reject Confirmation Dialog */}
      <AlertDialog open={confirmReject} onOpenChange={setConfirmReject}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              Reject Suggestion?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to reject this suggestion? This action will notify the user and cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isReviewing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleReject}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={isReviewing}
            >
              {isReviewing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Reject Suggestion
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// Pending Suggestions Tab
function PendingSuggestionsTab() {
  const [page, setPage] = useState(1)
  const [selectedSuggestion, setSelectedSuggestion] = useState<Suggestion | null>(null)
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)

  const { data, isLoading, refetch } = usePendingSuggestions({ page, page_size: 20 })
  const reviewSuggestion = useReviewSuggestion()

  const handleReview = async (decision: ReviewDecision, notes?: string) => {
    if (!selectedSuggestion) return

    await reviewSuggestion.mutateAsync({
      suggestionId: selectedSuggestion.id,
      data: { action: decision, review_notes: notes },
    })
    refetch()
  }

  const handleOpenReview = (suggestion: Suggestion) => {
    setSelectedSuggestion(suggestion)
    setReviewDialogOpen(true)
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[...Array(5)].map((_, i) => (
          <Skeleton key={i} className="h-16 rounded-xl" />
        ))}
      </div>
    )
  }

  if (!data?.suggestions.length) {
    return (
      <div className="text-center py-12">
        <Inbox className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
        <p className="mt-4 text-lg font-medium">No pending suggestions</p>
        <p className="text-sm text-muted-foreground mt-2">All suggestions have been reviewed!</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border/50 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30">
              <TableHead>Field</TableHead>
              <TableHead>Meta ID</TableHead>
              <TableHead>Current → Suggested</TableHead>
              <TableHead>Submitted</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.suggestions.map((suggestion: Suggestion) => (
              <TableRow key={suggestion.id} className="hover:bg-muted/20">
                <TableCell className="font-medium capitalize">{suggestion.field_name}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{suggestion.media_id}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-2 max-w-xs">
                    <span className="text-xs text-red-500 truncate max-w-[100px]">
                      {suggestion.current_value || '(empty)'}
                    </span>
                    <span className="text-muted-foreground">→</span>
                    <span className="text-xs text-emerald-500 truncate max-w-[100px]">
                      {suggestion.suggested_value}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{formatTimeAgo(suggestion.created_at)}</TableCell>
                <TableCell className="text-right">
                  <Button size="sm" onClick={() => handleOpenReview(suggestion)} className="rounded-lg">
                    <Eye className="mr-2 h-4 w-4" />
                    Review
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {data.total > 20 && (
        <div className="flex justify-center items-center gap-2 pt-4">
          <Button
            variant="outline"
            size="icon"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-xl"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-4 text-sm text-muted-foreground">
            Page {page} of {Math.ceil(data.total / 20)}
          </span>
          <Button
            variant="outline"
            size="icon"
            disabled={page >= Math.ceil(data.total / 20)}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-xl"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Review Dialog */}
      <ReviewDialog
        open={reviewDialogOpen}
        onOpenChange={setReviewDialogOpen}
        suggestion={selectedSuggestion}
        onReview={handleReview}
        isReviewing={reviewSuggestion.isPending}
      />
    </div>
  )
}

// All Suggestions Tab (with filters)
function AllSuggestionsTab() {
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState<SuggestionStatus | 'all'>('all')
  const [search, setSearch] = useState('')

  const { data, isLoading } = useSuggestions({
    page,
    page_size: 20,
    status: statusFilter === 'all' ? undefined : statusFilter,
  })

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[...Array(5)].map((_, i) => (
          <Skeleton key={i} className="h-16 rounded-xl" />
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search suggestions..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9 rounded-xl"
          />
        </div>
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as SuggestionStatus | 'all')}>
          <SelectTrigger className="w-[150px] rounded-xl">
            <Filter className="mr-2 h-4 w-4" />
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Status</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="approved">Approved</SelectItem>
            <SelectItem value="auto_approved">Auto-Approved</SelectItem>
            <SelectItem value="rejected">Rejected</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {!data?.suggestions.length ? (
        <div className="text-center py-12">
          <FileText className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-muted-foreground">No suggestions found</p>
        </div>
      ) : (
        <div className="rounded-xl border border-border/50 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/30">
                <TableHead>Status</TableHead>
                <TableHead>Field</TableHead>
                <TableHead>Meta ID</TableHead>
                <TableHead>Suggested Value</TableHead>
                <TableHead>
                  <ArrowUpDown className="h-4 w-4 inline mr-1" />
                  Submitted
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.suggestions.map((suggestion: Suggestion) => {
                const config = statusConfig[suggestion.status]
                const StatusIcon = config.icon
                return (
                  <TableRow key={suggestion.id} className="hover:bg-muted/20">
                    <TableCell>
                      <Badge variant="outline" className={config.color}>
                        <StatusIcon className="mr-1 h-3 w-3" />
                        {config.label}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium capitalize">{suggestion.field_name}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{suggestion.media_id}</TableCell>
                    <TableCell className="max-w-[200px] truncate text-sm">{suggestion.suggested_value}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatTimeAgo(suggestion.created_at)}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Pagination */}
      {data && data.total > 20 && (
        <div className="flex justify-center items-center gap-2 pt-4">
          <Button
            variant="outline"
            size="icon"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-xl"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-4 text-sm text-muted-foreground">
            Page {page} of {Math.ceil(data.total / 20)}
          </span>
          <Button
            variant="outline"
            size="icon"
            disabled={page >= Math.ceil(data.total / 20)}
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

// Helper to parse episode link field names
function parseEpisodeLinkField(
  fieldName: string | null,
): { fileId: string; field: string; displayField: string } | null {
  if (!fieldName || !fieldName.startsWith('episode_link:')) return null
  const parts = fieldName.split(':')
  if (parts.length < 3) return null

  const fieldDisplayMap: Record<string, string> = {
    season_number: 'Season',
    episode_number: 'Episode',
    episode_end: 'Episode End',
  }

  return {
    fileId: parts[1],
    field: parts[2],
    displayField: fieldDisplayMap[parts[2]] || parts[2],
  }
}

// Helper to format field name for display
function formatStreamFieldName(fieldName: string | null): string {
  if (!fieldName) return ''

  const episodeInfo = parseEpisodeLinkField(fieldName)
  if (episodeInfo) {
    return `Episode ${episodeInfo.displayField}`
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

// Helper to format suggestion type for display
function formatStreamSuggestionType(type: string): string {
  const typeMap: Record<string, string> = {
    report_broken: 'Broken Report',
    field_correction: 'Field Correction',
    language_add: 'Add Language',
    language_remove: 'Remove Language',
    mark_duplicate: 'Mark Duplicate',
    other: 'Other',
  }
  return typeMap[type] || type
}

// Stream Suggestions Tab
function StreamSuggestionsTab() {
  const [page, setPage] = useState(1)
  const [suggestionType, setSuggestionType] = useState<string>('all')
  const { data, isLoading, refetch } = usePendingStreamSuggestions({
    page,
    page_size: 20,
    suggestion_type: suggestionType === 'all' ? undefined : suggestionType,
  })
  const { data: stats } = useStreamSuggestionStats()
  const reviewSuggestion = useReviewStreamSuggestion()
  const [selectedSuggestion, setSelectedSuggestion] = useState<StreamSuggestion | null>(null)
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const [reviewNotes, setReviewNotes] = useState('')

  const handleReview = async (action: 'approve' | 'reject') => {
    if (!selectedSuggestion) return
    try {
      await reviewSuggestion.mutateAsync({
        suggestionId: selectedSuggestion.id,
        data: { action, review_notes: reviewNotes || undefined },
      })
      setReviewDialogOpen(false)
      setSelectedSuggestion(null)
      setReviewNotes('')
      refetch()
    } catch (error) {
      // Error handled by mutation
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[...Array(5)].map((_, i) => (
          <Skeleton key={i} className="h-16 rounded-xl" />
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Stats bar */}
      {stats && (
        <div className="flex items-center gap-4 p-3 rounded-xl bg-muted/50">
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-primary" />
            <span className="text-sm">
              <strong>{stats.pending}</strong> Pending
            </span>
          </div>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            <span className="text-sm">
              <strong>{stats.approved + stats.auto_approved}</strong> Approved
            </span>
          </div>
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-red-500" />
            <span className="text-sm">
              <strong>{stats.rejected}</strong> Rejected
            </span>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-4">
        <Select value={suggestionType} onValueChange={setSuggestionType}>
          <SelectTrigger className="w-[180px] rounded-xl">
            <Filter className="mr-2 h-4 w-4" />
            <SelectValue placeholder="Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="report_broken">Broken Reports</SelectItem>
            <SelectItem value="field_correction">Field Corrections</SelectItem>
            <SelectItem value="language_add">Language Add</SelectItem>
            <SelectItem value="language_remove">Language Remove</SelectItem>
            <SelectItem value="other">Other</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {!data?.suggestions.length ? (
        <div className="text-center py-12">
          <Film className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-muted-foreground">No stream suggestions pending</p>
        </div>
      ) : (
        <div className="space-y-3">
          {data.suggestions.map((suggestion) => {
            const episodeInfo = parseEpisodeLinkField(suggestion.field_name)
            const isEpisodeLink = !!episodeInfo

            return (
              <Card key={suggestion.id} className="glass border-border/50 hover:border-primary/30 transition-colors">
                <CardContent className="p-4">
                  <div className="flex items-start gap-4">
                    {/* Icon */}
                    <div
                      className={`p-2 rounded-xl flex-shrink-0 ${isEpisodeLink ? 'bg-blue-500/10' : 'bg-primary/10'}`}
                    >
                      <Film className={`h-5 w-5 ${isEpisodeLink ? 'text-blue-500' : 'text-primary'}`} />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="outline" className="text-xs">
                          {formatStreamSuggestionType(suggestion.suggestion_type)}
                        </Badge>
                        {suggestion.field_name && (
                          <Badge variant="secondary" className="text-xs">
                            {formatStreamFieldName(suggestion.field_name)}
                          </Badge>
                        )}
                        {suggestion.user_contribution_level && (
                          <Badge variant="outline" className="text-xs capitalize bg-primary/10 border-primary/30">
                            {suggestion.user_contribution_level}
                          </Badge>
                        )}
                      </div>

                      {/* Stream name */}
                      <p className="text-sm text-muted-foreground truncate" title={suggestion.stream_name || ''}>
                        <span className="font-medium text-foreground">Stream:</span>{' '}
                        {suggestion.stream_name || `ID: ${suggestion.stream_id}`}
                      </p>

                      {/* Episode link specific display */}
                      {isEpisodeLink && (
                        <div className="p-3 rounded-lg bg-muted/50 space-y-1">
                          <p className="text-xs text-muted-foreground">
                            File ID: <span className="font-mono">{episodeInfo.fileId}</span>
                          </p>
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-red-400">
                              {episodeInfo.displayField}: {suggestion.current_value || '(not set)'}
                            </span>
                            <span className="text-muted-foreground">→</span>
                            <span className="text-sm text-emerald-400 font-medium">
                              {suggestion.suggested_value || '(clear)'}
                            </span>
                          </div>
                        </div>
                      )}

                      {/* Regular field correction display */}
                      {!isEpisodeLink &&
                        suggestion.field_name &&
                        (suggestion.current_value || suggestion.suggested_value) && (
                          <div className="flex items-center gap-2 text-sm">
                            <span
                              className="text-red-400 truncate max-w-[150px]"
                              title={suggestion.current_value || ''}
                            >
                              {suggestion.current_value || '(empty)'}
                            </span>
                            <span className="text-muted-foreground flex-shrink-0">→</span>
                            <span
                              className="text-emerald-400 truncate max-w-[150px]"
                              title={suggestion.suggested_value || ''}
                            >
                              {suggestion.suggested_value || '(empty)'}
                            </span>
                          </div>
                        )}

                      {/* Reason preview */}
                      {suggestion.reason && (
                        <p className="text-xs text-muted-foreground truncate" title={suggestion.reason}>
                          <span className="font-medium">Reason:</span> {suggestion.reason}
                        </p>
                      )}

                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>by {suggestion.username || 'User'}</span>
                        <span>•</span>
                        <span>{formatTimeAgo(suggestion.created_at)}</span>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <Button
                        size="sm"
                        variant="outline"
                        className="rounded-lg"
                        onClick={() => {
                          setSelectedSuggestion(suggestion)
                          setReviewDialogOpen(true)
                        }}
                      >
                        <Eye className="h-4 w-4 mr-1" />
                        Review
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      {/* Pagination */}
      {data && data.total > 20 && (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="outline"
            size="icon"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-xl"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-4 text-sm text-muted-foreground">
            Page {page} of {Math.ceil(data.total / 20)}
          </span>
          <Button
            variant="outline"
            size="icon"
            disabled={page >= Math.ceil(data.total / 20)}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-xl"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Review Dialog */}
      <Dialog open={reviewDialogOpen} onOpenChange={setReviewDialogOpen}>
        <DialogContent className="sm:max-w-[600px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Eye className="h-5 w-5 text-primary" />
              Review Stream Suggestion
            </DialogTitle>
            <DialogDescription>Review this suggestion and approve or reject it.</DialogDescription>
          </DialogHeader>

          {selectedSuggestion && (
            <div className="space-y-4 py-4">
              {/* Type and user badges */}
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="outline">{formatStreamSuggestionType(selectedSuggestion.suggestion_type)}</Badge>
                {selectedSuggestion.field_name && (
                  <Badge variant="secondary">{formatStreamFieldName(selectedSuggestion.field_name)}</Badge>
                )}
                <Badge variant="outline" className="capitalize bg-primary/10 border-primary/30">
                  {selectedSuggestion.user_contribution_level || 'new'} (
                  {selectedSuggestion.user_contribution_points ?? 0} pts)
                </Badge>
              </div>

              {/* Stream info */}
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">Stream</label>
                <p className="text-sm font-mono bg-muted p-2 rounded-lg break-all">
                  {selectedSuggestion.stream_name || selectedSuggestion.stream_id}
                </p>
              </div>

              {selectedSuggestion.media_id && (
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">Media ID</label>
                  <p className="text-sm font-mono">{selectedSuggestion.media_id}</p>
                </div>
              )}

              {/* Episode link specific display */}
              {parseEpisodeLinkField(selectedSuggestion.field_name) && (
                <div className="p-4 rounded-xl bg-blue-500/5 border border-blue-500/20 space-y-3">
                  <div className="flex items-center gap-2">
                    <Film className="h-4 w-4 text-blue-500" />
                    <span className="font-medium text-blue-600 dark:text-blue-400">Episode Link Correction</span>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">File ID</p>
                      <p className="font-mono text-sm">
                        {parseEpisodeLinkField(selectedSuggestion.field_name)?.fileId}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">Field</p>
                      <p className="font-medium text-sm">
                        {parseEpisodeLinkField(selectedSuggestion.field_name)?.displayField}
                      </p>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="p-3 rounded-lg bg-red-500/10">
                      <p className="text-xs text-muted-foreground mb-1">Current Value</p>
                      <p className="text-lg font-bold text-red-500">
                        {selectedSuggestion.current_value || '(not set)'}
                      </p>
                    </div>
                    <div className="p-3 rounded-lg bg-emerald-500/10">
                      <p className="text-xs text-muted-foreground mb-1">New Value</p>
                      <p className="text-lg font-bold text-emerald-500">
                        {selectedSuggestion.suggested_value || '(clear)'}
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Regular field correction display */}
              {!parseEpisodeLinkField(selectedSuggestion.field_name) &&
                (selectedSuggestion.current_value || selectedSuggestion.suggested_value) && (
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted-foreground">Current Value</label>
                      <div className="p-3 rounded-lg bg-red-500/5 border border-red-500/20">
                        <p className="text-sm break-words">{selectedSuggestion.current_value || '(empty)'}</p>
                      </div>
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted-foreground">Suggested Value</label>
                      <div className="p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
                        <p className="text-sm break-words">{selectedSuggestion.suggested_value || '(empty)'}</p>
                      </div>
                    </div>
                  </div>
                )}

              {/* Reason */}
              {selectedSuggestion.reason && (
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">User's Reason</label>
                  <div className="p-3 rounded-lg bg-muted/50">
                    <p className="text-sm">{selectedSuggestion.reason}</p>
                  </div>
                </div>
              )}

              {/* Submitted info */}
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span>Submitted by: {selectedSuggestion.username || selectedSuggestion.user_id}</span>
                <span>•</span>
                <span>{formatTimeAgo(selectedSuggestion.created_at)}</span>
              </div>

              {/* Review notes */}
              <div className="space-y-2">
                <label className="text-sm font-medium">Review Notes (optional)</label>
                <Textarea
                  value={reviewNotes}
                  onChange={(e) => setReviewNotes(e.target.value)}
                  placeholder="Add notes about your decision..."
                  rows={2}
                />
              </div>
            </div>
          )}

          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setReviewDialogOpen(false)} disabled={reviewSuggestion.isPending}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => handleReview('reject')} disabled={reviewSuggestion.isPending}>
              {reviewSuggestion.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <XCircle className="h-4 w-4 mr-2" />
              )}
              Reject
            </Button>
            <Button
              className="bg-emerald-600 hover:bg-emerald-700"
              onClick={() => handleReview('approve')}
              disabled={reviewSuggestion.isPending}
            >
              {reviewSuggestion.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4 mr-2" />
              )}
              Approve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// Helper to format torrent data for display
function formatTorrentData(
  data: Record<string, unknown>,
): { label: string; value: string; type: 'text' | 'link' | 'badge' | 'size' }[] {
  const fields: { label: string; value: string; type: 'text' | 'link' | 'badge' | 'size' }[] = []

  if (data.name) fields.push({ label: 'Torrent Name', value: String(data.name), type: 'text' })
  if (data.title) fields.push({ label: 'Title', value: String(data.title), type: 'text' })
  if (data.meta_type) fields.push({ label: 'Type', value: String(data.meta_type), type: 'badge' })
  if (data.meta_id) fields.push({ label: 'Media ID', value: String(data.meta_id), type: 'link' })
  if (data.info_hash) fields.push({ label: 'Info Hash', value: String(data.info_hash), type: 'text' })
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

  return fields
}

function formatBytes(bytes: number | string): string {
  const size = typeof bytes === 'string' ? parseFloat(bytes) : bytes
  if (isNaN(size) || size === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(size) / Math.log(k))
  return parseFloat((size / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// Contributions Tab (Torrent Imports)
function ContributionsTab() {
  const [page, setPage] = useState(1)
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [selectedContribution, setSelectedContribution] = useState<Contribution | null>(null)
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const [reviewNotes, setReviewNotes] = useState('')

  const { data, isLoading, refetch } = usePendingContributions({
    contribution_type: typeFilter === 'all' ? undefined : (typeFilter as 'torrent' | 'stream' | 'metadata'),
    page,
    page_size: 20,
  })
  const reviewContribution = useReviewContribution()

  const handleReview = async (action: 'approved' | 'rejected') => {
    if (!selectedContribution) return
    try {
      await reviewContribution.mutateAsync({
        contributionId: selectedContribution.id,
        data: { status: action, review_notes: reviewNotes || undefined },
      })
      setReviewDialogOpen(false)
      setSelectedContribution(null)
      setReviewNotes('')
      refetch()
    } catch (error) {
      // Error handled by mutation
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[...Array(5)].map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-4">
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger className="w-[180px] rounded-xl">
            <Filter className="mr-2 h-4 w-4" />
            <SelectValue placeholder="Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="torrent">Torrent Imports</SelectItem>
            <SelectItem value="stream">New Streams</SelectItem>
            <SelectItem value="metadata">Metadata Fixes</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {!data?.items.length ? (
        <div className="text-center py-12">
          <Magnet className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-muted-foreground">No pending content imports</p>
        </div>
      ) : (
        <div className="space-y-3">
          {data.items.map((contribution) => {
            const isTorrent = contribution.contribution_type === 'torrent'
            const isStream = contribution.contribution_type === 'stream'
            const torrentData = contribution.data as Record<string, unknown>

            return (
              <Card key={contribution.id} className="glass border-border/50 hover:border-primary/30 transition-colors">
                <CardContent className="p-4">
                  <div className="flex items-start gap-4">
                    {/* Icon */}
                    <div
                      className={`p-2 rounded-xl flex-shrink-0 ${isTorrent ? 'bg-orange-500/10' : isStream ? 'bg-blue-500/10' : 'bg-primary/10'}`}
                    >
                      {isTorrent ? (
                        <Tag className="h-5 w-5 text-orange-500" />
                      ) : isStream ? (
                        <Magnet className="h-5 w-5 text-blue-500" />
                      ) : (
                        <FileIcon className="h-5 w-5 text-primary" />
                      )}
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="outline" className="text-xs capitalize">
                          {contribution.contribution_type}
                        </Badge>
                        {!!torrentData.meta_type && (
                          <Badge variant="secondary" className="text-xs capitalize">
                            {String(torrentData.meta_type)}
                          </Badge>
                        )}
                        {!!torrentData.resolution && (
                          <Badge variant="outline" className="text-xs bg-blue-500/10 border-blue-500/30">
                            {String(torrentData.resolution)}
                          </Badge>
                        )}
                        {!!torrentData.quality && (
                          <Badge variant="outline" className="text-xs bg-emerald-500/10 border-emerald-500/30">
                            {String(torrentData.quality)}
                          </Badge>
                        )}
                        {/* Anonymous indicator */}
                        <Badge
                          variant="outline"
                          className={`text-xs ${
                            torrentData.is_anonymous === true
                              ? 'bg-gray-500/10 border-gray-500/30 text-gray-500'
                              : 'bg-primary/10 border-primary/30 text-primary'
                          }`}
                        >
                          {torrentData.is_anonymous === true ? 'Anonymous' : 'Linked'}
                        </Badge>
                      </div>

                      {/* Torrent/Stream name */}
                      <p className="font-medium truncate" title={String(torrentData.name || torrentData.title || '')}>
                        {String(torrentData.name || torrentData.title || 'Untitled')}
                      </p>

                      {/* Target ID */}
                      {contribution.target_id && (
                        <p className="text-sm text-muted-foreground">
                          <span className="font-medium">Target:</span>{' '}
                          <span className="font-mono">{contribution.target_id}</span>
                        </p>
                      )}

                      {/* Info hash for torrents */}
                      {!!torrentData.info_hash && (
                        <p
                          className="text-xs text-muted-foreground font-mono truncate"
                          title={String(torrentData.info_hash)}
                        >
                          Hash: {String(torrentData.info_hash).slice(0, 16)}...
                        </p>
                      )}

                      <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        <span>{formatTimeAgo(contribution.created_at)}</span>
                        {!!torrentData.total_size && (
                          <>
                            <span>•</span>
                            <span className="flex items-center gap-1">
                              <HardDrive className="h-3 w-3" />
                              {formatBytes(torrentData.total_size as number)}
                            </span>
                          </>
                        )}
                        {!!torrentData.file_count && (
                          <>
                            <span>•</span>
                            <span>{String(torrentData.file_count)} files</span>
                          </>
                        )}
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <Button
                        size="sm"
                        variant="outline"
                        className="rounded-lg"
                        onClick={() => {
                          setSelectedContribution(contribution)
                          setReviewDialogOpen(true)
                        }}
                      >
                        <Eye className="h-4 w-4 mr-1" />
                        Review
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      {/* Pagination */}
      {data && data.total > 20 && (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="outline"
            size="icon"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-xl"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-4 text-sm text-muted-foreground">
            Page {page} of {Math.ceil(data.total / 20)}
          </span>
          <Button
            variant="outline"
            size="icon"
            disabled={page >= Math.ceil(data.total / 20)}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-xl"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Review Dialog */}
      <Dialog open={reviewDialogOpen} onOpenChange={setReviewDialogOpen}>
        <DialogContent className="sm:max-w-[700px] max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Eye className="h-5 w-5 text-primary" />
              Review Content Import
            </DialogTitle>
            <DialogDescription>
              Review this{' '}
              {selectedContribution?.contribution_type === 'torrent'
                ? 'torrent import'
                : selectedContribution?.contribution_type === 'stream'
                  ? 'stream import'
                  : 'content import'}{' '}
              and approve or reject it.
            </DialogDescription>
          </DialogHeader>

          {selectedContribution && (
            <div className="space-y-4 py-4">
              {/* Type badges */}
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="outline" className="capitalize">
                  {selectedContribution.contribution_type}
                </Badge>
                {selectedContribution.target_id && (
                  <Badge variant="secondary" className="font-mono">
                    {selectedContribution.target_id}
                  </Badge>
                )}
              </div>

              {/* Structured data display */}
              <div className="space-y-3">
                <h4 className="font-medium text-sm text-muted-foreground">Contribution Details</h4>
                <div className="grid grid-cols-2 gap-3">
                  {formatTorrentData(selectedContribution.data as Record<string, unknown>).map((field, idx) => (
                    <div
                      key={idx}
                      className={`p-3 rounded-lg bg-muted/50 ${field.type === 'text' && String(field.value).length > 30 ? 'col-span-2' : ''}`}
                    >
                      <p className="text-xs text-muted-foreground mb-1">{field.label}</p>
                      {field.type === 'badge' ? (
                        <Badge variant="outline">{field.value}</Badge>
                      ) : field.type === 'link' ? (
                        <a
                          href={`https://www.imdb.com/title/${field.value}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm font-mono text-primary hover:underline flex items-center gap-1"
                        >
                          {field.value}
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : field.type === 'size' ? (
                        <p className="text-sm font-medium">{formatBytes(field.value)}</p>
                      ) : (
                        <p className="text-sm font-medium break-all">{field.value}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Magnet link if available */}
              {!!(selectedContribution.data as Record<string, unknown>).magnet_link && (
                <div className="space-y-2">
                  <h4 className="font-medium text-sm text-muted-foreground">Magnet Link</h4>
                  <div className="p-3 rounded-lg bg-muted/50">
                    <p className="text-xs font-mono break-all line-clamp-3">
                      {String((selectedContribution.data as Record<string, unknown>).magnet_link)}
                    </p>
                  </div>
                </div>
              )}

              {/* Submitted info */}
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span>Submitted: {formatTimeAgo(selectedContribution.created_at)}</span>
              </div>

              {/* Review notes */}
              <div className="space-y-2">
                <label className="text-sm font-medium">Review Notes (optional)</label>
                <Textarea
                  value={reviewNotes}
                  onChange={(e) => setReviewNotes(e.target.value)}
                  placeholder="Add notes about your decision..."
                  rows={2}
                />
              </div>
            </div>
          )}

          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => setReviewDialogOpen(false)}
              disabled={reviewContribution.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => handleReview('rejected')}
              disabled={reviewContribution.isPending}
            >
              {reviewContribution.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <XCircle className="h-4 w-4 mr-2" />
              )}
              Reject
            </Button>
            <Button
              className="bg-emerald-600 hover:bg-emerald-700"
              onClick={() => handleReview('approved')}
              disabled={reviewContribution.isPending}
            >
              {reviewContribution.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4 mr-2" />
              )}
              Approve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// Contribution Settings Tab (Admin only)
function ContributionSettingsTab() {
  const { data: settings, isLoading } = useContributionSettings()
  const updateSettings = useUpdateContributionSettings()
  const resetSettings = useResetContributionSettings()

  // Form state
  const [formData, setFormData] = useState({
    auto_approval_threshold: 25,
    contributor_threshold: 10,
    trusted_threshold: 50,
    expert_threshold: 200,
    points_per_metadata_edit: 5,
    points_per_stream_edit: 3,
    points_for_rejection_penalty: -2,
    max_pending_suggestions_per_user: 20,
    allow_auto_approval: true,
    require_reason_for_edits: false,
  })
  const [hasChanges, setHasChanges] = useState(false)

  // Initialize form with settings data when loaded
  useState(() => {
    if (settings) {
      setFormData({
        auto_approval_threshold: settings.auto_approval_threshold,
        contributor_threshold: settings.contributor_threshold,
        trusted_threshold: settings.trusted_threshold,
        expert_threshold: settings.expert_threshold,
        points_per_metadata_edit: settings.points_per_metadata_edit,
        points_per_stream_edit: settings.points_per_stream_edit,
        points_for_rejection_penalty: settings.points_for_rejection_penalty,
        max_pending_suggestions_per_user: settings.max_pending_suggestions_per_user,
        allow_auto_approval: settings.allow_auto_approval,
        require_reason_for_edits: settings.require_reason_for_edits,
      })
    }
  })

  // Update form when settings load
  if (settings && !hasChanges) {
    const needsUpdate =
      formData.auto_approval_threshold !== settings.auto_approval_threshold ||
      formData.contributor_threshold !== settings.contributor_threshold ||
      formData.trusted_threshold !== settings.trusted_threshold ||
      formData.expert_threshold !== settings.expert_threshold ||
      formData.points_per_metadata_edit !== settings.points_per_metadata_edit ||
      formData.points_per_stream_edit !== settings.points_per_stream_edit ||
      formData.points_for_rejection_penalty !== settings.points_for_rejection_penalty ||
      formData.max_pending_suggestions_per_user !== settings.max_pending_suggestions_per_user ||
      formData.allow_auto_approval !== settings.allow_auto_approval ||
      formData.require_reason_for_edits !== settings.require_reason_for_edits

    if (needsUpdate) {
      setFormData({
        auto_approval_threshold: settings.auto_approval_threshold,
        contributor_threshold: settings.contributor_threshold,
        trusted_threshold: settings.trusted_threshold,
        expert_threshold: settings.expert_threshold,
        points_per_metadata_edit: settings.points_per_metadata_edit,
        points_per_stream_edit: settings.points_per_stream_edit,
        points_for_rejection_penalty: settings.points_for_rejection_penalty,
        max_pending_suggestions_per_user: settings.max_pending_suggestions_per_user,
        allow_auto_approval: settings.allow_auto_approval,
        require_reason_for_edits: settings.require_reason_for_edits,
      })
    }
  }

  const handleChange = (field: string, value: number | boolean) => {
    setFormData((prev) => ({ ...prev, [field]: value }))
    setHasChanges(true)
  }

  const handleSave = async () => {
    await updateSettings.mutateAsync(formData)
    setHasChanges(false)
  }

  const handleReset = async () => {
    await resetSettings.mutateAsync()
    setHasChanges(false)
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Card className="glass border-border/50">
          <CardContent className="p-6">
            <div className="space-y-4">
              {[...Array(4)].map((_, i) => (
                <Skeleton key={i} className="h-12 rounded-lg" />
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Card className="glass border-border/50">
        <CardContent className="p-6">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <Settings className="h-5 w-5 text-primary" />
              <div>
                <h3 className="font-semibold">Contribution Settings</h3>
                <p className="text-sm text-muted-foreground">
                  Configure auto-approval thresholds and contribution points
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleReset}
                disabled={resetSettings.isPending}
                className="rounded-xl"
              >
                {resetSettings.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="h-4 w-4 mr-2" />
                )}
                Reset to Defaults
              </Button>
              <Button
                size="sm"
                onClick={handleSave}
                disabled={!hasChanges || updateSettings.isPending}
                className="rounded-xl bg-gradient-to-r from-primary to-primary/80"
              >
                {updateSettings.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4 mr-2" />
                )}
                Save Changes
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Thresholds Section */}
            <div className="space-y-4">
              <h4 className="font-medium flex items-center gap-2">
                <Zap className="h-4 w-4 text-primary" />
                Level Thresholds
              </h4>
              <div className="space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="auto_approval_threshold" className="text-sm">
                    Auto-Approval Points Threshold
                  </Label>
                  <Input
                    id="auto_approval_threshold"
                    type="number"
                    value={formData.auto_approval_threshold}
                    onChange={(e) => handleChange('auto_approval_threshold', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="contributor_threshold" className="text-sm">
                    Contributor Level (points)
                  </Label>
                  <Input
                    id="contributor_threshold"
                    type="number"
                    value={formData.contributor_threshold}
                    onChange={(e) => handleChange('contributor_threshold', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="trusted_threshold" className="text-sm">
                    Trusted Level (points)
                  </Label>
                  <Input
                    id="trusted_threshold"
                    type="number"
                    value={formData.trusted_threshold}
                    onChange={(e) => handleChange('trusted_threshold', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="expert_threshold" className="text-sm">
                    Expert Level (points)
                  </Label>
                  <Input
                    id="expert_threshold"
                    type="number"
                    value={formData.expert_threshold}
                    onChange={(e) => handleChange('expert_threshold', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
              </div>
            </div>

            {/* Points Configuration */}
            <div className="space-y-4">
              <h4 className="font-medium flex items-center gap-2">
                <ThumbsUp className="h-4 w-4 text-emerald-500" />
                Points Configuration
              </h4>
              <div className="space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="points_per_metadata_edit" className="text-sm">
                    Points per Metadata Edit
                  </Label>
                  <Input
                    id="points_per_metadata_edit"
                    type="number"
                    value={formData.points_per_metadata_edit}
                    onChange={(e) => handleChange('points_per_metadata_edit', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="points_per_stream_edit" className="text-sm">
                    Points per Stream Edit
                  </Label>
                  <Input
                    id="points_per_stream_edit"
                    type="number"
                    value={formData.points_per_stream_edit}
                    onChange={(e) => handleChange('points_per_stream_edit', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="points_for_rejection_penalty" className="text-sm text-red-500">
                    Rejection Penalty (negative)
                  </Label>
                  <Input
                    id="points_for_rejection_penalty"
                    type="number"
                    max={0}
                    value={formData.points_for_rejection_penalty}
                    onChange={(e) => handleChange('points_for_rejection_penalty', parseInt(e.target.value) || 0)}
                    className="rounded-xl"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="max_pending_suggestions_per_user" className="text-sm">
                    Max Pending per User
                  </Label>
                  <Input
                    id="max_pending_suggestions_per_user"
                    type="number"
                    min={1}
                    value={formData.max_pending_suggestions_per_user}
                    onChange={(e) => handleChange('max_pending_suggestions_per_user', parseInt(e.target.value) || 1)}
                    className="rounded-xl"
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Feature Flags */}
          <div className="mt-6 pt-6 border-t border-border/50">
            <h4 className="font-medium flex items-center gap-2 mb-4">
              <Settings className="h-4 w-4 text-blue-500" />
              Feature Flags
            </h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
                <div>
                  <Label htmlFor="allow_auto_approval" className="font-medium">
                    Auto-Approval Enabled
                  </Label>
                  <p className="text-xs text-muted-foreground mt-1">Allow trusted users to auto-approve their edits</p>
                </div>
                <Switch
                  id="allow_auto_approval"
                  checked={formData.allow_auto_approval}
                  onCheckedChange={(checked) => handleChange('allow_auto_approval', checked)}
                />
              </div>
              <div className="flex items-center justify-between p-4 bg-muted/50 rounded-xl">
                <div>
                  <Label htmlFor="require_reason_for_edits" className="font-medium">
                    Require Reason for Edits
                  </Label>
                  <p className="text-xs text-muted-foreground mt-1">
                    Users must provide a reason for their suggestions
                  </p>
                </div>
                <Switch
                  id="require_reason_for_edits"
                  checked={formData.require_reason_for_edits}
                  onCheckedChange={(checked) => handleChange('require_reason_for_edits', checked)}
                />
              </div>
            </div>
          </div>

          {hasChanges && (
            <div className="mt-6 p-4 bg-primary/10 border border-primary/30 rounded-lg">
              <p className="text-sm text-primary dark:text-primary">
                <AlertTriangle className="inline h-4 w-4 mr-2" />
                You have unsaved changes. Click "Save Changes" to apply them.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// Annotation Requests Tab
function AnnotationRequestsTab() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const { data, isLoading, refetch } = useStreamsNeedingAnnotation({
    page,
    per_page: 20,
    search: search || undefined,
  })
  const updateFileLinks = useUpdateFileLinks()

  // Annotation dialog state
  const [selectedStream, setSelectedStream] = useState<{
    streamId: number
    streamName: string
    mediaId: number
    mediaTitle: string
  } | null>(null)
  const [annotationDialogOpen, setAnnotationDialogOpen] = useState(false)
  const [annotationFiles, setAnnotationFiles] = useState<FileLink[]>([])
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [isSavingAnnotation, setIsSavingAnnotation] = useState(false)

  // Handle search
  const handleSearch = () => {
    setSearch(searchInput)
    setPage(1)
  }

  // Handle opening annotation dialog
  const handleOpenAnnotation = async (stream: {
    stream_id: number
    stream_name: string
    media_id: number
    media_title: string
  }) => {
    setIsLoadingFiles(true)
    try {
      // Fetch stream files from the API
      const files = await catalogApi.getStreamFiles(stream.stream_id)
      setAnnotationFiles(
        files.map((f) => ({
          file_id: f.file_id,
          file_name: f.file_name,
          size: f.size,
          season_number: f.season_number,
          episode_number: f.episode_number,
          episode_end: f.episode_end,
        })),
      )
      setSelectedStream({
        streamId: stream.stream_id,
        streamName: stream.stream_name,
        mediaId: stream.media_id,
        mediaTitle: stream.media_title,
      })
      setAnnotationDialogOpen(true)
    } catch (error) {
      console.error('Failed to load stream files:', error)
    } finally {
      setIsLoadingFiles(false)
    }
  }

  // Handle saving annotations (direct update, not suggestions)
  const handleSaveAnnotation = async (editedFiles: EditedFileLink[]) => {
    if (!selectedStream) return

    setIsSavingAnnotation(true)
    try {
      // Convert to the format expected by the API
      const updates = editedFiles
        .filter((f) => f.included)
        .map((f) => ({
          file_id: f.file_id,
          season_number: f.season_number,
          episode_number: f.episode_number,
          episode_end: f.episode_end ?? null,
        }))

      await updateFileLinks.mutateAsync({
        stream_id: selectedStream.streamId,
        media_id: selectedStream.mediaId,
        updates,
      })

      // Refresh the list
      refetch()
      setAnnotationDialogOpen(false)
      setSelectedStream(null)
    } catch (error) {
      console.error('Failed to save annotations:', error)
      throw error // Re-throw so the dialog can show the error
    } finally {
      setIsSavingAnnotation(false)
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[...Array(5)].map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by stream name or series title..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            className="pl-9 rounded-xl"
          />
        </div>
        <Button onClick={handleSearch} className="rounded-xl">
          Search
        </Button>
      </div>

      {/* Stats */}
      {data && (
        <div className="p-3 rounded-xl bg-muted/50 flex items-center gap-4">
          <div className="flex items-center gap-2">
            <FileVideo className="h-4 w-4 text-cyan-500" />
            <span className="text-sm">
              <strong>{data.total}</strong> streams need annotation
            </span>
          </div>
        </div>
      )}

      {!data?.items.length ? (
        <div className="text-center py-12">
          <FileVideo className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-lg font-medium">No annotation requests</p>
          <p className="text-sm text-muted-foreground mt-2">All series streams have proper episode mappings!</p>
        </div>
      ) : (
        <div className="space-y-3">
          {data.items.map((stream) => (
            <Card key={stream.stream_id} className="glass border-border/50 hover:border-cyan-500/30 transition-colors">
              <CardContent className="p-4">
                <div className="flex items-start gap-4">
                  {/* Icon */}
                  <div className="p-2 rounded-xl flex-shrink-0 bg-cyan-500/10">
                    <FileVideo className="h-5 w-5 text-cyan-500" />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0 space-y-2">
                    {/* Badges */}
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge variant="outline" className="text-xs bg-cyan-500/10 border-cyan-500/30">
                        {stream.unmapped_count} / {stream.file_count} files need mapping
                      </Badge>
                      {stream.resolution && (
                        <Badge variant="outline" className="text-xs">
                          {stream.resolution}
                        </Badge>
                      )}
                      {stream.source && (
                        <Badge variant="secondary" className="text-xs">
                          {stream.source}
                        </Badge>
                      )}
                    </div>

                    {/* Stream name */}
                    <p className="font-medium truncate" title={stream.stream_name}>
                      {stream.stream_name}
                    </p>

                    {/* Media info */}
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Tv className="h-3.5 w-3.5" />
                      <span className="truncate" title={stream.media_title}>
                        {stream.media_title}
                        {stream.media_year && ` (${stream.media_year})`}
                      </span>
                    </div>

                    {/* Info hash */}
                    {stream.info_hash && (
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Hash className="h-3 w-3" />
                        <span className="font-mono truncate">{stream.info_hash.slice(0, 16)}...</span>
                      </div>
                    )}

                    {/* Metadata */}
                    <div className="flex items-center gap-4 text-xs text-muted-foreground">
                      <span>{formatTimeAgo(stream.created_at)}</span>
                      {stream.size && (
                        <>
                          <span>•</span>
                          <span className="flex items-center gap-1">
                            <HardDrive className="h-3 w-3" />
                            {formatBytes(stream.size)}
                          </span>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <Button
                      size="sm"
                      className="rounded-lg bg-gradient-to-r from-cyan-600 to-teal-600 hover:from-cyan-500 hover:to-teal-500"
                      onClick={() =>
                        handleOpenAnnotation({
                          stream_id: stream.stream_id,
                          stream_name: stream.stream_name,
                          media_id: stream.media_id,
                          media_title: stream.media_title,
                        })
                      }
                      disabled={isLoadingFiles}
                    >
                      {isLoadingFiles ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <FileVideo className="h-4 w-4 mr-1" />
                      )}
                      Annotate
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Pagination */}
      {data && data.total > 20 && (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="outline"
            size="icon"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-xl"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-4 text-sm text-muted-foreground">
            Page {page} of {data.pages}
          </span>
          <Button
            variant="outline"
            size="icon"
            disabled={page >= data.pages}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-xl"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Annotation Dialog */}
      {selectedStream && (
        <FileAnnotationDialog
          open={annotationDialogOpen}
          onOpenChange={(open) => {
            setAnnotationDialogOpen(open)
            if (!open) setSelectedStream(null)
          }}
          streamName={`${selectedStream.streamName} (${selectedStream.mediaTitle})`}
          initialFiles={annotationFiles}
          onSave={handleSaveAnnotation}
          isLoading={isSavingAnnotation}
        />
      )}
    </div>
  )
}

// Main Moderator Dashboard Page
export function ModeratorDashboardPage() {
  const { user } = useAuth()
  const [activeTab, setActiveTab] = useState('contributions')

  const { data: pendingData } = usePendingSuggestions({ page: 1, page_size: 1 })
  const { data: suggestionStats } = useSuggestionStats()
  const { data: streamStats } = useStreamSuggestionStats()
  const { data: pendingContributions } = usePendingContributions({ page: 1, page_size: 1 })
  const { data: annotationData } = useStreamsNeedingAnnotation({ page: 1, per_page: 1 })

  const pendingCount = pendingData?.total ?? 0
  const pendingContributionsCount = pendingContributions?.total ?? 0
  const streamPendingCount = streamStats?.pending ?? 0
  const annotationCount = annotationData?.total ?? 0

  // Calculate combined today's stats from both metadata and stream suggestions
  const approvedToday = (suggestionStats?.approved_today ?? 0) + (streamStats?.approved_today ?? 0)
  const rejectedToday = (suggestionStats?.rejected_today ?? 0) + (streamStats?.rejected_today ?? 0)

  // Check if user has moderator or admin role
  const isModerator = user?.role === 'moderator' || user?.role === 'admin'

  if (!isModerator) {
    return (
      <div className="text-center py-12">
        <Shield className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
        <p className="mt-4 text-lg font-medium">Access Denied</p>
        <p className="text-sm text-muted-foreground mt-2">
          You need moderator or admin privileges to access this page.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
          <div className="p-2 rounded-xl bg-gradient-to-br from-primary to-primary/80 shadow-lg shadow-primary/20">
            <Shield className="h-5 w-5 text-white" />
          </div>
          Moderator Dashboard
        </h1>
        <p className="text-muted-foreground mt-1">Review and manage user-submitted metadata corrections</p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-orange-500/10">
                <Magnet className="h-4 w-4 text-orange-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{pendingContributionsCount}</p>
                <p className="text-xs text-muted-foreground">Content Imports</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-cyan-500/10">
                <FileVideo className="h-4 w-4 text-cyan-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{annotationCount}</p>
                <p className="text-xs text-muted-foreground">Annotations</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <Clock className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{pendingCount + streamPendingCount}</p>
                <p className="text-xs text-muted-foreground">Stream/Meta Edits</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-emerald-500/10">
                <ThumbsUp className="h-4 w-4 text-emerald-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{approvedToday}</p>
                <p className="text-xs text-muted-foreground">Approved Today</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-red-500/10">
                <ThumbsDown className="h-4 w-4 text-red-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{rejectedToday}</p>
                <p className="text-xs text-muted-foreground">Rejected Today</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
        <TabsList className="h-auto p-1.5 bg-muted/50 rounded-xl grid grid-cols-2 sm:grid-cols-6 gap-1 w-full">
          <TabsTrigger
            value="contributions"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <Magnet className="mr-1.5 h-4 w-4" />
            <span className="hidden sm:inline">Content</span> Imports
            {pendingContributionsCount > 0 && (
              <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-xs bg-orange-500/20 text-orange-600">
                {pendingContributionsCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="annotations"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <FileVideo className="mr-1.5 h-4 w-4" />
            <span className="hidden sm:inline">File</span> Annotations
            {annotationCount > 0 && (
              <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-xs bg-cyan-500/20 text-cyan-600">
                {annotationCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="streams"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <Film className="mr-1.5 h-4 w-4" />
            Streams
            {streamPendingCount > 0 && (
              <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-xs bg-blue-500/20 text-blue-600">
                {streamPendingCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="pending"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <Clock className="mr-1.5 h-4 w-4" />
            Metadata
            {pendingCount > 0 && (
              <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-xs bg-primary/20 text-primary">
                {pendingCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="all"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <FileText className="mr-1.5 h-4 w-4" />
            History
          </TabsTrigger>
          {user?.role === 'admin' && (
            <TabsTrigger
              value="settings"
              className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
            >
              <Settings className="mr-1.5 h-4 w-4" />
              Settings
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="contributions">
          <ContributionsTab />
        </TabsContent>

        <TabsContent value="annotations">
          <AnnotationRequestsTab />
        </TabsContent>

        <TabsContent value="streams">
          <StreamSuggestionsTab />
        </TabsContent>

        <TabsContent value="pending">
          <PendingSuggestionsTab />
        </TabsContent>

        <TabsContent value="all">
          <AllSuggestionsTab />
        </TabsContent>

        {user?.role === 'admin' && (
          <TabsContent value="settings">
            <ContributionSettingsTab />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
