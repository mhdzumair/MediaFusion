import { useRef, useState, useEffect, useCallback } from 'react'
import Hls from 'hls.js'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { 
  Play, 
  Pause, 
  Volume2, 
  VolumeX, 
  Maximize, 
  Minimize,
  SkipBack,
  SkipForward,
  Loader2,
  AlertCircle,
  Settings,
  Download,
  ExternalLink,
  Check,
} from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

export interface VideoSource {
  src: string
  type?: string
  label?: string
  quality?: string
  headers?: Record<string, string>  // HTTP headers for the stream
}

// Content types that indicate a downloadable file, not a streamable video
const DOWNLOAD_CONTENT_TYPES = [
  'application/force-download',
  'application/octet-stream',
  'application/x-download',
  'binary/octet-stream',
]

// Content types that are streamable
const STREAMABLE_CONTENT_TYPES = [
  'video/',
  'application/vnd.apple.mpegurl', // HLS
  'application/x-mpegurl',          // HLS
  'application/dash+xml',           // DASH
]

/**
 * Check if a URL is directly streamable or a force-download
 * Returns: { streamable: boolean, contentType?: string, error?: string }
 */
async function checkStreamability(url: string): Promise<{ 
  streamable: boolean
  contentType?: string 
  error?: string 
}> {
  try {
    // Skip check for blob URLs or data URLs
    if (url.startsWith('blob:') || url.startsWith('data:')) {
      return { streamable: true }
    }
    
    // Make a HEAD request to check content-type without downloading the file
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 5000) // 5 second timeout
    
    const response = await fetch(url, { 
      method: 'HEAD',
      signal: controller.signal,
      // Some servers may require GET, so we allow redirects
      redirect: 'follow',
    })
    
    clearTimeout(timeoutId)
    
    const contentType = response.headers.get('content-type')?.toLowerCase() || ''
    
    // Check for download content types
    if (DOWNLOAD_CONTENT_TYPES.some(type => contentType.includes(type))) {
      return { 
        streamable: false, 
        contentType,
        error: 'This stream provider returned a download link instead of a streamable video'
      }
    }
    
    // Check for streamable content types
    if (STREAMABLE_CONTENT_TYPES.some(type => contentType.includes(type))) {
      return { streamable: true, contentType }
    }
    
    // For unknown content types, assume streamable and let the video player handle it
    return { streamable: true, contentType }
  } catch (err) {
    // If HEAD request fails (CORS, network error), let the video player try anyway
    // The video player will handle the actual error if it can't play
    console.warn('Streamability check failed:', err)
    return { streamable: true }
  }
}

export interface VideoPlayerProps {
  sources: VideoSource[]
  poster?: string
  autoPlay?: boolean
  startTime?: number // Resume position in seconds
  onTimeUpdate?: (currentTime: number, duration: number) => void
  onEnded?: () => void
  onError?: (error: string) => void
  onAudioIssue?: () => void  // Called when audio appears to not be playing (unsupported codec)
  className?: string
  /** If true, skip streamability check (for known streamable URLs) */
  skipStreamCheck?: boolean
}

