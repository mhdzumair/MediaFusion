import { useState, useCallback, useMemo } from 'react'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { X, Copy, Check, Download, AlertTriangle, Volume2 } from 'lucide-react'
import { VideoPlayer, type VideoSource } from './VideoPlayer'
import { ExternalPlayerMenu } from './ExternalPlayerMenu'

// Audio codecs NOT supported by browser's HTML5 video element
// Browser supports: AAC, MP3, Opus, FLAC, Vorbis
// Not supported: DTS, TrueHD, AC3, EAC3 (Dolby Digital)
const UNSUPPORTED_AUDIO_CODECS = [
  'dts',
  'dts-hd',
  'dtshd',
  'truehd',
  'atmos', // Dolby Atmos (typically uses TrueHD or EAC3)
  'ac3',
  'ac-3',
  'eac3',
  'e-ac-3',
  'dolby',
  'dd5.1',
  'dd7.1',
  'dd+',
  'ddp',
]

/**
 * Check if the audio codec string contains an unsupported codec
 */
function hasUnsupportedAudioCodec(audioInfo?: string): boolean {
  if (!audioInfo) return false
  const lowerAudio = audioInfo.toLowerCase()
  return UNSUPPORTED_AUDIO_CODECS.some((codec) => lowerAudio.includes(codec))
}

/**
 * Extract the specific unsupported codec name for display
 */
function getUnsupportedCodecName(audioInfo?: string): string | null {
  if (!audioInfo) return null
  const lowerAudio = audioInfo.toLowerCase()

  // Match display-friendly names
  if (lowerAudio.includes('truehd') || lowerAudio.includes('atmos')) return 'Dolby TrueHD/Atmos'
  if (lowerAudio.includes('dts-hd') || lowerAudio.includes('dtshd')) return 'DTS-HD'
  if (lowerAudio.includes('dts')) return 'DTS'
  if (
    lowerAudio.includes('eac3') ||
    lowerAudio.includes('e-ac-3') ||
    lowerAudio.includes('ddp') ||
    lowerAudio.includes('dd+')
  )
    return 'Dolby Digital Plus (EAC3)'
  if (
    lowerAudio.includes('ac3') ||
    lowerAudio.includes('ac-3') ||
    lowerAudio.includes('dolby') ||
    lowerAudio.includes('dd5') ||
    lowerAudio.includes('dd7')
  )
    return 'Dolby Digital (AC3)'

  return null
}

export interface StreamInfo {
  id?: string
  name: string
  title?: string
  url?: string
  quality?: string
  resolution?: string
  size?: string
  source?: string
  codec?: string
  audio?: string
  behaviorHints?: Record<string, unknown> // Contains headers and other hints for HTTP streams
}

export interface PlayerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stream: StreamInfo | null
  contentTitle?: string
  poster?: string
  startTime?: number
  onTimeUpdate?: (currentTime: number, duration: number) => void
  onEnded?: () => void
}

