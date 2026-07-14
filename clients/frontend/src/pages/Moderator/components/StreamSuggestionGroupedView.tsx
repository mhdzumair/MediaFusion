import { useMemo, useState } from 'react'
import { CheckCircle2, ChevronDown, ChevronRight, Eye, Film, Hash, Loader2, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { StreamSuggestion } from '@/lib/api'

import { IssueTriageControls } from './IssueTriageControls'
import {
  formatStreamFieldName,
  formatStreamSuggestionType,
  formatTimeAgo,
  groupStreamSuggestionsByInfoHash,
  groupSuggestionsByFile,
  isIssueStreamSuggestion,
  parseEpisodeLinkField,
  truncateInfoHash,
  type StreamSuggestionInfoHashGroup,
} from './helpers'

interface StreamSuggestionGroupedViewProps {
  suggestions: StreamSuggestion[]
  bulkModeEnabled: boolean
  selectedSuggestionIds: string[]
  isAnyActionPending: boolean
  isBulkApprovingSelected: boolean
  isBulkRejectingSelected: boolean
  onToggleSuggestionSelection: (suggestionId: string, checked: boolean) => void
  onReviewSuggestion: (suggestion: StreamSuggestion) => void
  onBulkReviewGroup: (suggestionIds: string[], action: 'approve' | 'reject') => void
  onRefetch: () => void
  getReviewerLabel: (suggestion: StreamSuggestion) => string | null
  getReviewBadge: (suggestion: StreamSuggestion) => { label: string; className: string } | null
  isRelinkSuggestion: (suggestion: StreamSuggestion) => boolean
  buildRelinkCurrentValue: (suggestion: StreamSuggestion) => string
  buildRelinkSuggestedValue: (suggestion: StreamSuggestion) => string
}

function pendingIdsInGroup(group: StreamSuggestionInfoHashGroup): string[] {
  return group.suggestions.filter((suggestion) => suggestion.status === 'pending').map((suggestion) => suggestion.id)
}

function renderSuggestionChanges(suggestion: StreamSuggestion) {
  const episodeInfo = parseEpisodeLinkField(suggestion.field_name)
  if (episodeInfo) {
    if (episodeInfo.field === 'clear') {
      return (
        <div className="flex items-center gap-2 text-sm">
          <Badge variant="secondary" className="text-[10px]">
            Remove file link
          </Badge>
          <span className="text-emerald-400 font-medium">Clear season / episode / end</span>
        </div>
      )
    }
    return (
      <div className="flex items-center gap-2 text-sm">
        <Badge variant="secondary" className="text-[10px]">
          {episodeInfo.displayField}
        </Badge>
        <span className="text-red-400">{suggestion.current_value || '(not set)'}</span>
        <span className="text-muted-foreground">→</span>
        <span className="text-emerald-400 font-medium">{suggestion.suggested_value || '(clear)'}</span>
      </div>
    )
  }

  if (suggestion.field_name && (suggestion.current_value || suggestion.suggested_value)) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <span className="text-red-400 truncate max-w-[150px]" title={suggestion.current_value || ''}>
          {suggestion.current_value || '(empty)'}
        </span>
        <span className="text-muted-foreground flex-shrink-0">→</span>
        <span className="text-emerald-400 truncate max-w-[150px]" title={suggestion.suggested_value || ''}>
          {suggestion.suggested_value || '(empty)'}
        </span>
      </div>
    )
  }

  return null
}

