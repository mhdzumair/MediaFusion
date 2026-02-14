import { useState, useEffect, useMemo } from 'react'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Edit, Loader2, CheckCircle2, AlertCircle, Monitor, Volume2, Film, Languages, HardDrive } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCreateStreamSuggestion } from '@/hooks'
import { TagInput } from '@/components/ui/tag-input'
import type { StreamFieldName as ApiStreamFieldName } from '@/lib/api'

// Predefined options
const RESOLUTION_OPTIONS = ['4K', '2160p', '1080p', '720p', '480p', '360p']
const QUALITY_OPTIONS = ['WEB-DL', 'WEBRip', 'BluRay', 'BDRip', 'HDRip', 'HDTV', 'DVDRip', 'CAM', 'TS']
const CODEC_OPTIONS = ['x265', 'x264', 'HEVC', 'H.265', 'H.264', 'AVC', 'VP9', 'AV1']
const AUDIO_OPTIONS = ['AAC', 'AC3', 'DTS', 'DTS-HD', 'Atmos', 'TrueHD', 'DD5.1', 'DD+', 'FLAC']
const HDR_OPTIONS = ['HDR', 'HDR10', 'HDR10+', 'Dolby Vision', 'DV', 'HLG', 'SDR']

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

interface StreamEditSheetProps {
  streamId: number
  streamName?: string // Raw torrent/stream name
  currentValues?: {
    name?: string
    resolution?: string
    quality?: string
    codec?: string
    bit_depth?: string
    audio_formats?: string
    channels?: string
    hdr_formats?: string
    languages?: string[]
    size?: string
    source?: string
  }
  trigger?: React.ReactNode
  onSuccess?: () => void
  mediaType?: 'movie' | 'series'
  episodeLinks?: {
    file_id: number
    file_name: string
    season_number?: number
    episode_number?: number
    episode_end?: number
  }[]
}

const CLEAR_VALUE = '__CLEAR__'

