import { useState } from 'react'
import { CheckCircle2, ChevronLeft, ChevronRight, Clock, Eye, Film, Filter, Loader2, XCircle } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useDebounce, usePendingStreamSuggestions, useReviewStreamSuggestion, useStreamSuggestionStats } from '@/hooks'
import type { StreamSuggestion, StreamSuggestionStatus } from '@/lib/api'

import { formatStreamFieldName, formatStreamSuggestionType, parseEpisodeLinkField, formatTimeAgo } from './helpers'

interface StreamSuggestionsTabProps {
  statusFilter: 'all' | StreamSuggestionStatus
  onStatusFilterChange: (status: 'all' | StreamSuggestionStatus) => void
}

export function StreamSuggestionsTab({ statusFilter, onStatusFilterChange }: StreamSuggestionsTabProps) {
  const [page, setPage] = useState(1)
  const [suggestionType, setSuggestionType] = useState<string>('all')
  const [uploaderQuery, setUploaderQuery] = useState('')
  const [reviewerQuery, setReviewerQuery] = useState('')
  const [selectedSuggestion, setSelectedSuggestion] = useState<StreamSuggestion | null>(null)
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const [reviewNotes, setReviewNotes] = useState('')
  const debouncedUploaderQuery = useDebounce(uploaderQuery, 350)
  const debouncedReviewerQuery = useDebounce(reviewerQuery, 350)

  const { data, isLoading, refetch } = usePendingStreamSuggestions({
    page,
    page_size: 20,
    suggestion_type: suggestionType === 'all' ? undefined : suggestionType,
    status: statusFilter,
    uploader_query: debouncedUploaderQuery.trim() || undefined,
    reviewer_query: debouncedReviewerQuery.trim() || undefined,
  })
  const { data: stats } = useStreamSuggestionStats()
  const reviewSuggestion = useReviewStreamSuggestion()
  const getReviewerLabel = (suggestion: StreamSuggestion): string | null => {
    if (suggestion.status === 'pending') return null
    if (suggestion.reviewer_name) return suggestion.reviewer_name
    if (suggestion.reviewed_by === 'auto') return 'Auto-approved'
    if (suggestion.reviewed_by) return `User #${suggestion.reviewed_by}`
    return null
  }
  const getReviewBadge = (suggestion: StreamSuggestion): { label: string; className: string } | null => {
    if (suggestion.status === 'pending') return null
    if (suggestion.status === 'auto_approved' || suggestion.reviewed_by === 'auto') {
      return {
        label: 'Auto',
        className: 'bg-blue-500/10 border-blue-500/30 text-blue-500',
      }
    }
    if (suggestion.status === 'approved') {
      return {
        label: 'Approved',
        className: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-500',
      }
    }
    return {
      label: 'Rejected',
      className: 'bg-red-500/10 border-red-500/30 text-red-500',
    }
  }
  const selectedReviewerLabel = selectedSuggestion ? getReviewerLabel(selectedSuggestion) : null
  const selectedReviewBadge = selectedSuggestion ? getReviewBadge(selectedSuggestion) : null

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
    } catch {
      // Error handled by mutation
    }
  }

  const showInitialLoading = isLoading && !data

  return (
    <div className="space-y-4">
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

      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={suggestionType}
          onValueChange={(value) => {
            setSuggestionType(value)
            setPage(1)
          }}
        >
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
        <Select
          value={statusFilter}
          onValueChange={(value) => {
            onStatusFilterChange(value as 'all' | StreamSuggestionStatus)
            setPage(1)
          }}
        >
          <SelectTrigger className="w-[180px] rounded-xl">
            <Clock className="mr-2 h-4 w-4" />
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Statuses</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="approved">Approved</SelectItem>
            <SelectItem value="auto_approved">Auto-Approved</SelectItem>
            <SelectItem value="rejected">Rejected</SelectItem>
          </SelectContent>
        </Select>
        <Input
          value={uploaderQuery}
          onChange={(event) => {
            setUploaderQuery(event.target.value)
            setPage(1)
          }}
          placeholder="Submitted by (username or ID)"
          className="w-[220px] rounded-xl"
        />
        <Input
          value={reviewerQuery}
          onChange={(event) => {
            setReviewerQuery(event.target.value)
            setPage(1)
          }}
          placeholder="Approved by (username or ID)"
          className="w-[220px] rounded-xl"
        />
      </div>

      {showInitialLoading ? (
        <div className="space-y-4">
          {[...Array(5)].map((_, i) => (
            <Skeleton key={i} className="h-16 rounded-xl" />
          ))}
        </div>
      ) : !data?.suggestions.length ? (
        <div className="text-center py-12">
          <Film className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-muted-foreground">No stream suggestions found</p>
        </div>
      ) : (
        <div className="space-y-3">
          {data.suggestions.map((suggestion) => {
            const episodeInfo = parseEpisodeLinkField(suggestion.field_name)
            const isEpisodeLink = !!episodeInfo
            const reviewerLabel = getReviewerLabel(suggestion)
            const reviewBadge = getReviewBadge(suggestion)

            return (
              <Card key={suggestion.id} className="glass border-border/50 hover:border-primary/30 transition-colors">
                <CardContent className="p-4">
                  <div className="flex items-start gap-4">
                    <div
                      className={`p-2 rounded-xl flex-shrink-0 ${isEpisodeLink ? 'bg-blue-500/10' : 'bg-primary/10'}`}
                    >
                      <Film className={`h-5 w-5 ${isEpisodeLink ? 'text-blue-500' : 'text-primary'}`} />
                    </div>

                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge
                          variant="outline"
                          className={`text-xs capitalize ${
                            suggestion.status === 'approved' || suggestion.status === 'auto_approved'
                              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-500'
                              : suggestion.status === 'rejected'
                                ? 'bg-red-500/10 border-red-500/30 text-red-500'
                                : 'bg-amber-500/10 border-amber-500/30 text-amber-500'
                          }`}
                        >
                          {suggestion.status}
                        </Badge>
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

                      <p className="text-sm text-muted-foreground truncate" title={suggestion.stream_name || ''}>
                        <span className="font-medium text-foreground">Stream:</span>{' '}
                        {suggestion.stream_name || `ID: ${suggestion.stream_id}`}
                      </p>

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

                      {suggestion.reason && (
                        <p className="text-xs text-muted-foreground truncate" title={suggestion.reason}>
                          <span className="font-medium">Reason:</span> {suggestion.reason}
                        </p>
                      )}

                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>by {suggestion.username || 'User'}</span>
                        <span>•</span>
                        <span>{formatTimeAgo(suggestion.created_at)}</span>
                        {reviewerLabel && reviewBadge && (
                          <>
                            <span>•</span>
                            <span className="inline-flex items-center gap-1.5" title={reviewerLabel}>
                              <Badge variant="outline" className={`h-5 px-1.5 text-[10px] ${reviewBadge.className}`}>
                                {reviewBadge.label}
                              </Badge>
                              <span>Reviewed by: {reviewerLabel}</span>
                            </span>
                          </>
                        )}
                      </div>
                    </div>

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
                        {suggestion.status === 'pending' ? 'Review' : 'View'}
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

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

      <Dialog open={reviewDialogOpen} onOpenChange={setReviewDialogOpen}>
        <DialogContent
          scrollMode="contained"
          className="sm:max-w-[600px] max-h-[90vh] flex flex-col overflow-hidden min-h-0"
        >
          <DialogHeader className="shrink-0">
            <DialogTitle className="flex items-center gap-2">
              <Eye className="h-5 w-5 text-primary" />
              Review Stream Suggestion
            </DialogTitle>
            <DialogDescription>Review this suggestion and approve or reject it.</DialogDescription>
          </DialogHeader>

          <ScrollArea className="flex-1 min-h-0 pr-1">
            {selectedSuggestion && (
              <div className="space-y-4 py-4">
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

                {parseEpisodeLinkField(selectedSuggestion.field_name) && (
                  <div className="p-4 rounded-xl bg-blue-500/5 border border-blue-500/20 space-y-3">
                    <div className="flex items-center gap-2">
                      <Film className="h-4 w-4 text-blue-500" />
                      <span className="font-medium text-blue-600 dark:text-blue-400">Episode Link Correction</span>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
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
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
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

                {!parseEpisodeLinkField(selectedSuggestion.field_name) &&
                  (selectedSuggestion.current_value || selectedSuggestion.suggested_value) && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
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

                {selectedSuggestion.reason && (
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-muted-foreground">User's Reason</label>
                    <div className="p-3 rounded-lg bg-muted/50">
                      <p className="text-sm">{selectedSuggestion.reason}</p>
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
                  <span>Submitted by: {selectedSuggestion.username || selectedSuggestion.user_id}</span>
                  <span>•</span>
                  <span>{formatTimeAgo(selectedSuggestion.created_at)}</span>
                  {selectedReviewerLabel && selectedReviewBadge && (
                    <>
                      <span>•</span>
                      <span className="inline-flex items-center gap-1.5" title={selectedReviewerLabel}>
                        <Badge variant="outline" className={`h-5 px-1.5 text-[10px] ${selectedReviewBadge.className}`}>
                          {selectedReviewBadge.label}
                        </Badge>
                        <span>Reviewed by: {selectedReviewerLabel}</span>
                      </span>
                      {selectedSuggestion.reviewed_at && (
                        <>
                          <span>•</span>
                          <span>Reviewed: {formatTimeAgo(selectedSuggestion.reviewed_at)}</span>
                        </>
                      )}
                    </>
                  )}
                </div>

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
          </ScrollArea>

          <DialogFooter className="gap-2 shrink-0">
            <Button variant="outline" onClick={() => setReviewDialogOpen(false)} disabled={reviewSuggestion.isPending}>
              {selectedSuggestion?.status === 'pending' ? 'Cancel' : 'Close'}
            </Button>
            {selectedSuggestion?.status === 'pending' && (
              <>
                <Button
                  variant="destructive"
                  onClick={() => handleReview('reject')}
                  disabled={reviewSuggestion.isPending}
                >
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
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
