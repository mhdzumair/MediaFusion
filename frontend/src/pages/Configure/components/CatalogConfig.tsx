import { useState } from 'react'
import { Check, ChevronUp, ChevronDown, Search, Settings2 } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { CATALOGS } from './constants'
import type { ConfigSectionProps, CatalogConfig as CatalogConfigType } from './types'

const SORT_OPTIONS = [
  { value: 'default', label: 'Default (Latest)' },
  { value: 'latest', label: 'Latest Added' },
  { value: 'popular', label: 'Popular (Rating)' },
  { value: 'rating', label: 'Rating' },
  { value: 'year', label: 'Year' },
  { value: 'release_date', label: 'Release Date' },
  { value: 'title', label: 'Title (A-Z)' },
]

const ORDER_OPTIONS = [
  { value: 'desc', label: 'Descending' },
  { value: 'asc', label: 'Ascending' },
]

export function CatalogConfig({ config, onChange }: ConfigSectionProps) {
  const [search, setSearch] = useState('')
  const [showReorder, setShowReorder] = useState(false)
  const [searchFocused, setSearchFocused] = useState(false)
  
  // Legacy selected_catalogs (sc)
  const legacySelectedCatalogs = config.sc || []
  
  // Migrate legacy sc to cc format if needed
  const getMigratedCatalogConfigs = (): CatalogConfigType[] => {
    const existingConfigs = config.cc || []
    
    // If we have new format configs, use them
    if (existingConfigs.length > 0) {
      return existingConfigs
    }
    
    // Migrate legacy selected_catalogs to new format
    if (legacySelectedCatalogs.length > 0) {
      return legacySelectedCatalogs.map(id => ({ ci: id, en: true }))
    }
    
    return []
  }
  
  // Get the effective catalog configs (migrated if necessary)
  const catalogConfigs = getMigratedCatalogConfigs()
  
  // Get enabled catalog IDs in order
  const enabledCatalogIds = catalogConfigs.filter(c => c.en !== false).map(c => c.ci)
  
  // Get config for a specific catalog
  const getCatalogConfig = (catalogId: string): CatalogConfigType | undefined => {
    return catalogConfigs.find(c => c.ci === catalogId)
  }
  
  // Check if catalog is enabled
  const isCatalogEnabled = (catalogId: string): boolean => {
    const cfg = getCatalogConfig(catalogId)
    return cfg ? cfg.en !== false : false
  }
  
  // Update catalog configs
  const updateCatalogConfigs = (newConfigs: CatalogConfigType[]) => {
    // Clear legacy sc when using new format
    onChange({ ...config, cc: newConfigs, sc: [] })
  }
  
  const toggleCatalog = (catalogId: string) => {
    const existingConfig = getCatalogConfig(catalogId)
    
    if (existingConfig) {
      // Toggle existing config
      if (existingConfig.en !== false) {
        // Disable: remove from list
        updateCatalogConfigs(catalogConfigs.filter(c => c.ci !== catalogId))
      } else {
        // Enable: set en to true
        updateCatalogConfigs(
          catalogConfigs.map(c => c.ci === catalogId ? { ...c, en: true } : c)
        )
      }
    } else {
      // Add new config (enabled by default)
      updateCatalogConfigs([...catalogConfigs, { ci: catalogId, en: true }])
    }
  }
  
  const updateCatalogSort = (catalogId: string, sort: string | null, order: 'asc' | 'desc') => {
    const existingConfig = getCatalogConfig(catalogId)
    // Convert 'default' to null (no custom sort)
    const sortValue = (sort === 'default' || sort === '') ? null : sort
    
    if (existingConfig) {
      updateCatalogConfigs(
        catalogConfigs.map(c => 
          c.ci === catalogId 
            ? { ...c, s: sortValue as CatalogConfigType['s'], o: order } 
            : c
        )
      )
    } else {
      // Add new config with sort settings
      updateCatalogConfigs([
        ...catalogConfigs, 
        { ci: catalogId, en: true, s: sortValue as CatalogConfigType['s'], o: order }
      ])
    }
  }
  
  const selectAll = (category: string) => {
    const categoryItems = CATALOGS[category as keyof typeof CATALOGS] || []
    const categoryIds = categoryItems.map(c => c.id)
    const allSelected = categoryIds.every(id => isCatalogEnabled(id))
    
    if (allSelected) {
      // Deselect all in category
      updateCatalogConfigs(catalogConfigs.filter(c => !categoryIds.includes(c.ci)))
    } else {
      // Select all in category (add missing ones)
      const existing = new Set(catalogConfigs.map(c => c.ci))
      const newConfigs = [...catalogConfigs]
      for (const id of categoryIds) {
        if (!existing.has(id)) {
          newConfigs.push({ ci: id, en: true })
        } else {
          // Enable if disabled
          const idx = newConfigs.findIndex(c => c.ci === id)
          if (idx !== -1 && newConfigs[idx].en === false) {
            newConfigs[idx] = { ...newConfigs[idx], en: true }
          }
        }
      }
      updateCatalogConfigs(newConfigs)
    }
  }
  
  const selectAllCatalogs = () => {
    const allIds = Object.values(CATALOGS).flat().map(c => c.id)
    const allSelected = allIds.every(id => isCatalogEnabled(id))
    
    if (allSelected) {
      updateCatalogConfigs([])
    } else {
      const existing = new Set(catalogConfigs.map(c => c.ci))
      const newConfigs = [...catalogConfigs]
      for (const id of allIds) {
        if (!existing.has(id)) {
          newConfigs.push({ ci: id, en: true })
        } else {
          // Enable if disabled
          const idx = newConfigs.findIndex(c => c.ci === id)
          if (idx !== -1 && newConfigs[idx].en === false) {
            newConfigs[idx] = { ...newConfigs[idx], en: true }
          }
        }
      }
      updateCatalogConfigs(newConfigs)
    }
  }
  
  const moveCatalog = (catalogId: string, direction: 'up' | 'down') => {
    const enabledConfigs = catalogConfigs.filter(c => c.en !== false)
    const currentIndex = enabledConfigs.findIndex(c => c.ci === catalogId)
    if (currentIndex === -1) return
    
    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
    if (newIndex < 0 || newIndex >= enabledConfigs.length) return
    
    // Swap in enabled configs
    const newEnabled = [...enabledConfigs]
    ;[newEnabled[currentIndex], newEnabled[newIndex]] = [newEnabled[newIndex], newEnabled[currentIndex]]
    
    // Rebuild full list: enabled first (in order), then disabled
    const disabledConfigs = catalogConfigs.filter(c => c.en === false)
    updateCatalogConfigs([...newEnabled, ...disabledConfigs])
  }
  
  // Get MDBList catalogs from config
  const mdbLists = config.mdb?.l || []
  
  // Get catalog name by id (including MDBList catalogs)
  const getCatalogName = (id: string) => {
    // Check if it's an MDBList catalog
    if (id.startsWith('mdblist_')) {
      const parts = id.split('_')
      const listId = parseInt(parts[parts.length - 1])
      const mdbList = mdbLists.find(l => l.i === listId)
      if (mdbList) {
        return `MDBList: ${mdbList.t}`
      }
    }
    
    // Check MediaFusion catalogs
    for (const items of Object.values(CATALOGS)) {
      const catalog = items.find(c => c.id === id)
      if (catalog) return catalog.name
    }
    return id
  }
  
  const filteredCatalogs = Object.entries(CATALOGS).reduce((acc, [category, items]) => {
    const filtered = items.filter(
      item => 
        item.name.toLowerCase().includes(search.toLowerCase()) ||
        item.id.toLowerCase().includes(search.toLowerCase())
    )
    if (filtered.length > 0) {
      acc[category] = filtered
    }
    return acc
  }, {} as Record<string, typeof CATALOGS['Movies']>)
  
  // Render sort settings popover for a catalog
  const renderSortSettings = (catalogId: string) => {
    const cfg = getCatalogConfig(catalogId)
    const currentSort = cfg?.s || 'default'
    const currentOrder = cfg?.o || 'desc'
    const hasCustomSort = cfg?.s !== undefined && cfg?.s !== null
    
    return (
      <Popover>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "h-7 w-7",
              hasCustomSort && "text-primary"
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <Settings2 className="h-4 w-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-64" onClick={(e) => e.stopPropagation()}>
          <div className="space-y-3">
            <div className="font-medium text-sm">Sort Settings</div>
            <div className="space-y-2">
              <Label className="text-xs">Sort By</Label>
              <Select
                value={currentSort}
                onValueChange={(value) => updateCatalogSort(catalogId, value, currentOrder)}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Default (Latest)" />
                </SelectTrigger>
                <SelectContent>
                  {SORT_OPTIONS.map(opt => (
                    <SelectItem key={opt.value} value={opt.value} className="text-xs">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label className="text-xs">Order</Label>
              <Select
                value={currentOrder}
                onValueChange={(value) => updateCatalogSort(catalogId, currentSort, value as 'asc' | 'desc')}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ORDER_OPTIONS.map(opt => (
                    <SelectItem key={opt.value} value={opt.value} className="text-xs">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    )
  }
  
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              📚 Catalog Configuration
            </CardTitle>
            <CardDescription>
              Select and configure sorting for each catalog in your streaming experience
            </CardDescription>
          </div>
          <Badge variant="secondary" className="text-sm">
            {enabledCatalogIds.length} selected
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Enable Catalogs Toggle */}
        <div className="flex items-center justify-between pb-4 border-b">
          <div className="space-y-0.5">
            <Label>Enable Catalogs</Label>
            <p className="text-xs text-muted-foreground">
              Show catalog browsing in Stremio
            </p>
          </div>
          <Switch
            checked={config.ec !== false}
            onCheckedChange={(checked) => onChange({ ...config, ec: checked })}
          />
        </div>
        
        {/* Enable IMDb Metadata */}
        <div className="flex items-center justify-between pb-4 border-b">
          <div className="space-y-0.5">
            <Label>Enable IMDb Metadata</Label>
            <p className="text-xs text-muted-foreground">
              Use IMDb for additional metadata
            </p>
          </div>
          <Switch
            checked={config.eim === true}
            onCheckedChange={(checked) => onChange({ ...config, eim: checked })}
          />
        </div>
        
        {/* Search and Actions */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onFocus={() => setSearchFocused(true)}
              onBlur={() => setSearchFocused(false)}
              readOnly={!searchFocused}
              placeholder="Search catalogs..."
              className="pl-9"
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              data-form-type="other"
              data-lpignore="true"
            />
          </div>
          <Button 
            variant={showReorder ? "default" : "outline"} 
            size="sm" 
            onClick={() => setShowReorder(!showReorder)}
          >
            {showReorder ? 'Done Reordering' : 'Reorder'}
          </Button>
          <Button variant="outline" size="sm" onClick={selectAllCatalogs}>
            {enabledCatalogIds.length === Object.values(CATALOGS).flat().length ? 'Deselect All' : 'Select All'}
          </Button>
        </div>
        
        {/* Reorder Panel */}
        {showReorder && enabledCatalogIds.length > 0 && (
          <div className="border rounded-lg p-4 space-y-2 bg-muted/30">
            <p className="text-sm font-medium mb-3">Catalog Order (Top = Higher Priority)</p>
            <ScrollArea className="h-[300px] pr-4">
              <div className="space-y-1">
                {catalogConfigs.filter(c => c.en !== false).map((cfg, index) => (
                  <div
                    key={cfg.ci}
                    className="flex items-center gap-2 p-2 rounded-lg border bg-background"
                  >
                    <Badge variant="outline" className="w-8 justify-center shrink-0">
                      {index + 1}
                    </Badge>
                    <span className="flex-1 text-sm truncate">{getCatalogName(cfg.ci)}</span>
                    {cfg.s && (
                      <Badge variant="secondary" className="text-xs shrink-0">
                        {SORT_OPTIONS.find(o => o.value === cfg.s)?.label || cfg.s}
                      </Badge>
                    )}
                    {renderSortSettings(cfg.ci)}
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => moveCatalog(cfg.ci, 'up')}
                        disabled={index === 0}
                      >
                        <ChevronUp className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => moveCatalog(cfg.ci, 'down')}
                        disabled={index === catalogConfigs.filter(c => c.en !== false).length - 1}
                      >
                        <ChevronDown className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </div>
        )}
        
        {/* Catalog Tabs */}
        <Tabs defaultValue="Movies" className="w-full">
          <TabsList className="h-auto flex-wrap gap-1 bg-muted/50 p-2 rounded-xl">
            {Object.keys(filteredCatalogs).map((category) => (
              <TabsTrigger key={category} value={category} className="px-4 py-2.5 text-sm">
                {category}
                <Badge variant="secondary" className="ml-2 h-5 px-1.5 text-xs">
                  {filteredCatalogs[category]?.length || 0}
                </Badge>
              </TabsTrigger>
            ))}
          </TabsList>
          
          {Object.entries(filteredCatalogs).map(([category, items]) => (
            <TabsContent key={category} value={category} className="mt-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm text-muted-foreground">
                  {items.filter(i => isCatalogEnabled(i.id)).length} of {items.length} selected
                </p>
                <Button variant="ghost" size="sm" onClick={() => selectAll(category)}>
                  {items.every(i => isCatalogEnabled(i.id)) ? 'Deselect All' : 'Select All'}
                </Button>
              </div>
              
              <ScrollArea className="h-[300px] pr-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {items.map((catalog) => {
                    const isSelected = isCatalogEnabled(catalog.id)
                    const cfg = getCatalogConfig(catalog.id)
                    const position = catalogConfigs.filter(c => c.en !== false).findIndex(c => c.ci === catalog.id)
                    return (
                      <div
                        key={catalog.id}
                        className={cn(
                          "flex items-center gap-2 p-3 rounded-lg border text-left transition-colors",
                          isSelected
                            ? "border-primary bg-primary/10"
                            : "border-border hover:border-primary/50 hover:bg-muted/50"
                        )}
                      >
                        <button
                          onClick={() => toggleCatalog(catalog.id)}
                          className="flex items-center gap-3 flex-1 min-w-0"
                        >
                          {isSelected && position >= 0 && (
                            <Badge variant="secondary" className="w-6 h-6 p-0 justify-center shrink-0 text-xs">
                              {position + 1}
                            </Badge>
                          )}
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium truncate">{catalog.name}</p>
                            <div className="flex items-center gap-1">
                              <p className="text-xs text-muted-foreground truncate">{catalog.id}</p>
                              {cfg?.s && (
                                <Badge variant="outline" className="text-[10px] h-4 px-1 shrink-0">
                                  {cfg.s}
                                </Badge>
                              )}
                            </div>
                          </div>
                          {isSelected && (
                            <Check className="h-4 w-4 text-primary shrink-0" />
                          )}
                        </button>
                        {isSelected && renderSortSettings(catalog.id)}
                      </div>
                    )
                  })}
                </div>
              </ScrollArea>
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
    </Card>
  )
}
