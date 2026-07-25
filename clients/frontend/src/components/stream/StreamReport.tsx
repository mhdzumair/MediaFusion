import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Flag,
  Loader2,
  AlertTriangle,
  Wrench,
  Languages,
  MoreHorizontal,
  CheckCircle2,
  Ban,
  ThumbsUp,
  ThumbsDown,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCreateStreamSuggestion } from '@/hooks'
import { useStreamCommunityStats } from '@/contexts/StreamCommunityContext'
import type { StreamSuggestionType } from '@/lib/api'

interface StreamReportProps {
  streamId: number
  streamName?: string
  currentQuality?: string
  currentLanguage?: string
  isBlocked?: boolean
  className?: string
  variant?: 'button' | 'icon'
  trigger?: React.ReactNode
}

const suggestionTypes: { value: StreamSuggestionType; label: string; icon: React.ReactNode; description: string }[] = [
  {
    value: 'report_broken',
    label: 'Report Broken',
    icon: <AlertTriangle className="h-4 w-4" />,
    description: 'Stream is not working or cannot be played',
  },
  {
    value: 'field_correction',
    label: 'Quality/Info Correction',
    icon: <Wrench className="h-4 w-4" />,
    description: 'Incorrect resolution, codec, quality, or audio label',
  },
  {
    value: 'language_add',
    label: 'Add Language',
    icon: <Languages className="h-4 w-4" />,
    description: 'Add a missing language to this stream',
  },
  {
    value: 'language_remove',
    label: 'Remove Language',
    icon: <Languages className="h-4 w-4" />,
    description: 'Remove an incorrect language from this stream',
  },
  {
    value: 'other',
    label: 'Other Issue',
    icon: <MoreHorizontal className="h-4 w-4" />,
    description: 'Other problems or suggestions',
  },
]

