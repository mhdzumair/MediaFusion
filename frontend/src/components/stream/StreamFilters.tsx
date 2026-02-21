/* eslint-disable react-refresh/only-export-components */
import { useState } from 'react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import {
  ArrowUpDown,
  Filter,
  X,
  ChevronDown,
  Zap,
  Clock,
  Magnet,
  Newspaper,
  Link2,
  HardDrive,
  Send,
  Globe,
  History,
} from 'lucide-react'

export type SortBy = 'quality' | 'size' | 'seeders' | 'source'
export type SortOrder = 'asc' | 'desc'

export type CachedFilter = 'all' | 'cached' | 'not_cached'
export type StreamType = 'torrent' | 'usenet' | 'http' | 'telegram' | 'direct'

export interface StreamFilterState {
  sortBy: SortBy
  sortOrder: SortOrder
  qualityFilter: string[]
  resolutionFilter: string[]
  sourceFilter: string[]
  codecFilter: string[]
  cachedFilter: CachedFilter
  streamTypeFilter: StreamType[]
  minSizeGB: number | null
  maxSizeGB: number | null
  lastPlayedOnly: boolean // Show only last played stream
}

const RESOLUTION_OPTIONS = ['4K', '2160p', '1080p', '720p', '480p', 'SD']
const QUALITY_OPTIONS = ['WEB-DL', 'WEBRip', 'BluRay', 'BDRip', 'HDRip', 'HDTV', 'DVDRip', 'CAM']
const CODEC_OPTIONS = ['HEVC', 'H.265', 'AVC', 'H.264', 'VP9', 'AV1']

interface StreamFiltersProps {
  filters: StreamFilterState
  onFiltersChange: (filters: StreamFilterState) => void
  availableSources?: string[]
  availableResolutions?: string[]
  availableQualities?: string[]
  availableCodecs?: string[]
  availableStreamTypes?: StreamType[]
  totalStreams: number
  filteredCount: number
  showCachedFilter?: boolean // Show cached filter when debrid provider is active
  hasLastPlayed?: boolean // Whether there's a last played stream to filter by
}

