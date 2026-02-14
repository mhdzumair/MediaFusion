import { useState, useEffect } from 'react'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { 
  Save, 
  X, 
  TestTube2, 
  Loader2,
  Settings2,
  Filter,
  ListTree,
  Info,
} from 'lucide-react'
import type { 
  UserRSSFeed, 
  UserRSSFeedCreate, 
  UserRSSFeedUpdate,
  RSSFeedParsingPatterns,
  RSSFeedFilters,
  CatalogPattern,
} from '@/lib/api'
import { useCreateRssFeed, useUpdateRssFeed, useTestRssFeedUrl } from '@/hooks'
import { TORRENT_TYPES, PARSING_PATTERN_FIELDS, FILTER_FIELDS } from './constants'
import { CatalogPatternsEditor } from './CatalogPatternsEditor'
import { RegexTesterModal } from './RegexTesterModal'

interface RSSFeedSlideOverProps {
  open: boolean
  onClose: () => void
  feed?: UserRSSFeed | null
  onSuccess?: () => void
}

const defaultParsingPatterns: RSSFeedParsingPatterns = {
  title: 'title',
  description: 'description',
  pubDate: 'pubDate',
}

const defaultFilters: RSSFeedFilters = {}

export function RSSFeedSlideOver({ open, onClose, feed, onSuccess }: RSSFeedSlideOverProps) {
  const isEdit = !!feed
  
  // Form state
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [isActive, setIsActive] = useState(true)
  const [source, setSource] = useState('')
  const [torrentType, setTorrentType] = useState('public')
  const [autoDetectCatalog, setAutoDetectCatalog] = useState(false)
  const [parsingPatterns, setParsingPatterns] = useState<RSSFeedParsingPatterns>(defaultParsingPatterns)
  const [filters, setFilters] = useState<RSSFeedFilters>(defaultFilters)
  const [catalogPatterns, setCatalogPatterns] = useState<CatalogPattern[]>([])
  
  // Test state
  const [testResult, setTestResult] = useState<{
    status: string
    message: string
    sample_item?: Record<string, unknown>
    detected_patterns?: Record<string, unknown>
  } | null>(null)
  
  // Regex tester state
  const [regexTesterOpen, setRegexTesterOpen] = useState(false)
  const [regexTesterField, setRegexTesterField] = useState<string>('')
  const [regexTesterSource, setRegexTesterSource] = useState<string>('')
  
  // Mutations
  const createFeed = useCreateRssFeed()
  const updateFeed = useUpdateRssFeed()
  const testFeedUrl = useTestRssFeedUrl()
  
  // Populate form when editing
  useEffect(() => {
    if (feed) {
      setName(feed.name)
      setUrl(feed.url)
      setIsActive(feed.is_active)
      setSource(feed.source || '')
      setTorrentType(feed.torrent_type || 'public')
      setAutoDetectCatalog(feed.auto_detect_catalog)
      setParsingPatterns(feed.parsing_patterns || defaultParsingPatterns)
      setFilters(feed.filters || defaultFilters)
      setCatalogPatterns(feed.catalog_patterns || [])
    } else {
      // Reset form for new feed
      setName('')
      setUrl('')
      setIsActive(true)
      setSource('')
      setTorrentType('public')
      setAutoDetectCatalog(false)
      setParsingPatterns(defaultParsingPatterns)
      setFilters(defaultFilters)
      setCatalogPatterns([])
      setTestResult(null)
    }
  }, [feed, open])
  
  const handleTest = async () => {
    if (!url) return
    
    try {
      const result = await testFeedUrl.mutateAsync({ url, patterns: parsingPatterns as Record<string, unknown> })
      setTestResult(result)
      
      // Auto-fill detected patterns
      if (result.status === 'success' && result.detected_patterns) {
        setParsingPatterns(prev => ({
          ...prev,
          ...Object.fromEntries(
            Object.entries(result.detected_patterns || {}).filter(([_, v]) => v)
          ),
        }))
      }
    } catch (error) {
      console.error('Test failed:', error)
    }
  }
  
  const handleSave = async () => {
    if (!name || !url) return
    
    const data: UserRSSFeedCreate | UserRSSFeedUpdate = {
      name,
      url,
      is_active: isActive,
      source: source || undefined,
      torrent_type: torrentType,
      auto_detect_catalog: autoDetectCatalog,
      parsing_patterns: parsingPatterns,
      filters,
      catalog_patterns: catalogPatterns,
    }
    
    try {
      if (isEdit && feed) {
        await updateFeed.mutateAsync({ feedId: feed.id, data: data as UserRSSFeedUpdate })
      } else {
        await createFeed.mutateAsync(data as UserRSSFeedCreate)
      }
      onSuccess?.()
      onClose()
    } catch (error) {
      console.error('Save failed:', error)
    }
  }
  
  const updateParsingPattern = (key: string, value: string) => {
    setParsingPatterns(prev => ({ ...prev, [key]: value || undefined }))
  }
  
  const updateFilter = (key: string, value: string | number | string[] | undefined) => {
    setFilters(prev => ({ ...prev, [key]: value }))
  }
  
  const openRegexTester = (field: string) => {
    // Get source content from test result if available
    const sourceContent = testResult?.sample_item 
      ? JSON.stringify(testResult.sample_item, null, 2)
      : ''
    setRegexTesterField(field)
    setRegexTesterSource(sourceContent)
    setRegexTesterOpen(true)
  }
  
  const handleRegexApply = (pattern: string) => {
    updateParsingPattern(`${regexTesterField}_regex`, pattern)
    setRegexTesterOpen(false)
  }
  
  const isPending = createFeed.isPending || updateFeed.isPending
  
  return (
    <>
      <Sheet open={open} onOpenChange={onClose}>
        <SheetContent className="w-full sm:max-w-2xl overflow-hidden flex flex-col">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2">
              <Settings2 className="h-5 w-5" />
              {isEdit ? 'Edit RSS Feed' : 'Add RSS Feed'}
            </SheetTitle>
            <SheetDescription>
              Configure your RSS feed settings, parsing patterns, and filters.
            </SheetDescription>
          </SheetHeader>
          
          <ScrollArea className="flex-1 -mx-6 px-6">
            <div className="space-y-4 py-4">
              <Accordion type="multiple" defaultValue={['basic', 'patterns']} className="space-y-2">
                {/* Basic Info */}
                <AccordionItem value="basic" className="border rounded-xl px-4">
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center gap-2">
                      <Info className="h-4 w-4 text-blue-500" />
                      <span>Basic Information</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="space-y-4 pt-2">
                    <div className="grid gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="name">Feed Name *</Label>
                        <Input
                          id="name"
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                          placeholder="My RSS Feed"
                        />
                      </div>
                      
                      <div className="space-y-2">
                        <Label htmlFor="url">Feed URL *</Label>
                        <div className="flex gap-2">
                          <Input
                            id="url"
                            value={url}
                            onChange={(e) => setUrl(e.target.value)}
                            placeholder="https://example.com/feed.xml"
                            className="flex-1"
                          />
                          <Button 
                            variant="outline" 
                            onClick={handleTest}
                            disabled={!url || testFeedUrl.isPending}
                          >
                            {testFeedUrl.isPending ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <TestTube2 className="h-4 w-4" />
                            )}
                          </Button>
                        </div>
                        {testResult && (
                          <div className={`text-sm p-2 rounded-lg ${
                            testResult.status === 'success' 
                              ? 'bg-emerald-500/10 text-emerald-500' 
                              : 'bg-red-500/10 text-red-500'
                          }`}>
                            {testResult.message}
                          </div>
                        )}
                      </div>
                      
                      <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                          <Label htmlFor="source">Source Name</Label>
                          <Input
                            id="source"
                            value={source}
                            onChange={(e) => setSource(e.target.value)}
                            placeholder="Optional source identifier"
                          />
                        </div>
                        
                        <div className="space-y-2">
                          <Label htmlFor="torrentType">Torrent Type</Label>
                          <Select value={torrentType} onValueChange={setTorrentType}>
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {TORRENT_TYPES.map(type => (
                                <SelectItem key={type.value} value={type.value}>
                                  {type.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                      
                      <div className="flex items-center justify-between">
                        <Label htmlFor="active">Enable Feed</Label>
                        <Switch
                          id="active"
                          checked={isActive}
                          onCheckedChange={setIsActive}
                        />
                      </div>
                    </div>
                  </AccordionContent>
                </AccordionItem>
                
                {/* Parsing Patterns */}
                <AccordionItem value="patterns" className="border rounded-xl px-4">
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center gap-2">
                      <Settings2 className="h-4 w-4 text-primary" />
                      <span>Parsing Patterns</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="space-y-4 pt-2">
                    <p className="text-sm text-muted-foreground">
                      Configure how to extract data from RSS items. Use dot notation for nested fields (e.g., enclosure.@url).
                    </p>
                    
                    <div className="grid gap-3">
                      {PARSING_PATTERN_FIELDS.map(field => (
                        <div key={field.key} className="space-y-1">
                          <Label className="text-xs">{field.label}</Label>
                          <div className="flex gap-2">
                            <Input
                              value={(parsingPatterns as Record<string, string | undefined>)[field.key] || ''}
                              onChange={(e) => updateParsingPattern(field.key, e.target.value)}
                              placeholder={field.placeholder}
                              className="text-sm"
                            />
                            {field.hasRegex && (
                              <>
                                <Input
                                  value={(parsingPatterns as Record<string, string | undefined>)[`${field.key}_regex`] || ''}
                                  onChange={(e) => updateParsingPattern(`${field.key}_regex`, e.target.value)}
                                  placeholder="Regex pattern"
                                  className="text-sm flex-1"
                                />
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => openRegexTester(field.key)}
                                  disabled={!testResult?.sample_item}
                                  title={testResult?.sample_item ? 'Test regex' : 'Test feed first'}
                                >
                                  <TestTube2 className="h-3 w-3" />
                                </Button>
                              </>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                    
                    <div className="space-y-1">
                      <Label className="text-xs">Episode Name Parser</Label>
                      <Input
                        value={parsingPatterns.episode_name_parser || ''}
                        onChange={(e) => updateParsingPattern('episode_name_parser', e.target.value)}
                        placeholder="Regex for episode naming"
                        className="text-sm"
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
                
                {/* Filters */}
                <AccordionItem value="filters" className="border rounded-xl px-4">
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center gap-2">
                      <Filter className="h-4 w-4 text-primary" />
                      <span>Filters</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="space-y-4 pt-2">
                    <p className="text-sm text-muted-foreground">
                      Filter torrents based on title, size, and seeders.
                    </p>
                    
                    <div className="grid gap-3">
                      {FILTER_FIELDS.map(field => (
                        <div key={field.key} className="space-y-1">
                          <Label className="text-xs">{field.label}</Label>
                          {field.type === 'number' ? (
                            <Input
                              type="number"
                              value={(filters as Record<string, number | undefined>)[field.key] || ''}
                              onChange={(e) => updateFilter(
                                field.key, 
                                e.target.value ? parseInt(e.target.value) : undefined
                              )}
                              placeholder={field.placeholder}
                              className="text-sm"
                            />
                          ) : field.key === 'category_filter' ? (
                            <Input
                              value={Array.isArray(filters.category_filter) 
                                ? filters.category_filter.join(', ') 
                                : ''}
                              onChange={(e) => updateFilter(
                                'category_filter',
                                e.target.value 
                                  ? e.target.value.split(',').map(s => s.trim())
                                  : undefined
                              )}
                              placeholder={field.placeholder}
                              className="text-sm"
                            />
                          ) : (
                            <Input
                              value={(filters as Record<string, string | undefined>)[field.key] || ''}
                              onChange={(e) => updateFilter(field.key, e.target.value || undefined)}
                              placeholder={field.placeholder}
                              className="text-sm"
                            />
                          )}
                        </div>
                      ))}
                    </div>
                  </AccordionContent>
                </AccordionItem>
                
                {/* Catalog Settings */}
                <AccordionItem value="catalogs" className="border rounded-xl px-4">
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center gap-2">
                      <ListTree className="h-4 w-4 text-emerald-500" />
                      <span>Catalog Settings</span>
                      {autoDetectCatalog && (
                        <Badge variant="secondary" className="ml-2">Auto-detect</Badge>
                      )}
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="space-y-4 pt-2">
                    <div className="flex items-center justify-between">
                      <div className="space-y-1">
                        <Label>Auto-detect Catalog</Label>
                        <p className="text-xs text-muted-foreground">
                          Automatically assign catalogs based on content
                        </p>
                      </div>
                      <Switch
                        checked={autoDetectCatalog}
                        onCheckedChange={setAutoDetectCatalog}
                      />
                    </div>
                    
                    {autoDetectCatalog && (
                      <CatalogPatternsEditor
                        patterns={catalogPatterns}
                        onChange={setCatalogPatterns}
                        sampleData={testResult?.sample_item}
                      />
                    )}
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
              
              {/* Sample Data Preview */}
              {testResult?.sample_item && (
                <div className="space-y-2">
                  <Label className="text-sm font-medium">Sample Feed Item</Label>
                  <Textarea
                    value={JSON.stringify(testResult.sample_item, null, 2)}
                    readOnly
                    className="font-mono text-xs h-40"
                  />
                </div>
              )}
            </div>
          </ScrollArea>
          
          {/* Footer */}
          <div className="flex justify-end gap-2 pt-4 border-t">
            <Button variant="outline" onClick={onClose}>
              <X className="mr-2 h-4 w-4" />
              Cancel
            </Button>
            <Button 
              onClick={handleSave}
              disabled={!name || !url || isPending}
              className="bg-gradient-to-r from-primary to-primary/80"
            >
              {isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              {isEdit ? 'Save Changes' : 'Create Feed'}
            </Button>
          </div>
        </SheetContent>
      </Sheet>
      
      <RegexTesterModal
        open={regexTesterOpen}
        onClose={() => setRegexTesterOpen(false)}
        sourceContent={regexTesterSource}
        fieldName={regexTesterField}
        currentPattern={(parsingPatterns as Record<string, string | undefined>)[`${regexTesterField}_regex`] || ''}
        onApply={handleRegexApply}
      />
    </>
  )
}