function formatTime(seconds: number): string {
  if (isNaN(seconds) || !isFinite(seconds)) return '0:00'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function VideoPlayer({
  sources,
  poster,
  autoPlay = false,
  startTime = 0,
  onTimeUpdate,
  onEnded,
  onError,
  onAudioIssue,
  className,
  skipStreamCheck = false,
}: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const hlsRef = useRef<Hls | null>(null)
  const [currentSourceIndex, setCurrentSourceIndex] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)
  const [isMuted, setIsMuted] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [showControls, setShowControls] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isDownloadOnly, setIsDownloadOnly] = useState(false)
  const [checkingStream, setCheckingStream] = useState(true)
  const [copiedLink, setCopiedLink] = useState(false)
  const [audioIssueDetected, setAudioIssueDetected] = useState(false)
  const controlsTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const audioCheckTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const currentSource = sources[currentSourceIndex]

  // Check if current source is streamable
  useEffect(() => {
    if (!currentSource?.src || skipStreamCheck) {
      setCheckingStream(false)
      setIsDownloadOnly(false)
      return
    }

    let cancelled = false
    setCheckingStream(true)
    setIsDownloadOnly(false)
    setError(null)

    checkStreamability(currentSource.src).then(result => {
      if (cancelled) return
      setCheckingStream(false)
      
      if (!result.streamable) {
        setIsDownloadOnly(true)
        setIsLoading(false)
        setError(result.error || 'This URL cannot be streamed directly')
        onError?.(result.error || 'Download-only URL')
      }
    })

    return () => { cancelled = true }
  }, [currentSource?.src, skipStreamCheck, onError])

  // Determine if the current source is HLS
  const isHlsSource = useCallback((src: string) => {
    return /\.m3u8($|\?)/.test(src) || 
           currentSource?.type === 'application/vnd.apple.mpegurl' ||
           currentSource?.type === 'application/x-mpegurl'
  }, [currentSource?.type])

  // Initialize HLS.js or native playback
  useEffect(() => {
    const video = videoRef.current
    const src = currentSource?.src
    if (!video || !src || isDownloadOnly || checkingStream) return

    // Destroy previous HLS instance
    if (hlsRef.current) {
      hlsRef.current.destroy()
      hlsRef.current = null
    }

    if (isHlsSource(src) && Hls.isSupported()) {
      // Use hls.js for HLS streams (Chrome, Firefox, etc.)
      const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: false,
      })
      hlsRef.current = hls
      let networkRetries = 0
      const MAX_NETWORK_RETRIES = 3

      hls.loadSource(src)
      hls.attachMedia(video)

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        networkRetries = 0  // Reset retries on successful load
        if (autoPlay) {
          video.play().catch(() => {
            // Autoplay may be blocked by browser policy
          })
        }
      })

      hls.on(Hls.Events.FRAG_LOADED, () => {
        networkRetries = 0  // Reset retries on successful fragment load
      })

      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              networkRetries++
              if (networkRetries <= MAX_NETWORK_RETRIES) {
                console.log(`[VideoPlayer] HLS network error, retry ${networkRetries}/${MAX_NETWORK_RETRIES}`)
                hls.startLoad()
              } else {
                console.log('[VideoPlayer] HLS network error, max retries reached - stopping')
                setError('Network error: unable to load stream')
                onError?.('Network error: unable to load stream')
                hls.destroy()
                hlsRef.current = null
              }
              break
            case Hls.ErrorTypes.MEDIA_ERROR:
              // Try to recover from media error
              hls.recoverMediaError()
              break
            default:
              setError('Failed to load HLS stream')
              onError?.('Failed to load HLS stream')
              hls.destroy()
              hlsRef.current = null
              break
          }
        }
      })
    } else if (isHlsSource(src) && video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS support (Safari)
      video.src = src
      if (autoPlay) {
        video.play().catch(() => {})
      }
    } else {
      // Non-HLS source — use native playback
      video.src = src
      if (autoPlay) {
        video.play().catch(() => {})
      }
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy()
        hlsRef.current = null
      }
      // Stop the video element from making any further network requests
      video.pause()
      video.removeAttribute('src')
      video.load()
    }
  }, [currentSource?.src, isDownloadOnly, checkingStream, isHlsSource, autoPlay, onError])

  // Handle video events
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const handlePlay = () => setIsPlaying(true)
    const handlePause = () => setIsPlaying(false)
    const handleTimeUpdate = () => {
      setCurrentTime(video.currentTime)
      onTimeUpdate?.(video.currentTime, video.duration)
    }
    const handleLoadedMetadata = () => {
      setDuration(video.duration)
      setIsLoading(false)
      // Resume from start time if provided
      if (startTime > 0 && startTime < video.duration) {
        video.currentTime = startTime
      }
      
      // Check for audio track issues after metadata loads
      // Use audioTracks API if available (not all browsers support this)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const audioTracks = (video as any).audioTracks as { length: number } | undefined
      if (audioTracks !== undefined && typeof audioTracks.length === 'number') {
        // If the video has no audio tracks, there's likely an unsupported codec issue
        // Note: An empty audioTracks list usually means the browser can't decode the audio
        if (audioTracks.length === 0 && video.duration > 0) {
          // Video loaded but no audio tracks detected - likely unsupported codec
          console.log('[VideoPlayer] No audio tracks detected - possible unsupported codec')
          setAudioIssueDetected(true)
          onAudioIssue?.()
        }
      } else {
        // audioTracks not supported - schedule a delayed audio check
        // We'll check by examining the video element's webkitAudioDecodedByteCount if available
        // or just wait and see if the user notices no audio
        if (audioCheckTimeoutRef.current) {
          clearTimeout(audioCheckTimeoutRef.current)
        }
        audioCheckTimeoutRef.current = setTimeout(() => {
          // Check webkitAudioDecodedByteCount (Chrome/Safari) or mozAudioDecodedByteCount (Firefox)
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const extendedVideo = video as any
          const audioDecodedBytes = extendedVideo.webkitAudioDecodedByteCount ?? extendedVideo.mozDecodedFrames
          
          // If video is playing but no audio bytes decoded after 3 seconds, likely an issue
          if (audioDecodedBytes !== undefined && audioDecodedBytes === 0 && video.currentTime > 1) {
            console.log('[VideoPlayer] No audio bytes decoded - possible unsupported codec')
            setAudioIssueDetected(true)
            onAudioIssue?.()
          }
        }, 3000)
      }
    }
    const handleWaiting = () => setIsLoading(true)
    const handleCanPlay = () => setIsLoading(false)
    const handleEnded = () => {
      setIsPlaying(false)
      onEnded?.()
    }
    const handleError = () => {
      // Skip native error if HLS.js is handling the stream
      if (hlsRef.current) return
      const errorMessage = video.error?.message || 'Failed to load video'
      setError(errorMessage)
      onError?.(errorMessage)
    }
    const handleVolumeChange = () => {
      setVolume(video.volume)
      setIsMuted(video.muted)
    }
    
    video.addEventListener('play', handlePlay)
    video.addEventListener('pause', handlePause)
    video.addEventListener('timeupdate', handleTimeUpdate)
    video.addEventListener('loadedmetadata', handleLoadedMetadata)
    video.addEventListener('waiting', handleWaiting)
    video.addEventListener('canplay', handleCanPlay)
    video.addEventListener('ended', handleEnded)
    video.addEventListener('error', handleError)
    video.addEventListener('volumechange', handleVolumeChange)

    return () => {
      video.removeEventListener('play', handlePlay)
      video.removeEventListener('pause', handlePause)
      video.removeEventListener('timeupdate', handleTimeUpdate)
      video.removeEventListener('loadedmetadata', handleLoadedMetadata)
      video.removeEventListener('waiting', handleWaiting)
      video.removeEventListener('canplay', handleCanPlay)
      video.removeEventListener('ended', handleEnded)
      video.removeEventListener('error', handleError)
      video.removeEventListener('volumechange', handleVolumeChange)
      
      if (audioCheckTimeoutRef.current) {
        clearTimeout(audioCheckTimeoutRef.current)
      }
    }
  }, [onTimeUpdate, onEnded, onError, onAudioIssue, startTime, audioIssueDetected, isPlaying])

  // Cleanup video element on unmount to stop buffering
  useEffect(() => {
    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy()
        hlsRef.current = null
      }
      const video = videoRef.current
      if (video) {
        video.pause()
        video.removeAttribute('src')
        video.load()
      }
    }
  }, [])

  // Handle fullscreen changes
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement)
    }

    document.addEventListener('fullscreenchange', handleFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }, [])

  // Auto-hide controls
  const resetControlsTimeout = useCallback(() => {
    if (controlsTimeoutRef.current) {
      clearTimeout(controlsTimeoutRef.current)
    }
    setShowControls(true)
    
    if (isPlaying) {
      controlsTimeoutRef.current = setTimeout(() => {
        setShowControls(false)
      }, 3000)
    }
  }, [isPlaying])

  useEffect(() => {
    if (!isPlaying) {
      setShowControls(true)
    } else {
      resetControlsTimeout()
    }
    
    return () => {
      if (controlsTimeoutRef.current) {
        clearTimeout(controlsTimeoutRef.current)
      }
    }
  }, [isPlaying, resetControlsTimeout])

  // Player controls
  const togglePlay = () => {
    const video = videoRef.current
    if (!video) return

    if (isPlaying) {
      video.pause()
    } else {
      video.play()
    }
  }

  const toggleMute = () => {
    const video = videoRef.current
    if (!video) return
    video.muted = !video.muted
  }

  const handleVolumeChange = (values: number[]) => {
    const video = videoRef.current
    if (!video) return
    video.volume = values[0]
    video.muted = values[0] === 0
  }

  const handleSeek = (values: number[]) => {
    const video = videoRef.current
    if (!video) return
    video.currentTime = values[0]
  }

  const skip = (seconds: number) => {
    const video = videoRef.current
    if (!video) return
    video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + seconds))
  }

  const toggleFullscreen = async () => {
    const container = containerRef.current
    if (!container) return

    if (document.fullscreenElement) {
      await document.exitFullscreen()
    } else {
      await container.requestFullscreen()
    }
  }

  const changeSource = (index: number) => {
    const video = videoRef.current
    
    const currentPos = video?.currentTime || 0
    
    // Destroy existing HLS instance before switching
    if (hlsRef.current) {
      hlsRef.current.destroy()
      hlsRef.current = null
    }

    setCurrentSourceIndex(index)
    setError(null)
    setIsLoading(true)
    setIsDownloadOnly(false)
    setCheckingStream(true)
    
    // After source change, resume from same position (if video is available)
    if (video) {
      video.addEventListener('loadedmetadata', () => {
        video.currentTime = currentPos
        video.play().catch(() => {})
      }, { once: true })
    }
  }

  if (sources.length === 0) {
    return (
      <div className={cn('relative bg-black flex items-center justify-center aspect-video', className)}>
        <div className="text-center text-white/60">
          <AlertCircle className="h-12 w-12 mx-auto mb-2" />
          <p>No video source available</p>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative bg-black group',
        isFullscreen ? 'fixed inset-0 z-50' : 'aspect-video',
        className
      )}
      onMouseMove={resetControlsTimeout}
      onMouseLeave={() => isPlaying && setShowControls(false)}
    >
      <video
        ref={videoRef}
        poster={poster}
        playsInline
        className="w-full h-full object-contain"
        onClick={!isDownloadOnly && !checkingStream ? togglePlay : undefined}
      />

      {/* Loading Spinner - only show when not checking stream and not download-only */}
      {isLoading && !error && !checkingStream && !isDownloadOnly && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/50">
          <Loader2 className="h-12 w-12 text-white animate-spin" />
        </div>
      )}

      {/* Download-Only State - Show when URL is a force-download */}
      {isDownloadOnly && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/90">
          <div className="text-center text-white max-w-md px-6">
            <Download className="h-16 w-16 mx-auto mb-4 text-primary" />
            <h3 className="text-lg font-semibold mb-2">Download Only</h3>
            <p className="text-sm text-white/70 mb-6">
              This stream provider returned a download link instead of a streamable video. 
              You can download the file or open it in an external player.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
              <Button 
                variant="default" 
                className="bg-primary hover:bg-primary/90"
                asChild
              >
                <a href={currentSource.src} download target="_blank" rel="noopener noreferrer">
                  <Download className="mr-2 h-4 w-4" />
                  Download File
                </a>
              </Button>
              <Button 
                variant="outline" 
                className="border-white/20 text-white hover:bg-white/10"
                onClick={() => {
                  navigator.clipboard.writeText(currentSource.src)
                  setCopiedLink(true)
                  setTimeout(() => setCopiedLink(false), 2000)
                }}
              >
                {copiedLink ? (
                  <>
                    <Check className="mr-2 h-4 w-4 text-emerald-500" />
                    Copied!
                  </>
                ) : (
                  <>
                    <ExternalLink className="mr-2 h-4 w-4" />
                    Copy Link
                  </>
                )}
              </Button>
            </div>
            {sources.length > 1 && (
              <Button 
                variant="ghost" 
                size="sm" 
                className="mt-4 text-white/60 hover:text-white"
                onClick={() => changeSource((currentSourceIndex + 1) % sources.length)}
              >
                Try another source
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Checking Stream State */}
      {checkingStream && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/80">
          <div className="text-center text-white">
            <Loader2 className="h-12 w-12 mx-auto mb-2 animate-spin text-primary" />
            <p className="text-sm text-white/70">Checking stream...</p>
          </div>
        </div>
      )}

      {/* Error State (for non-download errors) */}
      {error && !isDownloadOnly && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/80">
          <div className="text-center text-white max-w-md px-6">
            <AlertCircle className="h-12 w-12 mx-auto mb-2 text-red-500" />
            <p className="text-sm font-medium">Failed to load video</p>
            <p className="text-xs text-white/60 mt-1">{error}</p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-3 mt-4">
              <Button 
                variant="outline" 
                size="sm"
                className="border-white/20 text-white hover:bg-white/10"
                asChild
              >
                <a href={currentSource.src} download target="_blank" rel="noopener noreferrer">
                  <Download className="mr-2 h-4 w-4" />
                  Try Download
                </a>
              </Button>
              {sources.length > 1 && (
                <Button 
                  variant="outline" 
                  size="sm"
                  className="border-white/20 text-white hover:bg-white/10"
                  onClick={() => changeSource((currentSourceIndex + 1) % sources.length)}
                >
                  Try another source
                </Button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Play button overlay when paused */}
      {!isPlaying && !isLoading && !error && !isDownloadOnly && !checkingStream && (
        <div 
          className="absolute inset-0 flex items-center justify-center cursor-pointer"
          onClick={togglePlay}
        >
          <div className="w-20 h-20 rounded-full bg-primary/80 flex items-center justify-center transition-transform hover:scale-110">
            <Play className="h-10 w-10 text-white fill-white ml-1" />
          </div>
        </div>
      )}

      {/* Controls - hide when download-only or checking */}
      {!isDownloadOnly && !checkingStream && (
      <div
        className={cn(
          'absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-4 transition-opacity duration-300',
          showControls ? 'opacity-100' : 'opacity-0 pointer-events-none'
        )}
      >
        {/* Progress bar */}
        <div className="mb-3">
          <Slider
            value={[currentTime]}
            max={duration || 100}
            step={1}
            onValueChange={handleSeek}
            className="cursor-pointer"
          />
          <div className="flex justify-between text-xs text-white/70 mt-1">
            <span>{formatTime(currentTime)}</span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>

        {/* Control buttons */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-white hover:bg-white/20"
              onClick={togglePlay}
            >
              {isPlaying ? (
                <Pause className="h-5 w-5" />
              ) : (
                <Play className="h-5 w-5" />
              )}
            </Button>
            
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-white hover:bg-white/20"
              onClick={() => skip(-10)}
            >
              <SkipBack className="h-4 w-4" />
            </Button>
            
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-white hover:bg-white/20"
              onClick={() => skip(10)}
            >
              <SkipForward className="h-4 w-4" />
            </Button>

            <div className="flex items-center gap-2 group/volume">
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-white hover:bg-white/20"
                onClick={toggleMute}
              >
                {isMuted || volume === 0 ? (
                  <VolumeX className="h-4 w-4" />
                ) : (
                  <Volume2 className="h-4 w-4" />
                )}
              </Button>
              <div className="w-0 overflow-hidden group-hover/volume:w-20 transition-all">
                <Slider
                  value={[isMuted ? 0 : volume]}
                  max={1}
                  step={0.1}
                  onValueChange={handleVolumeChange}
                  className="w-full"
                />
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Quality selector */}
            {sources.length > 1 && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 text-white hover:bg-white/20 text-xs"
                  >
                    <Settings className="h-4 w-4 mr-1" />
                    {currentSource.quality || currentSource.label || 'Quality'}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {sources.map((source, i) => (
                    <DropdownMenuItem
                      key={i}
                      onClick={() => changeSource(i)}
                      className={cn(i === currentSourceIndex && 'bg-primary/20')}
                    >
                      {source.quality || source.label || `Source ${i + 1}`}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            )}

            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-white hover:bg-white/20"
              onClick={toggleFullscreen}
            >
              {isFullscreen ? (
                <Minimize className="h-4 w-4" />
              ) : (
                <Maximize className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      </div>
      )}
    </div>
  )
}

