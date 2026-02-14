import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { ExternalLink, Copy, Check } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import type { ExternalIds } from '@/lib/api'

// Provider configuration with colors, URLs, and display info
const PROVIDER_CONFIG = {
  imdb: {
    name: 'IMDb',
    color: 'bg-primary/20 text-primary dark:text-primary border-primary/30',
    urlTemplate: 'https://www.imdb.com/title/{id}',
    icon: '🎬',
  },
  tmdb: {
    name: 'TMDB',
    color: 'bg-green-500/20 text-green-600 dark:text-green-400 border-green-500/30',
    // URL differs by type - handled in getUrl
    urlTemplate: 'https://www.themoviedb.org/{type}/{id}',
    icon: '🎞️',
  },
  tvdb: {
    name: 'TVDB',
    color: 'bg-blue-500/20 text-blue-600 dark:text-blue-400 border-blue-500/30',
    urlTemplate: 'https://www.thetvdb.com/dereferrer/{type}/{id}',
    icon: '📺',
  },
  mal: {
    name: 'MAL',
    color: 'bg-primary/20 text-primary dark:text-primary border-primary/30',
    urlTemplate: 'https://myanimelist.net/anime/{id}',
    icon: '🎌',
  },
  kitsu: {
    name: 'Kitsu',
    color: 'bg-orange-500/20 text-orange-600 dark:text-orange-400 border-orange-500/30',
    urlTemplate: 'https://kitsu.io/anime/{id}',
    icon: '🦊',
  },
} as const

type ProviderKey = keyof typeof PROVIDER_CONFIG

interface ExternalIdsDisplayProps {
  externalIds?: ExternalIds
  mediaType?: 'movie' | 'series' | 'tv'
  className?: string
  compact?: boolean
}

function getProviderUrl(provider: ProviderKey, id: string, mediaType?: string): string {
  const config = PROVIDER_CONFIG[provider]
  let url: string = config.urlTemplate
  
  // Handle TMDB type-specific URLs
  if (provider === 'tmdb') {
    const type = mediaType === 'movie' ? 'movie' : 'tv'
    url = url.replace('{type}', type)
  }
  if (provider === 'tvdb') {
    url = url.replace('{type}', mediaType === 'movie' ? 'series' : 'series')
  }
  
  return url.replace('{id}', id)
}

function ExternalIdBadge({ 
  provider, 
  id, 
  mediaType,
  compact = false,
}: { 
  provider: ProviderKey
  id: string
  mediaType?: string
  compact?: boolean
}) {
  const [copied, setCopied] = useState(false)
  const config = PROVIDER_CONFIG[provider]
  const url = getProviderUrl(provider, id, mediaType)
  
  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    await navigator.clipboard.writeText(id)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border font-mono text-xs transition-all hover:scale-105",
              config.color,
              compact && "px-1.5"
            )}
          >
            {!compact && <span className="text-[10px]">{config.icon}</span>}
            <span className="font-semibold">{config.name}</span>
            <span className="opacity-80">{compact ? id.slice(-6) : id}</span>
            <ExternalLink className="h-3 w-3 opacity-60" />
          </a>
        </TooltipTrigger>
        <TooltipContent className="flex items-center gap-2">
          <span>Open {config.name}: {id}</span>
          <button
            onClick={handleCopy}
            className="p-1 hover:bg-muted rounded"
          >
            {copied ? (
              <Check className="h-3 w-3 text-green-500" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
          </button>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export function ExternalIdsDisplay({ 
  externalIds, 
  mediaType,
  className,
  compact = false,
}: ExternalIdsDisplayProps) {
  if (!externalIds) return null
  
  const providers: { key: ProviderKey; id: string | null | undefined }[] = [
    { key: 'imdb', id: externalIds.imdb },
    { key: 'tmdb', id: externalIds.tmdb },
    { key: 'tvdb', id: externalIds.tvdb },
    { key: 'mal', id: externalIds.mal },
    { key: 'kitsu', id: externalIds.kitsu },
  ]
  
  const availableProviders = providers.filter(p => p.id)
  
  if (availableProviders.length === 0) {
    return (
      <div className={cn("text-xs text-muted-foreground", className)}>
        No external IDs available
      </div>
    )
  }
  
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {availableProviders.map(({ key, id }) => (
        <ExternalIdBadge
          key={key}
          provider={key}
          id={id!}
          mediaType={mediaType}
          compact={compact}
        />
      ))}
    </div>
  )
}

// Compact version for cards
export function ExternalIdsBadges({ 
  externalIds,
  mediaType,
}: { 
  externalIds?: ExternalIds
  mediaType?: 'movie' | 'series' | 'tv'
}) {
  return <ExternalIdsDisplay externalIds={externalIds} mediaType={mediaType} compact />
}

