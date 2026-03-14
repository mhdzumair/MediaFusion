import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Eye,
  FileText as FileIcon,
  HardDrive,
  Library,
  ListChecks,
  Loader2,
  Magnet,
  Tag,
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
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  useAdminRejectApprovedContribution,
  useAuth,
  useBulkReviewContributions,
  useContributionContributors,
  useContributions,
  useDebounce,
  useFlagContributionForAdminReview,
  useReviewContribution,
} from '@/hooks'
import type { Contribution, ContributionStatus, ContributionType } from '@/lib/api'

import {
  formatBytes,
  formatTimeAgo,
  formatTorrentData,
  getContentDetailLink,
  getContributionMediaPreview,
  getContributionUploaderLabel,
  getInternalMediaId,
  getLibraryBrowseLink,
} from './helpers'
import { ModeratorMediaPoster } from './ModeratorMediaPoster'

interface ContributionsTabProps {
  statusFilter: 'all' | ContributionStatus
  onStatusFilterChange: (status: 'all' | ContributionStatus) => void
  typeFilter: string
  onTypeFilterChange: (value: string) => void
  contributorFilter: string
  onContributorFilterChange: (value: string) => void
  uploaderQuery: string
  onUploaderQueryChange: (value: string) => void
  reviewerQuery: string
  onReviewerQueryChange: (value: string) => void
  page: number
  onPageChange: (value: number) => void
}

