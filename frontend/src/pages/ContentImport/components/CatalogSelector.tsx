import { useState, useMemo } from 'react'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Folder, Film, Tv, Trophy, Check, ChevronsUpDown } from 'lucide-react'
import { useAvailableCatalogs } from '@/hooks'
import type { ContentType } from '@/lib/constants'
import type { CatalogInfo } from '@/lib/api'
import { cn } from '@/lib/utils'

interface CatalogSelectorProps {
  contentType: ContentType
  selectedCatalogs: string[]
  onChange: (catalogs: string[]) => void
  quality?: string // Some catalogs depend on quality
  className?: string
  compact?: boolean
}

// Quality-based catalog suggestions for movies
const QUALITY_CATALOG_MAP: Record<string, string[]> = {
  'BluRay REMUX': ['mediafusion_movies_hdrip', 'mediafusion_movies_blurayrip'],
  BluRay: ['mediafusion_movies_blurayrip', 'mediafusion_movies_hdrip'],
  'WEB-DL': ['mediafusion_movies_webdl', 'mediafusion_movies_webrip'],
  WEBRip: ['mediafusion_movies_webrip', 'mediafusion_movies_webdl'],
  HDRip: ['mediafusion_movies_hdrip'],
  DVDRip: ['mediafusion_movies_dvdrip'],
  HDTV: ['mediafusion_movies_hdtv'],
  CAM: ['mediafusion_movies_tcrip', 'mediafusion_movies_predvd'],
  TeleSync: ['mediafusion_movies_tcrip', 'mediafusion_movies_predvd'],
}

export function CatalogSelector({
  contentType,
  selectedCatalogs,
  onChange,
  quality,
  className,
  compact = false,
}: CatalogSelectorProps) {
  const [open, setOpen] = useState(false)
  const { data: availableCatalogs, isLoading } = useAvailableCatalogs()

  // Get catalogs based on content type
  const filteredCatalogs = useMemo((): CatalogInfo[] => {
    if (!availableCatalogs) return []

    switch (contentType) {
      case 'movie':
        return availableCatalogs.movies || []
      case 'series':
        return availableCatalogs.series || []
      case 'sports':
        // Sports might use series or a separate category
        return availableCatalogs.series || []
      default:
        return []
    }
  }, [availableCatalogs, contentType])

  // Get suggested catalogs based on quality
  const suggestedCatalogs = useMemo(() => {
    if (!quality || contentType !== 'movie') return []
    return QUALITY_CATALOG_MAP[quality] || []
  }, [quality, contentType])

  const handleToggle = (catalogName: string) => {
    const newSelection = selectedCatalogs.includes(catalogName)
      ? selectedCatalogs.filter((c: string) => c !== catalogName)
      : [...selectedCatalogs, catalogName]
    onChange(newSelection)
  }

  const handleSelectAll = () => {
    onChange(filteredCatalogs.map((c: CatalogInfo) => c.name))
  }

  const handleClear = () => {
    onChange([])
  }

  const handleSelectSuggested = () => {
    if (suggestedCatalogs.length > 0) {
      const availableNames = filteredCatalogs.map((c: CatalogInfo) => c.name)
      const validSuggestions = suggestedCatalogs.filter((name: string) => availableNames.includes(name))
      onChange(validSuggestions)
    }
  }

  const ContentTypeIcon = contentType === 'movie' ? Film : contentType === 'series' ? Tv : Trophy

  if (isLoading) {
    return (
      <div className={cn('space-y-1.5', className)}>
        <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
          <Folder className="h-3 w-3" />
          Catalogs
        </Label>
        <Skeleton className="h-9 w-full rounded-lg" />
      </div>
    )
  }

  return (
    <div className={cn('space-y-1.5', className)}>
      <Label className="text-xs text-muted-foreground flex items-center gap-1.5">
        <Folder className="h-3 w-3" />
        Catalogs
        {selectedCatalogs.length > 0 && (
          <Badge variant="secondary" className="text-[10px] h-4 ml-1">
            {selectedCatalogs.length} selected
          </Badge>
        )}
      </Label>

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className={cn('w-full justify-between rounded-lg font-normal', compact && 'h-8 text-xs')}
          >
            <span className="flex items-center gap-2 truncate">
              <ContentTypeIcon className="h-4 w-4 text-muted-foreground" />
              {selectedCatalogs.length > 0 ? (
                <span className="truncate">
                  {selectedCatalogs.length === 1 ? selectedCatalogs[0] : `${selectedCatalogs.length} catalogs selected`}
                </span>
              ) : (
                <span className="text-muted-foreground">Select catalogs...</span>
              )}
            </span>
            <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[300px] p-0" align="start">
          <div className="p-2 border-b space-y-2">
            <div className="flex gap-2">
              <Button variant="outline" size="sm" className="flex-1 h-7 text-xs" onClick={handleSelectAll}>
                Select All
              </Button>
              <Button variant="outline" size="sm" className="flex-1 h-7 text-xs" onClick={handleClear}>
                Clear
              </Button>
            </div>
            {suggestedCatalogs.length > 0 && (
              <Button variant="secondary" size="sm" className="w-full h-7 text-xs" onClick={handleSelectSuggested}>
                <Check className="h-3 w-3 mr-1" />
                Select Suggested ({suggestedCatalogs.length})
              </Button>
            )}
          </div>

          <ScrollArea className="h-[250px]" onWheel={(e) => e.stopPropagation()}>
            <div className="p-2 space-y-1">
              {filteredCatalogs.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No catalogs available for {contentType}
                </p>
              ) : (
                filteredCatalogs.map((catalog: CatalogInfo) => {
                  const isSelected = selectedCatalogs.includes(catalog.name)
                  const isSuggested = suggestedCatalogs.includes(catalog.name)

                  return (
                    <div
                      key={catalog.name}
                      className={cn(
                        'flex items-center gap-2 p-2 rounded-md cursor-pointer transition-colors',
                        isSelected ? 'bg-accent' : 'hover:bg-accent/50',
                        isSuggested && !isSelected && 'border border-primary/30',
                      )}
                      onClick={() => handleToggle(catalog.name)}
                    >
                      <Checkbox checked={isSelected} onCheckedChange={() => handleToggle(catalog.name)} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm truncate">{catalog.display_name || catalog.name}</p>
                        {isSuggested && !isSelected && (
                          <p className="text-[10px] text-primary">Suggested for {quality}</p>
                        )}
                      </div>
                      {isSelected && <Check className="h-4 w-4 text-primary flex-shrink-0" />}
                    </div>
                  )
                })
              )}
            </div>
          </ScrollArea>
        </PopoverContent>
      </Popover>

      {/* Selected Catalogs Badges */}
      {selectedCatalogs.length > 0 && !compact && (
        <div className="flex flex-wrap gap-1 pt-1">
          {selectedCatalogs.slice(0, 3).map((catalogId) => (
            <Badge
              key={catalogId}
              variant="secondary"
              className="text-[10px] cursor-pointer hover:bg-destructive/20"
              onClick={() => handleToggle(catalogId)}
            >
              {catalogId}
            </Badge>
          ))}
          {selectedCatalogs.length > 3 && (
            <Badge variant="outline" className="text-[10px]">
              +{selectedCatalogs.length - 3} more
            </Badge>
          )}
        </div>
      )}
    </div>
  )
}
