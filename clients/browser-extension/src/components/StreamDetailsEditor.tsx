import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/textarea'
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
import { Settings2, X, Plus, ChevronDown, ChevronUp, HelpCircle, Image, Globe, Lock } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TorrentType, ContentType } from '@/lib/types'

// Common options for stream details
const RESOLUTION_OPTIONS = ['480p', '576p', '720p', '1080p', '2160p', '4320p']
const QUALITY_OPTIONS = ['CAM', 'HDCAM', 'TS', 'TC', 'SCR', 'DVDScr', 'DVDRip', 'HDRip', 'WebRip', 'WEB-DL', 'HDTV', 'BluRay', 'Remux']
const CODEC_OPTIONS = ['x264', 'x265', 'HEVC', 'H.264', 'H.265', 'AV1', 'VP9', 'DivX', 'XviD']
const AUDIO_OPTIONS = ['AAC', 'AC3', 'DTS', 'DTS-HD', 'TrueHD', 'Atmos', 'FLAC', 'EAC3', 'DD+', 'DD5.1', '7.1']
const HDR_OPTIONS = ['HDR', 'HDR10', 'HDR10+', 'Dolby Vision', 'DV', 'HLG']
const LANGUAGE_OPTIONS = ['English', 'Hindi', 'Tamil', 'Telugu', 'Bengali', 'Spanish', 'French', 'German', 'Japanese', 'Korean', 'Chinese', 'Russian', 'Italian', 'Portuguese', 'Multi']

const TORRENT_TYPE_OPTIONS: { value: TorrentType; label: string; icon: typeof Globe }[] = [
  { value: 'public', label: 'Public', icon: Globe },
  { value: 'semi-private', label: 'Semi-Private', icon: Globe },
  { value: 'private', label: 'Private', icon: Lock },
  { value: 'web-seed', label: 'Web-Seed', icon: Globe },
]

export interface StreamDetails {
  resolution?: string
  quality?: string
  codec?: string
  audio?: string[]
  hdr?: string[]
  languages?: string[]
  // New fields
  torrentType?: TorrentType
  posterUrl?: string
  episodeNameParser?: string
}

interface StreamDetailsEditorProps {
  details: StreamDetails
  onChange: (details: StreamDetails) => void
  className?: string
  defaultExpanded?: boolean
  contentType?: ContentType
}

