import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Edit,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Monitor,
  Volume2,
  Film,
  ChevronRight,
  ChevronLeft,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCreateStreamSuggestion } from '@/hooks'
import type { StreamFieldName as ApiStreamFieldName } from '@/lib/api'

// Predefined options
const RESOLUTION_OPTIONS = ['2160p', '1080p', '720p', '480p', '360p']
const QUALITY_OPTIONS = ['BluRay', 'WEB-DL', 'WEBRip', 'HDRip', 'HDTV', 'DVDRip', 'CAM', 'TS']
const CODEC_OPTIONS = ['x265', 'x264', 'HEVC', 'H.265', 'H.264', 'AVC', 'VP9', 'AV1']
const AUDIO_OPTIONS = ['DTS', 'DTS-HD', 'Atmos', 'TrueHD', 'AAC', 'AC3', 'DD5.1', 'DD+', 'FLAC']
const HDR_OPTIONS = ['HDR', 'HDR10', 'HDR10+', 'Dolby Vision', 'DV', 'HLG']
const SOURCE_OPTIONS = ['BluRay', 'WEB', 'HDTV', 'DVD', 'AMZN', 'NF', 'DSNP', 'ATVP', 'MAX']

type StreamFieldName =
  | 'name'
  | 'resolution'
  | 'quality'
  | 'codec'
  | 'bit_depth'
  | 'audio_formats'
  | 'channels'
  | 'hdr_formats'
  | 'source'
  | 'languages'

interface FieldState {
  value: string
  original: string
  isModified: boolean
}

interface StreamEditDialogProps {
  streamId: number
  streamName?: string
  currentValues?: {
    name?: string
    resolution?: string
    quality?: string
    codec?: string
    bit_depth?: string
    audio_formats?: string
    channels?: string
    hdr_formats?: string
    source?: string
    languages?: string[]
    size?: string
  }
  trigger?: React.ReactNode
}

const STEPS = [
  { id: 'video', title: 'Video', icon: Monitor },
  { id: 'audio', title: 'Audio & Language', icon: Volume2 },
  { id: 'source', title: 'Source', icon: Film },
  { id: 'review', title: 'Review', icon: CheckCircle2 },
]

