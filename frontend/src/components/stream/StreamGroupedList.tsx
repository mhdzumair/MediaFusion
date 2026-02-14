import { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { ChevronDown, ChevronRight, Film, Layers } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { CatalogStreamInfo } from '@/lib/api'

interface QualityGroup {
  name: string
  displayName: string
  streams: CatalogStreamInfo[]
  color: string
  icon: string
}

interface StreamGroupedListProps {
  streams: CatalogStreamInfo[]
  renderStream: (stream: CatalogStreamInfo, index: number) => React.ReactNode
  groupBy?: 'quality' | 'source' | 'none'
}

export function StreamGroupedList({
  streams,
  renderStream,
  groupBy = 'quality',
}: StreamGroupedListProps) {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set(['4K/UHD', '1080p']))

  const groups = useMemo(() => {
    if (groupBy === 'none') {
      return [{ name: 'all', displayName: 'All Streams', streams, color: '', icon: '' }]
    }

    if (groupBy === 'source') {
      // Group by source
      const sourceGroups: Record<string, CatalogStreamInfo[]> = {}
      
      streams.forEach((stream) => {
        const source = stream.source || 'Unknown Source'
        if (!sourceGroups[source]) {
          sourceGroups[source] = []
        }
        sourceGroups[source].push(stream)
      })

      return Object.entries(sourceGroups)
        .sort((a, b) => b[1].length - a[1].length) // Sort by count
        .map(([source, groupStreams]) => ({
          name: source,
          displayName: source,
          streams: groupStreams,
          color: 'bg-muted',
          icon: '📦',
        }))
    }

    // Group by quality tier
    const qualityGroups: QualityGroup[] = [
      { name: '4K/UHD', displayName: '4K / Ultra HD', streams: [], color: 'bg-primary/10 text-primary border-primary/20', icon: '🌟' },
      { name: '1080p', displayName: '1080p / Full HD', streams: [], color: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20', icon: '✨' },
      { name: '720p', displayName: '720p / HD', streams: [], color: 'bg-blue-500/10 text-blue-500 border-blue-500/20', icon: '📺' },
      { name: 'SD', displayName: 'SD / Other', streams: [], color: 'bg-muted text-muted-foreground', icon: '📼' },
    ]

    streams.forEach((stream) => {
      const resolution = (stream.resolution || '').toUpperCase()
      
      if (resolution.includes('4K') || resolution.includes('2160')) {
        qualityGroups[0].streams.push(stream)
      } else if (resolution.includes('1080')) {
        qualityGroups[1].streams.push(stream)
      } else if (resolution.includes('720')) {
        qualityGroups[2].streams.push(stream)
      } else {
        qualityGroups[3].streams.push(stream)
      }
    })

    // Filter out empty groups
    return qualityGroups.filter((g) => g.streams.length > 0)
  }, [streams, groupBy])

  const toggleGroup = (groupName: string) => {
    const newExpanded = new Set(expandedGroups)
    if (newExpanded.has(groupName)) {
      newExpanded.delete(groupName)
    } else {
      newExpanded.add(groupName)
    }
    setExpandedGroups(newExpanded)
  }

  const expandAll = () => {
    setExpandedGroups(new Set(groups.map((g) => g.name)))
  }

  const collapseAll = () => {
    setExpandedGroups(new Set())
  }

  if (groupBy === 'none' || groups.length === 1) {
    return (
      <div className="space-y-3">
        {streams.map((stream, index) => renderStream(stream, index))}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Expand/Collapse controls */}
      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={expandAll} className="text-xs h-7">
          Expand All
        </Button>
        <Button variant="ghost" size="sm" onClick={collapseAll} className="text-xs h-7">
          Collapse All
        </Button>
      </div>

      {groups.map((group) => (
        <Collapsible
          key={group.name}
          open={expandedGroups.has(group.name)}
          onOpenChange={() => toggleGroup(group.name)}
        >
          <CollapsibleTrigger asChild>
            <div className={`flex items-center justify-between p-3 rounded-xl border cursor-pointer hover:bg-muted/30 transition-colors ${group.color}`}>
              <div className="flex items-center gap-3">
                <span className="text-lg">{group.icon}</span>
                <div>
                  <h3 className="font-medium">{group.displayName}</h3>
                  <p className="text-xs text-muted-foreground">
                    {group.streams.length} stream{group.streams.length !== 1 ? 's' : ''} available
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-xs">
                  {group.streams.length}
                </Badge>
                {expandedGroups.has(group.name) ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </div>
            </div>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-3 space-y-3">
            {group.streams.map((stream, index) => renderStream(stream, index))}
          </CollapsibleContent>
        </Collapsible>
      ))}
    </div>
  )
}

// Quality Tier Badge Component
interface QualityTierBadgeProps {
  resolution?: string
  quality?: string
  codec?: string
  audio?: string
  hdr?: string[]
}

export function QualityTierBadge({ resolution, quality, codec, audio, hdr }: QualityTierBadgeProps) {
  const isHighQuality = resolution?.toLowerCase().includes('4k') || resolution?.toLowerCase().includes('2160')
  const isHD = resolution?.toLowerCase().includes('1080')
  
  // Determine tier color
  let tierClass = 'bg-muted text-muted-foreground'
  if (isHighQuality) {
    tierClass = 'bg-primary/20 text-primary dark:text-primary border-primary/30'
  } else if (isHD) {
    tierClass = 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {/* Resolution Badge */}
      {resolution && (
        <Badge variant="outline" className={`text-xs ${tierClass}`}>
          {resolution}
        </Badge>
      )}
      
      {/* Quality Badge */}
      {quality && (
        <Badge variant="outline" className="text-xs">
          {quality}
        </Badge>
      )}
      
      {/* Codec Badge */}
      {codec && (
        <Badge variant="outline" className="text-xs bg-primary/10 text-primary dark:text-primary border-primary/30">
          {codec}
        </Badge>
      )}
      
      {/* Audio Badge */}
      {audio && (
        <Badge variant="outline" className="text-xs bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/30">
          🎧 {audio}
        </Badge>
      )}
      
      {/* HDR Badges */}
      {hdr && hdr.length > 0 && hdr.map((format) => (
        <Badge 
          key={format} 
          variant="outline" 
          className="text-xs bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/30"
        >
          {format}
        </Badge>
      ))}
    </div>
  )
}

// View mode toggle component
export type ViewMode = 'list' | 'grouped'

interface ViewModeToggleProps {
  mode: ViewMode
  onModeChange: (mode: ViewMode) => void
}

export function ViewModeToggle({ mode, onModeChange }: ViewModeToggleProps) {
  return (
    <div className="flex items-center gap-1 bg-muted/50 rounded-lg p-1 border border-border/50">
      <Button
        variant={mode === 'list' ? 'default' : 'ghost'}
        size="sm"
        className={cn(
          'h-7 px-2.5 rounded-md transition-all',
          mode === 'list' 
            ? 'bg-primary hover:bg-primary/90 text-white shadow-sm' 
            : 'hover:bg-muted'
        )}
        onClick={() => onModeChange('list')}
      >
        <Layers className="h-3.5 w-3.5 mr-1.5" />
        List
      </Button>
      <Button
        variant={mode === 'grouped' ? 'default' : 'ghost'}
        size="sm"
        className={cn(
          'h-7 px-2.5 rounded-md transition-all',
          mode === 'grouped' 
            ? 'bg-primary hover:bg-primary/90 text-white shadow-sm' 
            : 'hover:bg-muted'
        )}
        onClick={() => onModeChange('grouped')}
      >
        <Film className="h-3.5 w-3.5 mr-1.5" />
        Grouped
      </Button>
    </div>
  )
}