export function StreamDetailsEditor({
  details,
  onChange,
  className,
  defaultExpanded = false,
  contentType,
}: StreamDetailsEditorProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded)
  const [customAudio, setCustomAudio] = useState('')
  const [customLanguage, setCustomLanguage] = useState('')

  const updateField = <K extends keyof StreamDetails>(field: K, value: StreamDetails[K]) => {
    onChange({ ...details, [field]: value })
  }
  
  // Show episode parser for series and sports content
  const showEpisodeParser = contentType === 'series' || contentType === 'sports'

  const addToArray = (field: 'audio' | 'hdr' | 'languages', value: string) => {
    if (!value) return
    const current = details[field] || []
    if (!current.includes(value)) {
      updateField(field, [...current, value])
    }
  }

  const removeFromArray = (field: 'audio' | 'hdr' | 'languages', value: string) => {
    const current = details[field] || []
    updateField(field, current.filter(v => v !== value))
  }

  return (
    <Card className={cn("border-dashed", className)}>
      <CardHeader className="pb-2 cursor-pointer" onClick={() => setIsExpanded(!isExpanded)}>
        <CardTitle className="text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Settings2 className="h-4 w-4" />
            Stream Details
          </div>
          <Button variant="ghost" size="sm" className="h-6 w-6 p-0">
            {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </CardTitle>
        {!isExpanded && (
          <div className="flex flex-wrap gap-1 mt-1">
            {details.resolution && <Badge variant="secondary" className="text-xs">{details.resolution}</Badge>}
            {details.quality && <Badge variant="secondary" className="text-xs">{details.quality}</Badge>}
            {details.codec && <Badge variant="secondary" className="text-xs">{details.codec}</Badge>}
            {details.audio?.map(a => <Badge key={a} variant="outline" className="text-xs">{a}</Badge>)}
            {details.hdr?.map(h => <Badge key={h} variant="outline" className="text-xs text-orange-500">{h}</Badge>)}
          </div>
        )}
      </CardHeader>
      
      {isExpanded && (
        <CardContent className="space-y-4">
          {/* Torrent Type */}
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Torrent Type</Label>
            <Select 
              value={details.torrentType || 'public'} 
              onValueChange={(v) => updateField('torrentType', v as TorrentType)}
            >
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="Select type" />
              </SelectTrigger>
              <SelectContent>
                {TORRENT_TYPE_OPTIONS.map(opt => (
                  <SelectItem key={opt.value} value={opt.value}>
                    <span className="flex items-center gap-2">
                      <opt.icon className="h-3 w-3" />
                      {opt.label}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Basic Specs Row */}
          <div className="grid grid-cols-3 gap-2">
            {/* Resolution */}
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Resolution</Label>
              <Select value={details.resolution || ''} onValueChange={(v) => updateField('resolution', v || undefined)}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Select" />
                </SelectTrigger>
                <SelectContent>
                  {RESOLUTION_OPTIONS.map(opt => (
                    <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Quality */}
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Quality</Label>
              <Select value={details.quality || ''} onValueChange={(v) => updateField('quality', v || undefined)}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Select" />
                </SelectTrigger>
                <SelectContent>
                  {QUALITY_OPTIONS.map(opt => (
                    <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Codec */}
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Codec</Label>
              <Select value={details.codec || ''} onValueChange={(v) => updateField('codec', v || undefined)}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Select" />
                </SelectTrigger>
                <SelectContent>
                  {CODEC_OPTIONS.map(opt => (
                    <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Audio */}
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">Audio Formats</Label>
            <div className="flex flex-wrap gap-1 min-h-[24px]">
              {(details.audio || []).map(a => (
                <Badge key={a} variant="secondary" className="text-xs gap-1">
                  {a}
                  <button onClick={() => removeFromArray('audio', a)} className="hover:text-destructive">
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
            <div className="flex gap-1">
              <Select onValueChange={(v) => { addToArray('audio', v) }}>
                <SelectTrigger className="h-7 text-xs flex-1">
                  <SelectValue placeholder="Add audio format" />
                </SelectTrigger>
                <SelectContent>
                  {AUDIO_OPTIONS.filter(opt => !(details.audio || []).includes(opt)).map(opt => (
                    <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input 
                value={customAudio}
                onChange={(e) => setCustomAudio(e.target.value)}
                placeholder="Custom"
                className="h-7 text-xs w-20"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && customAudio) {
                    addToArray('audio', customAudio)
                    setCustomAudio('')
                  }
                }}
              />
              <Button 
                size="sm" 
                variant="ghost" 
                className="h-7 w-7 p-0"
                onClick={() => {
                  if (customAudio) {
                    addToArray('audio', customAudio)
                    setCustomAudio('')
                  }
                }}
              >
                <Plus className="h-3 w-3" />
              </Button>
            </div>
          </div>

          {/* HDR */}
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">HDR Formats</Label>
            <div className="flex flex-wrap gap-1 min-h-[24px]">
              {(details.hdr || []).map(h => (
                <Badge key={h} variant="outline" className="text-xs gap-1 text-orange-500 border-orange-500/30">
                  {h}
                  <button onClick={() => removeFromArray('hdr', h)} className="hover:text-destructive">
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
            <Select onValueChange={(v) => { addToArray('hdr', v) }}>
              <SelectTrigger className="h-7 text-xs">
                <SelectValue placeholder="Add HDR format" />
              </SelectTrigger>
              <SelectContent>
                {HDR_OPTIONS.filter(opt => !(details.hdr || []).includes(opt)).map(opt => (
                  <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Languages */}
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">Languages</Label>
            <div className="flex flex-wrap gap-1 min-h-[24px]">
              {(details.languages || []).map(l => (
                <Badge key={l} variant="secondary" className="text-xs gap-1">
                  {l}
                  <button onClick={() => removeFromArray('languages', l)} className="hover:text-destructive">
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
            <div className="flex gap-1">
              <Select onValueChange={(v) => { addToArray('languages', v) }}>
                <SelectTrigger className="h-7 text-xs flex-1">
                  <SelectValue placeholder="Add language" />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGE_OPTIONS.filter(opt => !(details.languages || []).includes(opt)).map(opt => (
                    <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input 
                value={customLanguage}
                onChange={(e) => setCustomLanguage(e.target.value)}
                placeholder="Custom"
                className="h-7 text-xs w-20"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && customLanguage) {
                    addToArray('languages', customLanguage)
                    setCustomLanguage('')
                  }
                }}
              />
              <Button 
                size="sm" 
                variant="ghost" 
                className="h-7 w-7 p-0"
                onClick={() => {
                  if (customLanguage) {
                    addToArray('languages', customLanguage)
                    setCustomLanguage('')
                  }
                }}
              >
                <Plus className="h-3 w-3" />
              </Button>
            </div>
          </div>

          {/* Poster URL */}
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground flex items-center gap-1">
              <Image className="h-3 w-3" />
              Poster URL (optional)
            </Label>
            <Input
              value={details.posterUrl || ''}
              onChange={(e) => updateField('posterUrl', e.target.value || undefined)}
              placeholder="https://example.com/poster.jpg"
              className="h-8 text-xs"
            />
          </div>

          {/* Episode Name Parser - for series and sports */}
          {showEpisodeParser && (
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground flex items-center gap-1">
                Episode Name Parser (optional)
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <HelpCircle className="h-3 w-3 text-muted-foreground cursor-help" />
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs text-xs">
                      <p className="font-semibold mb-1">Regex pattern to extract episode names</p>
                      <p className="text-muted-foreground mb-2">
                        Use named group <code className="bg-muted px-1">(?P&lt;episode_name&gt;...)</code> to capture the episode title.
                      </p>
                      <p className="font-semibold mb-1">Examples:</p>
                      <ul className="text-[10px] space-y-1">
                        <li><code>S\d+E\d+\.(?P&lt;episode_name&gt;.*?)\.1080p</code></li>
                        <li><code>Episode\.(\d+)\.(?P&lt;episode_name&gt;.*?)\.720p</code></li>
                        <li><code>Grand\.Prix\.(?P&lt;episode_name&gt;.*?)\.Sky</code></li>
                      </ul>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </Label>
              <Textarea
                value={details.episodeNameParser || ''}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => updateField('episodeNameParser', e.target.value || undefined)}
                placeholder={contentType === 'sports' 
                  ? "e.g., Grand\\.Prix\\.(?P<episode_name>.*?)\\.Sky"
                  : "e.g., S\\d+E\\d+\\.(?P<episode_name>.*?)\\.1080p"
                }
                className="h-16 text-xs font-mono"
              />
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}