export function StreamEditDialog({ streamId, streamName, currentValues, trigger }: StreamEditDialogProps) {
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(0)
  const [reason, setReason] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitResults, setSubmitResults] = useState<{ field: string; success: boolean }[]>([])
  const createSuggestion = useCreateStreamSuggestion()

  const getInitialFields = (): Record<StreamFieldName, FieldState> => ({
    name: { value: currentValues?.name || '', original: currentValues?.name || '', isModified: false },
    resolution: {
      value: currentValues?.resolution || '',
      original: currentValues?.resolution || '',
      isModified: false,
    },
    quality: { value: currentValues?.quality || '', original: currentValues?.quality || '', isModified: false },
    codec: { value: currentValues?.codec || '', original: currentValues?.codec || '', isModified: false },
    bit_depth: { value: currentValues?.bit_depth || '', original: currentValues?.bit_depth || '', isModified: false },
    audio_formats: {
      value: currentValues?.audio_formats || '',
      original: currentValues?.audio_formats || '',
      isModified: false,
    },
    channels: { value: currentValues?.channels || '', original: currentValues?.channels || '', isModified: false },
    hdr_formats: {
      value: currentValues?.hdr_formats || '',
      original: currentValues?.hdr_formats || '',
      isModified: false,
    },
    source: { value: currentValues?.source || '', original: currentValues?.source || '', isModified: false },
    languages: {
      value: Array.isArray(currentValues?.languages) ? currentValues.languages.join(', ') : '',
      original: Array.isArray(currentValues?.languages) ? currentValues.languages.join(', ') : '',
      isModified: false,
    },
  })

  const [fields, setFields] = useState<Record<StreamFieldName, FieldState>>(getInitialFields())

  useEffect(() => {
    if (open) {
      setFields(getInitialFields())
      setStep(0)
      setReason('')
      setSubmitResults([])
    }
  }, [open])

  const updateField = (fieldName: StreamFieldName, value: string) => {
    setFields((prev) => ({
      ...prev,
      [fieldName]: {
        ...prev[fieldName],
        value,
        isModified: value !== prev[fieldName].original,
      },
    }))
  }

  const modifiedFields = Object.entries(fields).filter(([_, state]) => state.isModified)
  const modifiedCount = modifiedFields.length

  const handleSubmit = async () => {
    if (modifiedCount === 0) return

    setIsSubmitting(true)
    setSubmitResults([])
    const results: { field: string; success: boolean }[] = []

    for (const [fieldName, state] of modifiedFields) {
      try {
        await createSuggestion.mutateAsync({
          streamId,
          data: {
            suggestion_type: 'field_correction',
            field_name: fieldName as ApiStreamFieldName,
            current_value: state.original || undefined,
            suggested_value: state.value,
            reason: reason.trim() || undefined,
          },
        })
        results.push({ field: fieldName, success: true })
      } catch (error) {
        results.push({ field: fieldName, success: false })
      }
    }

    setSubmitResults(results)
    setIsSubmitting(false)

    const successCount = results.filter((r) => r.success).length
    if (successCount > 0 && successCount === results.length) {
      setTimeout(() => setOpen(false), 1500)
    }
  }

  const CLEAR_VALUE = '__CLEAR__'

  const renderSelectWithInput = (fieldName: StreamFieldName, label: string, options: string[], placeholder: string) => {
    const state = fields[fieldName]
    // Filter out empty strings from options
    const validOptions = options.filter((opt) => opt && opt.trim() !== '')

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label className="text-sm">{label}</Label>
          {state.isModified && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-emerald-500/20 text-emerald-400">
              Modified
            </Badge>
          )}
        </div>
        <Select
          value={state.value || CLEAR_VALUE}
          onValueChange={(v) => updateField(fieldName, v === CLEAR_VALUE ? '' : v)}
        >
          <SelectTrigger className={cn('rounded-xl', state.isModified && 'border-emerald-500/50 bg-emerald-500/5')}>
            <SelectValue placeholder={placeholder} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={CLEAR_VALUE}>Clear</SelectItem>
            {validOptions.map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={state.value}
          onChange={(e) => updateField(fieldName, e.target.value)}
          placeholder="Or enter custom value"
          className={cn('rounded-xl text-sm', state.isModified && 'border-emerald-500/50 bg-emerald-500/5')}
        />
      </div>
    )
  }

  const renderStep = () => {
    switch (step) {
      case 0: // Video
        return (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              {renderSelectWithInput('resolution', 'Resolution', RESOLUTION_OPTIONS, 'Select resolution')}
              {renderSelectWithInput('quality', 'Quality', QUALITY_OPTIONS, 'Select quality')}
            </div>
            <div className="grid grid-cols-2 gap-4">
              {renderSelectWithInput('codec', 'Codec', CODEC_OPTIONS, 'Select codec')}
              {renderSelectWithInput('hdr_formats', 'HDR', HDR_OPTIONS, 'Select HDR format')}
            </div>
          </div>
        )

      case 1: // Audio & Language
        return (
          <div className="space-y-4">
            {renderSelectWithInput('audio_formats', 'Audio Format', AUDIO_OPTIONS, 'Select audio')}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-sm">Languages</Label>
                {fields.languages.isModified && (
                  <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-emerald-500/20 text-emerald-400">
                    Modified
                  </Badge>
                )}
              </div>
              <Textarea
                value={fields.languages.value}
                onChange={(e) => updateField('languages', e.target.value)}
                placeholder="English, Spanish, French (comma-separated)"
                rows={2}
                className={cn('rounded-xl', fields.languages.isModified && 'border-emerald-500/50 bg-emerald-500/5')}
              />
            </div>
          </div>
        )

      case 2: // Source
        return (
          <div className="space-y-4">
            {renderSelectWithInput('source', 'Source', SOURCE_OPTIONS, 'Select source')}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-sm">Stream Name</Label>
                {fields.name.isModified && (
                  <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-emerald-500/20 text-emerald-400">
                    Modified
                  </Badge>
                )}
              </div>
              <Input
                value={fields.name.value}
                onChange={(e) => updateField('name', e.target.value)}
                placeholder="Stream display name"
                className={cn('rounded-xl', fields.name.isModified && 'border-emerald-500/50 bg-emerald-500/5')}
              />
            </div>
          </div>
        )

      case 3: // Review
        return (
          <div className="space-y-4">
            {modifiedCount === 0 ? (
              <div className="text-center py-6 text-muted-foreground">
                <AlertCircle className="h-10 w-10 mx-auto mb-2 opacity-50" />
                <p>No changes made</p>
                <p className="text-sm mt-1">Go back and modify some fields</p>
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <Label className="text-sm">Changes to submit ({modifiedCount})</Label>
                  <div className="space-y-2">
                    {modifiedFields.map(([field, state]) => (
                      <div key={field} className="p-3 rounded-lg bg-muted/50 space-y-1">
                        <p className="text-sm font-medium capitalize">{field.replace('_', ' ')}</p>
                        <div className="flex items-center gap-2 text-xs">
                          <span className="text-muted-foreground line-through">{state.original || '(empty)'}</span>
                          <ChevronRight className="h-3 w-3" />
                          <span className="text-emerald-400">{state.value || '(empty)'}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <Separator />
                <div className="space-y-2">
                  <Label className="text-sm">Reason for changes (optional)</Label>
                  <Textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Explain why these changes are needed..."
                    rows={2}
                    className="rounded-xl"
                  />
                </div>
              </>
            )}

            {submitResults.length > 0 && (
              <div className="p-3 rounded-xl bg-muted/50 space-y-2">
                <p className="text-sm font-medium">Results</p>
                {submitResults.map(({ field, success }) => (
                  <div key={field} className="flex items-center gap-2 text-sm">
                    {success ? (
                      <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-red-500" />
                    )}
                    <span className="capitalize">{field.replace('_', ' ')}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )

      default:
        return null
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger || (
          <Button variant="outline" size="sm" className="gap-1.5">
            <Edit className="h-4 w-4" />
            Edit Stream
          </Button>
        )}
      </DialogTrigger>

      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Edit className="h-5 w-5 text-emerald-500" />
            Edit Stream
          </DialogTitle>
          <DialogDescription className="line-clamp-1">
            {streamName || 'Suggest corrections to stream information'}
          </DialogDescription>
        </DialogHeader>

        {/* Progress Steps */}
        <div className="py-2">
          <div className="flex justify-between mb-2">
            {STEPS.map((s, i) => {
              const Icon = s.icon
              return (
                <button
                  key={s.id}
                  onClick={() => setStep(i)}
                  className={cn(
                    'flex flex-col items-center gap-1 text-xs transition-colors',
                    i === step ? 'text-emerald-500' : i < step ? 'text-muted-foreground' : 'text-muted-foreground/50',
                  )}
                >
                  <div
                    className={cn(
                      'w-8 h-8 rounded-full flex items-center justify-center transition-colors',
                      i === step ? 'bg-emerald-500/20' : i < step ? 'bg-muted' : 'bg-muted/50',
                    )}
                  >
                    <Icon className="h-4 w-4" />
                  </div>
                  <span className="hidden sm:block">{s.title}</span>
                </button>
              )
            })}
          </div>
          <Progress value={(step / (STEPS.length - 1)) * 100} className="h-1" />
        </div>

        {/* Step Content */}
        <div className="py-4 min-h-[200px]">{renderStep()}</div>

        <DialogFooter className="flex-row gap-2">
          <Button
            variant="outline"
            onClick={() => setStep(Math.max(0, step - 1))}
            disabled={step === 0}
            className="flex-1 sm:flex-none"
          >
            <ChevronLeft className="h-4 w-4 mr-1" />
            Back
          </Button>

          {step < STEPS.length - 1 ? (
            <Button
              onClick={() => setStep(step + 1)}
              className="flex-1 sm:flex-none bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500"
            >
              Next
              <ChevronRight className="h-4 w-4 ml-1" />
            </Button>
          ) : (
            <Button
              onClick={handleSubmit}
              disabled={modifiedCount === 0 || isSubmitting}
              className="flex-1 sm:flex-none bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500"
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
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
