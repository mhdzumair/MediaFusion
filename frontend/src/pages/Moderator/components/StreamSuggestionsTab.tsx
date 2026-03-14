import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Eye,
  Film,
  Filter,
  ListChecks,
  Loader2,
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
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
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
import {
  useBulkReviewStreamSuggestions,
  useDebounce,
  usePendingStreamSuggestions,
  useReviewStreamSuggestion,
  useStreamSuggestionStats,
} from '@/hooks'
import type { StreamSuggestion, StreamSuggestionStatus } from '@/lib/api'

import { formatStreamFieldName, formatStreamSuggestionType, parseEpisodeLinkField, formatTimeAgo } from './helpers'
import { ModeratorMediaPoster } from './ModeratorMediaPoster'

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
  const [bulkModeEnabled, setBulkModeEnabled] = useState(false)
  const [selectedSuggestionIds, setSelectedSuggestionIds] = useState<string[]>([])
  const [bulkApproveDialogOpen, setBulkApproveDialogOpen] = useState(false)
  const [bulkRejectDialogOpen, setBulkRejectDialogOpen] = useState(false)
  const [bulkApproveNotes, setBulkApproveNotes] = useState('')
  const [bulkRejectNotes, setBulkRejectNotes] = useState('')
  const [isBulkApprovingSelected, setIsBulkApprovingSelected] = useState(false)
  const [isBulkRejectingSelected, setIsBulkRejectingSelected] = useState(false)
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
  const bulkReviewSuggestions = useBulkReviewStreamSuggestions()
  const isAnyActionPending = reviewSuggestion.isPending || bulkReviewSuggestions.isPending
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
  const suggestionsOnPage = useMemo(() => data?.suggestions ?? [], [data?.suggestions])
  const pendingSuggestionsOnPage = suggestionsOnPage.filter((suggestion) => suggestion.status === 'pending')
  const pendingSuggestionIdsOnPage = pendingSuggestionsOnPage.map((suggestion) => suggestion.id)
  const pendingSuggestionIdsKey = pendingSuggestionIdsOnPage.join('|')
  const pendingSuggestionIdSet = useMemo(
    () => new Set(pendingSuggestionIdsKey ? pendingSuggestionIdsKey.split('|') : []),
    [pendingSuggestionIdsKey],
  )
  const selectedSuggestionIdSet = new Set(selectedSuggestionIds)
  const selectedPendingSuggestionIds = selectedSuggestionIds.filter((suggestionId) =>
    pendingSuggestionIdSet.has(suggestionId),
  )
  const selectedPendingCount = selectedPendingSuggestionIds.length
  const allPendingOnPageSelected =
    pendingSuggestionsOnPage.length > 0 &&
    pendingSuggestionsOnPage.every((suggestion) => selectedSuggestionIdSet.has(suggestion.id))
  const hasSomePendingOnPageSelected = selectedPendingCount > 0 && !allPendingOnPageSelected

  useEffect(() => {
    setSelectedSuggestionIds((previousSelection) => {
      const nextSelection = previousSelection.filter((suggestionId) => pendingSuggestionIdSet.has(suggestionId))
      if (
        nextSelection.length === previousSelection.length &&
        nextSelection.every((suggestionId, idx) => suggestionId === previousSelection[idx])
      ) {
        return previousSelection
      }
      return nextSelection
    })
  }, [pendingSuggestionIdSet])

  const toggleSuggestionSelection = (suggestionId: string, checked: boolean) => {
    setSelectedSuggestionIds((currentSelection) => {
      if (checked) {
        if (currentSelection.includes(suggestionId)) return currentSelection
        return [...currentSelection, suggestionId]
      }
      return currentSelection.filter((item) => item !== suggestionId)
    })
  }

  const toggleSelectAllPendingOnPage = (checked: boolean) => {
    if (checked) {
      setSelectedSuggestionIds((currentSelection) =>
        Array.from(new Set([...currentSelection, ...pendingSuggestionIdsOnPage])),
      )
      return
    }
    setSelectedSuggestionIds((currentSelection) =>
      currentSelection.filter((suggestionId) => !pendingSuggestionIdSet.has(suggestionId)),
    )
  }
  const getBaseSuggestionType = (value: string): string => (value.includes(':') ? value.split(':', 1)[0] : value)
  const isRelinkSuggestion = (suggestion: StreamSuggestion): boolean => {
    const suggestionType = getBaseSuggestionType(suggestion.suggestion_type)
    return suggestionType === 'relink_media' || suggestionType === 'add_media_link'
  }
  const getContentPath = (mediaType: string | null, mediaId: number | null): string | null => {
    if (!mediaId) return null
    if (mediaType !== 'movie' && mediaType !== 'series' && mediaType !== 'tv') return null
    return `/dashboard/content/${mediaType}/${mediaId}`
  }
  const getMediaLabel = (
    mediaTitle: string | null,
    mediaType: string | null,
    mediaId: number | null,
    mediaYear: number | null,
  ): string => {
    const title = mediaTitle?.trim() || (mediaId ? `Media #${mediaId}` : 'Unknown media')
    const yearSuffix = mediaYear ? ` (${mediaYear})` : ''
    const typePrefix = mediaType ? `${mediaType.toUpperCase()} • ` : ''
    return `${typePrefix}${title}${yearSuffix}`
  }
  const parseRelinkPayload = (value: string | null): Record<string, unknown> | null => {
    if (!value) return null
    try {
      const parsed = JSON.parse(value)
      return typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : null
    } catch {
      return null
    }
  }
  const buildRelinkCurrentValue = (suggestion: StreamSuggestion): string => {
    const sourceMediaId = suggestion.source_media_id ?? suggestion.media_id
    const sourceTitle = suggestion.source_media_title || suggestion.current_value
    if (sourceMediaId) {
      return sourceTitle ? `mf:${sourceMediaId} (${sourceTitle})` : `mf:${sourceMediaId}`
    }
    return sourceTitle || '(empty)'
  }
  const buildRelinkSuggestedValue = (suggestion: StreamSuggestion): string => {
    const payload = parseRelinkPayload(suggestion.suggested_value)
    const payloadTargetMediaId =
      typeof payload?.target_media_id === 'number' && payload.target_media_id > 0 ? payload.target_media_id : null
    const targetMediaId = suggestion.target_media_id ?? payloadTargetMediaId
    const payloadTargetTitle =
      typeof payload?.target_title === 'string' && payload.target_title.trim() ? payload.target_title.trim() : null
    const targetTitle = suggestion.target_media_title || payloadTargetTitle
    if (targetMediaId) {
      return targetTitle ? `mf:${targetMediaId} (${targetTitle})` : `mf:${targetMediaId}`
    }
    if (targetTitle) return targetTitle
    return suggestion.suggested_value || '(empty)'
  }

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

  const handleBulkReviewSelected = async (action: 'approve' | 'reject') => {
    if (!selectedPendingCount) return
    if (action === 'approve') {
      setIsBulkApprovingSelected(true)
    } else {
      setIsBulkRejectingSelected(true)
    }

    try {
      const reviewNotes = (action === 'approve' ? bulkApproveNotes : bulkRejectNotes).trim() || undefined
      await bulkReviewSuggestions.mutateAsync({
        suggestionIds: selectedPendingSuggestionIds,
        action,
        reviewNotes,
      })
      if (action === 'approve') {
        setBulkApproveDialogOpen(false)
        setBulkApproveNotes('')
      } else {
        setBulkRejectDialogOpen(false)
        setBulkRejectNotes('')
      }
      setSelectedSuggestionIds([])
      refetch()
    } catch {
      // Error handled by mutation
    } finally {
      if (action === 'approve') {
        setIsBulkApprovingSelected(false)
      } else {
        setIsBulkRejectingSelected(false)
      }
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
            <SelectItem value="relink_media">Relink Media</SelectItem>
            <SelectItem value="add_media_link">Add Media Link</SelectItem>
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
        <Button
          variant={bulkModeEnabled ? 'default' : 'outline'}
          className="rounded-xl"
          onClick={() => {
            setBulkModeEnabled((value) => !value)
            setSelectedSuggestionIds([])
          }}
          disabled={isAnyActionPending}
        >
          <ListChecks className="h-4 w-4 mr-2" />
          {bulkModeEnabled ? 'Exit Bulk Mode' : 'Bulk Action Mode'}
        </Button>
      </div>

      {bulkModeEnabled && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border/50 bg-muted/20 p-3">
          <span className="text-xs text-muted-foreground">Bulk actions apply to pending suggestions only.</span>
          <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Checkbox
              checked={allPendingOnPageSelected ? true : hasSomePendingOnPageSelected ? 'indeterminate' : false}
              onCheckedChange={(checked) => toggleSelectAllPendingOnPage(checked === true)}
              disabled={!pendingSuggestionsOnPage.length || isAnyActionPending}
            />
            Select all pending on this page
          </label>
          <span className="text-sm text-muted-foreground">
            Selected: <span className="font-medium text-foreground">{selectedPendingCount}</span>
          </span>
          <Button
            className="rounded-xl bg-emerald-600 hover:bg-emerald-700"
            onClick={() => setBulkApproveDialogOpen(true)}
            disabled={!selectedPendingCount || isAnyActionPending}
          >
            {isBulkApprovingSelected ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <CheckCircle2 className="h-4 w-4 mr-2" />
            )}
            Approve Selected
          </Button>
          <Button
            variant="destructive"
            className="rounded-xl"
            onClick={() => setBulkRejectDialogOpen(true)}
            disabled={!selectedPendingCount || isAnyActionPending}
          >
            {isBulkRejectingSelected ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Trash2 className="h-4 w-4 mr-2" />
            )}
            Reject Selected
          </Button>
        </div>
      )}

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
            const isRelink = isRelinkSuggestion(suggestion)
            const reviewerLabel = getReviewerLabel(suggestion)
            const reviewBadge = getReviewBadge(suggestion)

            return (
              <Card key={suggestion.id} className="glass border-border/50 hover:border-primary/30 transition-colors">
                <CardContent className="p-4">
                  <div className="flex items-start gap-4">
                    {bulkModeEnabled && (
                      <div className="pt-1">
                        <Checkbox
                          checked={selectedSuggestionIdSet.has(suggestion.id)}
                          onCheckedChange={(checked) => toggleSuggestionSelection(suggestion.id, checked === true)}
                          disabled={suggestion.status !== 'pending' || isAnyActionPending}
                        />
                      </div>
                    )}
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

                      {isRelink && (
                        <div className="rounded-lg border border-border/50 bg-muted/30 p-2 text-xs text-muted-foreground">
                          <p
                            className="truncate"
                            title={getMediaLabel(
                              suggestion.source_media_title,
                              suggestion.source_media_type,
                              suggestion.source_media_id,
                              suggestion.source_media_year,
                            )}
                          >
                            <span className="font-medium text-foreground">Source:</span>{' '}
                            {getMediaLabel(
                              suggestion.source_media_title,
                              suggestion.source_media_type,
                              suggestion.source_media_id,
                              suggestion.source_media_year,
                            )}
                          </p>
                          <p
                            className="truncate"
                            title={getMediaLabel(
                              suggestion.target_media_title,
                              suggestion.target_media_type,
                              suggestion.target_media_id,
                              suggestion.target_media_year,
                            )}
                          >
                            <span className="font-medium text-foreground">Target:</span>{' '}
                            {getMediaLabel(
                              suggestion.target_media_title,
                              suggestion.target_media_type,
                              suggestion.target_media_id,
                              suggestion.target_media_year,
                            )}
                          </p>
                        </div>
                      )}

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

      <AlertDialog open={bulkApproveDialogOpen} onOpenChange={setBulkApproveDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Approve selected pending stream suggestions?</AlertDialogTitle>
            <AlertDialogDescription>
              This approves {selectedPendingCount} selected pending suggestion{selectedPendingCount === 1 ? '' : 's'}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label className="text-sm font-medium">Review Notes (optional)</label>
            <Textarea
              value={bulkApproveNotes}
              onChange={(event) => setBulkApproveNotes(event.target.value)}
              placeholder="Add notes for approved suggestions..."
              rows={3}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isBulkApprovingSelected}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => handleBulkReviewSelected('approve')}
              disabled={isBulkApprovingSelected || !selectedPendingCount}
              className="bg-emerald-600 text-white hover:bg-emerald-700"
            >
              {isBulkApprovingSelected ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Approve Selected
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={bulkRejectDialogOpen} onOpenChange={setBulkRejectDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reject selected pending stream suggestions?</AlertDialogTitle>
            <AlertDialogDescription>
              This rejects {selectedPendingCount} selected pending suggestion{selectedPendingCount === 1 ? '' : 's'}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label className="text-sm font-medium">Review Notes (optional)</label>
            <Textarea
              value={bulkRejectNotes}
              onChange={(event) => setBulkRejectNotes(event.target.value)}
              placeholder="Add notes for rejected suggestions..."
              rows={3}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isBulkRejectingSelected}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => handleBulkReviewSelected('reject')}
              disabled={isBulkRejectingSelected || !selectedPendingCount}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isBulkRejectingSelected ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Reject Selected
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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

                {isRelinkSuggestion(selectedSuggestion) && (
                  <div className="space-y-3 rounded-xl border border-border/50 bg-muted/20 p-3">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Media Relink Preview
                    </p>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div className="space-y-2 rounded-lg border border-border/40 bg-background/60 p-3">
                        <p className="text-xs font-medium text-muted-foreground">Source Media</p>
                        <div className="flex items-start gap-3">
                          <div className="h-20 w-14 shrink-0 overflow-hidden rounded-md border border-border/50 bg-muted">
                            {getContentPath(
                              selectedSuggestion.source_media_type,
                              selectedSuggestion.source_media_id,
                            ) ? (
                              <Link
                                to={
                                  getContentPath(
                                    selectedSuggestion.source_media_type,
                                    selectedSuggestion.source_media_id,
                                  )!
                                }
                              >
                                <ModeratorMediaPoster
                                  mediaType={selectedSuggestion.source_media_type}
                                  mediaId={selectedSuggestion.source_media_id}
                                  posterUrl={selectedSuggestion.source_media_poster_url}
                                  title={selectedSuggestion.source_media_title}
                                  fallbackIconSizeClassName="h-4 w-4"
                                />
                              </Link>
                            ) : (
                              <ModeratorMediaPoster
                                mediaType={selectedSuggestion.source_media_type}
                                mediaId={selectedSuggestion.source_media_id}
                                posterUrl={selectedSuggestion.source_media_poster_url}
                                title={selectedSuggestion.source_media_title}
                                fallbackIconSizeClassName="h-4 w-4"
                              />
                            )}
                          </div>
                          <div className="min-w-0 space-y-1">
                            <p className="text-sm font-medium break-words">
                              {selectedSuggestion.source_media_title ||
                                `Media #${selectedSuggestion.source_media_id || 'N/A'}`}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {[selectedSuggestion.source_media_type, selectedSuggestion.source_media_year]
                                .map((value) => (value === null || value === undefined ? null : String(value)))
                                .filter((value): value is string => Boolean(value))
                                .join(' • ') || 'Unknown'}
                            </p>
                            {getContentPath(
                              selectedSuggestion.source_media_type,
                              selectedSuggestion.source_media_id,
                            ) && (
                              <Link
                                to={
                                  getContentPath(
                                    selectedSuggestion.source_media_type,
                                    selectedSuggestion.source_media_id,
                                  )!
                                }
                                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                              >
                                Open Source
                                <ExternalLink className="h-3 w-3" />
                              </Link>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="space-y-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3">
                        <p className="text-xs font-medium text-emerald-500">Target Media</p>
                        <div className="flex items-start gap-3">
                          <div className="h-20 w-14 shrink-0 overflow-hidden rounded-md border border-border/50 bg-muted">
                            {getContentPath(
                              selectedSuggestion.target_media_type,
                              selectedSuggestion.target_media_id,
                            ) ? (
                              <Link
                                to={
                                  getContentPath(
                                    selectedSuggestion.target_media_type,
                                    selectedSuggestion.target_media_id,
                                  )!
                                }
                              >
                                <ModeratorMediaPoster
                                  mediaType={selectedSuggestion.target_media_type}
                                  mediaId={selectedSuggestion.target_media_id}
                                  posterUrl={selectedSuggestion.target_media_poster_url}
                                  title={selectedSuggestion.target_media_title}
                                  fallbackIconSizeClassName="h-4 w-4"
                                />
                              </Link>
                            ) : (
                              <ModeratorMediaPoster
                                mediaType={selectedSuggestion.target_media_type}
                                mediaId={selectedSuggestion.target_media_id}
                                posterUrl={selectedSuggestion.target_media_poster_url}
                                title={selectedSuggestion.target_media_title}
                                fallbackIconSizeClassName="h-4 w-4"
                              />
                            )}
                          </div>
                          <div className="min-w-0 space-y-1">
                            <p className="text-sm font-medium break-words">
                              {selectedSuggestion.target_media_title ||
                                `Media #${selectedSuggestion.target_media_id || 'N/A'}`}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {[selectedSuggestion.target_media_type, selectedSuggestion.target_media_year]
                                .map((value) => (value === null || value === undefined ? null : String(value)))
                                .filter((value): value is string => Boolean(value))
                                .join(' • ') || 'Unknown'}
                            </p>
                            {getContentPath(
                              selectedSuggestion.target_media_type,
                              selectedSuggestion.target_media_id,
                            ) && (
                              <Link
                                to={
                                  getContentPath(
                                    selectedSuggestion.target_media_type,
                                    selectedSuggestion.target_media_id,
                                  )!
                                }
                                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                              >
                                Open Target
                                <ExternalLink className="h-3 w-3" />
                              </Link>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
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
                  (selectedSuggestion.current_value ||
                    selectedSuggestion.suggested_value ||
                    isRelinkSuggestion(selectedSuggestion)) && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-muted-foreground">
                          {isRelinkSuggestion(selectedSuggestion) ? 'Current Linked Media' : 'Current Value'}
                        </label>
                        <div className="p-3 rounded-lg bg-red-500/5 border border-red-500/20">
                          <p className="text-sm break-words">
                            {isRelinkSuggestion(selectedSuggestion)
                              ? buildRelinkCurrentValue(selectedSuggestion)
                              : selectedSuggestion.current_value || '(empty)'}
                          </p>
                        </div>
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-muted-foreground">
                          {isRelinkSuggestion(selectedSuggestion) ? 'Suggested Linked Media' : 'Suggested Value'}
                        </label>
                        <div className="p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
                          <p className="text-sm break-words">
                            {isRelinkSuggestion(selectedSuggestion)
                              ? buildRelinkSuggestedValue(selectedSuggestion)
                              : selectedSuggestion.suggested_value || '(empty)'}
                          </p>
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
