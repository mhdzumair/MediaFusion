import { useState } from 'react'
import { ChevronLeft, ChevronRight, Eye, Filter, Inbox } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useDebounce, usePendingSuggestions, useReviewSuggestion } from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import type { Suggestion, SuggestionStatus } from '@/lib/api'

import { formatTimeAgo, getSuggestionMediaSummary, statusConfig, type ReviewDecision } from './helpers'
import { ModeratorMediaPoster } from './ModeratorMediaPoster'
import { ReviewDialog } from './ReviewDialog'

interface PendingSuggestionsTabProps {
  statusFilter: SuggestionStatus | 'all'
  onStatusFilterChange: (status: SuggestionStatus | 'all') => void
}

export function PendingSuggestionsTab({ statusFilter, onStatusFilterChange }: PendingSuggestionsTabProps) {
  const { toast } = useToast()
  const [page, setPage] = useState(1)
  const [uploaderQuery, setUploaderQuery] = useState('')
  const [reviewerQuery, setReviewerQuery] = useState('')
  const [selectedSuggestion, setSelectedSuggestion] = useState<Suggestion | null>(null)
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const debouncedUploaderQuery = useDebounce(uploaderQuery, 350)
  const debouncedReviewerQuery = useDebounce(reviewerQuery, 350)

  const { data, isLoading, refetch } = usePendingSuggestions({
    page,
    page_size: 20,
    status: statusFilter,
    uploader_query: debouncedUploaderQuery.trim() || undefined,
    reviewer_query: debouncedReviewerQuery.trim() || undefined,
  })
  const reviewSuggestion = useReviewSuggestion()
  const getReviewBadge = (suggestion: Suggestion): { label: string; className: string } | null => {
    if (suggestion.status === 'pending') return null
    if (suggestion.status === 'auto_approved') {
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

  const handleReview = async (decision: ReviewDecision, notes?: string) => {
    if (!selectedSuggestion) return
    try {
      await reviewSuggestion.mutateAsync({
        suggestionId: selectedSuggestion.id,
        data: { action: decision, review_notes: notes },
      })
      refetch()
    } catch (error) {
      toast({
        title: 'Review failed',
        description: error instanceof Error ? error.message : 'Unable to review suggestion',
        variant: 'destructive',
      })
      throw error
    }
  }

  const showInitialLoading = isLoading && !data

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={statusFilter}
          onValueChange={(v) => {
            onStatusFilterChange(v as SuggestionStatus | 'all')
            setPage(1)
          }}
        >
          <SelectTrigger className="w-[180px] rounded-xl">
            <Filter className="mr-2 h-4 w-4" />
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
          <Inbox className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-lg font-medium">No metadata suggestions found</p>
          <p className="text-sm text-muted-foreground mt-2">Try changing the status filter.</p>
        </div>
      ) : (
        <div className="rounded-xl border border-border/50 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/30">
                <TableHead>Status</TableHead>
                <TableHead>Field</TableHead>
                <TableHead>Media</TableHead>
                <TableHead>Current → Suggested</TableHead>
                <TableHead>Submitted</TableHead>
                <TableHead>Reviewed By</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.suggestions.map((suggestion: Suggestion) => {
                const reviewBadge = getReviewBadge(suggestion)
                return (
                  <TableRow key={suggestion.id} className="hover:bg-muted/20">
                    <TableCell>
                      <Badge variant="outline" className={statusConfig[suggestion.status].color}>
                        {statusConfig[suggestion.status].label}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium capitalize">{suggestion.field_name}</TableCell>
                    <TableCell>
                      <div className="flex max-w-sm items-center gap-3">
                        <div className="h-14 w-10 shrink-0 overflow-hidden rounded-md border border-border/50 bg-muted">
                          <ModeratorMediaPoster
                            mediaType={suggestion.media_type}
                            mediaId={suggestion.media_id}
                            posterUrl={suggestion.media_poster_url}
                            title={suggestion.media_title}
                            fallbackIconSizeClassName="h-4 w-4"
                          />
                        </div>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{suggestion.media_title || 'Unknown title'}</p>
                          <p className="truncate text-xs text-muted-foreground">
                            {getSuggestionMediaSummary(suggestion)}
                          </p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="max-w-xs space-y-1">
                        <p className="truncate text-xs text-red-500">
                          <span className="text-muted-foreground">Current:</span>{' '}
                          {suggestion.current_value || '(empty)'}
                        </p>
                        <p className="truncate text-xs text-emerald-500">
                          <span className="text-muted-foreground">Suggested:</span> {suggestion.suggested_value}
                        </p>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatTimeAgo(suggestion.created_at)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {suggestion.reviewed_by ? (
                        <div className="space-y-1">
                          <div className="inline-flex items-center gap-1.5">
                            {reviewBadge ? (
                              <Badge variant="outline" className={`h-5 px-1.5 text-[10px] ${reviewBadge.className}`}>
                                {reviewBadge.label}
                              </Badge>
                            ) : null}
                            <p title={suggestion.reviewed_by}>{suggestion.reviewed_by}</p>
                          </div>
                          {suggestion.reviewed_at ? <p>({formatTimeAgo(suggestion.reviewed_at)})</p> : null}
                        </div>
                      ) : (
                        <span>-</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {suggestion.status === 'pending' ? (
                        <Button
                          size="sm"
                          onClick={() => {
                            setSelectedSuggestion(suggestion)
                            setReviewDialogOpen(true)
                          }}
                          className="rounded-lg"
                        >
                          <Eye className="mr-2 h-4 w-4" />
                          Review
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">Reviewed</span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

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
