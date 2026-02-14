import { useState, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { Edit, Loader2, CheckCircle2, AlertCircle, Tv, Calendar, Clock, FileText } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCreateEpisodeSuggestion } from '@/hooks'
import type { EpisodeEditableField } from '@/lib/api'

type FieldName = 'title' | 'overview' | 'air_date' | 'runtime_minutes'

interface FieldState {
  value: string
  original: string
  isModified: boolean
}

export interface EpisodeData {
  id: number
  episode_number: number
  title?: string
  overview?: string
  air_date?: string // ISO date string (YYYY-MM-DD)
  runtime_minutes?: number
  season_number?: number
  series_title?: string
}

interface EpisodeEditSheetProps {
  episode: EpisodeData
  trigger?: React.ReactNode
  onSuccess?: () => void
}

export function EpisodeEditSheet({ episode, trigger, onSuccess }: EpisodeEditSheetProps) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitResults, setSubmitResults] = useState<{ field: string; success: boolean }[]>([])

  const createEpisodeSuggestion = useCreateEpisodeSuggestion()

  // Field states - initialized from episode data
  const getInitialFields = (): Record<FieldName, FieldState> => {
    return {
      title: {
        value: episode.title || '',
        original: episode.title || '',
        isModified: false,
      },
      overview: {
        value: episode.overview || '',
        original: episode.overview || '',
        isModified: false,
      },
      air_date: {
        value: episode.air_date || '',
        original: episode.air_date || '',
        isModified: false,
      },
      runtime_minutes: {
        value: episode.runtime_minutes?.toString() || '',
        original: episode.runtime_minutes?.toString() || '',
        isModified: false,
      },
    }
  }

  const [fields, setFields] = useState<Record<FieldName, FieldState>>(getInitialFields())

  // Reset when episode changes or sheet opens (during render, not in effect)
  const [prevOpen, setPrevOpen] = useState(open)
  const [prevEpisode, setPrevEpisode] = useState(episode)
  if (open && (open !== prevOpen || prevEpisode !== episode)) {
    setPrevOpen(open)
    setPrevEpisode(episode)
    setFields(getInitialFields())
    setReason('')
    setSubmitResults([])
  }

  const updateField = (fieldName: FieldName, value: string) => {
    setFields((prev) => ({
      ...prev,
      [fieldName]: {
        ...prev[fieldName],
        value,
        isModified: value !== prev[fieldName].original,
      },
    }))
  }

  // Calculate all modifications
  const modifiedFields = useMemo(() => {
    const result: { field: FieldName; currentValue: string; newValue: string }[] = []

    Object.entries(fields).forEach(([key, state]) => {
      if (state.isModified) {
        result.push({
          field: key as FieldName,
          currentValue: state.original,
          newValue: state.value,
        })
      }
    })

    return result
  }, [fields])

  const modifiedCount = modifiedFields.length

  const handleSubmit = async () => {
    if (modifiedCount === 0) return

    setIsSubmitting(true)
    setSubmitResults([])
    const results: { field: string; success: boolean }[] = []

    for (const { field, currentValue, newValue } of modifiedFields) {
      try {
        await createEpisodeSuggestion.mutateAsync({
          episodeId: episode.id,
          data: {
            field_name: field as EpisodeEditableField,
            current_value: currentValue || undefined,
            suggested_value: newValue,
            reason: reason.trim() || undefined,
          },
        })
        results.push({ field, success: true })
      } catch (error) {
        console.error(`Failed to submit ${field}:`, error)
        results.push({ field, success: false })
      }
    }

    setSubmitResults(results)
    setIsSubmitting(false)

    const successCount = results.filter((r) => r.success).length
    if (successCount > 0 && successCount === results.length) {
      setTimeout(() => {
        setOpen(false)
        onSuccess?.()
      }, 1500)
    }
  }

  const formatFieldName = (field: string): string => {
    const names: Record<string, string> = {
      title: 'Title',
      overview: 'Overview',
      air_date: 'Air Date',
      runtime_minutes: 'Runtime',
    }
    return names[field] || field
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        {trigger || (
          <Button variant="ghost" size="sm" className="gap-1.5 h-8 px-2">
            <Edit className="h-3.5 w-3.5" />
            <span className="sr-only sm:not-sr-only">Edit</span>
          </Button>
        )}
      </SheetTrigger>

      <SheetContent className="w-full sm:max-w-[480px] p-0 flex flex-col">
        <SheetHeader className="px-6 py-4 border-b">
          <SheetTitle className="flex items-center gap-2">
            <Edit className="h-5 w-5 text-primary" />
            Edit Episode
          </SheetTitle>
          <SheetDescription>Suggest corrections to this episode's information</SheetDescription>
        </SheetHeader>

        <ScrollArea className="flex-1 px-6">
          <div className="py-6 space-y-6">
            {/* Episode Preview */}
            <div className="p-4 rounded-xl bg-muted/50">
              <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
                <Tv className="h-4 w-4" />
                {episode.series_title && <span className="font-medium">{episode.series_title}</span>}
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="secondary" className="rounded-lg">
                  S{(episode.season_number || 1).toString().padStart(2, '0')}E
                  {episode.episode_number.toString().padStart(2, '0')}
                </Badge>
                <span className="font-semibold truncate">{episode.title || `Episode ${episode.episode_number}`}</span>
              </div>
            </div>

            {/* Basic Info Section */}
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <FileText className="h-4 w-4" />
                Episode Information
              </div>

              <div className="space-y-3">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">Title</Label>
                    {fields.title.isModified && (
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                        Modified
                      </Badge>
                    )}
                  </div>
                  <Input
                    value={fields.title.value}
                    onChange={(e) => updateField('title', e.target.value)}
                    placeholder="Episode title"
                    className={cn('rounded-xl', fields.title.isModified && 'border-primary/50 bg-primary/5')}
                  />
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">Overview</Label>
                    {fields.overview.isModified && (
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                        Modified
                      </Badge>
                    )}
                  </div>
                  <Textarea
                    value={fields.overview.value}
                    onChange={(e) => updateField('overview', e.target.value)}
                    placeholder="Episode summary/description"
                    rows={4}
                    className={cn(
                      'rounded-xl resize-none',
                      fields.overview.isModified && 'border-primary/50 bg-primary/5',
                    )}
                  />
                </div>
              </div>
            </div>

            <Separator />

            {/* Date & Runtime Section */}
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <Calendar className="h-4 w-4" />
                Schedule & Duration
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">Air Date</Label>
                    {fields.air_date.isModified && (
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                        Modified
                      </Badge>
                    )}
                  </div>
                  <Input
                    type="date"
                    value={fields.air_date.value}
                    onChange={(e) => updateField('air_date', e.target.value)}
                    className={cn('rounded-xl', fields.air_date.isModified && 'border-primary/50 bg-primary/5')}
                  />
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      Runtime (min)
                    </Label>
                    {fields.runtime_minutes.isModified && (
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-primary/20 text-primary">
                        Modified
                      </Badge>
                    )}
                  </div>
                  <Input
                    type="number"
                    min="1"
                    value={fields.runtime_minutes.value}
                    onChange={(e) => updateField('runtime_minutes', e.target.value)}
                    placeholder="e.g., 45"
                    className={cn('rounded-xl', fields.runtime_minutes.isModified && 'border-primary/50 bg-primary/5')}
                  />
                </div>
              </div>
            </div>

            <Separator />

            {/* Reason Section */}
            <div className="space-y-1.5">
              <Label className="text-xs">Reason for changes (optional)</Label>
              <Textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Explain why these changes are needed..."
                rows={2}
                className="rounded-xl resize-none"
              />
              <p className="text-xs text-muted-foreground">
                Providing a reason helps moderators review your suggestions faster
              </p>
            </div>

            {/* Submit Results */}
            {submitResults.length > 0 && (
              <div className="p-4 rounded-xl bg-muted/50 space-y-2">
                <p className="text-sm font-medium">Results</p>
                {submitResults.map(({ field, success }) => (
                  <div key={field} className="flex items-center gap-2 text-sm">
                    {success ? (
                      <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-red-500" />
                    )}
                    <span>{formatFieldName(field)}</span>
                    {success && <span className="text-xs text-muted-foreground">(submitted for review)</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </ScrollArea>

        <SheetFooter className="px-6 py-4 border-t">
          <div className="flex items-center justify-between w-full">
            <div className="text-sm text-muted-foreground">
              {modifiedCount > 0 ? (
                <span className="text-primary font-medium">
                  {modifiedCount} change{modifiedCount !== 1 ? 's' : ''}
                </span>
              ) : (
                'No changes'
              )}
            </div>
            <Button
              onClick={handleSubmit}
              disabled={modifiedCount === 0 || isSubmitting}
              className="bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70 rounded-xl"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Submitting...
                </>
              ) : (
                `Submit ${modifiedCount} Edit${modifiedCount !== 1 ? 's' : ''}`
              )}
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