export function StreamSuggestionGroupedView({
  suggestions,
  bulkModeEnabled,
  selectedSuggestionIds,
  isAnyActionPending,
  isBulkApprovingSelected,
  isBulkRejectingSelected,
  onToggleSuggestionSelection,
  onReviewSuggestion,
  onBulkReviewGroup,
  onRefetch,
  getReviewerLabel,
  getReviewBadge,
  isRelinkSuggestion,
  buildRelinkCurrentValue,
  buildRelinkSuggestedValue,
}: StreamSuggestionGroupedViewProps) {
  const groups = useMemo(() => groupStreamSuggestionsByInfoHash(suggestions), [suggestions])
  const selectedSuggestionIdSet = useMemo(() => new Set(selectedSuggestionIds), [selectedSuggestionIds])
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})

  const isGroupOpen = (key: string, pendingCount: number) => openGroups[key] ?? pendingCount > 0

  const toggleGroup = (key: string, pendingCount: number) => {
    setOpenGroups((current) => ({
      ...current,
      [key]: !(current[key] ?? pendingCount > 0),
    }))
  }

  return (
    <div className="space-y-3">
      {groups.map((group) => {
        const fileGroups = groupSuggestionsByFile(group.suggestions)
        const groupPendingIds = pendingIdsInGroup(group)
        const allGroupPendingSelected =
          groupPendingIds.length > 0 && groupPendingIds.every((id) => selectedSuggestionIdSet.has(id))
        const someGroupPendingSelected =
          groupPendingIds.some((id) => selectedSuggestionIdSet.has(id)) && !allGroupPendingSelected
        const open = isGroupOpen(group.key, group.pendingCount)

        return (
          <Card key={group.key} className="glass border-border/50 overflow-hidden">
            <Collapsible open={open} onOpenChange={() => toggleGroup(group.key, group.pendingCount)}>
              <div className="flex items-start gap-3 p-4 border-b border-border/40 bg-muted/20">
                {bulkModeEnabled && (
                  <div className="pt-1">
                    <Checkbox
                      checked={allGroupPendingSelected ? true : someGroupPendingSelected ? 'indeterminate' : false}
                      onCheckedChange={(checked) => {
                        for (const suggestionId of groupPendingIds) {
                          onToggleSuggestionSelection(suggestionId, checked === true)
                        }
                      }}
                      disabled={!groupPendingIds.length || isAnyActionPending}
                    />
                  </div>
                )}
                <div className="p-2 rounded-xl bg-primary/10 flex-shrink-0">
                  <Film className="h-5 w-5 text-primary" />
                </div>
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="font-medium truncate" title={group.streamName || undefined}>
                      {group.streamName || `Stream #${group.streamId}`}
                    </p>
                    {group.pendingCount > 0 && (
                      <Badge className="bg-amber-500/10 border-amber-500/30 text-amber-500">
                        {group.pendingCount} pending
                      </Badge>
                    )}
                    <Badge variant="outline" className="text-xs">
                      {group.suggestions.length} suggestion{group.suggestions.length === 1 ? '' : 's'}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground flex-wrap">
                    {group.infoHash ? (
                      <span className="inline-flex items-center gap-1 font-mono" title={group.infoHash}>
                        <Hash className="h-3 w-3" />
                        {truncateInfoHash(group.infoHash)}
                      </span>
                    ) : (
                      <span>Stream ID: {group.streamId}</span>
                    )}
                    {group.sourceMediaTitle && (
                      <>
                        <span>•</span>
                        <span className="truncate max-w-[260px]" title={group.sourceMediaTitle}>
                          {group.sourceMediaTitle}
                        </span>
                      </>
                    )}
                    {group.streamType && (
                      <>
                        <span>•</span>
                        <span className="uppercase">{group.streamType}</span>
                      </>
                    )}
                    <span>•</span>
                    <span>Stream #{group.streamId}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {groupPendingIds.length > 0 && (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        className="rounded-lg hidden sm:inline-flex"
                        disabled={isAnyActionPending}
                        onClick={() => onBulkReviewGroup(groupPendingIds, 'approve')}
                      >
                        {isBulkApprovingSelected ? (
                          <Loader2 className="h-4 w-4 animate-spin mr-1" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4 mr-1" />
                        )}
                        Approve all
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="rounded-lg hidden sm:inline-flex"
                        disabled={isAnyActionPending}
                        onClick={() => onBulkReviewGroup(groupPendingIds, 'reject')}
                      >
                        {isBulkRejectingSelected ? (
                          <Loader2 className="h-4 w-4 animate-spin mr-1" />
                        ) : (
                          <Trash2 className="h-4 w-4 mr-1" />
                        )}
                        Reject all
                      </Button>
                    </>
                  )}
                  <CollapsibleTrigger asChild>
                    <Button size="sm" variant="ghost" className="rounded-lg">
                      {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </Button>
                  </CollapsibleTrigger>
                </div>
              </div>

              <CollapsibleContent>
                <CardContent className="p-4 space-y-4">
                  {fileGroups.map((fileGroup) => (
                    <div key={fileGroup.key} className="space-y-2">
                      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                        {fileGroup.label}
                      </p>
                      <div className="space-y-2">
                        {fileGroup.suggestions.map((suggestion) => {
                          const episodeInfo = parseEpisodeLinkField(suggestion.field_name)
                          const isRelink = isRelinkSuggestion(suggestion)
                          const reviewerLabel = getReviewerLabel(suggestion)
                          const reviewBadge = getReviewBadge(suggestion)

                          return (
                            <div
                              key={suggestion.id}
                              className="rounded-lg border border-border/50 bg-background/40 p-3"
                            >
                              <div className="flex items-start gap-3">
                                {bulkModeEnabled && (
                                  <div className="pt-0.5">
                                    <Checkbox
                                      checked={selectedSuggestionIdSet.has(suggestion.id)}
                                      onCheckedChange={(checked) =>
                                        onToggleSuggestionSelection(suggestion.id, checked === true)
                                      }
                                      disabled={suggestion.status !== 'pending' || isAnyActionPending}
                                    />
                                  </div>
                                )}
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
                                    {suggestion.field_name && !episodeInfo && (
                                      <Badge variant="secondary" className="text-xs">
                                        {formatStreamFieldName(suggestion.field_name)}
                                      </Badge>
                                    )}
                                  </div>

                                  {isRelink && (
                                    <div className="rounded-lg border border-border/50 bg-muted/30 p-2 text-xs text-muted-foreground space-y-1">
                                      <p className="truncate" title={buildRelinkCurrentValue(suggestion)}>
                                        <span className="font-medium text-foreground">Source:</span>{' '}
                                        {buildRelinkCurrentValue(suggestion)}
                                      </p>
                                      <p className="truncate" title={buildRelinkSuggestedValue(suggestion)}>
                                        <span className="font-medium text-foreground">Target:</span>{' '}
                                        {buildRelinkSuggestedValue(suggestion)}
                                      </p>
                                    </div>
                                  )}

                                  {renderSuggestionChanges(suggestion)}

                                  {suggestion.reason && (
                                    <p className="text-xs text-muted-foreground truncate" title={suggestion.reason}>
                                      <span className="font-medium">Reason:</span> {suggestion.reason}
                                    </p>
                                  )}

                                  {isIssueStreamSuggestion(suggestion) && (
                                    <IssueTriageControls suggestion={suggestion} onUpdated={onRefetch} />
                                  )}

                                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                    <span>by {suggestion.username || 'User'}</span>
                                    <span>•</span>
                                    <span>{formatTimeAgo(suggestion.created_at)}</span>
                                    {reviewerLabel && reviewBadge && (
                                      <>
                                        <span>•</span>
                                        <Badge
                                          variant="outline"
                                          className={`h-5 px-1.5 text-[10px] ${reviewBadge.className}`}
                                        >
                                          {reviewBadge.label}
                                        </Badge>
                                        <span>{reviewerLabel}</span>
                                      </>
                                    )}
                                  </div>
                                </div>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="rounded-lg flex-shrink-0"
                                  onClick={() => onReviewSuggestion(suggestion)}
                                  disabled={isIssueStreamSuggestion(suggestion)}
                                  title={
                                    isIssueStreamSuggestion(suggestion)
                                      ? 'Use issue triage to acknowledge reports'
                                      : undefined
                                  }
                                >
                                  <Eye className="h-4 w-4 mr-1" />
                                  {suggestion.status === 'pending'
                                    ? isIssueStreamSuggestion(suggestion)
                                      ? 'Triage below'
                                      : 'Review'
                                    : 'View'}
                                </Button>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </CardContent>
              </CollapsibleContent>
            </Collapsible>
          </Card>
        )
      })}
    </div>
  )
}
