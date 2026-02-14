import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { api } from '@/lib/api'
import type { ContentType, TorrentSourceType, SportsCategory } from '@/lib/types'
import {
  Upload,
  Check,
  X,
  AlertCircle,
  Loader2,
  FileVideo,
  Link2,
  Film,
  Tv,
  Trophy,
  Play,
  Pause,
  RotateCcw,
  Search,
  Magnet,
  FileBox,
  RefreshCw,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { 
  guessContentTypeFromTitle, 
  guessSportsCategoryFromTitle,
  extractTitleFromMagnet,
} from '@/lib/content-detection'

interface TorrentItem {
  magnetLink: string
  title: string
  size?: string
  seeders?: number
  url?: string
  type?: 'magnet' | 'torrent'
}

interface BulkUploadData {
  torrents: TorrentItem[]
  sourceUrl: string
  pageTitle: string
  timestamp: number
}

type ItemStatus = 'pending' | 'analyzing' | 'importing' | 'success' | 'error' | 'warning' | 'skipped'

interface ProcessedItem {
  torrent: TorrentItem
  status: ItemStatus
  error?: string
  matchTitle?: string
  matchId?: string
  // Per-item content type
  contentType: ContentType
  detectedContentType: ContentType
  sportsCategory?: SportsCategory
  // Source type (magnet or torrent file)
  sourceType: TorrentSourceType
}

interface BulkUploadTabProps {
  bulkData: BulkUploadData
}

// Content type filter options
type ContentFilter = 'all' | ContentType
type SourceFilter = 'all' | TorrentSourceType

export function BulkUploadTab({ bulkData }: BulkUploadTabProps) {
  // Items state with content type detection
  const [items, setItems] = useState<ProcessedItem[]>([])
  
  // Filter state
  const [contentFilter, setContentFilter] = useState<ContentFilter>('all')
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all')
  const [textFilter, setTextFilter] = useState('')
  
  // Bulk action state
  const [bulkContentType, setBulkContentType] = useState<ContentType | ''>('')
  const [bulkImdbId, setBulkImdbId] = useState('')
  
  // Processing state
  const [autoImport, setAutoImport] = useState(true)
  const [isProcessing, setIsProcessing] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [currentIndex, setCurrentIndex] = useState(0)
  
  // Auto-scroll state
  const [autoScroll, setAutoScroll] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  // Initialize items from bulk data with auto-detection
  useEffect(() => {
    const processedItems: ProcessedItem[] = bulkData.torrents.map((torrent) => {
      // Extract title from magnet if available
      const title = torrent.magnetLink 
        ? extractTitleFromMagnet(torrent.magnetLink) || torrent.title
        : torrent.title
      
      // Auto-detect content type
      const detectedType = guessContentTypeFromTitle(title)
      const sportsCategory = detectedType === 'sports' 
        ? guessSportsCategoryFromTitle(title) || undefined 
        : undefined
      
      // Determine source type
      const sourceType: TorrentSourceType = torrent.type === 'torrent' || torrent.url?.endsWith('.torrent')
        ? 'torrent'
        : 'magnet'

      return {
        torrent: { ...torrent, title },
        status: 'pending' as ItemStatus,
        contentType: detectedType,
        detectedContentType: detectedType,
        sportsCategory,
        sourceType,
      }
    })
    setItems(processedItems)
  }, [bulkData])

  // Compute stats
  const stats = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        if (item.status === 'success') acc.success++
        else if (item.status === 'error') acc.error++
        else if (item.status === 'warning') acc.warning++
        else if (item.status === 'pending' || item.status === 'skipped') acc.pending++
        return acc
      },
      { success: 0, error: 0, warning: 0, pending: 0 }
    )
  }, [items])

  // Compute content type counts
  const contentCounts = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        acc[item.contentType]++
        return acc
      },
      { movie: 0, series: 0, sports: 0 } as Record<ContentType, number>
    )
  }, [items])

  // Compute source type counts
  const sourceCounts = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        acc[item.sourceType]++
        return acc
      },
      { magnet: 0, torrent: 0 } as Record<TorrentSourceType, number>
    )
  }, [items])

  // Filter items
  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      // Content filter
      if (contentFilter !== 'all' && item.contentType !== contentFilter) return false
      
      // Source filter
      if (sourceFilter !== 'all' && item.sourceType !== sourceFilter) return false
      
      // Text filter
      if (textFilter) {
        const searchLower = textFilter.toLowerCase()
        const titleMatch = item.torrent.title.toLowerCase().includes(searchLower)
        const matchTitleMatch = item.matchTitle?.toLowerCase().includes(searchLower)
        if (!titleMatch && !matchTitleMatch) return false
      }
      
      return true
    })
  }, [items, contentFilter, sourceFilter, textFilter])

  // Visible item indices (for bulk actions)
  const visibleIndices = useMemo(() => {
    return new Set(filteredItems.map((_, i) => items.indexOf(filteredItems[i])))
  }, [filteredItems, items])

  const updateItem = useCallback((index: number, updates: Partial<ProcessedItem>) => {
    setItems(prev => prev.map((item, i) => 
      i === index ? { ...item, ...updates } : item
    ))
  }, [])

  const updateItemContentType = useCallback((index: number, newType: ContentType) => {
    setItems(prev => prev.map((item, i) => {
      if (i !== index) return item
      const sportsCategory = newType === 'sports' 
        ? guessSportsCategoryFromTitle(item.torrent.title) || undefined
        : undefined
      return { ...item, contentType: newType, sportsCategory }
    }))
  }, [])

  // Auto-scroll to current processing item
  useEffect(() => {
    if (autoScroll && isProcessing) {
      const itemRef = itemRefs.current.get(currentIndex)
      if (itemRef) {
        itemRef.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }
  }, [currentIndex, autoScroll, isProcessing])

  const processItem = useCallback(async (index: number) => {
    const item = items[index]
    if (!item || item.status !== 'pending') return

    try {
      // Analyze
      updateItem(index, { status: 'analyzing' })
      const analysis = await api.analyzeMagnet(item.torrent.magnetLink, item.contentType)

      if (analysis.status === 'error') {
        updateItem(index, { status: 'error', error: analysis.error || 'Analysis failed' })
        return
      }

      // Get the best match
      const match = analysis.matches?.[0]
      if (!match && item.contentType !== 'sports') {
        updateItem(index, { status: 'error', error: 'No metadata match found' })
        return
      }

      const matchTitle = match?.title || analysis.parsed_title
      const matchId = bulkImdbId || match?.imdb_id || match?.id

      if (!autoImport) {
        updateItem(index, { 
          status: 'pending', 
          matchTitle, 
          matchId,
        })
        return
      }

      // Import
      updateItem(index, { status: 'importing', matchTitle, matchId })

      const getMetaId = () => {
        if (bulkImdbId) return bulkImdbId
        if (!match) return undefined
        if (match.imdb_id) return match.imdb_id
        if (match.tmdb_id) return `tmdb:${match.tmdb_id}`
        if (match.mal_id) return `mal:${match.mal_id}`
        if (match.kitsu_id) return `kitsu:${match.kitsu_id}`
        return match.id
      }

      const result = await api.importMagnet({
        magnet_link: item.torrent.magnetLink,
        meta_type: item.contentType,
        meta_id: getMetaId(),
        title: matchTitle,
        resolution: analysis.resolution,
        quality: analysis.quality,
        codec: analysis.codec,
        audio: analysis.audio?.join(','),
        hdr: analysis.hdr?.join(','),
        languages: analysis.languages?.join(','),
        sports_category: item.sportsCategory,
      })

      if (result.status === 'success' || result.status === 'processing') {
        updateItem(index, { status: 'success', matchTitle, matchId })
      } else if (result.status === 'warning') {
        updateItem(index, { status: 'warning', error: result.message, matchTitle, matchId })
      } else {
        updateItem(index, { status: 'error', error: result.message, matchTitle, matchId })
      }
    } catch (error) {
      updateItem(index, { 
        status: 'error', 
        error: error instanceof Error ? error.message : 'Processing failed' 
      })
    }
  }, [items, autoImport, bulkImdbId, updateItem])

  const startProcessing = async () => {
    setIsProcessing(true)
    setIsPaused(false)

    for (let i = currentIndex; i < items.length; i++) {
      if (isPaused) {
        setCurrentIndex(i)
        break
      }
      
      if (items[i].status === 'pending') {
        setCurrentIndex(i)
        await processItem(i)
        // Small delay between items
        await new Promise(resolve => setTimeout(resolve, 500))
      }
    }

    setIsProcessing(false)
  }

  const togglePause = () => {
    setIsPaused(!isPaused)
  }

  const resetAll = () => {
    setItems(prev => prev.map(item => ({
      ...item,
      status: 'pending' as ItemStatus,
      error: undefined,
      matchTitle: undefined,
      matchId: undefined,
    })))
    setCurrentIndex(0)
    setIsProcessing(false)
    setIsPaused(false)
  }

  const retryItem = async (index: number) => {
    updateItem(index, { status: 'pending', error: undefined })
    await processItem(index)
  }

  const toggleItemSkip = (index: number) => {
    const item = items[index]
    if (item.status === 'pending') {
      updateItem(index, { status: 'skipped' })
    } else if (item.status === 'skipped') {
      updateItem(index, { status: 'pending' })
    }
  }

  const selectAllVisible = () => {
    setItems(prev => prev.map((item, i) => 
      visibleIndices.has(i) && item.status === 'skipped'
        ? { ...item, status: 'pending' as ItemStatus }
        : item
    ))
  }

  const deselectAll = () => {
    setItems(prev => prev.map(item => 
      item.status === 'pending'
        ? { ...item, status: 'skipped' as ItemStatus }
        : item
    ))
  }

  const applyBulkContentType = () => {
    if (!bulkContentType) return
    setItems(prev => prev.map((item, i) => {
      if (!visibleIndices.has(i)) return item
      const sportsCategory = bulkContentType === 'sports'
        ? guessSportsCategoryFromTitle(item.torrent.title) || undefined
        : undefined
      return { ...item, contentType: bulkContentType, sportsCategory }
    }))
    setBulkContentType('')
  }

  const progress = items.length > 0 
    ? ((items.filter(i => i.status === 'success' || i.status === 'error' || i.status === 'warning').length) / items.length) * 100
    : 0

  const pendingCount = filteredItems.filter(i => i.status === 'pending').length

  return (
    <div className="space-y-3">
      {/* Header Info */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <FileVideo className="h-4 w-4" />
            Bulk Upload
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Link2 className="h-3 w-3" />
            <span className="truncate" title={bulkData.sourceUrl}>
              {bulkData.pageTitle || bulkData.sourceUrl}
            </span>
          </div>
          
          <div className="flex flex-wrap gap-1">
            <Badge variant="outline" className="text-[10px]">{items.length} total</Badge>
            <Badge variant="secondary" className="bg-green-500/20 text-green-500 text-[10px]">
              {stats.success} done
            </Badge>
            {stats.warning > 0 && (
              <Badge variant="secondary" className="bg-yellow-500/20 text-yellow-500 text-[10px]">
                {stats.warning} warnings
              </Badge>
            )}
            {stats.error > 0 && (
              <Badge variant="secondary" className="bg-red-500/20 text-red-500 text-[10px]">
                {stats.error} failed
              </Badge>
            )}
          </div>

          {isProcessing && (
            <Progress value={progress} className="h-1.5" />
          )}
        </CardContent>
      </Card>

      {/* Filters */}
      <Card>
        <CardContent className="pt-3 space-y-2">
          {/* Content Type Filter */}
          <div className="flex flex-wrap gap-1">
            <Button
              size="sm"
              variant={contentFilter === 'all' ? 'default' : 'outline'}
              onClick={() => setContentFilter('all')}
              className="h-6 px-2 text-[10px]"
            >
              All ({items.length})
            </Button>
            <Button
              size="sm"
              variant={contentFilter === 'movie' ? 'default' : 'outline'}
              onClick={() => setContentFilter('movie')}
              className="h-6 px-2 text-[10px]"
            >
              <Film className="h-2.5 w-2.5 mr-1" />
              Movies ({contentCounts.movie})
            </Button>
            <Button
              size="sm"
              variant={contentFilter === 'series' ? 'default' : 'outline'}
              onClick={() => setContentFilter('series')}
              className="h-6 px-2 text-[10px]"
            >
              <Tv className="h-2.5 w-2.5 mr-1" />
              Series ({contentCounts.series})
            </Button>
            <Button
              size="sm"
              variant={contentFilter === 'sports' ? 'default' : 'outline'}
              onClick={() => setContentFilter('sports')}
              className="h-6 px-2 text-[10px]"
            >
              <Trophy className="h-2.5 w-2.5 mr-1" />
              Sports ({contentCounts.sports})
            </Button>
          </div>

          {/* Source Type Filter */}
          <div className="flex gap-1">
            <Button
              size="sm"
              variant={sourceFilter === 'all' ? 'secondary' : 'ghost'}
              onClick={() => setSourceFilter('all')}
              className="h-5 px-2 text-[9px]"
            >
              All
            </Button>
            <Button
              size="sm"
              variant={sourceFilter === 'magnet' ? 'secondary' : 'ghost'}
              onClick={() => setSourceFilter('magnet')}
              className="h-5 px-2 text-[9px]"
            >
              <Magnet className="h-2 w-2 mr-1" />
              Magnets ({sourceCounts.magnet})
            </Button>
            <Button
              size="sm"
              variant={sourceFilter === 'torrent' ? 'secondary' : 'ghost'}
              onClick={() => setSourceFilter('torrent')}
              className="h-5 px-2 text-[9px]"
            >
              <FileBox className="h-2 w-2 mr-1" />
              Torrents ({sourceCounts.torrent})
            </Button>
          </div>

          {/* Text Search */}
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
            <Input
              value={textFilter}
              onChange={(e) => setTextFilter(e.target.value)}
              placeholder="Search by name..."
              className="h-7 pl-7 text-xs"
            />
            {textFilter && (
              <Button
                variant="ghost"
                size="sm"
                className="absolute right-1 top-1/2 -translate-y-1/2 h-5 w-5 p-0"
                onClick={() => setTextFilter('')}
              >
                <X className="h-3 w-3" />
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Bulk Actions */}
      <Card>
        <CardContent className="pt-3 space-y-2">
          {/* IMDb ID for all */}
          <div className="flex gap-2">
            <Input
              value={bulkImdbId}
              onChange={(e) => setBulkImdbId(e.target.value)}
              placeholder="IMDb ID for all (tt1234567)"
              className="h-7 text-xs flex-1"
              pattern="tt\d{7,8}"
            />
          </div>

          {/* Bulk content type change */}
          <div className="flex gap-2">
            <Select value={bulkContentType} onValueChange={(v) => setBulkContentType(v as ContentType | '')}>
              <SelectTrigger className="h-7 text-xs flex-1">
                <SelectValue placeholder="Change type for visible..." />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="movie">Movie</SelectItem>
                <SelectItem value="series">Series</SelectItem>
                <SelectItem value="sports">Sports</SelectItem>
              </SelectContent>
            </Select>
            <Button
              size="sm"
              variant="secondary"
              onClick={applyBulkContentType}
              disabled={!bulkContentType}
              className="h-7 text-xs"
            >
              Apply to Visible
            </Button>
          </div>

          {/* Selection actions */}
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={selectAllVisible}
              className="h-6 text-[10px] flex-1"
            >
              Select Visible ({filteredItems.length})
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={deselectAll}
              className="h-6 text-[10px] flex-1"
            >
              Deselect All
            </Button>
          </div>

          {/* Auto import toggle */}
          <div className="flex items-center justify-between">
            <Label className="text-xs">Auto import after analysis</Label>
            <Switch
              checked={autoImport}
              onCheckedChange={setAutoImport}
              disabled={isProcessing}
            />
          </div>

          {/* Auto scroll toggle */}
          <div className="flex items-center justify-between">
            <Label className="text-xs">Auto-scroll to current</Label>
            <Switch
              checked={autoScroll}
              onCheckedChange={setAutoScroll}
            />
          </div>
        </CardContent>
      </Card>

      {/* Torrent List */}
      <ScrollArea className="h-[200px] rounded-lg border" ref={scrollRef}>
        <div className="p-2 space-y-1">
          {filteredItems.map((item) => {
            const actualIndex = items.indexOf(item)
            return (
              <TorrentItemRow
                key={actualIndex}
                ref={(el) => {
                  if (el) itemRefs.current.set(actualIndex, el)
                  else itemRefs.current.delete(actualIndex)
                }}
                item={item}
                displayIndex={filteredItems.indexOf(item)}
                isCurrentlyProcessing={isProcessing && currentIndex === actualIndex}
                onToggleSkip={() => toggleItemSkip(actualIndex)}
                onRetry={() => retryItem(actualIndex)}
                onContentTypeChange={(type) => updateItemContentType(actualIndex, type)}
                disabled={isProcessing}
              />
            )
          })}
          {filteredItems.length === 0 && (
            <div className="text-center text-xs text-muted-foreground py-4">
              No torrents match the current filters
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Actions */}
      <div className="flex gap-2">
        {!isProcessing ? (
          <>
            <Button
              onClick={startProcessing}
              disabled={pendingCount === 0}
              className="flex-1"
            >
              <Upload className="h-4 w-4 mr-2" />
              Start Upload ({pendingCount})
            </Button>
            {(stats.success > 0 || stats.error > 0) && (
              <Button variant="outline" onClick={resetAll}>
                <RotateCcw className="h-4 w-4" />
              </Button>
            )}
          </>
        ) : (
          <>
            <Button
              onClick={togglePause}
              variant={isPaused ? 'default' : 'secondary'}
              className="flex-1"
            >
              {isPaused ? (
                <>
                  <Play className="h-4 w-4 mr-2" />
                  Resume
                </>
              ) : (
                <>
                  <Pause className="h-4 w-4 mr-2" />
                  Pause
                </>
              )}
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

interface TorrentItemRowProps {
  item: ProcessedItem
  displayIndex: number
  isCurrentlyProcessing: boolean
  onToggleSkip: () => void
  onRetry: () => void
  onContentTypeChange: (type: ContentType) => void
  disabled: boolean
}

const TorrentItemRow = ({ 
  item, 
  displayIndex,
  isCurrentlyProcessing, 
  onToggleSkip, 
  onRetry,
  onContentTypeChange,
  disabled,
}: TorrentItemRowProps & { ref?: React.Ref<HTMLDivElement> }) => {
  const statusIcons = {
    pending: null,
    skipped: <X className="h-3 w-3 text-muted-foreground" />,
    analyzing: <Loader2 className="h-3 w-3 animate-spin text-blue-500" />,
    importing: <Loader2 className="h-3 w-3 animate-spin text-primary" />,
    success: <Check className="h-3 w-3 text-green-500" />,
    warning: <AlertCircle className="h-3 w-3 text-yellow-500" />,
    error: <AlertCircle className="h-3 w-3 text-red-500" />,
  }

  const contentTypeIcons = {
    movie: Film,
    series: Tv,
    sports: Trophy,
  }

  const ContentIcon = contentTypeIcons[item.contentType]
  const isDetectedDifferent = item.contentType !== item.detectedContentType

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 p-1.5 rounded text-xs transition-colors",
        isCurrentlyProcessing && "bg-primary/10 border border-primary/30 ring-2 ring-primary/20",
        item.status === 'success' && "bg-green-500/5",
        item.status === 'warning' && "bg-yellow-500/5",
        item.status === 'error' && "bg-red-500/5",
        item.status === 'skipped' && "opacity-40"
      )}
    >
      {/* Index */}
      <span className="text-muted-foreground w-4 text-[10px] flex-shrink-0">{displayIndex + 1}.</span>
      
      {/* Status icon */}
      <div className="w-4 h-4 flex items-center justify-center flex-shrink-0">
        {statusIcons[item.status]}
      </div>

      {/* Content type selector */}
      <Select 
        value={item.contentType} 
        onValueChange={onContentTypeChange}
        disabled={disabled || item.status === 'success'}
      >
        <SelectTrigger className="h-5 w-16 text-[9px] px-1 flex-shrink-0">
          <ContentIcon className="h-2.5 w-2.5 mr-0.5" />
          <span className="truncate">{item.contentType}</span>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="movie" className="text-xs">
            <span className="flex items-center gap-1">
              <Film className="h-3 w-3" />Movie
            </span>
          </SelectItem>
          <SelectItem value="series" className="text-xs">
            <span className="flex items-center gap-1">
              <Tv className="h-3 w-3" />Series
            </span>
          </SelectItem>
          <SelectItem value="sports" className="text-xs">
            <span className="flex items-center gap-1">
              <Trophy className="h-3 w-3" />Sports
            </span>
          </SelectItem>
        </SelectContent>
      </Select>

      {/* Title and info */}
      <div className="flex-1 min-w-0">
        <p className="truncate font-medium text-[11px]" title={item.torrent.title}>
          {item.matchTitle || item.torrent.title}
        </p>
        <div className="flex items-center gap-1">
          {item.error && (
            <p className="text-red-500 truncate text-[9px]" title={item.error}>
              {item.error}
            </p>
          )}
          {item.matchId && !item.error && (
            <p className="text-muted-foreground text-[9px]">{item.matchId}</p>
          )}
          {isDetectedDifferent && (
            <Badge variant="outline" className="text-[8px] h-3 px-1 text-yellow-500 border-yellow-500/30">
              was: {item.detectedContentType}
            </Badge>
          )}
        </div>
      </div>

      {/* Size badge */}
      {item.torrent.size && (
        <Badge variant="outline" className="text-[9px] h-4 flex-shrink-0">
          {item.torrent.size}
        </Badge>
      )}

      {/* Source type indicator */}
      <div className="flex-shrink-0" title={item.sourceType}>
        {item.sourceType === 'magnet' ? (
          <Magnet className="h-3 w-3 text-muted-foreground" />
        ) : (
          <FileBox className="h-3 w-3 text-muted-foreground" />
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-0.5 flex-shrink-0">
        {(item.status === 'error' || item.status === 'warning') && !disabled && (
          <Button
            variant="ghost"
            size="sm"
            className="h-5 w-5 p-0"
            onClick={onRetry}
            title="Retry"
          >
            <RefreshCw className="h-3 w-3" />
          </Button>
        )}
        {(item.status === 'pending' || item.status === 'skipped') && !disabled && (
          <Button
            variant="ghost"
            size="sm"
            className="h-5 w-5 p-0"
            onClick={onToggleSkip}
            title={item.status === 'skipped' ? 'Include' : 'Skip'}
          >
            {item.status === 'skipped' ? (
              <RotateCcw className="h-3 w-3" />
            ) : (
              <X className="h-3 w-3" />
            )}
          </Button>
        )}
      </div>
    </div>
  )
}