export function PlayerDialog({
  open,
  onOpenChange,
  stream,
  contentTitle,
  poster,
  startTime,
  onTimeUpdate,
  onEnded,
}: PlayerDialogProps) {
  const [copied, setCopied] = useState(false)
  const [dismissedWarning, setDismissedWarning] = useState(false)
  const [runtimeAudioIssue, setRuntimeAudioIssue] = useState(false)

  const handleCopy = useCallback(async () => {
    if (!stream?.url) return
    await navigator.clipboard.writeText(stream.url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [stream])

  // Check for unsupported audio codec based on stream metadata
  const audioWarning = useMemo(() => {
    if (!stream?.audio) return null
    if (!hasUnsupportedAudioCodec(stream.audio)) return null

    return {
      hasWarning: true,
      codecName: getUnsupportedCodecName(stream.audio),
    }
  }, [stream])

  // Handle runtime audio issue detection (when player detects no audio output)
  const handleAudioIssue = useCallback(() => {
    setRuntimeAudioIssue(true)
  }, [])

  // Reset dismissed state and runtime issue when stream changes
  const streamId = stream?.url
  const [lastStreamId, setLastStreamId] = useState<string | undefined>()
  if (streamId !== lastStreamId) {
    setLastStreamId(streamId)
    setDismissedWarning(false)
    setRuntimeAudioIssue(false)
  }

  if (!stream?.url) return null

  // Extract headers from behaviorHints if available
  const proxyHeaders = stream.behaviorHints?.proxyHeaders as { request?: Record<string, string> } | undefined
  const headers = proxyHeaders?.request

  // Prepare video sources
  const sources: VideoSource[] = [
    {
      src: stream.url,
      label: stream.quality || stream.resolution || 'Default',
      quality: stream.quality || stream.resolution,
      headers,
    },
  ]

  // Show warning if we have metadata-based warning OR runtime-detected issue
  const showAudioWarning = (audioWarning?.hasWarning || runtimeAudioIssue) && !dismissedWarning
  const warningMessage =
    runtimeAudioIssue && !audioWarning?.hasWarning
      ? 'an unsupported audio codec' // Runtime detected, no metadata
      : audioWarning?.codecName || 'an unsupported audio codec'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-[95vw] sm:max-w-[900px] lg:max-w-[1100px] p-0 overflow-hidden bg-black border-border/50 gap-0"
        hideCloseButton
        // Prevent the dialog from closing when clicking outside, losing focus, or interacting
        // with browser-native elements (fullscreen prompts, video controls, etc.).
        // This is critical for video playback where focus shifts are common.
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        onFocusOutside={(e) => e.preventDefault()}
      >
        <DialogHeader className="absolute top-0 left-0 right-0 z-10 p-4 bg-gradient-to-b from-black/80 to-transparent">
          <DialogTitle className="flex items-center justify-between text-white">
            <div className="flex flex-col gap-1 min-w-0">
              <span className="text-lg font-semibold truncate">{contentTitle || stream.title || stream.name}</span>
              {stream.source && <span className="text-xs text-white/60 font-normal">{stream.source}</span>}
            </div>
            <DialogClose asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0 text-white hover:bg-white/20">
                <X className="h-4 w-4" />
                <span className="sr-only">Close</span>
              </Button>
            </DialogClose>
          </DialogTitle>
          <DialogDescription className="sr-only">
            Video player for {contentTitle || stream.title || stream.name}
          </DialogDescription>
        </DialogHeader>

        {/* Unsupported Audio Codec Warning */}
        {showAudioWarning && (
          <div className="absolute top-16 left-0 right-0 z-20 px-4">
            <div className="bg-yellow-500/90 text-black rounded-lg p-3 flex items-start gap-3 shadow-lg">
              <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">
                  {runtimeAudioIssue ? 'No audio detected' : 'Audio may not play in browser'}
                </p>
                <p className="text-xs mt-0.5 opacity-80">
                  This video uses {warningMessage} which browsers cannot decode. Video will play but you{' '}
                  {runtimeAudioIssue ? "won't" : 'may not'} hear audio. Use an external player for full audio support.
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <ExternalPlayerMenu streamUrl={stream.url} variant="prominent" />
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-black/70 hover:text-black hover:bg-black/10 h-8 px-2"
                  onClick={() => setDismissedWarning(true)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* Video Player */}
        <VideoPlayer
          sources={sources}
          poster={poster}
          autoPlay
          startTime={startTime}
          onTimeUpdate={onTimeUpdate}
          onEnded={onEnded}
          onAudioIssue={handleAudioIssue}
          className="w-full"
        />

        {/* Bottom Info Bar */}
        <div className="p-4 bg-black/90 border-t border-white/10">
          <div className="flex items-center justify-between gap-4">
            {/* Stream Info */}
            <div className="flex flex-wrap items-center gap-2">
              {stream.quality && (
                <Badge variant="secondary" className="bg-primary/20 text-primary text-xs">
                  {stream.quality}
                </Badge>
              )}
              {stream.resolution && (
                <Badge variant="outline" className="text-xs text-white/70 border-white/20">
                  {stream.resolution}
                </Badge>
              )}
              {stream.size && (
                <Badge variant="outline" className="text-xs text-white/70 border-white/20">
                  {stream.size}
                </Badge>
              )}
              {stream.codec && (
                <Badge variant="outline" className="text-xs text-white/70 border-white/20">
                  {stream.codec}
                </Badge>
              )}
              {stream.audio && (
                <Badge
                  variant="outline"
                  className={`text-xs ${
                    audioWarning?.hasWarning
                      ? 'text-yellow-400 border-yellow-400/50 bg-yellow-400/10'
                      : 'text-white/70 border-white/20'
                  }`}
                  title={audioWarning?.hasWarning ? `${audioWarning.codecName} - may not play in browser` : undefined}
                >
                  {audioWarning?.hasWarning && <Volume2 className="h-3 w-3 mr-1" />}
                  {stream.audio}
                </Badge>
              )}
            </div>

            {/* Actions */}
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="text-white/70 hover:text-white hover:bg-white/10"
                onClick={handleCopy}
              >
                {copied ? (
                  <>
                    <Check className="mr-2 h-4 w-4 text-emerald-500" />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy className="mr-2 h-4 w-4" />
                    Copy URL
                  </>
                )}
              </Button>

              <ExternalPlayerMenu streamUrl={stream.url} />

              <Button variant="ghost" size="sm" className="text-white/70 hover:text-white hover:bg-white/10" asChild>
                <a href={stream.url} download target="_blank" rel="noopener noreferrer">
                  <Download className="mr-2 h-4 w-4" />
                  Download
                </a>
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
