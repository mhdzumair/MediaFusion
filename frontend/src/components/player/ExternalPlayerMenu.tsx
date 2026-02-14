import { useState, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { 
  ExternalLink,
  MonitorPlay,
  Tv,
  PlaySquare,
  Smartphone,
  Copy,
  Check,
  AlertCircle,
} from 'lucide-react'
import { useToast } from '@/hooks'

interface ExternalPlayerMenuProps {
  streamUrl: string
  className?: string
  variant?: 'default' | 'prominent'  // 'prominent' shows a bigger CTA style button
}

interface ExternalPlayer {
  name: string
  icon: React.ReactNode
  protocol: string
  description: string
  platforms: ('desktop' | 'mobile' | 'tv')[]
  requiresHandler?: boolean  // True if requires a custom protocol handler to be installed
}

const EXTERNAL_PLAYERS: ExternalPlayer[] = [
  {
    name: 'VLC',
    icon: <PlaySquare className="h-4 w-4" />,
    protocol: 'vlc://',
    description: 'VLC Media Player',
    platforms: ['desktop'],
    requiresHandler: true,  // Requires vlc-protocol handler
  },
  {
    name: 'Infuse',
    icon: <Tv className="h-4 w-4" />,
    protocol: 'infuse://x-callback-url/play?url=',
    description: 'Infuse (Apple TV, iOS, Mac)',
    platforms: ['desktop', 'mobile', 'tv'],
  },
  {
    name: 'mpv',
    icon: <MonitorPlay className="h-4 w-4" />,
    protocol: 'mpv://',
    description: 'mpv (requires mpv-handler)',
    platforms: ['desktop'],
    requiresHandler: true,  // Requires mpv-handler
  },
  {
    name: 'IINA',
    icon: <MonitorPlay className="h-4 w-4" />,
    protocol: 'iina://weblink?url=',
    description: 'IINA (macOS)',
    platforms: ['desktop'],
  },
  {
    name: 'Outplayer',
    icon: <Smartphone className="h-4 w-4" />,
    protocol: 'outplayer://',
    description: 'Outplayer (iOS)',
    platforms: ['mobile'],
  },
  {
    name: 'nPlayer',
    icon: <Smartphone className="h-4 w-4" />,
    protocol: 'nplayer-',
    description: 'nPlayer (iOS, Android)',
    platforms: ['mobile'],
  },
]

function buildPlayerUrl(player: ExternalPlayer, streamUrl: string): string {
  // Different players have different URL encoding requirements
  switch (player.name) {
    case 'VLC':
      return `${player.protocol}${streamUrl}`
    case 'Infuse':
      return `${player.protocol}${encodeURIComponent(streamUrl)}`
    case 'mpv':
      // mpv-handler expects the URL without encoding
      return `${player.protocol}${streamUrl}`
    case 'IINA':
      return `${player.protocol}${encodeURIComponent(streamUrl)}`
    case 'Outplayer':
      return `${player.protocol}${streamUrl}`
    case 'nPlayer':
      return `${player.protocol}${streamUrl}`
    default:
      return `${player.protocol}${streamUrl}`
  }
}

export function ExternalPlayerMenu({ streamUrl, className, variant = 'default' }: ExternalPlayerMenuProps) {
  const [copied, setCopied] = useState(false)
  const { toast } = useToast()
  
  const handleCopyUrl = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(streamUrl)
      setCopied(true)
      toast({
        title: 'URL Copied',
        description: 'Stream URL copied to clipboard. Paste it into your preferred player.',
      })
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast({
        title: 'Copy Failed',
        description: 'Unable to copy URL. Please try again.',
        variant: 'destructive',
      })
    }
  }, [streamUrl, toast])
  
  const handlePlayerClick = useCallback((player: ExternalPlayer) => {
    const url = buildPlayerUrl(player, streamUrl)
    
    // Use iframe-based approach for more reliable protocol handling
    // This is less likely to be blocked than link.click() or window.open()
    const iframe = document.createElement('iframe')
    iframe.style.display = 'none'
    document.body.appendChild(iframe)
    
    // Set a timeout to detect if the protocol handler didn't open anything
    const startTime = Date.now()
    let protocolOpened = false
    
    const handleBlur = () => {
      protocolOpened = true
    }
    window.addEventListener('blur', handleBlur)
    
    // Try to open the protocol URL via iframe
    try {
      if (iframe.contentWindow) {
        iframe.contentWindow.location.href = url
      }
    } catch {
      // Some browsers may throw on protocol URLs in iframes
      // Fall back to direct location change
      window.location.href = url
    }
    
    // After a short delay, check if we should show a help message
    setTimeout(() => {
      window.removeEventListener('blur', handleBlur)
      document.body.removeChild(iframe)
      
      const elapsed = Date.now() - startTime
      
      // If the window didn't lose focus within 500ms, the protocol probably didn't work
      if (!protocolOpened && elapsed < 600) {
        const handlerNote = player.requiresHandler 
          ? ` ${player.name} requires a protocol handler to be installed.`
          : ''
        
        toast({
          title: `${player.name} may not have opened`,
          description: `If the player didn't launch, try using "Copy URL" and paste it into your player.${handlerNote}`,
          action: (
            <Button variant="outline" size="sm" onClick={handleCopyUrl}>
              <Copy className="mr-1 h-3 w-3" />
              Copy URL
            </Button>
          ),
        })
      }
    }, 500)
  }, [streamUrl, toast, handleCopyUrl])

  const buttonClass = variant === 'prominent' 
    ? "bg-primary/20 hover:bg-primary/30 text-primary border border-primary/30"
    : className || "text-white/70 hover:text-white hover:bg-white/10"

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant={variant === 'prominent' ? 'outline' : 'ghost'}
          size="sm"
          className={buttonClass}
        >
          <ExternalLink className="mr-2 h-4 w-4" />
          External Player
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Open in External Player</DropdownMenuLabel>
        <DropdownMenuSeparator />
        
        {/* Copy URL - Primary/most reliable option */}
        <DropdownMenuItem
          onClick={handleCopyUrl}
          className="flex items-center gap-2 cursor-pointer bg-muted/50"
        >
          {copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
          <div className="flex-1">
            <p className="text-sm font-medium">Copy URL</p>
            <p className="text-xs text-muted-foreground">Paste into any player (most reliable)</p>
          </div>
        </DropdownMenuItem>
        
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs text-muted-foreground font-normal">
          Quick Launch (requires app installed)
        </DropdownMenuLabel>
        
        {EXTERNAL_PLAYERS.map((player) => (
          <DropdownMenuItem
            key={player.name}
            onClick={() => handlePlayerClick(player)}
            className="flex items-center gap-2 cursor-pointer"
          >
            {player.icon}
            <div className="flex-1">
              <p className="text-sm">{player.name}</p>
              <p className="text-xs text-muted-foreground">{player.description}</p>
            </div>
            {player.requiresHandler && (
              <span title="Requires protocol handler">
                <AlertCircle className="h-3 w-3 text-muted-foreground" />
              </span>
            )}
          </DropdownMenuItem>
        ))}
        
        <DropdownMenuSeparator />
        <div className="px-2 py-1.5">
          <p className="text-xs text-muted-foreground">
            Tip: If quick launch doesn't work, copy the URL and paste it into your player.
          </p>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