export function ContributionsTab({
  statusFilter,
  onStatusFilterChange,
  typeFilter,
  onTypeFilterChange,
  contributorFilter,
  onContributorFilterChange,
  uploaderQuery,
  onUploaderQueryChange,
  reviewerQuery,
  onReviewerQueryChange,
  page,
  onPageChange,
}: ContributionsTabProps) {
  const { user } = useAuth()
  const [selectedContribution, setSelectedContribution] = useState<Contribution | null>(null)
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)
  const [reviewNotes, setReviewNotes] = useState('')
  const [bulkApproveDialogOpen, setBulkApproveDialogOpen] = useState(false)
  const [isBulkApproving, setIsBulkApproving] = useState(false)
  const [bulkApproveSelectedDialogOpen, setBulkApproveSelectedDialogOpen] = useState(false)
  const [bulkApproveSelectedNotes, setBulkApproveSelectedNotes] = useState('')
  const [isBulkApprovingSelected, setIsBulkApprovingSelected] = useState(false)
  const [bulkModeEnabled, setBulkModeEnabled] = useState(false)
  const [selectedContributionIds, setSelectedContributionIds] = useState<string[]>([])
  const [bulkRejectDialogOpen, setBulkRejectDialogOpen] = useState(false)
  const [bulkRejectNotes, setBulkRejectNotes] = useState('')
  const [isBulkRejectingSelected, setIsBulkRejectingSelected] = useState(false)

  const selectedContributionData = (selectedContribution?.data as Record<string, unknown> | undefined) ?? undefined
  const selectedMediaPreview = selectedContribution ? getContributionMediaPreview(selectedContribution) : null
  const selectedMediaId =
    getInternalMediaId(selectedContribution?.media_id) ??
    getInternalMediaId(selectedContributionData?.media_id) ??
    getInternalMediaId(selectedContribution?.mediafusion_id) ??
    getInternalMediaId(selectedContributionData?.mediafusion_id)
  const selectedContentLink = selectedMediaPreview ? getContentDetailLink(selectedMediaPreview, selectedMediaId) : null
  const selectedLibraryLink = selectedMediaPreview
    ? getLibraryBrowseLink(selectedMediaPreview)
    : '/dashboard/library?tab=browse'
  const selectedMediaOpenLink = selectedContentLink ?? selectedLibraryLink
  const selectedMediaOpenLabel = selectedContentLink ? 'Open Content' : 'Open in Library'
  const selectedImdbId =
    selectedMediaPreview?.metaId?.toLowerCase().startsWith('tt') === true ? selectedMediaPreview.metaId : null

  const debouncedUploaderQuery = useDebounce(uploaderQuery, 350)
  const debouncedReviewerQuery = useDebounce(reviewerQuery, 350)
  const { data: contributorOptions } = useContributionContributors({
    contribution_type: typeFilter === 'all' ? undefined : (typeFilter as ContributionType),
    contribution_status: statusFilter === 'all' ? undefined : statusFilter,
    limit: 100,
  })

  const { data, isLoading, refetch } = useContributions({
    contribution_type: typeFilter === 'all' ? undefined : (typeFilter as ContributionType),
    contribution_status: statusFilter === 'all' ? undefined : statusFilter,
    contributor: contributorFilter === 'all' ? undefined : contributorFilter,
    uploader_query: debouncedUploaderQuery.trim() || undefined,
    reviewer_query: debouncedReviewerQuery.trim() || undefined,
    page,
    page_size: 20,
  })
  const reviewContribution = useReviewContribution()
  const flagForAdminReview = useFlagContributionForAdminReview()
  const adminRejectApprovedContribution = useAdminRejectApprovedContribution()
  const bulkReviewContributions = useBulkReviewContributions()
  const isAdmin = user?.role === 'admin'
  const isAnyActionPending =
    reviewContribution.isPending ||
    bulkReviewContributions.isPending ||
    flagForAdminReview.isPending ||
    adminRejectApprovedContribution.isPending
  const contributionsOnPage = useMemo(() => data?.items ?? [], [data?.items])
  const pendingContributions = contributionsOnPage.filter((contribution) => contribution.status === 'pending')
  const selectableContributions = contributionsOnPage.filter(
    (contribution) => contribution.status === 'pending' || (isAdmin && contribution.status === 'approved'),
  )
  const selectableContributionIds = selectableContributions.map((contribution) => contribution.id)
  const selectableContributionIdsKey = selectableContributionIds.join('|')
  const selectableContributionIdSet = useMemo(
    () => new Set(selectableContributionIdsKey ? selectableContributionIdsKey.split('|') : []),
    [selectableContributionIdsKey],
  )
  const contributionById = useMemo(
    () =>
      new Map<string, Contribution>(
        contributionsOnPage.map((contribution) => [contribution.id, contribution] as const),
      ),
    [contributionsOnPage],
  )
  const selectedContributionIdSet = new Set(selectedContributionIds)
  const selectedPendingContributionIds = selectedContributionIds.filter(
    (contributionId) => contributionById.get(contributionId)?.status === 'pending',
  )
  const selectedApprovedContributionIds = selectedContributionIds.filter(
    (contributionId) => contributionById.get(contributionId)?.status === 'approved',
  )
  const selectedPendingCount = selectedPendingContributionIds.length
  const selectedApprovedCount = selectedApprovedContributionIds.length
  const selectedTotalCount = selectedPendingCount + selectedApprovedCount
  const allSelectableOnPageSelected =
    selectableContributions.length > 0 &&
    selectableContributions.every((contribution) => selectedContributionIdSet.has(contribution.id))
  const hasSomeSelectableOnPageSelected = selectedTotalCount > 0 && !allSelectableOnPageSelected

  useEffect(() => {
    setSelectedContributionIds((previousSelection) => {
      const nextSelection = previousSelection.filter((contributionId) =>
        selectableContributionIdSet.has(contributionId),
      )
      if (
        nextSelection.length === previousSelection.length &&
        nextSelection.every((contributionId, idx) => contributionId === previousSelection[idx])
      ) {
        return previousSelection
      }
      return nextSelection
    })
  }, [selectableContributionIdSet])

  const toggleContributionSelection = (contributionId: string, checked: boolean) => {
    setSelectedContributionIds((currentSelection) => {
      if (checked) {
        if (currentSelection.includes(contributionId)) return currentSelection
        return [...currentSelection, contributionId]
      }
      return currentSelection.filter((item) => item !== contributionId)
    })
  }

  const toggleSelectAllSelectableOnPage = (checked: boolean) => {
    if (checked) {
      setSelectedContributionIds((currentSelection) =>
        Array.from(new Set([...currentSelection, ...selectableContributionIds])),
      )
      return
    }
    setSelectedContributionIds((currentSelection) =>
      currentSelection.filter((contributionId) => !selectableContributionIdSet.has(contributionId)),
    )
  }
  const getReviewerLabel = (contribution: Contribution): string | null => {
    if (contribution.status === 'pending') return null
    if (contribution.reviewer_name) return contribution.reviewer_name
    if (contribution.reviewed_by === 'auto') return 'Auto-approved'
    if (contribution.reviewed_by) return `User #${contribution.reviewed_by}`
    return null
  }
  const getReviewBadge = (contribution: Contribution): { label: string; className: string } | null => {
    if (contribution.status === 'pending') return null
    if (contribution.reviewed_by === 'auto') {
      return {
        label: 'Auto',
        className: 'bg-blue-500/10 border-blue-500/30 text-blue-500',
      }
    }
    if (contribution.status === 'approved') {
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
  const selectedReviewerLabel = selectedContribution ? getReviewerLabel(selectedContribution) : null
  const selectedReviewBadge = selectedContribution ? getReviewBadge(selectedContribution) : null

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
    } catch {
      // Error handled by mutation
    }
  }

  const handleApproveAllPending = async () => {
    if (!pendingContributions.length) return
    setIsBulkApproving(true)
    try {
      await bulkReviewContributions.mutateAsync({
        action: 'approve',
        contribution_type: typeFilter === 'all' ? undefined : (typeFilter as ContributionType),
      })
      setBulkApproveDialogOpen(false)
      setSelectedContribution(null)
      setReviewDialogOpen(false)
      setReviewNotes('')
      refetch()
    } catch {
      // Error handled by mutation
    } finally {
      setIsBulkApproving(false)
    }
  }

  const handleApproveSelectedContributions = async () => {
    if (!selectedPendingCount) return
    setIsBulkApprovingSelected(true)
    try {
      await bulkReviewContributions.mutateAsync({
        action: 'approve',
        contribution_ids: selectedPendingContributionIds,
        review_notes: bulkApproveSelectedNotes.trim() || undefined,
      })
      setBulkApproveSelectedDialogOpen(false)
      setBulkApproveSelectedNotes('')
      setSelectedContributionIds([])
      refetch()
    } catch {
      // Error handled by mutation
    } finally {
      setIsBulkApprovingSelected(false)
    }
  }

  const handleRejectSelectedContributions = async () => {
    if (!selectedTotalCount) return
    setIsBulkRejectingSelected(true)
    try {
      const reviewNotes = bulkRejectNotes.trim() || undefined
      if (selectedPendingContributionIds.length > 0) {
        await bulkReviewContributions.mutateAsync({
          action: 'reject',
          contribution_ids: selectedPendingContributionIds,
          review_notes: reviewNotes,
        })
      }
      if (isAdmin && selectedApprovedContributionIds.length > 0) {
        for (const contributionId of selectedApprovedContributionIds) {
          await adminRejectApprovedContribution.mutateAsync({
            contributionId,
            data: { review_notes: reviewNotes },
          })
        }
      }
      setBulkRejectDialogOpen(false)
      setBulkRejectNotes('')
      setSelectedContributionIds([])
      refetch()
    } catch {
      // Error handled by mutation
    } finally {
      setIsBulkRejectingSelected(false)
    }
  }

  const handleFlagForAdmin = async () => {
    if (!selectedContribution) return
    try {
      await flagForAdminReview.mutateAsync({
        contributionId: selectedContribution.id,
        data: { reason: reviewNotes || undefined },
      })
      setReviewDialogOpen(false)
      setSelectedContribution(null)
      setReviewNotes('')
      refetch()
    } catch {
      // Error handled by mutation
    }
  }

  const handleAdminRejectApproved = async () => {
    if (!selectedContribution) return
    try {
      await adminRejectApprovedContribution.mutateAsync({
        contributionId: selectedContribution.id,
        data: { review_notes: reviewNotes || undefined },
      })
      setReviewDialogOpen(false)
      setSelectedContribution(null)
      setReviewNotes('')
      refetch()
    } catch {
      // Error handled by mutation
    }
  }

  const showInitialLoading = isLoading && !data

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={typeFilter}
          onValueChange={(value) => {
            onTypeFilterChange(value)
          }}
        >
          <SelectTrigger className="w-[180px] rounded-xl">
            <Clock className="mr-2 h-4 w-4" />
            <SelectValue placeholder="Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="torrent">Torrent Imports</SelectItem>
            <SelectItem value="stream">New Streams</SelectItem>
            <SelectItem value="metadata">Metadata Fixes</SelectItem>
            <SelectItem value="telegram">Telegram Uploads</SelectItem>
            <SelectItem value="youtube">YouTube Imports</SelectItem>
            <SelectItem value="nzb">NZB Imports</SelectItem>
            <SelectItem value="http">HTTP Imports</SelectItem>
            <SelectItem value="acestream">AceStream Imports</SelectItem>
          </SelectContent>
        </Select>

        <Select
          value={statusFilter}
          onValueChange={(value) => {
            onStatusFilterChange(value as 'all' | ContributionStatus)
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
            <SelectItem value="rejected">Rejected</SelectItem>
          </SelectContent>
        </Select>

        <Select
          value={contributorFilter}
          onValueChange={(value) => {
            onContributorFilterChange(value)
          }}
        >
          <SelectTrigger className="w-[280px] rounded-xl">
            <Clock className="mr-2 h-4 w-4" />
            <SelectValue placeholder="Contributor" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Contributors</SelectItem>
            {(contributorOptions?.items ?? []).map((contributor) => (
              <SelectItem key={contributor.key} value={contributor.key}>
                {contributor.label} ({contributor.total})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Input
          value={uploaderQuery}
          onChange={(event) => {
            onUploaderQueryChange(event.target.value)
          }}
          placeholder="Submitted by (username, anonymous name, or ID)"
          className="w-[300px] rounded-xl"
        />

        <Input
          value={reviewerQuery}
          onChange={(event) => {
            onReviewerQueryChange(event.target.value)
          }}
          placeholder='Approved by (username, ID, or "auto")'
          className="w-[260px] rounded-xl"
        />

        {pendingContributions.length > 0 && (
          <Button
            variant="outline"
            className="rounded-xl"
            onClick={() => setBulkApproveDialogOpen(true)}
            disabled={isBulkApproving || reviewContribution.isPending || bulkReviewContributions.isPending}
          >
            {isBulkApproving ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <CheckCircle2 className="h-4 w-4 mr-2 text-emerald-500" />
            )}
            Approve All Pending
          </Button>
        )}

        <Button
          variant={bulkModeEnabled ? 'default' : 'outline'}
          className="rounded-xl"
          onClick={() => {
            setBulkModeEnabled((value) => !value)
            setSelectedContributionIds([])
          }}
          disabled={isAnyActionPending}
        >
          <ListChecks className="h-4 w-4 mr-2" />
          {bulkModeEnabled ? 'Exit Bulk Mode' : 'Bulk Action Mode'}
        </Button>
      </div>

      {bulkModeEnabled && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border/50 bg-muted/20 p-3">
          <span className="text-xs text-muted-foreground">
            {isAdmin
              ? 'Bulk reject supports pending and approved contributions.'
              : 'Bulk reject supports pending only (approved rejection requires admin).'}
          </span>
          <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Checkbox
              checked={allSelectableOnPageSelected ? true : hasSomeSelectableOnPageSelected ? 'indeterminate' : false}
              onCheckedChange={(checked) => toggleSelectAllSelectableOnPage(checked === true)}
              disabled={!selectableContributions.length || isAnyActionPending}
            />
            Select all eligible on this page
          </label>
          <span className="text-sm text-muted-foreground">
            Selected: <span className="font-medium text-foreground">{selectedTotalCount}</span>
            {isAdmin ? (
              <span className="ml-2 text-xs">
                (pending: {selectedPendingCount}, approved: {selectedApprovedCount})
              </span>
            ) : null}
          </span>
          <Button
            className="rounded-xl bg-emerald-600 hover:bg-emerald-700"
            onClick={() => setBulkApproveSelectedDialogOpen(true)}
            disabled={!selectedPendingCount || isAnyActionPending || bulkReviewContributions.isPending}
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
            disabled={!selectedTotalCount || isAnyActionPending || bulkReviewContributions.isPending}
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
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
      ) : !data?.items.length ? (
        <div className="text-center py-12">
          <Magnet className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
          <p className="mt-4 text-muted-foreground">No content imports found</p>
        </div>
      ) : (
        <div className="space-y-3">
          {data.items.map((contribution) => {
            const isTorrent = contribution.contribution_type === 'torrent'
            const isStream = contribution.contribution_type === 'stream'
            const isPending = contribution.status === 'pending'
            const torrentData = contribution.data as Record<string, unknown>
            const uploaderLabel = getContributionUploaderLabel(contribution)
            const reviewerLabel = getReviewerLabel(contribution)
            const reviewBadge = getReviewBadge(contribution)

            return (
              <Card key={contribution.id} className="glass border-border/50 hover:border-primary/30 transition-colors">
                <CardContent className="p-4">
                  <div className="flex items-start gap-4">
                    {bulkModeEnabled && (
                      <div className="pt-1">
                        <Checkbox
                          checked={selectedContributionIdSet.has(contribution.id)}
                          onCheckedChange={(checked) => toggleContributionSelection(contribution.id, checked === true)}
                          disabled={
                            (contribution.status !== 'pending' && !(isAdmin && contribution.status === 'approved')) ||
                            isAnyActionPending
                          }
                        />
                      </div>
                    )}
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

                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="outline" className="text-xs capitalize">
                          {contribution.contribution_type}
                        </Badge>
                        <Badge
                          variant="outline"
                          className={`text-xs capitalize ${
                            contribution.status === 'approved'
                              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-500'
                              : contribution.status === 'rejected'
                                ? 'bg-red-500/10 border-red-500/30 text-red-500'
                                : 'bg-amber-500/10 border-amber-500/30 text-amber-500'
                          }`}
                        >
                          {contribution.status}
                        </Badge>
                        {contribution.admin_review_requested && (
                          <Badge
                            variant="outline"
                            className="text-xs bg-violet-500/10 border-violet-500/30 text-violet-500"
                          >
                            Admin review requested
                          </Badge>
                        )}
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
                        <Badge
                          variant="outline"
                          className={`text-xs ${
                            torrentData.is_anonymous === true
                              ? 'bg-gray-500/10 border-gray-500/30 text-gray-500'
                              : 'bg-primary/10 border-primary/30 text-primary'
                          }`}
                          title={uploaderLabel}
                        >
                          {uploaderLabel}
                        </Badge>
                      </div>

                      <p className="font-medium truncate" title={String(torrentData.name || torrentData.title || '')}>
                        {String(torrentData.name || torrentData.title || 'Untitled')}
                      </p>

                      {contribution.target_id && (
                        <p className="text-sm text-muted-foreground">
                          <span className="font-medium">Target:</span>{' '}
                          <span className="font-mono">{contribution.target_id}</span>
                        </p>
                      )}

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
                        {isPending ? 'Review' : 'View'}
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
            onClick={() => onPageChange(page - 1)}
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
            onClick={() => onPageChange(page + 1)}
            className="rounded-xl"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      <AlertDialog open={bulkApproveDialogOpen} onOpenChange={setBulkApproveDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Approve all pending content imports?</AlertDialogTitle>
            <AlertDialogDescription>
              This approves all pending content imports for the current type filter.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isBulkApproving}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleApproveAllPending}
              disabled={isBulkApproving || bulkReviewContributions.isPending}
              className="bg-emerald-600 text-white hover:bg-emerald-700"
            >
              {isBulkApproving ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Approve All
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={bulkApproveSelectedDialogOpen} onOpenChange={setBulkApproveSelectedDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Approve selected pending contributions?</AlertDialogTitle>
            <AlertDialogDescription>
              This approves {selectedPendingCount} selected pending contribution{selectedPendingCount === 1 ? '' : 's'}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label className="text-sm font-medium">Review Notes (optional)</label>
            <Textarea
              value={bulkApproveSelectedNotes}
              onChange={(event) => setBulkApproveSelectedNotes(event.target.value)}
              placeholder="Add notes for the approved contributions..."
              rows={3}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isBulkApprovingSelected}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleApproveSelectedContributions}
              disabled={isBulkApprovingSelected || bulkReviewContributions.isPending || !selectedPendingCount}
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
            <AlertDialogTitle>Reject selected contributions?</AlertDialogTitle>
            <AlertDialogDescription>
              This rejects {selectedTotalCount} selected contribution{selectedTotalCount === 1 ? '' : 's'}
              {isAdmin && selectedApprovedCount > 0
                ? ` (${selectedPendingCount} pending, ${selectedApprovedCount} approved with admin rollback)`
                : ` (${selectedPendingCount} pending)`}
              .
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label className="text-sm font-medium">Review Notes (optional)</label>
            <Textarea
              value={bulkRejectNotes}
              onChange={(event) => setBulkRejectNotes(event.target.value)}
              placeholder="Add notes for the rejected contributions..."
              rows={3}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isBulkRejectingSelected}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleRejectSelectedContributions}
              disabled={isBulkRejectingSelected || bulkReviewContributions.isPending || !selectedTotalCount}
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
          className="sm:max-w-[700px] max-h-[90vh] flex flex-col overflow-hidden min-h-0"
          style={{ height: 'min(90dvh, calc(100dvh - 2rem))' }}
        >
          <DialogHeader className="shrink-0">
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

          <ScrollArea className="flex-1 min-h-0 pr-1">
            {selectedContribution && (
              <div className="space-y-4 py-4">
                <div className="rounded-lg border border-border/50 bg-muted/20 p-4">
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
                    <div className="h-28 w-20 shrink-0 overflow-hidden rounded-md border border-border/50 bg-muted">
                      <ModeratorMediaPoster
                        mediaType={selectedMediaPreview?.metaType}
                        mediaId={selectedMediaId}
                        imdbId={selectedImdbId}
                        posterUrl={selectedMediaPreview?.posterUrl}
                        title={selectedMediaPreview?.title}
                        fallbackIconSizeClassName="h-5 w-5"
                      />
                    </div>
                    <div className="min-w-0 flex-1 space-y-2">
                      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Media to Review
                      </p>
                      <p className="break-words text-base font-semibold">
                        {selectedMediaPreview?.title || 'Untitled'}
                        {selectedMediaPreview?.year ? (
                          <span className="ml-2 text-sm font-normal text-muted-foreground">
                            ({selectedMediaPreview.year})
                          </span>
                        ) : null}
                      </p>
                      <div className="flex items-center gap-2 flex-wrap">
                        <Button asChild variant="outline" size="sm" className="h-7 rounded-lg">
                          <Link to={selectedMediaOpenLink}>
                            <Library className="h-3.5 w-3.5 mr-1.5" />
                            {selectedMediaOpenLabel}
                          </Link>
                        </Button>
                        {selectedMediaPreview?.metaType && (
                          <Badge variant="outline" className="capitalize">
                            {selectedMediaPreview.metaType}
                          </Badge>
                        )}
                        {selectedImdbId ? (
                          <a
                            href={`https://www.imdb.com/title/${selectedImdbId}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs font-mono text-primary hover:underline inline-flex items-center gap-1"
                          >
                            {selectedImdbId}
                            <ExternalLink className="h-3 w-3" />
                          </a>
                        ) : selectedMediaPreview?.metaId ? (
                          <Badge variant="secondary" className="font-mono text-xs">
                            {selectedMediaPreview.metaId}
                          </Badge>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </div>

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

                <div className="space-y-3">
                  <h4 className="font-medium text-sm text-muted-foreground">Contribution Details</h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {formatTorrentData(selectedContributionData ?? {}).map((field, idx) => (
                      <div
                        key={idx}
                        className={`p-3 rounded-lg bg-muted/50 ${
                          field.type === 'text' && String(field.value).length > 30 ? 'sm:col-span-2' : ''
                        }`}
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

                {!!selectedContributionData?.magnet_link && (
                  <div className="space-y-2">
                    <h4 className="font-medium text-sm text-muted-foreground">Magnet Link</h4>
                    <div className="p-3 rounded-lg bg-muted/50">
                      <p className="text-xs font-mono break-all line-clamp-3">
                        {String(selectedContributionData.magnet_link)}
                      </p>
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
                  <span>By: {getContributionUploaderLabel(selectedContribution)}</span>
                  <span>•</span>
                  <span>Submitted: {formatTimeAgo(selectedContribution.created_at)}</span>
                  {selectedContribution.admin_review_requested && (
                    <>
                      <span>•</span>
                      <span className="inline-flex items-center gap-1.5">
                        <Badge
                          variant="outline"
                          className="h-5 px-1.5 text-[10px] bg-violet-500/10 border-violet-500/30 text-violet-500"
                        >
                          Escalated
                        </Badge>
                        <span>{selectedContribution.admin_review_reason || 'Waiting for admin action'}</span>
                      </span>
                    </>
                  )}
                  {selectedReviewerLabel && selectedReviewBadge && (
                    <>
                      <span>•</span>
                      <span className="inline-flex items-center gap-1.5" title={selectedReviewerLabel}>
                        <Badge variant="outline" className={`h-5 px-1.5 text-[10px] ${selectedReviewBadge.className}`}>
                          {selectedReviewBadge.label}
                        </Badge>
                        <span>Reviewed by: {selectedReviewerLabel}</span>
                      </span>
                      {selectedContribution.reviewed_at && (
                        <>
                          <span>•</span>
                          <span>Reviewed: {formatTimeAgo(selectedContribution.reviewed_at)}</span>
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
            <Button variant="outline" onClick={() => setReviewDialogOpen(false)} disabled={isAnyActionPending}>
              {selectedContribution?.status === 'pending' ? 'Cancel' : 'Close'}
            </Button>
            {selectedContribution?.status === 'pending' && (
              <>
                <Button variant="destructive" onClick={() => handleReview('rejected')} disabled={isAnyActionPending}>
                  {isAnyActionPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <XCircle className="h-4 w-4 mr-2" />
                  )}
                  Reject
                </Button>
                <Button
                  className="bg-emerald-600 hover:bg-emerald-700"
                  onClick={() => handleReview('approved')}
                  disabled={isAnyActionPending}
                >
                  {isAnyActionPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-4 w-4 mr-2" />
                  )}
                  Approve
                </Button>
              </>
            )}
            {selectedContribution?.status === 'approved' && (
              <>
                {!isAdmin && (
                  <Button
                    variant="outline"
                    onClick={handleFlagForAdmin}
                    disabled={isAnyActionPending || selectedContribution.admin_review_requested === true}
                  >
                    {isAnyActionPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : selectedContribution.admin_review_requested ? (
                      'Already Flagged'
                    ) : (
                      'Flag for Admin Reject'
                    )}
                  </Button>
                )}
                {isAdmin && (
                  <Button variant="destructive" onClick={handleAdminRejectApproved} disabled={isAnyActionPending}>
                    {isAnyActionPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <>
                        <XCircle className="h-4 w-4 mr-2" />
                        Reject (Admin)
                      </>
                    )}
                  </Button>
                )}
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
