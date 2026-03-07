import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, ExternalLink, Eye, Loader2, XCircle } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ScrollArea } from '@/components/ui/scroll-area'
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Suggestion } from '@/lib/api'
import { useState } from 'react'

import { formatTimeAgo, getSuggestionContentPath, getSuggestionMediaSummary, type ReviewDecision } from './helpers'
import { ModeratorMediaPoster } from './ModeratorMediaPoster'

interface ReviewDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  suggestion: Suggestion | null
  onReview: (decision: ReviewDecision, notes?: string) => Promise<void>
  isReviewing: boolean
}

export function ReviewDialog({ open, onOpenChange, suggestion, onReview, isReviewing }: ReviewDialogProps) {
  const [notes, setNotes] = useState('')
  const [confirmReject, setConfirmReject] = useState(false)
  const [reviewError, setReviewError] = useState<string | null>(null)

  const handleApprove = async () => {
    try {
      setReviewError(null)
      await onReview('approve', notes || undefined)
      setNotes('')
      onOpenChange(false)
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : 'Unable to approve suggestion')
    }
  }

  const handleReject = async () => {
    try {
      setReviewError(null)
      await onReview('reject', notes || undefined)
      setNotes('')
      setConfirmReject(false)
      onOpenChange(false)
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : 'Unable to reject suggestion')
      setConfirmReject(false)
    }
  }

  if (!suggestion) return null
  const contentPath = getSuggestionContentPath(suggestion)

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          scrollMode="contained"
          className="sm:max-w-[720px] max-h-[90vh] flex flex-col overflow-hidden min-h-0"
        >
          <DialogHeader className="shrink-0">
            <DialogTitle className="flex items-center gap-2">
              <Eye className="h-5 w-5 text-primary" />
              Review Suggestion
            </DialogTitle>
            <DialogDescription>Review and approve or reject this metadata correction suggestion.</DialogDescription>
          </DialogHeader>

          <ScrollArea className="flex-1 min-h-0 pr-1">
            <div className="space-y-6 py-4">
              <div className="rounded-lg border border-border/50 bg-muted/20 p-4">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
                  <div className="h-24 w-16 shrink-0 overflow-hidden rounded-md border border-border/50 bg-muted">
                    <ModeratorMediaPoster
                      mediaType={suggestion.media_type}
                      mediaId={suggestion.media_id}
                      posterUrl={suggestion.media_poster_url}
                      title={suggestion.media_title}
                      fallbackIconSizeClassName="h-5 w-5"
                    />
                  </div>
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Media</p>
                        <p className="truncate text-base font-semibold">{suggestion.media_title || 'Unknown title'}</p>
                      </div>
                      {contentPath ? (
                        <Button asChild variant="outline" size="sm" className="h-8 rounded-lg">
                          <Link to={contentPath}>
                            <ExternalLink className="mr-2 h-3.5 w-3.5" />
                            Open
                          </Link>
                        </Button>
                      ) : null}
                    </div>
                    <p className="text-xs text-muted-foreground">{getSuggestionMediaSummary(suggestion)}</p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">Field</label>
                  <p className="font-medium capitalize">{suggestion.field_name}</p>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">Current Value</label>
                  <ScrollArea className="max-h-52 rounded-lg border border-red-500/20 bg-red-500/5">
                    <p className="whitespace-pre-wrap break-all p-3 text-sm">{suggestion.current_value || '(empty)'}</p>
                  </ScrollArea>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">Suggested Value</label>
                  <ScrollArea className="max-h-52 rounded-lg border border-emerald-500/20 bg-emerald-500/5">
                    <p className="whitespace-pre-wrap break-all p-3 text-sm">{suggestion.suggested_value}</p>
                  </ScrollArea>
                </div>
              </div>

              {suggestion.reason && (
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">User's Reason</label>
                  <div className="p-3 rounded-lg bg-muted/50">
                    <p className="text-sm">{suggestion.reason}</p>
                  </div>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
                <span>Submitted by: {suggestion.username || suggestion.user_id}</span>
                <span>•</span>
                <span>{formatTimeAgo(suggestion.created_at)}</span>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">Review Notes (optional)</label>
                <Textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Add notes about your decision..."
                  rows={3}
                />
              </div>

              {reviewError ? <p className="text-sm text-destructive">{reviewError}</p> : null}
            </div>
          </ScrollArea>

          <DialogFooter className="gap-2 shrink-0">
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
