import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Progress } from '@/components/ui/progress'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { 
  Flag,
  Loader2,
  AlertTriangle,
  Wrench,
  Languages,
  MoreHorizontal,
  CheckCircle2,
  Users,
  Ban,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCreateStreamSuggestion } from '@/hooks'
import { streamSuggestionsApi } from '@/lib/api/stream-suggestions'
import type { StreamSuggestionType } from '@/lib/api'

interface StreamReportProps {
  streamId: number
  streamName?: string
  currentQuality?: string
  currentLanguage?: string
  className?: string
  variant?: 'button' | 'icon'
  trigger?: React.ReactNode  // Custom trigger element
}

const suggestionTypes: { value: StreamSuggestionType; label: string; icon: React.ReactNode; description: string }[] = [
  { 
    value: 'report_broken', 
    label: 'Report Broken', 
    icon: <AlertTriangle className="h-4 w-4" />,
    description: 'Stream is not working or cannot be played'
  },
  { 
    value: 'field_correction', 
    label: 'Quality/Info Correction', 
    icon: <Wrench className="h-4 w-4" />,
    description: 'Incorrect resolution, codec, quality, or audio label'
  },
  { 
    value: 'language_add', 
    label: 'Add Language', 
    icon: <Languages className="h-4 w-4" />,
    description: 'Add a missing language to this stream'
  },
  { 
    value: 'language_remove', 
    label: 'Remove Language', 
    icon: <Languages className="h-4 w-4" />,
    description: 'Remove an incorrect language from this stream'
  },
  { 
    value: 'other', 
    label: 'Other Issue', 
    icon: <MoreHorizontal className="h-4 w-4" />,
    description: 'Other problems or suggestions'
  },
]