export function StreamEditSheet({
  streamId,
  streamName,
  currentValues,
  trigger,
  onSuccess,
  mediaType: _mediaType,
  episodeLinks: _episodeLinks,
}: StreamEditSheetProps) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitResults, setSubmitResults] = useState<{ field: string; success: boolean }[]>([])
  const [languages, setLanguages] = useState<string[]>([])

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
      value: currentValues?.languages?.join(', ') || '',
      original: currentValues?.languages?.join(', ') || '',
      isModified: false,
    },
  })

  const [fields, setFields] = useState<Record<StreamFieldName, FieldState>>(getInitialFields())

  // Reset when sheet opens
  useEffect(() => {
    if (open) {
      setFields(getInitialFields())
      setLanguages(currentValues?.languages || [])
      setReason('')
      setSubmitResults([])
    }
  }, [open, currentValues])

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

  // Track language modifications
  const languagesModified = useMemo(() => {
    const original = currentValues?.languages || []
    return JSON.stringify([...languages].sort()) !== JSON.stringify([...original].sort())
  }, [languages, currentValues?.languages])

  // Calculate all modifications
  const modifiedFields = useMemo(() => {
    const result: { field: StreamFieldName; currentValue: string; newValue: string }[] = []

    Object.entries(fields).forEach(([key, state]) => {
      if (state.isModified && key !== 'languages') {
        result.push({ field: key as StreamFieldName, currentValue: state.original, newValue: state.value })
      }
    })

    if (languagesModified) {
      result.push({
        field: 'languages',
        currentValue: currentValues?.languages?.join(', ') || '',
        newValue: languages.join(', '),
      })
    }

    return result
  }, [fields, languagesModified, currentValues?.languages, languages])

  const modifiedCount = modifiedFields.length

  const handleSubmit = async () => {
    if (modifiedCount === 0) return

    setIsSubmitting(true)
    setSubmitResults([])
    const results: { field: string; success: boolean }[] = []

    // Submit field corrections
    for (const { field, currentValue, newValue } of modifiedFields) {
      try {
        await createSuggestion.mutateAsync({
          streamId,
          data: {
            suggestion_type: 'field_correction',
            field_name: field as ApiStreamFieldName,
            current_value: currentValue || undefined,
            suggested_value: newValue,
            reason: reason.trim() || undefined,
          },
        })
        results.push({ field, success: true })
      } catch (error) {
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

  const renderSelectField = (fieldName: StreamFieldName, label: string, options: string[], icon: React.ReactNode) => {
    const state = fields[fieldName]
    const validOptions = options.filter((opt) => opt && opt.trim() !== '')

    return (
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label className="text-xs flex items-center gap-1.5">
            {icon}
            {label}
          </Label>
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
            <SelectValue placeholder={`Select ${label.toLowerCase()}`} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={CLEAR_VALUE}>
              <span className="text-muted-foreground">Clear</span>
            </SelectItem>
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
          className={cn('rounded-xl text-xs', state.isModified && 'border-emerald-500/50 bg-emerald-500/5')}
        />
      </div>
    )
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        {trigger || (
          <Button variant="outline" size="sm" className="gap-1.5 rounded-xl">
            <Edit className="h-4 w-4" />
            Edit Stream
          </Button>
        )}
      </SheetTrigger>

      <SheetContent className="w-full sm:max-w-[480px] p-0 flex flex-col">
        <SheetHeader className="px-6 py-4 border-b">
          <SheetTitle className="flex items-center gap-2">
            <Edit className="h-5 w-5 text-emerald-500" />
            Edit Stream
          </SheetTitle>
          <SheetDescription className="line-clamp-1">Suggest corrections to stream information</SheetDescription>
        </SheetHeader>

        <ScrollArea className="flex-1 px-6">
          <div className="py-6 space-y-6">
            {/* Stream Info (Read-only) */}
            <div className="p-4 rounded-xl bg-muted/50 space-y-2">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Film className="h-4 w-4 text-muted-foreground" />
                Stream Name
              </div>
              <p className="text-xs font-mono text-muted-foreground break-all">
                {streamName || currentValues?.name || 'Unknown stream'}
              </p>
              <div className="flex flex-wrap gap-2 pt-1">
                {currentValues?.size && (
                  <Badge variant="outline" className="text-xs">
                    <HardDrive className="h-3 w-3 mr-1" />
                    {currentValues.size}
                  </Badge>
                )}
                {currentValues?.source && (
                  <Badge variant="outline" className="text-xs">
                    {currentValues.source}
                  </Badge>
                )}
              </div>
            </div>

            <Separator />

            {/* Video Section */}
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <Monitor className="h-4 w-4" />
                Video Quality
              </div>

              <div className="grid grid-cols-2 gap-4">
                {renderSelectField('resolution', 'Resolution', RESOLUTION_OPTIONS, null)}
                {renderSelectField('quality', 'Quality', QUALITY_OPTIONS, null)}
              </div>

              <div className="grid grid-cols-2 gap-4">
                {renderSelectField('codec', 'Codec', CODEC_OPTIONS, null)}
                {renderSelectField('hdr_formats', 'HDR', HDR_OPTIONS, null)}
              </div>
            </div>

            <Separator />

            {/* Audio Section */}
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <Volume2 className="h-4 w-4" />
                Audio
              </div>

              {renderSelectField('audio_formats', 'Audio Format', AUDIO_OPTIONS, null)}
            </div>

            <Separator />

            {/* Languages Section */}
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <Languages className="h-4 w-4" />
                Languages
              </div>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label className="text-xs">Available Languages</Label>
                  {languagesModified && (
                    <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-emerald-500/20 text-emerald-400">
                      Modified
                    </Badge>
                  )}
                </div>
                <TagInput
                  value={languages}
                  onChange={setLanguages}
                  placeholder="Add language (e.g., English)..."
                  className={cn(languagesModified && 'border-emerald-500/50 bg-emerald-500/5')}
                />
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
                    <span className="capitalize">{field.replace('_', ' ')}</span>
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
                <span className="text-emerald-500 font-medium">
                  {modifiedCount} change{modifiedCount !== 1 ? 's' : ''}
                </span>
              ) : (
                'No changes'
              )}
            </div>
            <Button
              onClick={handleSubmit}
              disabled={modifiedCount === 0 || isSubmitting}
              className="bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 rounded-xl"
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