export function StreamReport({
  streamId,
  streamName,
  currentQuality,
  currentLanguage,
  isBlocked = false,
  className,
  variant = 'button',
  trigger,
}: StreamReportProps) {
  const createSuggestion = useCreateStreamSuggestion()
  const { stats, isLoading: communityLoading } = useStreamCommunityStats(streamId)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedType, setSelectedType] = useState<StreamSuggestionType>('report_broken')
  const [currentValue, setCurrentValue] = useState('')
  const [suggestedValue, setSuggestedValue] = useState('')
  const [reason, setReason] = useState('')

  const showCommunityPanel = dialogOpen && (selectedType === 'report_broken' || selectedType === 'other')

  const selectedTypeInfo = suggestionTypes.find((t) => t.value === selectedType)

  const handleTypeChange = (value: StreamSuggestionType) => {
    setSelectedType(value)
    if (value === 'field_correction') {
      setCurrentValue(currentQuality || '')
    } else if (value === 'language_add' || value === 'language_remove') {
      setCurrentValue(currentLanguage || '')
    } else {
      setCurrentValue('')
    }
    setSuggestedValue('')
  }

  const handleSubmit = async () => {
    try {
      await createSuggestion.mutateAsync({
        streamId,
        data: {
          suggestion_type: selectedType,
          current_value: currentValue || undefined,
          suggested_value: suggestedValue || undefined,
          reason: reason.trim() || undefined,
        },
      })
      setDialogOpen(false)
      setCurrentValue('')
      setSuggestedValue('')
      setReason('')
    } catch {
      // Error handled by mutation
    }
  }

  const needsSuggestedValue = selectedType === 'field_correction' || selectedType === 'language_add'
  const userAlreadyReportedIssue = selectedType === 'report_broken' && stats?.user_has_issue_report === true

  const defaultTrigger =
    variant === 'icon' ? (
      <Button variant="ghost" size="icon" className={cn('h-8 w-8', className)}>
        <Flag className="h-4 w-4" />
      </Button>
    ) : (
      <Button variant="outline" size="sm" className={cn('gap-1.5', className)}>
        <Flag className="h-4 w-4" />
        Report Issue
      </Button>
    )

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      {trigger ? (
        <DialogTrigger asChild onClick={() => setDialogOpen(true)}>
          {trigger}
        </DialogTrigger>
      ) : (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <DialogTrigger asChild>{defaultTrigger}</DialogTrigger>
            </TooltipTrigger>
            <TooltipContent>
              <p>Report an issue with this stream</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}

      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Report Stream Issue</DialogTitle>
          <DialogDescription>
            {streamName ? (
              <>
                Report an issue with <span className="font-medium">{streamName}</span>
              </>
            ) : (
              'Report an issue with this stream'
            )}
            . Your report will be reviewed by moderators.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label>Issue Type</Label>
            <Select value={selectedType} onValueChange={(v) => handleTypeChange(v as StreamSuggestionType)}>
              <SelectTrigger>
                <SelectValue placeholder="Select issue type" />
              </SelectTrigger>
              <SelectContent>
                {suggestionTypes.map((type) => (
                  <SelectItem key={type.value} value={type.value}>
                    <span className="flex items-center gap-2">
                      {type.icon}
                      {type.label}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedTypeInfo && <p className="text-xs text-muted-foreground">{selectedTypeInfo.description}</p>}
          </div>

          {showCommunityPanel && (
            <Alert variant={isBlocked ? 'destructive' : undefined}>
              {communityLoading && !stats ? (
                <AlertDescription className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading community reports…
                </AlertDescription>
              ) : isBlocked ? (
                <>
                  <Ban className="h-4 w-4" />
                  <AlertDescription>This stream is blocked and may be hidden from some views.</AlertDescription>
                </>
              ) : stats ? (
                <>
                  <Flag className="h-4 w-4" />
                  <AlertDescription>
                    <div className="space-y-2 text-sm">
                      <p className="text-muted-foreground">
                        Reports are visible to the community. Moderators may triage or block streams manually.
                      </p>
                      <div className="flex flex-wrap items-center gap-3 text-foreground">
                        <span className="inline-flex items-center gap-1">
                          <Flag className="h-3.5 w-3.5" />
                          {stats.issue_report_count} issue report{stats.issue_report_count === 1 ? '' : 's'}
                        </span>
                        <span className="inline-flex items-center gap-1">
                          <ThumbsUp className="h-3.5 w-3.5 text-emerald-500" />
                          {stats.rating_up}
                          <ThumbsDown className="h-3.5 w-3.5 text-red-500" />
                          {stats.rating_down}
                          <span className="text-muted-foreground">(score {stats.rating_score})</span>
                        </span>
                      </div>
                      {userAlreadyReportedIssue && (
                        <p className="text-xs flex items-center gap-1 text-foreground">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          You have already submitted an issue report for this stream.
                        </p>
                      )}
                    </div>
                  </AlertDescription>
                </>
              ) : null}
            </Alert>
          )}

          {needsSuggestedValue && (
            <div className="space-y-2">
              <Label htmlFor="current">Current Value</Label>
              <Input
                id="current"
                value={currentValue}
                onChange={(e) => setCurrentValue(e.target.value)}
                placeholder={selectedType === 'field_correction' ? 'e.g., 720p, H.264' : 'e.g., English'}
              />
            </div>
          )}

          {needsSuggestedValue && (
            <div className="space-y-2">
              <Label htmlFor="suggested">{selectedType === 'language_add' ? 'Language to Add' : 'Correct Value'}</Label>
              <Input
                id="suggested"
                value={suggestedValue}
                onChange={(e) => setSuggestedValue(e.target.value)}
                placeholder={selectedType === 'field_correction' ? 'e.g., 1080p, HEVC' : 'e.g., Spanish'}
              />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="reason">
              {selectedType === 'report_broken' ? 'Error Details (optional)' : 'Additional Details (optional)'}
            </Label>
            <Textarea
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={
                selectedType === 'report_broken'
                  ? 'Describe what happens when you try to play (e.g., "No peers", "Stuck at buffering", "Error message")'
                  : 'Any additional information'
              }
              rows={3}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setDialogOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={
              createSuggestion.isPending ||
              (needsSuggestedValue && !suggestedValue.trim()) ||
              userAlreadyReportedIssue ||
              (selectedType === 'report_broken' && isBlocked)
            }
          >
            {createSuggestion.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Submitting...
              </>
            ) : (
              'Submit Report'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