export function StreamFilters({
  filters,
  onFiltersChange,
  availableSources = [],
  availableResolutions = [],
  availableQualities = [],
  availableCodecs = [],
  availableStreamTypes: _availableStreamTypes = [], // Currently showing all types, may use for filtering later
  totalStreams,
  filteredCount,
  showCachedFilter = false,
  hasLastPlayed = false,
}: StreamFiltersProps) {
  // Note: _availableStreamTypes is available if we want to conditionally show stream types
  void _availableStreamTypes
  // Use API-provided options if available, otherwise fall back to defaults
  // Filter out empty strings to prevent SelectItem errors
  const filteredResolutions = availableResolutions.filter((r) => r && r.trim() !== '')
  const filteredQualities = availableQualities.filter((q) => q && q.trim() !== '')
  const filteredCodecs = availableCodecs.filter((c) => c && c.trim() !== '')
  const filteredSources = availableSources.filter((s) => s && s.trim() !== '')

  const resolutionOptions = filteredResolutions.length > 0 ? filteredResolutions : RESOLUTION_OPTIONS
  const qualityOptions = filteredQualities.length > 0 ? filteredQualities : QUALITY_OPTIONS
  const codecOptions = filteredCodecs.length > 0 ? filteredCodecs : CODEC_OPTIONS
  const sourceOptions = filteredSources
  const [isOpen, setIsOpen] = useState(false)

  const updateFilter = <K extends keyof StreamFilterState>(key: K, value: StreamFilterState[K]) => {
    onFiltersChange({ ...filters, [key]: value })
  }

  const toggleArrayFilter = (
    key: 'qualityFilter' | 'resolutionFilter' | 'sourceFilter' | 'codecFilter',
    value: string,
  ) => {
    const current = filters[key]
    const newValue = current.includes(value) ? current.filter((v) => v !== value) : [...current, value]
    updateFilter(key, newValue)
  }

  const toggleStreamTypeFilter = (value: StreamType) => {
    const current = filters.streamTypeFilter
    const newValue = current.includes(value) ? current.filter((v) => v !== value) : [...current, value]
    updateFilter('streamTypeFilter', newValue)
  }

  const clearAllFilters = () => {
    onFiltersChange({
      ...filters,
      qualityFilter: [],
      resolutionFilter: [],
      sourceFilter: [],
      codecFilter: [],
      cachedFilter: 'all',
      streamTypeFilter: [],
      minSizeGB: null,
      maxSizeGB: null,
      lastPlayedOnly: false,
    })
  }

  const hasActiveFilters =
    filters.qualityFilter.length > 0 ||
    filters.resolutionFilter.length > 0 ||
    filters.sourceFilter.length > 0 ||
    filters.codecFilter.length > 0 ||
    filters.cachedFilter !== 'all' ||
    filters.streamTypeFilter.length > 0 ||
    filters.minSizeGB !== null ||
    filters.maxSizeGB !== null ||
    filters.lastPlayedOnly

  const activeFilterCount =
    filters.qualityFilter.length +
    filters.resolutionFilter.length +
    filters.sourceFilter.length +
    filters.codecFilter.length +
    (filters.cachedFilter !== 'all' ? 1 : 0) +
    filters.streamTypeFilter.length +
    (filters.minSizeGB !== null ? 1 : 0) +
    (filters.maxSizeGB !== null ? 1 : 0) +
    (filters.lastPlayedOnly ? 1 : 0)

  return (
    <div className="flex flex-col gap-3">
      {/* Sort + Filter row */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Sort Controls */}
        <div className="flex items-center gap-2">
          <Select value={filters.sortBy} onValueChange={(value: SortBy) => updateFilter('sortBy', value)}>
            <SelectTrigger className="w-[110px] sm:w-[130px] rounded-xl h-9 text-xs sm:text-sm">
              <ArrowUpDown className="h-3.5 w-3.5 mr-1 sm:mr-1.5 text-muted-foreground shrink-0" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="quality">Quality</SelectItem>
              <SelectItem value="size">Size</SelectItem>
              <SelectItem value="seeders">Seeders</SelectItem>
              <SelectItem value="source">Source</SelectItem>
            </SelectContent>
          </Select>

          <Button
            variant="outline"
            size="sm"
            className="h-9 px-2.5 rounded-xl"
            onClick={() => updateFilter('sortOrder', filters.sortOrder === 'asc' ? 'desc' : 'asc')}
          >
            {filters.sortOrder === 'desc' ? '↓' : '↑'}
          </Button>
        </div>

        {/* Filter Popover */}
        <Popover open={isOpen} onOpenChange={setIsOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-9 rounded-xl">
              <Filter className="h-3.5 w-3.5 mr-1 sm:mr-1.5" />
              <span className="hidden xs:inline">Filters</span>
              {activeFilterCount > 0 && (
                <Badge variant="secondary" className="ml-1 sm:ml-1.5 h-5 px-1.5 text-xs">
                  {activeFilterCount}
                </Badge>
              )}
              <ChevronDown className="h-3.5 w-3.5 ml-1 sm:ml-1.5" />
            </Button>
          </PopoverTrigger>
          <PopoverContent
            className="w-[calc(100vw-2rem)] sm:w-[420px] p-0 max-h-[80vh] flex flex-col"
            align="start"
            sideOffset={8}
          >
            <div className="p-3 sm:p-4 border-b border-border/50 shrink-0">
              <div className="flex items-center justify-between">
                <h4 className="font-semibold text-sm sm:text-base">Filter Streams</h4>
                {hasActiveFilters && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={clearAllFilters}
                    className="h-7 text-xs text-muted-foreground hover:text-foreground"
                  >
                    <X className="h-3 w-3 mr-1" />
                    Clear all
                  </Button>
                )}
              </div>
            </div>

            <ScrollArea className="flex-1 overscroll-contain">
              <div className="p-3 sm:p-4 space-y-4 sm:space-y-5">
                {/* Last Played Filter */}
                {hasLastPlayed && (
                  <div className="flex items-center justify-between p-2.5 sm:p-3 rounded-lg bg-primary/5 border border-primary/20 gap-3">
                    <div className="flex items-center gap-2 min-w-0">
                      <History className="h-4 w-4 text-primary shrink-0" />
                      <div className="min-w-0">
                        <Label className="text-xs sm:text-sm font-medium">Last Played Only</Label>
                        <p className="text-[10px] sm:text-xs text-muted-foreground">
                          Show only your last played stream
                        </p>
                      </div>
                    </div>
                    <Switch
                      checked={filters.lastPlayedOnly}
                      onCheckedChange={(checked) => updateFilter('lastPlayedOnly', checked)}
                      className="shrink-0"
                    />
                  </div>
                )}

                {/* Stream Type Filter */}
                <div className="space-y-2.5">
                  <Label className="text-xs sm:text-sm font-medium">Stream Type</Label>
                  <div className="grid grid-cols-3 gap-1.5 sm:gap-2">
                    <Badge
                      variant={filters.streamTypeFilter.includes('torrent') ? 'default' : 'outline'}
                      className="cursor-pointer justify-center py-1.5 sm:py-2 gap-1 sm:gap-1.5 text-[11px] sm:text-xs hover:bg-primary/10"
                      onClick={() => toggleStreamTypeFilter('torrent')}
                    >
                      <Magnet className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0" />
                      Torrent
                    </Badge>
                    <Badge
                      variant={filters.streamTypeFilter.includes('usenet') ? 'default' : 'outline'}
                      className="cursor-pointer justify-center py-1.5 sm:py-2 gap-1 sm:gap-1.5 text-[11px] sm:text-xs hover:bg-primary/10"
                      onClick={() => toggleStreamTypeFilter('usenet')}
                    >
                      <Newspaper className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0" />
                      Usenet
                    </Badge>
                    <Badge
                      variant={filters.streamTypeFilter.includes('telegram') ? 'default' : 'outline'}
                      className="cursor-pointer justify-center py-1.5 sm:py-2 gap-1 sm:gap-1.5 text-[11px] sm:text-xs hover:bg-primary/10"
                      onClick={() => toggleStreamTypeFilter('telegram')}
                    >
                      <Send className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0" />
                      Telegram
                    </Badge>
                    <Badge
                      variant={filters.streamTypeFilter.includes('http') ? 'default' : 'outline'}
                      className="cursor-pointer justify-center py-1.5 sm:py-2 gap-1 sm:gap-1.5 text-[11px] sm:text-xs hover:bg-primary/10"
                      onClick={() => toggleStreamTypeFilter('http')}
                    >
                      <Link2 className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0" />
                      HTTP
                    </Badge>
                    <Badge
                      variant={filters.streamTypeFilter.includes('direct') ? 'default' : 'outline'}
                      className="cursor-pointer justify-center py-1.5 sm:py-2 gap-1 sm:gap-1.5 text-[11px] sm:text-xs hover:bg-primary/10"
                      onClick={() => toggleStreamTypeFilter('direct')}
                    >
                      <Globe className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0" />
                      Direct
                    </Badge>
                  </div>
                </div>

                {/* Cached Filter */}
                {showCachedFilter && (
                  <div className="space-y-2.5">
                    <Label className="text-xs sm:text-sm font-medium">Cache Status</Label>
                    <div className="grid grid-cols-3 gap-1.5 sm:gap-2">
                      <Badge
                        variant={filters.cachedFilter === 'all' ? 'default' : 'outline'}
                        className="cursor-pointer justify-center py-1.5 sm:py-2 text-[11px] sm:text-xs hover:bg-primary/10"
                        onClick={() => updateFilter('cachedFilter', 'all')}
                      >
                        All
                      </Badge>
                      <Badge
                        variant={filters.cachedFilter === 'cached' ? 'default' : 'outline'}
                        className="cursor-pointer justify-center py-1.5 sm:py-2 gap-1 sm:gap-1.5 text-[11px] sm:text-xs hover:bg-primary/10"
                        onClick={() => updateFilter('cachedFilter', 'cached')}
                      >
                        <Zap className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0" />
                        Cached
                      </Badge>
                      <Badge
                        variant={filters.cachedFilter === 'not_cached' ? 'default' : 'outline'}
                        className="cursor-pointer justify-center py-1.5 sm:py-2 gap-1 sm:gap-1.5 text-[11px] sm:text-xs hover:bg-primary/10"
                        onClick={() => updateFilter('cachedFilter', 'not_cached')}
                      >
                        <Clock className="h-3 w-3 sm:h-3.5 sm:w-3.5 shrink-0" />
                        Not Cached
                      </Badge>
                    </div>
                  </div>
                )}

                <Separator />

                {/* Resolution Filter */}
                <div className="space-y-2.5">
                  <Label className="text-xs sm:text-sm font-medium">Resolution</Label>
                  <div className="flex flex-wrap gap-1.5 sm:gap-2">
                    {resolutionOptions.map((res) => (
                      <Badge
                        key={res}
                        variant={filters.resolutionFilter.includes(res) ? 'default' : 'outline'}
                        className="cursor-pointer py-1 sm:py-1.5 px-2 sm:px-3 text-[11px] sm:text-xs hover:bg-primary/10"
                        onClick={() => toggleArrayFilter('resolutionFilter', res)}
                      >
                        {res}
                      </Badge>
                    ))}
                  </div>
                </div>

                {/* Quality Filter */}
                <div className="space-y-2.5">
                  <Label className="text-xs sm:text-sm font-medium">Quality</Label>
                  <div className="flex flex-wrap gap-1.5 sm:gap-2">
                    {qualityOptions.map((quality) => (
                      <Badge
                        key={quality}
                        variant={filters.qualityFilter.includes(quality) ? 'default' : 'outline'}
                        className="cursor-pointer py-1 sm:py-1.5 px-2 sm:px-3 text-[11px] sm:text-xs hover:bg-primary/10"
                        onClick={() => toggleArrayFilter('qualityFilter', quality)}
                      >
                        {quality}
                      </Badge>
                    ))}
                  </div>
                </div>

                {/* Codec Filter */}
                <div className="space-y-2.5">
                  <Label className="text-xs sm:text-sm font-medium">Codec</Label>
                  <div className="flex flex-wrap gap-1.5 sm:gap-2">
                    {codecOptions.map((codec) => (
                      <Badge
                        key={codec}
                        variant={filters.codecFilter.includes(codec) ? 'default' : 'outline'}
                        className="cursor-pointer py-1 sm:py-1.5 px-2 sm:px-3 text-[11px] sm:text-xs hover:bg-primary/10"
                        onClick={() => toggleArrayFilter('codecFilter', codec)}
                      >
                        {codec}
                      </Badge>
                    ))}
                  </div>
                </div>

                <Separator />

                {/* Size Filter */}
                <div className="space-y-2.5">
                  <Label className="text-xs sm:text-sm font-medium">Size (GB)</Label>
                  <div className="flex items-center gap-2 sm:gap-3">
                    <div className="flex-1">
                      <Input
                        type="number"
                        placeholder="Min"
                        min={0}
                        step={0.1}
                        value={filters.minSizeGB ?? ''}
                        onChange={(e) => {
                          const value = e.target.value === '' ? null : parseFloat(e.target.value)
                          updateFilter('minSizeGB', value)
                        }}
                        className="h-8 sm:h-9 text-xs"
                      />
                    </div>
                    <span className="text-xs sm:text-sm text-muted-foreground">to</span>
                    <div className="flex-1">
                      <Input
                        type="number"
                        placeholder="Max"
                        min={0}
                        step={0.1}
                        value={filters.maxSizeGB ?? ''}
                        onChange={(e) => {
                          const value = e.target.value === '' ? null : parseFloat(e.target.value)
                          updateFilter('maxSizeGB', value)
                        }}
                        className="h-8 sm:h-9 text-xs"
                      />
                    </div>
                  </div>
                </div>

                {/* Source Filter */}
                {sourceOptions.length > 0 && (
                  <div className="space-y-2.5">
                    <Label className="text-xs sm:text-sm font-medium">Source</Label>
                    <ScrollArea className="max-h-28">
                      <div className="flex flex-wrap gap-1.5 sm:gap-2">
                        {sourceOptions.map((source) => (
                          <Badge
                            key={source}
                            variant={filters.sourceFilter.includes(source) ? 'default' : 'outline'}
                            className="cursor-pointer py-1 sm:py-1.5 px-2 sm:px-3 text-[11px] sm:text-xs hover:bg-primary/10"
                            onClick={() => toggleArrayFilter('sourceFilter', source)}
                          >
                            {source}
                          </Badge>
                        ))}
                      </div>
                    </ScrollArea>
                  </div>
                )}
              </div>
            </ScrollArea>
          </PopoverContent>
        </Popover>

        {/* Results count */}
        {hasActiveFilters && (
          <span className="text-[10px] sm:text-xs text-muted-foreground ml-auto">
            {filteredCount}/{totalStreams}
          </span>
        )}
      </div>

      {/* Active Filters Display */}
      {hasActiveFilters && (
        <div className="flex flex-wrap items-center gap-1 sm:gap-1.5">
          {[...filters.resolutionFilter, ...filters.qualityFilter, ...filters.codecFilter, ...filters.sourceFilter].map(
            (filter) => (
              <Badge key={filter} variant="secondary" className="text-[10px] sm:text-xs gap-1 py-0.5 px-1.5 sm:px-2">
                {filter}
                <X
                  className="h-2.5 w-2.5 sm:h-3 sm:w-3 cursor-pointer"
                  onClick={() => {
                    if (filters.resolutionFilter.includes(filter)) toggleArrayFilter('resolutionFilter', filter)
                    else if (filters.qualityFilter.includes(filter)) toggleArrayFilter('qualityFilter', filter)
                    else if (filters.codecFilter.includes(filter)) toggleArrayFilter('codecFilter', filter)
                    else if (filters.sourceFilter.includes(filter)) toggleArrayFilter('sourceFilter', filter)
                  }}
                />
              </Badge>
            ),
          )}
          {filters.cachedFilter !== 'all' && (
            <Badge variant="secondary" className="text-[10px] sm:text-xs gap-1 py-0.5 px-1.5 sm:px-2">
              {filters.cachedFilter === 'cached' ? (
                <>
                  <Zap className="h-2.5 w-2.5 sm:h-3 sm:w-3" />
                  Cached
                </>
              ) : (
                <>
                  <Clock className="h-2.5 w-2.5 sm:h-3 sm:w-3" />
                  Not Cached
                </>
              )}
              <X
                className="h-2.5 w-2.5 sm:h-3 sm:w-3 cursor-pointer"
                onClick={() => updateFilter('cachedFilter', 'all')}
              />
            </Badge>
          )}
          {filters.lastPlayedOnly && (
            <Badge variant="secondary" className="text-[10px] sm:text-xs gap-1 py-0.5 px-1.5 sm:px-2">
              <History className="h-2.5 w-2.5 sm:h-3 sm:w-3" />
              Last Played
              <X
                className="h-2.5 w-2.5 sm:h-3 sm:w-3 cursor-pointer"
                onClick={() => updateFilter('lastPlayedOnly', false)}
              />
            </Badge>
          )}
          {filters.streamTypeFilter.map((type) => (
            <Badge key={type} variant="secondary" className="text-[10px] sm:text-xs gap-1 py-0.5 px-1.5 sm:px-2">
              {type === 'torrent' && <Magnet className="h-2.5 w-2.5 sm:h-3 sm:w-3" />}
              {type === 'usenet' && <Newspaper className="h-2.5 w-2.5 sm:h-3 sm:w-3" />}
              {type === 'http' && <Link2 className="h-2.5 w-2.5 sm:h-3 sm:w-3" />}
              {type === 'telegram' && <Send className="h-2.5 w-2.5 sm:h-3 sm:w-3" />}
              {type === 'direct' && <Globe className="h-2.5 w-2.5 sm:h-3 sm:w-3" />}
              {type.charAt(0).toUpperCase() + type.slice(1)}
              <X className="h-2.5 w-2.5 sm:h-3 sm:w-3 cursor-pointer" onClick={() => toggleStreamTypeFilter(type)} />
            </Badge>
          ))}
          {(filters.minSizeGB !== null || filters.maxSizeGB !== null) && (
            <Badge variant="secondary" className="text-[10px] sm:text-xs gap-1 py-0.5 px-1.5 sm:px-2">
              <HardDrive className="h-2.5 w-2.5 sm:h-3 sm:w-3" />
              {filters.minSizeGB !== null && filters.maxSizeGB !== null
                ? `${filters.minSizeGB} - ${filters.maxSizeGB} GB`
                : filters.minSizeGB !== null
                  ? `≥ ${filters.minSizeGB} GB`
                  : `≤ ${filters.maxSizeGB} GB`}
              <X
                className="h-2.5 w-2.5 sm:h-3 sm:w-3 cursor-pointer"
                onClick={() => {
                  updateFilter('minSizeGB', null)
                  updateFilter('maxSizeGB', null)
                }}
              />
            </Badge>
          )}
        </div>
      )}
    </div>
  )
}

// Default filter state
export const defaultStreamFilters: StreamFilterState = {
  sortBy: 'quality',
  sortOrder: 'desc',
  qualityFilter: [],
  resolutionFilter: [],
  sourceFilter: [],
  codecFilter: [],
  cachedFilter: 'all',
  streamTypeFilter: [],
  minSizeGB: null,
  maxSizeGB: null,
  lastPlayedOnly: false,
}
