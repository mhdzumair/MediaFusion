import { useState } from 'react'
import { ArrowUp, ArrowDown, ChevronUp, ChevronDown, Plus, Minus, X } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Slider } from '@/components/ui/slider'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { RESOLUTIONS, QUALITY_GROUPS, SORTING_OPTIONS, LANGUAGES, STREAM_TYPES } from './constants'
import type { ConfigSectionProps, SortingOption } from './types'

export function StreamingPreferences({ config, onChange }: ConfigSectionProps) {
  const selectedResolutions = config.sr || RESOLUTIONS.map((r) => r.value)
  const selectedQualities = config.qf || QUALITY_GROUPS.map((q) => q.id)
  const sortingPriority = config.tsp || SORTING_OPTIONS.map((o) => ({ k: o.key, d: 'desc' as const }))
  const selectedLanguages = config.ls || LANGUAGES

  const toggleResolution = (value: string | null) => {
    const newResolutions = selectedResolutions.includes(value)
      ? selectedResolutions.filter((r) => r !== value)
      : [...selectedResolutions, value]
    onChange({ ...config, sr: newResolutions })
  }

  const moveResolution = (value: string | null, direction: 'up' | 'down') => {
    const currentIndex = selectedResolutions.indexOf(value)
    if (currentIndex === -1) return
    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
    if (newIndex < 0 || newIndex >= selectedResolutions.length) return
    const newResolutions = [...selectedResolutions]
    const [item] = newResolutions.splice(currentIndex, 1)
    newResolutions.splice(newIndex, 0, item)
    onChange({ ...config, sr: newResolutions })
  }

  const toggleQuality = (id: string) => {
    const newQualities = selectedQualities.includes(id)
      ? selectedQualities.filter((q) => q !== id)
      : [...selectedQualities, id]
    onChange({ ...config, qf: newQualities })
  }

  const moveQuality = (id: string, direction: 'up' | 'down') => {
    const currentIndex = selectedQualities.indexOf(id)
    if (currentIndex === -1) return
    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
    if (newIndex < 0 || newIndex >= selectedQualities.length) return
    const newQualities = [...selectedQualities]
    const [item] = newQualities.splice(currentIndex, 1)
    newQualities.splice(newIndex, 0, item)
    onChange({ ...config, qf: newQualities })
  }

  const toggleSortingOption = (key: string) => {
    const existing = sortingPriority.find((s) => s.k === key)
    let newPriority: SortingOption[]

    if (existing) {
      newPriority = sortingPriority.filter((s) => s.k !== key)
    } else {
      newPriority = [...sortingPriority, { k: key, d: 'desc' }]
    }
    onChange({ ...config, tsp: newPriority })
  }

  const toggleSortDirection = (key: string) => {
    const newPriority = sortingPriority.map((s) =>
      s.k === key ? { ...s, d: s.d === 'asc' ? ('desc' as const) : ('asc' as const) } : s,
    )
    onChange({ ...config, tsp: newPriority })
  }

  const moveSortingOption = (key: string, direction: 'up' | 'down') => {
    const currentIndex = sortingPriority.findIndex((s) => s.k === key)
    if (currentIndex === -1) return

    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
    if (newIndex < 0 || newIndex >= sortingPriority.length) return

    const newPriority = [...sortingPriority]
    const [item] = newPriority.splice(currentIndex, 1)
    newPriority.splice(newIndex, 0, item)
    onChange({ ...config, tsp: newPriority })
  }

  const toggleLanguage = (lang: string | null) => {
    const newLanguages = selectedLanguages.includes(lang)
      ? selectedLanguages.filter((l) => l !== lang)
      : [...selectedLanguages, lang]
    onChange({ ...config, ls: newLanguages })
  }

  const moveLanguage = (lang: string | null, direction: 'up' | 'down') => {
    const currentIndex = selectedLanguages.indexOf(lang)
    if (currentIndex === -1) return
    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
    if (newIndex < 0 || newIndex >= selectedLanguages.length) return
    const newLanguages = [...selectedLanguages]
    const [item] = newLanguages.splice(currentIndex, 1)
    newLanguages.splice(newIndex, 0, item)
    onChange({ ...config, ls: newLanguages })
  }

  const GB = 1024 * 1024 * 1024 // bytes in 1 GB
  const MAX_FILE_SIZE_GB = 200

  // max_size is stored in bytes (or 'inf')
  const maxSizeBytes = config.ms === 'inf' || config.ms === undefined ? Infinity : Number(config.ms)
  const maxSizeGB = maxSizeBytes === Infinity ? MAX_FILE_SIZE_GB : Math.round(maxSizeBytes / GB)
  const isNoMaxLimit = config.ms === 'inf' || config.ms === undefined || maxSizeBytes >= MAX_FILE_SIZE_GB * GB

  // min_size is stored in bytes (or 0)
  const minSizeBytes = config.mns === undefined ? 0 : Number(config.mns)
  const minSizeGB = Math.round(minSizeBytes / GB)
  const isNoMinLimit = minSizeBytes === 0 || config.mns === undefined

  // Stream type order
  const streamTypeOrder = config.sto || STREAM_TYPES.map((t) => t.value)

  const moveStreamType = (value: string, direction: 'up' | 'down') => {
    const currentIndex = streamTypeOrder.indexOf(value)
    if (currentIndex === -1) return
    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
    if (newIndex < 0 || newIndex >= streamTypeOrder.length) return
    const newOrder = [...streamTypeOrder]
    const [item] = newOrder.splice(currentIndex, 1)
    newOrder.splice(newIndex, 0, item)
    onChange({ ...config, sto: newOrder })
  }

  // Stream name filter
  const filterPatterns = config.snfp || []
  const [newPattern, setNewPattern] = useState('')

  const addPattern = () => {
    const trimmed = newPattern.trim()
    if (!trimmed || filterPatterns.includes(trimmed)) return
    onChange({ ...config, snfp: [...filterPatterns, trimmed] })
    setNewPattern('')
  }

  const removePattern = (pattern: string) => {
    onChange({ ...config, snfp: filterPatterns.filter((p) => p !== pattern) })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">⚙️ Streaming Preferences</CardTitle>
        <CardDescription>Configure resolution, quality, and sorting preferences for streams</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Resolution Selection & Order */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>Resolutions</Label>
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{selectedResolutions.length} selected</Badge>
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  onChange({
                    ...config,
                    sr: selectedResolutions.length === RESOLUTIONS.length ? [] : RESOLUTIONS.map((r) => r.value),
                  })
                }
              >
                {selectedResolutions.length === RESOLUTIONS.length ? 'Clear' : 'All'}
              </Button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Order determines sorting priority. Use arrows to reorder, X to remove.
          </p>

          {/* Selected resolutions - reorderable */}
          {selectedResolutions.length > 0 && (
            <div className="space-y-1">
              {selectedResolutions.map((resValue, index) => {
                const res = RESOLUTIONS.find((r) => r.value === resValue)
                if (!res) return null
                return (
                  <div
                    key={resValue || 'unknown'}
                    className="flex items-center gap-1.5 p-1.5 rounded-md border border-primary/30 bg-primary/5"
                  >
                    <Badge variant="outline" className="text-[10px] w-5 h-5 justify-center p-0 shrink-0">
                      {index + 1}
                    </Badge>
                    <div className="flex flex-col gap-0">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-4 w-4"
                        onClick={() => moveResolution(resValue, 'up')}
                        disabled={index === 0}
                      >
                        <ChevronUp className="h-2.5 w-2.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-4 w-4"
                        onClick={() => moveResolution(resValue, 'down')}
                        disabled={index === selectedResolutions.length - 1}
                      >
                        <ChevronDown className="h-2.5 w-2.5" />
                      </Button>
                    </div>
                    <span className="flex-1 text-sm font-medium">{res.label}</span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-5 w-5 text-muted-foreground hover:text-red-500 shrink-0"
                      onClick={() => toggleResolution(resValue)}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                )
              })}
            </div>
          )}

          {/* Available resolutions to add */}
          {RESOLUTIONS.filter((r) => !selectedResolutions.includes(r.value)).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {RESOLUTIONS.filter((r) => !selectedResolutions.includes(r.value)).map((res) => (
                <Button
                  key={res.value || 'unknown'}
                  variant="outline"
                  size="sm"
                  onClick={() => toggleResolution(res.value)}
                  className="h-7 text-xs"
                >
                  <Plus className="h-3 w-3 mr-1" />
                  {res.label}
                </Button>
              ))}
            </div>
          )}
        </div>

        {/* Quality Groups */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>Quality Filter</Label>
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{selectedQualities.length} selected</Badge>
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  onChange({
                    ...config,
                    qf: selectedQualities.length === QUALITY_GROUPS.length ? [] : QUALITY_GROUPS.map((q) => q.id),
                  })
                }
              >
                {selectedQualities.length === QUALITY_GROUPS.length ? 'Clear' : 'All'}
              </Button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Order determines sorting priority. Use arrows to reorder, X to remove.
          </p>

          {/* Selected qualities - reorderable */}
          {selectedQualities.length > 0 && (
            <div className="space-y-1">
              {selectedQualities.map((qualityId, index) => {
                const quality = QUALITY_GROUPS.find((q) => q.id === qualityId)
                if (!quality) return null
                return (
                  <TooltipProvider key={qualityId}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <div className="flex items-center gap-1.5 p-1.5 rounded-md border border-primary/30 bg-primary/5">
                          <Badge variant="outline" className="text-[10px] w-5 h-5 justify-center p-0 shrink-0">
                            {index + 1}
                          </Badge>
                          <div className="flex flex-col gap-0">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-4 w-4"
                              onClick={() => moveQuality(qualityId, 'up')}
                              disabled={index === 0}
                            >
                              <ChevronUp className="h-2.5 w-2.5" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-4 w-4"
                              onClick={() => moveQuality(qualityId, 'down')}
                              disabled={index === selectedQualities.length - 1}
                            >
                              <ChevronDown className="h-2.5 w-2.5" />
                            </Button>
                          </div>
                          <span className="flex-1 text-sm font-medium">{quality.label}</span>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-5 w-5 text-muted-foreground hover:text-red-500 shrink-0"
                            onClick={() => toggleQuality(qualityId)}
                          >
                            <X className="h-3 w-3" />
                          </Button>
                        </div>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>{quality.desc}</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )
              })}
            </div>
          )}

          {/* Available qualities to add */}
          {QUALITY_GROUPS.filter((q) => !selectedQualities.includes(q.id)).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {QUALITY_GROUPS.filter((q) => !selectedQualities.includes(q.id)).map((quality) => (
                <TooltipProvider key={quality.id}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => toggleQuality(quality.id)}
                        className="h-7 text-xs"
                      >
                        <Plus className="h-3 w-3 mr-1" />
                        {quality.label}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{quality.desc}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              ))}
            </div>
          )}
        </div>

        {/* File Size Filters */}
        <div className="space-y-4">
          <Label>File Size Filters</Label>

          {/* Min File Size */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-xs">Minimum Size (GB)</Label>
              <span className="text-sm font-medium tabular-nums">
                {isNoMinLimit ? 'No Minimum' : `${minSizeGB} GB`}
              </span>
            </div>
            <Slider
              min={0}
              max={50}
              step={1}
              value={[minSizeGB]}
              onValueChange={([val]) => {
                onChange({ ...config, mns: val <= 0 ? 0 : val * GB })
              }}
            />
            <div className="flex items-center gap-3">
              <Input
                type="number"
                min={0}
                max={50}
                placeholder="No minimum"
                value={isNoMinLimit ? '' : minSizeGB}
                onChange={(e) => {
                  const value = e.target.value
                  if (value === '') {
                    onChange({ ...config, mns: 0 })
                  } else {
                    const num = Math.max(0, Math.min(50, parseInt(value) || 0))
                    onChange({ ...config, mns: num <= 0 ? 0 : num * GB })
                  }
                }}
                className="w-24"
              />
              <p className="text-xs text-muted-foreground">
                Streams smaller than this will be filtered out. Set to 0 for no minimum.
              </p>
            </div>
          </div>

          {/* Max File Size */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-xs">Maximum Size (GB)</Label>
              <span className="text-sm font-medium tabular-nums">{isNoMaxLimit ? 'No Limit' : `${maxSizeGB} GB`}</span>
            </div>
            <Slider
              min={1}
              max={MAX_FILE_SIZE_GB}
              step={1}
              value={[maxSizeGB]}
              onValueChange={([val]) => {
                onChange({ ...config, ms: val >= MAX_FILE_SIZE_GB ? 'inf' : val * GB })
              }}
            />
            <div className="flex items-center gap-3">
              <Input
                type="number"
                min={1}
                max={MAX_FILE_SIZE_GB}
                placeholder="No limit"
                value={isNoMaxLimit ? '' : maxSizeGB}
                onChange={(e) => {
                  const value = e.target.value
                  if (value === '') {
                    onChange({ ...config, ms: 'inf' })
                  } else {
                    const num = Math.max(1, Math.min(MAX_FILE_SIZE_GB, parseInt(value) || 1))
                    onChange({ ...config, ms: num >= MAX_FILE_SIZE_GB ? 'inf' : num * GB })
                  }
                }}
                className="w-24"
              />
              <div className="flex items-center gap-2">
                <Switch
                  checked={isNoMaxLimit}
                  onCheckedChange={(checked) => {
                    onChange({ ...config, ms: checked ? 'inf' : 50 * GB })
                  }}
                />
                <Label className="text-xs text-muted-foreground">No limit</Label>
              </div>
            </div>
          </div>
        </div>

        {/* Max Streams per Resolution */}
        <div className="space-y-2">
          <Label>Max Streams per Resolution</Label>
          <Input
            type="number"
            min="1"
            max="50"
            value={config.mspr || 10}
            onChange={(e) => onChange({ ...config, mspr: parseInt(e.target.value) || 10 })}
          />
        </div>

        {/* Sorting Priority */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>Stream Sorting Priority</Label>
            <Badge variant="secondary">{sortingPriority.length} active</Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Active sorting options (in priority order). Use arrows to reorder.
          </p>

          {/* Active sorting options - reorderable */}
          <div className="space-y-2">
            {sortingPriority.map((activeSort, index) => {
              const option = SORTING_OPTIONS.find((o) => o.key === activeSort.k)
              if (!option) return null

              return (
                <div
                  key={activeSort.k}
                  className="flex items-center gap-2 p-3 rounded-lg border border-primary bg-primary/10"
                >
                  <Badge variant="outline" className="text-xs w-6 justify-center">
                    {index + 1}
                  </Badge>

                  {/* Move Up/Down buttons */}
                  <div className="flex flex-col gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-5 w-5"
                      onClick={() => moveSortingOption(activeSort.k, 'up')}
                      disabled={index === 0}
                    >
                      <ChevronUp className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-5 w-5"
                      onClick={() => moveSortingOption(activeSort.k, 'down')}
                      disabled={index === sortingPriority.length - 1}
                    >
                      <ChevronDown className="h-3 w-3" />
                    </Button>
                  </div>

                  <span className="flex-1 text-sm font-medium">{option.label}</span>

                  {/* Sort direction toggle */}
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => toggleSortDirection(activeSort.k)}
                        >
                          {activeSort.d === 'desc' ? (
                            <ArrowDown className="h-4 w-4 text-primary" />
                          ) : (
                            <ArrowUp className="h-4 w-4 text-primary" />
                          )}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>{activeSort.d === 'desc' ? option.desc : option.asc}</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>

                  {/* Remove button */}
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-muted-foreground hover:text-red-500"
                    onClick={() => toggleSortingOption(activeSort.k)}
                  >
                    <Minus className="h-4 w-4" />
                  </Button>
                </div>
              )
            })}
          </div>

          {/* Available options to add */}
          {SORTING_OPTIONS.filter((o) => !sortingPriority.some((s) => s.k === o.key)).length > 0 && (
            <div className="pt-2 border-t">
              <p className="text-xs text-muted-foreground mb-2">Available options:</p>
              <div className="flex flex-wrap gap-2">
                {SORTING_OPTIONS.filter((o) => !sortingPriority.some((s) => s.k === o.key)).map((option) => (
                  <Button
                    key={option.key}
                    variant="outline"
                    size="sm"
                    onClick={() => toggleSortingOption(option.key)}
                    className="h-8"
                  >
                    <Plus className="h-3 w-3 mr-1" />
                    {option.label}
                  </Button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Language Preferences */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>Preferred Languages</Label>
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{selectedLanguages.length} selected</Badge>
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  onChange({ ...config, ls: selectedLanguages.length === LANGUAGES.length ? [] : [...LANGUAGES] })
                }
              >
                {selectedLanguages.length === LANGUAGES.length ? 'Clear' : 'All'}
              </Button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Order matters for sorting -- languages higher in the list are prioritized. Use arrows to reorder.
          </p>

          {/* Selected languages - reorderable list */}
          {selectedLanguages.length > 0 && (
            <ScrollArea className="h-[200px] border rounded-lg p-2">
              <div className="space-y-1">
                {selectedLanguages.map((lang, index) => (
                  <div
                    key={lang}
                    className="flex items-center gap-1.5 p-1.5 rounded-md border border-primary/30 bg-primary/5"
                  >
                    <Badge variant="outline" className="text-[10px] w-5 h-5 justify-center p-0 shrink-0">
                      {index + 1}
                    </Badge>
                    <div className="flex flex-col gap-0">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-4 w-4"
                        onClick={() => moveLanguage(lang, 'up')}
                        disabled={index === 0}
                      >
                        <ChevronUp className="h-2.5 w-2.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-4 w-4"
                        onClick={() => moveLanguage(lang, 'down')}
                        disabled={index === selectedLanguages.length - 1}
                      >
                        <ChevronDown className="h-2.5 w-2.5" />
                      </Button>
                    </div>
                    <span className="flex-1 text-xs font-medium">{lang}</span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-5 w-5 text-muted-foreground hover:text-red-500 shrink-0"
                      onClick={() => toggleLanguage(lang)}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}

          {/* Available languages to add */}
          {LANGUAGES.filter((l) => !selectedLanguages.includes(l)).length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Add languages:</p>
              <ScrollArea className="h-[120px] border rounded-lg p-2">
                <div className="flex flex-wrap gap-1.5">
                  {LANGUAGES.filter((l) => !selectedLanguages.includes(l)).map((lang) => (
                    <Button
                      key={lang}
                      variant="outline"
                      size="sm"
                      onClick={() => toggleLanguage(lang)}
                      className="h-7 text-xs"
                    >
                      <Plus className="h-3 w-3 mr-1" />
                      {lang}
                    </Button>
                  ))}
                </div>
              </ScrollArea>
            </div>
          )}
        </div>

        {/* Display Options */}
        <div className="space-y-4 pt-4 border-t">
          <h4 className="text-sm font-medium">Display Options</h4>

          {/* Max Total Streams */}
          <div className="space-y-2">
            <Label>Max Total Streams</Label>
            <Input
              type="number"
              min="1"
              max="100"
              value={config.mxs ?? 25}
              onChange={(e) => onChange({ ...config, mxs: Math.max(1, Math.min(100, parseInt(e.target.value) || 25)) })}
            />
            <p className="text-xs text-muted-foreground">
              Maximum total number of streams returned (1-100). Applies after all other filters.
            </p>
          </div>

          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label>Live Search Streams</Label>
              <p className="text-xs text-muted-foreground">
                Enable on-demand search for streams (slower but more results)
              </p>
            </div>
            <Switch
              checked={config.lss === true}
              onCheckedChange={(checked) => onChange({ ...config, lss: checked })}
            />
          </div>

          {/* Stream Type Grouping */}
          <div className="space-y-3 pt-2">
            <Label>Stream Type Grouping</Label>
            <p className="text-xs text-muted-foreground">Choose how different stream types are ordered in results.</p>
            <div className="flex gap-2">
              <Button
                variant={config.stg === 'mixed' ? 'default' : 'outline'}
                size="sm"
                onClick={() => onChange({ ...config, stg: 'mixed' })}
              >
                Mixed
              </Button>
              <Button
                variant={config.stg !== 'mixed' ? 'default' : 'outline'}
                size="sm"
                onClick={() => onChange({ ...config, stg: 'separate' })}
              >
                Separate by Type
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {config.stg === 'mixed'
                ? 'Streams from all types are interleaved by your sorting preferences.'
                : 'Streams are grouped by type. Use arrows to set your preferred type order.'}
            </p>

            {/* Stream type order - only visible when "separate" is selected */}
            {config.stg !== 'mixed' && (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">Stream type priority order:</p>
                {streamTypeOrder.map((typeValue, index) => {
                  const streamType = STREAM_TYPES.find((t) => t.value === typeValue)
                  if (!streamType) return null
                  return (
                    <div
                      key={typeValue}
                      className="flex items-center gap-2 p-2 rounded-lg border border-border bg-muted/30"
                    >
                      <Badge variant="outline" className="text-xs w-6 justify-center">
                        {index + 1}
                      </Badge>
                      <div className="flex flex-col gap-0.5">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-5 w-5"
                          onClick={() => moveStreamType(typeValue, 'up')}
                          disabled={index === 0}
                        >
                          <ChevronUp className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-5 w-5"
                          onClick={() => moveStreamType(typeValue, 'down')}
                          disabled={index === streamTypeOrder.length - 1}
                        >
                          <ChevronDown className="h-3 w-3" />
                        </Button>
                      </div>
                      <span className="text-sm">{streamType.icon}</span>
                      <span className="flex-1 text-sm font-medium">{streamType.label}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Provider Grouping */}
          <div className="space-y-3 pt-2">
            <Label>Debrid Provider Grouping</Label>
            <p className="text-xs text-muted-foreground">
              Choose how streams from different debrid providers (RD, AD, TorBox, etc.) are ordered.
            </p>
            <div className="flex gap-2">
              <Button
                variant={config.pg === 'mixed' ? 'default' : 'outline'}
                size="sm"
                onClick={() => onChange({ ...config, pg: 'mixed' })}
              >
                Mixed
              </Button>
              <Button
                variant={config.pg !== 'mixed' ? 'default' : 'outline'}
                size="sm"
                onClick={() => onChange({ ...config, pg: 'separate' })}
              >
                Separate by Provider
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {config.pg === 'mixed'
                ? 'Streams from all providers are interleaved (e.g. RD #1, AD #1, TRB #1, RD #2, ...). Provider priority order is used.'
                : 'Streams are grouped per provider. All streams from the highest-priority provider appear first.'}
            </p>
          </div>
        </div>

        {/* Stream Name Filter */}
        <div className="space-y-4 pt-4 border-t">
          <h4 className="text-sm font-medium">Stream Name Filter</h4>
          <p className="text-xs text-muted-foreground">Filter streams by name using keywords or regex patterns.</p>

          {/* Filter Mode */}
          <div className="space-y-2">
            <Label>Filter Mode</Label>
            <div className="flex gap-2">
              <Button
                variant={config.snfm === 'disabled' || !config.snfm ? 'default' : 'outline'}
                size="sm"
                onClick={() => onChange({ ...config, snfm: 'disabled' })}
              >
                Disabled
              </Button>
              <Button
                variant={config.snfm === 'include' ? 'default' : 'outline'}
                size="sm"
                onClick={() => onChange({ ...config, snfm: 'include' })}
              >
                Include Only
              </Button>
              <Button
                variant={config.snfm === 'exclude' ? 'default' : 'outline'}
                size="sm"
                onClick={() => onChange({ ...config, snfm: 'exclude' })}
              >
                Exclude
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {config.snfm === 'include'
                ? 'Only streams matching at least one pattern will be shown.'
                : config.snfm === 'exclude'
                  ? 'Streams matching any pattern will be hidden.'
                  : 'No stream name filtering applied.'}
            </p>
          </div>

          {/* Only show pattern input and regex toggle when filter is active */}
          {config.snfm && config.snfm !== 'disabled' && (
            <>
              {/* Use Regex toggle */}
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Use Regex</Label>
                  <p className="text-xs text-muted-foreground">
                    Enable regular expression matching instead of simple keyword search
                  </p>
                </div>
                <Switch
                  checked={config.snfr === true}
                  onCheckedChange={(checked) => onChange({ ...config, snfr: checked })}
                />
              </div>

              {/* Pattern Input */}
              <div className="space-y-2">
                <Label>Patterns</Label>
                <div className="flex gap-2">
                  <Input
                    placeholder={config.snfr ? 'e.g. HDR|Atmos' : 'e.g. HEVC'}
                    value={newPattern}
                    onChange={(e) => setNewPattern(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        addPattern()
                      }
                    }}
                  />
                  <Button variant="outline" onClick={addPattern} disabled={!newPattern.trim()}>
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {config.snfr
                    ? 'Enter regex patterns. Case-insensitive. Press Enter to add.'
                    : 'Enter keywords. Case-insensitive substring match. Press Enter to add.'}
                </p>
              </div>

              {/* Pattern List */}
              {filterPatterns.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {filterPatterns.map((pattern) => (
                    <Badge key={pattern} variant="secondary" className="flex items-center gap-1 px-3 py-1">
                      <code className="text-xs">{pattern}</code>
                      <button
                        onClick={() => removePattern(pattern)}
                        className="ml-1 hover:text-red-500 transition-colors"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
