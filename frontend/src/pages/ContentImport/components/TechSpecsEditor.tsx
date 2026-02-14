import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Monitor, Film, Cpu, Music, Sun, Languages as LanguagesIcon, ChevronDown, X } from 'lucide-react'
import { RESOLUTION_OPTIONS, QUALITY_OPTIONS, CODEC_OPTIONS, AUDIO_OPTIONS, HDR_OPTIONS } from '@/lib/constants'
import { cn } from '@/lib/utils'

interface TechSpecsEditorProps {
  resolution?: string
  quality?: string
  codec?: string
  audio?: string[]
  hdr?: string[]
  languages?: string[]
  availableLanguages?: string[]
  onChange: (field: string, value: string | string[] | undefined) => void
  compact?: boolean
}

export function TechSpecsEditor({
  resolution,
  quality,
  codec,
  audio = [],
  hdr = [],
  languages = [],
  availableLanguages = [],
  onChange,
  compact = false,
}: TechSpecsEditorProps) {
  const handleAudioToggle = (value: string) => {
    const newAudio = audio.includes(value) ? audio.filter((a) => a !== value) : [...audio, value]
    onChange('audio', newAudio.length > 0 ? newAudio : undefined)
  }

  const handleHDRToggle = (value: string) => {
    const newHDR = hdr.includes(value) ? hdr.filter((h) => h !== value) : [...hdr, value]
    onChange('hdr', newHDR.length > 0 ? newHDR : undefined)
  }

  const handleLanguageToggle = (value: string) => {
    const newLanguages = languages.includes(value) ? languages.filter((l) => l !== value) : [...languages, value]
    onChange('languages', newLanguages.length > 0 ? newLanguages : undefined)
  }

  const clearAudio = () => onChange('audio', undefined)
  const clearHDR = () => onChange('hdr', undefined)
  const clearLanguages = () => onChange('languages', undefined)

  return (
    <div className={cn('space-y-4', compact && 'space-y-3')}>
      {/* Single Select Fields */}
      <div className={cn('grid gap-4', compact ? 'grid-cols-3' : 'grid-cols-2 md:grid-cols-3')}>
        {/* Resolution */}
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
            <Monitor className="h-3 w-3" />
            Resolution
          </Label>
          <Select
            value={resolution || '__NONE__'}
            onValueChange={(value) => onChange('resolution', value === '__NONE__' ? undefined : value)}
          >
            <SelectTrigger className={cn('rounded-lg', compact && 'h-8 text-xs')}>
              <SelectValue placeholder="Select..." />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__NONE__">Not Set</SelectItem>
              {RESOLUTION_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Quality */}
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
            <Film className="h-3 w-3" />
            Quality
          </Label>
          <Select
            value={quality || '__NONE__'}
            onValueChange={(value) => onChange('quality', value === '__NONE__' ? undefined : value)}
          >
            <SelectTrigger className={cn('rounded-lg', compact && 'h-8 text-xs')}>
              <SelectValue placeholder="Select..." />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__NONE__">Not Set</SelectItem>
              {QUALITY_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Codec */}
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
            <Cpu className="h-3 w-3" />
            Codec
          </Label>
          <Select
            value={codec || '__NONE__'}
            onValueChange={(value) => onChange('codec', value === '__NONE__' ? undefined : value)}
          >
            <SelectTrigger className={cn('rounded-lg', compact && 'h-8 text-xs')}>
              <SelectValue placeholder="Select..." />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__NONE__">Not Set</SelectItem>
              {CODEC_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Multi-Select Fields */}
      <div className={cn('grid gap-4', compact ? 'grid-cols-3' : 'grid-cols-1 md:grid-cols-3')}>
        {/* Audio Codecs */}
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
            <Music className="h-3 w-3" />
            Audio
          </Label>
          <MultiSelectPopover
            values={audio}
            options={AUDIO_OPTIONS}
            onToggle={handleAudioToggle}
            onClear={clearAudio}
            placeholder="Select audio..."
            compact={compact}
          />
        </div>

        {/* HDR */}
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
            <Sun className="h-3 w-3" />
            HDR
          </Label>
          <MultiSelectPopover
            values={hdr}
            options={HDR_OPTIONS}
            onToggle={handleHDRToggle}
            onClear={clearHDR}
            placeholder="Select HDR..."
            compact={compact}
          />
        </div>

        {/* Languages */}
        {availableLanguages.length > 0 && (
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
              <LanguagesIcon className="h-3 w-3" />
              Languages
            </Label>
            <MultiSelectPopover
              values={languages}
              options={availableLanguages.map((lang) => ({ value: lang, label: lang }))}
              onToggle={handleLanguageToggle}
              onClear={clearLanguages}
              placeholder="Select languages..."
              compact={compact}
            />
          </div>
        )}
      </div>

      {/* Current Selection Summary */}
      {(audio.length > 0 || hdr.length > 0 || languages.length > 0) && !compact && (
        <div className="flex flex-wrap gap-1.5 pt-2">
          {audio.map((a) => (
            <Badge key={a} variant="secondary" className="text-xs gap-1">
              {a}
              <X className="h-3 w-3 cursor-pointer hover:text-destructive" onClick={() => handleAudioToggle(a)} />
            </Badge>
          ))}
          {hdr.map((h) => (
            <Badge key={h} variant="outline" className="text-xs gap-1 border-primary/50 text-primary">
              {h}
              <X className="h-3 w-3 cursor-pointer hover:text-destructive" onClick={() => handleHDRToggle(h)} />
            </Badge>
          ))}
          {languages.map((l) => (
            <Badge key={l} variant="outline" className="text-xs gap-1 border-blue-500/50 text-blue-500">
              {l}
              <X className="h-3 w-3 cursor-pointer hover:text-destructive" onClick={() => handleLanguageToggle(l)} />
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

// Multi-select popover component
interface MultiSelectPopoverProps {
  values: string[]
  options: readonly { value: string; label: string }[]
  onToggle: (value: string) => void
  onClear: () => void
  placeholder: string
  compact?: boolean
}

function MultiSelectPopover({ values, options, onToggle, onClear, placeholder, compact }: MultiSelectPopoverProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          className={cn('w-full justify-between rounded-lg font-normal', compact && 'h-8 text-xs')}
        >
          {values.length > 0 ? (
            <span className="truncate">{values.length === 1 ? values[0] : `${values.length} selected`}</span>
          ) : (
            <span className="text-muted-foreground">{placeholder}</span>
          )}
          <ChevronDown className="h-4 w-4 opacity-50 flex-shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[200px] p-0" align="start">
        <ScrollArea className="h-[200px]">
          <div className="p-1">
            {values.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start text-xs text-muted-foreground mb-1"
                onClick={onClear}
              >
                Clear all
              </Button>
            )}
            {options.map((option) => (
              <div
                key={option.value}
                className="flex items-center space-x-2 px-2 py-1.5 hover:bg-accent rounded-sm cursor-pointer"
                onClick={() => onToggle(option.value)}
              >
                <Checkbox checked={values.includes(option.value)} onCheckedChange={() => onToggle(option.value)} />
                <span className="text-sm">{option.label}</span>
              </div>
            ))}
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}
