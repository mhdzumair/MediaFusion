import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
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
  Edit,
  Loader2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCreateStreamSuggestion } from '@/hooks'

// Stream editable fields (v5 schema) - matches backend STREAM_EDITABLE_FIELDS
const STREAM_EDITABLE_FIELDS = [
  { value: 'name', label: 'Stream Name', hint: 'The display name of the stream' },
  { value: 'resolution', label: 'Resolution', hint: 'e.g., 1080p, 2160p, 720p' },
  { value: 'quality', label: 'Quality', hint: 'e.g., BluRay, WEB-DL, HDRip' },
  { value: 'codec', label: 'Codec', hint: 'e.g., x264, x265, HEVC, AVC' },
  { value: 'bit_depth', label: 'Bit Depth', hint: 'e.g., 8-bit, 10-bit, 12-bit' },
  { value: 'audio_formats', label: 'Audio Formats', hint: 'e.g., DTS, AAC, Atmos, TrueHD' },
  { value: 'channels', label: 'Audio Channels', hint: 'e.g., 2.0, 5.1, 7.1, Atmos' },
  { value: 'hdr_formats', label: 'HDR Formats', hint: 'e.g., HDR, HDR10, HDR10+, DV (Dolby Vision)' },
  { value: 'source', label: 'Source', hint: 'e.g., BluRay, WEB, HDTV, CAM' },
  { value: 'languages', label: 'Languages', hint: 'Comma-separated list: English, Spanish, French' },
] as const

type StreamEditableField = typeof STREAM_EDITABLE_FIELDS[number]['value']

interface StreamEditProps {
  streamId: number
  streamName?: string
  // Current values for pre-filling
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
  }
  className?: string
  variant?: 'button' | 'icon'
}

export function StreamEdit({
  streamId,
  streamName,
  currentValues,
  className,
  variant = 'icon',
}: StreamEditProps) {
  const createSuggestion = useCreateStreamSuggestion()
  
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedField, setSelectedField] = useState<StreamEditableField>('resolution')
  const [suggestedValue, setSuggestedValue] = useState('')
  const [reason, setReason] = useState('')

  const selectedFieldInfo = STREAM_EDITABLE_FIELDS.find(f => f.value === selectedField)

  const getCurrentValue = (field: StreamEditableField): string => {
    if (!currentValues) return ''
    const val = currentValues[field as keyof typeof currentValues]
    if (Array.isArray(val)) return val.join(', ')
    return val || ''
  }

  const handleFieldChange = (field: StreamEditableField) => {
    setSelectedField(field)
    setSuggestedValue(getCurrentValue(field))
  }

  const handleSubmit = async () => {
    if (!suggestedValue.trim()) return
    
    try {
      await createSuggestion.mutateAsync({
        streamId,
        data: {
          suggestion_type: 'field_correction',
          field_name: selectedField,
          current_value: getCurrentValue(selectedField) || undefined,
          suggested_value: suggestedValue.trim(),
          reason: reason.trim() || undefined,
        },
      })
      setDialogOpen(false)
      setSuggestedValue('')
      setReason('')
    } catch (error) {
      // Error handled by mutation
    }
  }

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <DialogTrigger asChild>
              {variant === 'icon' ? (
                <Button 
                  variant="ghost" 
                  size="icon"
                  className={cn('h-8 w-8', className)}
                >
                  <Edit className="h-4 w-4" />
                </Button>
              ) : (
                <Button 
                  variant="outline" 
                  size="sm"
                  className={cn('gap-1.5', className)}
                >
                  <Edit className="h-4 w-4" />
                  Edit Stream
                </Button>
              )}
            </DialogTrigger>
          </TooltipTrigger>
          <TooltipContent>
            <p>Suggest an edit to this stream</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Edit Stream Info</DialogTitle>
          <DialogDescription>
            {streamName ? (
              <>Suggest an edit to <span className="font-medium line-clamp-1">{streamName}</span></>
            ) : (
              'Suggest an edit to this stream'
            )}. Your edit will be reviewed by moderators.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Field selection */}
          <div className="space-y-2">
            <Label>Field to Edit</Label>
            <Select
              value={selectedField}
              onValueChange={(v) => handleFieldChange(v as StreamEditableField)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a field" />
              </SelectTrigger>
              <SelectContent>
                {STREAM_EDITABLE_FIELDS.map((field) => (
                  <SelectItem key={field.value} value={field.value}>
                    {field.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedFieldInfo?.hint && (
              <p className="text-xs text-muted-foreground">{selectedFieldInfo.hint}</p>
            )}
          </div>

          {/* Current value */}
          <div className="space-y-2">
            <Label htmlFor="current">Current Value</Label>
            <Input
              id="current"
              value={getCurrentValue(selectedField)}
              disabled
              className="bg-muted"
              placeholder="(not set)"
            />
          </div>

          {/* Suggested value */}
          <div className="space-y-2">
            <Label htmlFor="suggested">Correct Value</Label>
            {selectedField === 'languages' ? (
              <Textarea
                id="suggested"
                value={suggestedValue}
                onChange={(e) => setSuggestedValue(e.target.value)}
                placeholder="English, Spanish, French"
                rows={2}
              />
            ) : (
              <Input
                id="suggested"
                value={suggestedValue}
                onChange={(e) => setSuggestedValue(e.target.value)}
                placeholder={selectedFieldInfo?.hint || 'Enter the correct value'}
              />
            )}
          </div>

          {/* Reason */}
          <div className="space-y-2">
            <Label htmlFor="reason">Reason (optional)</Label>
            <Textarea
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why should this be changed?"
              rows={2}
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
            disabled={!suggestedValue.trim() || createSuggestion.isPending}
          >
            {createSuggestion.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Submitting...
              </>
            ) : (
              'Submit Edit'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