export function StreamReport({
  streamId,
  streamName,
  currentQuality,
  currentLanguage,
  className,
  variant = 'button',
  trigger,
}: StreamReportProps) {
  const createSuggestion = useCreateStreamSuggestion()
  
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedType, setSelectedType] = useState<StreamSuggestionType>('report_broken')
  const [currentValue, setCurrentValue] = useState('')
  const [suggestedValue, setSuggestedValue] = useState('')
  const [reason, setReason] = useState('')

  // Fetch broken status when dialog opens and report_broken is selected
  const { data: brokenStatus, refetch: refetchBrokenStatus } = useQuery({
    queryKey: ['broken-status', streamId],
    queryFn: () => streamSuggestionsApi.getBrokenStatus(streamId),
    enabled: dialogOpen && selectedType === 'report_broken',
    staleTime: 30000, // 30 seconds
  })

  // Refetch broken status when dialog opens
  useEffect(() => {
    if (dialogOpen && selectedType === 'report_broken') {
      refetchBrokenStatus()
    }
  }, [dialogOpen, selectedType, refetchBrokenStatus])

  const selectedTypeInfo = suggestionTypes.find(t => t.value === selectedType)

  const handleTypeChange = (value: StreamSuggestionType) => {
    setSelectedType(value)
    // Pre-fill current value based on type
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
    } catch (error) {
      // Error handled by mutation
    }
  }

  const needsSuggestedValue = selectedType === 'field_correction' || selectedType === 'language_add'

  // Default trigger based on variant
  const defaultTrigger = variant === 'icon' ? (
                <Button 
                  variant="ghost" 
                  size="icon"
                  className={cn('h-8 w-8', className)}
                >
                  <Flag className="h-4 w-4" />
                </Button>
              ) : (
                <Button 
                  variant="outline" 
                  size="sm"
                  className={cn('gap-1.5', className)}
                >
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
              <DialogTrigger asChild>
                {defaultTrigger}
            </DialogTrigger>
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
              <>Report an issue with <span className="font-medium">{streamName}</span></>
            ) : (
              'Report an issue with this stream'
            )}. Your report will be reviewed by moderators.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Issue type selection */}
          <div className="space-y-2">
            <Label>Issue Type</Label>
            <Select
              value={selectedType}
              onValueChange={(v) => handleTypeChange(v as StreamSuggestionType)}
            >
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
            {selectedTypeInfo && (
              <p className="text-xs text-muted-foreground">{selectedTypeInfo.description}</p>
            )}
          </div>

          {/* Broken report status indicator */}
          {selectedType === 'report_broken' && brokenStatus && (
            <Alert variant={brokenStatus.is_blocked ? 'destructive' : brokenStatus.user_has_reported ? 'default' : undefined}>
              {brokenStatus.is_blocked ? (
                <>
                  <Ban className="h-4 w-4" />
                  <AlertDescription>
                    This stream is already blocked due to broken reports.
                  </AlertDescription>
                </>
              ) : brokenStatus.user_has_reported ? (
                <>
                  <CheckCircle2 className="h-4 w-4" />
                  <AlertDescription>
                    <div className="space-y-2">
                      <p>You have already reported this stream as broken.</p>
                      <div className="flex items-center gap-2 text-sm">
                        <Users className="h-3.5 w-3.5" />
                        <span>{brokenStatus.report_count} of {brokenStatus.threshold} reports</span>
                      </div>
                      <Progress 
                        value={(brokenStatus.report_count / brokenStatus.threshold) * 100} 
                        className="h-2"
                      />
                      {brokenStatus.reports_needed > 0 && (
                        <p className="text-xs text-muted-foreground">
                          {brokenStatus.reports_needed} more report{brokenStatus.reports_needed !== 1 ? 's' : ''} needed to block this stream
                        </p>
                      )}
                    </div>
                  </AlertDescription>
                </>
              ) : (
                <>
                  <Users className="h-4 w-4" />
                  <AlertDescription>
                    <div className="space-y-2">
                      <p>Broken reports require consensus from multiple users.</p>
                      <div className="flex items-center gap-2 text-sm">
                        <span>{brokenStatus.report_count} of {brokenStatus.threshold} reports received</span>
                      </div>
                      <Progress 
                        value={(brokenStatus.report_count / brokenStatus.threshold) * 100} 
                        className="h-2"
                      />
                      {brokenStatus.reports_needed > 0 && (
                        <p className="text-xs text-muted-foreground">
                          {brokenStatus.reports_needed} more report{brokenStatus.reports_needed !== 1 ? 's' : ''} needed to block this stream
                        </p>
                      )}
                    </div>
                  </AlertDescription>
                </>
              )}
            </Alert>
          )}

          {/* Current value (for corrections) */}
          {needsSuggestedValue && (
            <div className="space-y-2">
              <Label htmlFor="current">Current Value</Label>
              <Input
                id="current"
                value={currentValue}
                onChange={(e) => setCurrentValue(e.target.value)}
                placeholder={
                  selectedType === 'field_correction' 
                    ? 'e.g., 720p, H.264' 
                    : 'e.g., English'
                }
              />
            </div>
          )}

          {/* Suggested value (for corrections) */}
          {needsSuggestedValue && (
            <div className="space-y-2">
              <Label htmlFor="suggested">{selectedType === 'language_add' ? 'Language to Add' : 'Correct Value'}</Label>
              <Input
                id="suggested"
                value={suggestedValue}
                onChange={(e) => setSuggestedValue(e.target.value)}
                placeholder={
                  selectedType === 'field_correction' 
                    ? 'e.g., 1080p, HEVC' 
                    : 'e.g., Spanish'
                }
              />
            </div>
          )}

          {/* Reason / description */}
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
          <Button
            variant="outline"
            onClick={() => setDialogOpen(false)}
          >
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={
              createSuggestion.isPending || 
              (needsSuggestedValue && !suggestedValue.trim()) ||
              (selectedType === 'report_broken' && brokenStatus?.user_has_reported) ||
              (selectedType === 'report_broken' && brokenStatus?.is_blocked)
            }
          >
            {createSuggestion.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Submitting...
              </>
            ) : selectedType === 'report_broken' && brokenStatus?.user_has_reported ? (
              'Already Reported'
            ) : selectedType === 'report_broken' && brokenStatus?.is_blocked ? (
              'Already Blocked'
            ) : (
              'Submit Report'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

