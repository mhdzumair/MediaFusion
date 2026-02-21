import { useState, useCallback, useMemo } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  ArrowRight,
  ArrowLeft,
  Check,
  Loader2,
  Link2,
  Settings2,
  Filter,
  Sparkles,
  AlertCircle,
  CheckCircle2,
  Copy,
  Eye,
  ChevronDown,
  ChevronRight,
  FlaskConical,
} from 'lucide-react'
import { cn } from '@/lib/utils'
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

// Regex Test Modal Component
interface RegexTestModalProps {
  open: boolean
  onClose: () => void
  fieldName: string
  sourceContent: string
  initialPattern: string
  onApply: (pattern: string) => void
}

function RegexTestModal({ open, onClose, fieldName, sourceContent, initialPattern, onApply }: RegexTestModalProps) {
  const [pattern, setPattern] = useState(initialPattern)

  // Sync pattern when initialPattern or open changes (during render, not in effect)
  const [prevOpen, setPrevOpen] = useState(open)
  const [prevPattern, setPrevPattern] = useState(initialPattern)
  if ((open && !prevOpen) || prevPattern !== initialPattern) {
    setPrevOpen(open)
    setPrevPattern(initialPattern)
    setPattern(initialPattern)
  }

  // Derive result from pattern and sourceContent (useMemo instead of effect)
  const result = useMemo(() => {
    if (!pattern || !sourceContent) {
      return { match: null as string | null, groups: [] as string[], error: null as string | null }
    }
    try {
      const regex = new RegExp(pattern)
      const match = sourceContent.match(regex)
      if (match) {
        return { match: match[0], groups: match.slice(1), error: null }
      }
      return { match: null, groups: [], error: 'No match found' }
    } catch (e) {
      return { match: null, groups: [], error: e instanceof Error ? e.message : 'Invalid regex' }
    }
  }, [pattern, sourceContent])

  if (!open) return null

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FlaskConical className="h-5 w-5 text-primary" />
            Test Regex - {fieldName}
          </DialogTitle>
          <DialogDescription>Test your regex pattern against the extracted content</DialogDescription>
        </DialogHeader>

        <ScrollArea className="flex-1">
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Source Content</Label>
              <Textarea value={sourceContent} readOnly className="font-mono text-xs h-24 bg-muted" />
            </div>

            <div className="space-y-2">
              <Label>Regex Pattern</Label>
              <Input
                value={pattern}
                onChange={(e) => setPattern(e.target.value)}
                placeholder="Enter regex pattern"
                className="font-mono"
              />
            </div>

            <div className="space-y-2">
              <Label>Result</Label>
              <div
                className={cn(
                  'p-3 rounded-lg border font-mono text-sm',
                  result.error
                    ? 'bg-red-500/10 border-red-500/30'
                    : result.match
                      ? 'bg-emerald-500/10 border-emerald-500/30'
                      : 'bg-muted',
                )}
              >
                {result.error ? (
                  <span className="text-red-500">{result.error}</span>
                ) : result.match ? (
                  <div className="space-y-2">
                    <div>
                      <span className="text-muted-foreground">Full match:</span>{' '}
                      <span className="text-emerald-500">{result.match}</span>
                    </div>
                    {result.groups.length > 0 && (
                      <div>
                        <span className="text-muted-foreground">Capture groups:</span>
                        {result.groups.map((g, i) => (
                          <div key={i} className="ml-2">
                            <span className="text-muted-foreground">Group {i + 1}:</span>{' '}
                            <span className="text-primary">{g}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <span className="text-muted-foreground">Enter a pattern to test</span>
                )}
              </div>
            </div>
          </div>
        </ScrollArea>

        <div className="flex justify-end gap-2 pt-4 border-t">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              onApply(pattern)
              onClose()
            }}
            disabled={!pattern || !!result.error}
          >
            Apply Pattern
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

interface RSSFeedWizardProps {
  open: boolean
  onClose: () => void
  feed?: UserRSSFeed | null
  onSuccess?: () => void
}

type WizardStep = 'url' | 'patterns' | 'filters' | 'review'

const STEPS: { id: WizardStep; title: string; icon: React.ReactNode }[] = [
  { id: 'url', title: 'Feed URL', icon: <Link2 className="h-4 w-4" /> },
  { id: 'patterns', title: 'Parsing', icon: <Settings2 className="h-4 w-4" /> },
  { id: 'filters', title: 'Filters', icon: <Filter className="h-4 w-4" /> },
  { id: 'review', title: 'Review', icon: <Check className="h-4 w-4" /> },
]

const defaultParsingPatterns: RSSFeedParsingPatterns = {
  title: 'title',
  description: 'description',
  pubDate: 'pubDate',
}

const defaultFilters: RSSFeedFilters = {}

export function RSSFeedWizard({ open, onClose, feed, onSuccess }: RSSFeedWizardProps) {
  const isEdit = !!feed
  const [currentStep, setCurrentStep] = useState<WizardStep>('url')

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
  const [hasTestedUrl, setHasTestedUrl] = useState(false)
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set(['optional'])) // Start expanded

  // Regex test modal state
  const [regexTestModal, setRegexTestModal] = useState<{
    open: boolean
    fieldName: string
    fieldKey: string
    sourceContent: string
    initialPattern: string
  }>({ open: false, fieldName: '', fieldKey: '', sourceContent: '', initialPattern: '' })

  // Mutations
  const createFeed = useCreateRssFeed()
  const updateFeed = useUpdateRssFeed()
  const testFeedUrl = useTestRssFeedUrl()

  // Reset when opening/closing (during render, not in effect)
  const [prevOpen, setPrevOpen] = useState(open)
  const [prevFeed, setPrevFeed] = useState(feed)
  if (open && (!prevOpen || prevFeed !== feed)) {
    setPrevOpen(open)
    setPrevFeed(feed)
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
      setHasTestedUrl(true)
      setCurrentStep('patterns')
    } else {
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
      setHasTestedUrl(false)
      setCurrentStep('url')
    }
  }

  const handleTest = async () => {
    if (!url) return

    try {
      const result = await testFeedUrl.mutateAsync({ url, patterns: parsingPatterns as Record<string, unknown> })
      setTestResult(result)

      if (result.status === 'success') {
        setHasTestedUrl(true)

        // Auto-fill name from feed if empty
        if (!name && result.sample_item) {
          const item = result.sample_item as Record<string, unknown>
          const channel = item.channel as Record<string, unknown> | undefined
          const feed = item.feed as Record<string, unknown> | undefined
          const feedTitle = channel?.title || feed?.title || new URL(url).hostname
          if (typeof feedTitle === 'string') {
            setName(feedTitle)
          }
        }

        // Auto-fill detected patterns
        if (result.detected_patterns) {
          setParsingPatterns((prev) => ({
            ...prev,
            ...Object.fromEntries(Object.entries(result.detected_patterns || {}).filter(([, v]) => v)),
          }))
        }
      }
    } catch (error) {
      console.error('Test failed:', error)
      setTestResult({
        status: 'error',
        message: error instanceof Error ? error.message : 'Failed to test feed',
      })
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
    setParsingPatterns((prev) => ({ ...prev, [key]: value || undefined }))
  }

  const updateFilter = (key: string, value: string | number | string[] | undefined) => {
    setFilters((prev) => ({ ...prev, [key]: value }))
  }

  const toggleFieldExpand = (field: string) => {
    setExpandedFields((prev) => {
      const next = new Set(prev)
      if (next.has(field)) {
        next.delete(field)
      } else {
        next.add(field)
      }
      return next
    })
  }

  const getStepIndex = (step: WizardStep) => STEPS.findIndex((s) => s.id === step)
  const currentStepIndex = getStepIndex(currentStep)

  const canProceed = () => {
    switch (currentStep) {
      case 'url':
        return hasTestedUrl && url && name
      case 'patterns':
        return parsingPatterns.title
      case 'filters':
        return true
      case 'review':
        return true
      default:
        return false
    }
  }

  const nextStep = () => {
    const nextIndex = currentStepIndex + 1
    if (nextIndex < STEPS.length) {
      setCurrentStep(STEPS[nextIndex].id)
    }
  }

  const prevStep = () => {
    const prevIndex = currentStepIndex - 1
    if (prevIndex >= 0) {
      setCurrentStep(STEPS[prevIndex].id)
    }
  }

  const isPending = createFeed.isPending || updateFeed.isPending

  // Extract value from sample item for preview
  const extractPreviewValue = useCallback(
    (path: string): string => {
      if (!testResult?.sample_item || !path) return ''
      try {
        const parts = path.split('.')
        let value: unknown = testResult.sample_item
        for (const part of parts) {
          if (value && typeof value === 'object') {
            value = (value as Record<string, unknown>)[part]
          } else {
            return ''
          }
        }
        if (typeof value === 'string') return value
        if (typeof value === 'number') return String(value)
        return ''
      } catch {
        return ''
      }
    },
    [testResult],
  )

  // Open regex test modal
  const openRegexTest = (fieldKey: string, fieldLabel: string) => {
    const path = (parsingPatterns as Record<string, string | undefined>)[fieldKey] || ''
    const sourceContent = extractPreviewValue(path)
    const currentPattern = (parsingPatterns as Record<string, string | undefined>)[`${fieldKey}_regex`] || ''

    if (!sourceContent) {
      alert('Please test the feed first and set a field path to extract content for regex testing.')
      return
    }

    setRegexTestModal({
      open: true,
      fieldName: fieldLabel,
      fieldKey,
      sourceContent,
      initialPattern: currentPattern,
    })
  }

  const handleRegexApply = (pattern: string) => {
    updateParsingPattern(`${regexTestModal.fieldKey}_regex`, pattern)
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onClose}>
        <DialogContent className="max-w-5xl w-[95vw] h-[90vh] flex flex-col p-0 gap-0 overflow-hidden">
          {/* Header */}
          <DialogHeader className="px-6 py-4 border-b bg-gradient-to-r from-primary/10 to-primary/5">
            <DialogTitle className="flex items-center gap-2 text-xl">
              <Sparkles className="h-5 w-5 text-primary" />
              {isEdit ? 'Edit RSS Feed' : 'Add RSS Feed'}
            </DialogTitle>
            <DialogDescription>
              {isEdit ? 'Update your RSS feed configuration' : 'Configure a new RSS feed to scrape torrents'}
            </DialogDescription>
          </DialogHeader>

          {/* Progress Steps */}
          <div className="px-6 py-3 border-b bg-muted/30">
            <div className="flex items-center justify-between">
              {STEPS.map((step, index) => (
                <div key={step.id} className="flex items-center">
                  <button
                    onClick={() => {
                      if (index <= currentStepIndex || (index === currentStepIndex + 1 && canProceed())) {
                        setCurrentStep(step.id)
                      }
                    }}
                    disabled={index > currentStepIndex + 1 || (index === currentStepIndex + 1 && !canProceed())}
                    className={cn(
                      'flex items-center gap-2 px-3 py-1.5 rounded-full transition-all',
                      currentStep === step.id
                        ? 'bg-primary text-white'
                        : index < currentStepIndex
                          ? 'bg-emerald-500/20 text-emerald-500'
                          : 'bg-muted text-muted-foreground',
                      index <= currentStepIndex && 'cursor-pointer hover:opacity-80',
                      index > currentStepIndex + 1 && 'opacity-50 cursor-not-allowed',
                    )}
                  >
                    {index < currentStepIndex ? <CheckCircle2 className="h-4 w-4" /> : step.icon}
                    <span className="text-sm font-medium">{step.title}</span>
                  </button>
                  {index < STEPS.length - 1 && (
                    <div className={cn('w-12 h-0.5 mx-2', index < currentStepIndex ? 'bg-emerald-500' : 'bg-muted')} />
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Content */}
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-6">
              {/* Step 1: URL */}
              {currentStep === 'url' && (
                <div className="space-y-6">
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">Enter RSS Feed URL</CardTitle>
                      <CardDescription>Enter the URL of your RSS feed and test it to verify it works</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="space-y-2">
                        <Label htmlFor="url">Feed URL *</Label>
                        <div className="flex gap-2">
                          <Input
                            id="url"
                            value={url}
                            onChange={(e) => {
                              setUrl(e.target.value)
                              setHasTestedUrl(false)
                              setTestResult(null)
                            }}
                            placeholder="https://example.com/rss/feed.xml"
                            className="flex-1 font-mono text-sm"
                          />
                          <Button
                            onClick={handleTest}
                            disabled={!url || testFeedUrl.isPending}
                            className="min-w-[120px]"
                          >
                            {testFeedUrl.isPending ? (
                              <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Testing...
                              </>
                            ) : (
                              <>
                                <Eye className="mr-2 h-4 w-4" />
                                Test Feed
                              </>
                            )}
                          </Button>
                        </div>
                      </div>

                      {testResult && (
                        <div
                          className={cn(
                            'p-4 rounded-lg border',
                            testResult.status === 'success'
                              ? 'bg-emerald-500/10 border-emerald-500/30'
                              : 'bg-red-500/10 border-red-500/30',
                          )}
                        >
                          <div className="flex items-start gap-3">
                            {testResult.status === 'success' ? (
                              <CheckCircle2 className="h-5 w-5 text-emerald-500 mt-0.5" />
                            ) : (
                              <AlertCircle className="h-5 w-5 text-red-500 mt-0.5" />
                            )}
                            <div className="flex-1">
                              <p
                                className={cn(
                                  'font-medium',
                                  testResult.status === 'success' ? 'text-emerald-500' : 'text-red-500',
                                )}
                              >
                                {testResult.message}
                              </p>
                              {testResult.status === 'success' && testResult.sample_item && (
                                <p className="text-sm text-muted-foreground mt-1">
                                  Sample item detected. You can now configure parsing patterns.
                                </p>
                              )}
                            </div>
                          </div>
                        </div>
                      )}

                      {hasTestedUrl && (
                        <>
                          <div className="space-y-2">
                            <Label htmlFor="name">Feed Name *</Label>
                            <Input
                              id="name"
                              value={name}
                              onChange={(e) => setName(e.target.value)}
                              placeholder="My Torrent Feed"
                            />
                          </div>

                          <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                              <Label htmlFor="source">Source Name</Label>
                              <Input
                                id="source"
                                value={source}
                                onChange={(e) => setSource(e.target.value)}
                                placeholder="e.g., 1337x, RARBG"
                              />
                              <p className="text-xs text-muted-foreground">Identifier shown in stream results</p>
                            </div>

                            <div className="space-y-2">
                              <Label htmlFor="torrentType">Torrent Type</Label>
                              <Select value={torrentType} onValueChange={setTorrentType}>
                                <SelectTrigger>
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {TORRENT_TYPES.map((type) => (
                                    <SelectItem key={type.value} value={type.value}>
                                      {type.label}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                          </div>

                          <div className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
                            <div className="space-y-0.5">
                              <Label htmlFor="active" className="cursor-pointer">
                                Enable Feed
                              </Label>
                              <p className="text-xs text-muted-foreground">
                                Disable to pause scraping without deleting
                              </p>
                            </div>
                            <Switch id="active" checked={isActive} onCheckedChange={setIsActive} />
                          </div>
                        </>
                      )}
                    </CardContent>
                  </Card>

                  {/* Sample Data Preview */}
                  {testResult?.sample_item && (
                    <Card>
                      <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center justify-between">
                          <span>Sample Feed Item</span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              navigator.clipboard.writeText(JSON.stringify(testResult.sample_item, null, 2))
                            }
                          >
                            <Copy className="h-3 w-3 mr-1" />
                            Copy
                          </Button>
                        </CardTitle>
                      </CardHeader>
                      <CardContent>
                        <ScrollArea className="max-h-48 rounded-lg bg-black/50">
                          <pre className="p-3 font-mono text-xs">{JSON.stringify(testResult.sample_item, null, 2)}</pre>
                          <ScrollBar orientation="horizontal" />
                        </ScrollArea>
                      </CardContent>
                    </Card>
                  )}
                </div>
              )}

              {/* Step 2: Parsing Patterns */}
              {currentStep === 'patterns' && (
                <div className="space-y-6">
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">Configure Parsing Patterns</CardTitle>
                      <CardDescription>
                        Define how to extract data from each RSS item. Use dot notation for nested fields (e.g.,
                        enclosure.@url)
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {/* Essential fields */}
                      <div className="space-y-4">
                        <h4 className="text-sm font-semibold text-muted-foreground">Essential Fields</h4>
                        {PARSING_PATTERN_FIELDS.filter((f) =>
                          ['title', 'magnet', 'torrent', 'info_hash'].includes(f.key),
                        ).map((field) => (
                          <div key={field.key} className="space-y-2 p-3 bg-muted/30 rounded-lg">
                            <div className="flex items-center justify-between flex-wrap gap-2">
                              <Label className="flex items-center gap-2 font-medium">
                                {field.label}
                                {field.key === 'title' && (
                                  <Badge variant="destructive" className="text-xs">
                                    Required
                                  </Badge>
                                )}
                                {(field.key === 'magnet' || field.key === 'torrent' || field.key === 'info_hash') && (
                                  <Badge variant="secondary" className="text-xs">
                                    At least one required
                                  </Badge>
                                )}
                              </Label>
                            </div>
                            <div className="space-y-3">
                              <div className="space-y-1">
                                <Label className="text-xs text-muted-foreground">Field Path</Label>
                                <Input
                                  value={(parsingPatterns as Record<string, string | undefined>)[field.key] || ''}
                                  onChange={(e) => updateParsingPattern(field.key, e.target.value)}
                                  placeholder={field.placeholder}
                                  className="font-mono text-sm"
                                />
                                {extractPreviewValue((parsingPatterns as Record<string, string>)[field.key] || '') && (
                                  <p className="text-xs text-muted-foreground mt-1 truncate">
                                    <span className="text-emerald-500">Preview:</span>{' '}
                                    {extractPreviewValue(
                                      (parsingPatterns as Record<string, string>)[field.key] || '',
                                    ).substring(0, 80)}
                                    ...
                                  </p>
                                )}
                              </div>
                              {field.hasRegex && (
                                <div className="space-y-1">
                                  <div className="flex items-center justify-between">
                                    <Label className="text-xs text-muted-foreground">Regex Pattern (optional)</Label>
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="h-6 text-xs"
                                      onClick={() => openRegexTest(field.key, field.label)}
                                      disabled={
                                        !extractPreviewValue(
                                          (parsingPatterns as Record<string, string>)[field.key] || '',
                                        )
                                      }
                                    >
                                      <FlaskConical className="h-3 w-3 mr-1" />
                                      Test Regex
                                    </Button>
                                  </div>
                                  <Input
                                    value={
                                      (parsingPatterns as Record<string, string | undefined>)[`${field.key}_regex`] ||
                                      ''
                                    }
                                    onChange={(e) => updateParsingPattern(`${field.key}_regex`, e.target.value)}
                                    placeholder='e.g., magnet:\?[^\s<>"]+ or (\d+) for capture group'
                                    className="font-mono text-sm"
                                  />
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>

                      {/* Optional fields - collapsible */}
                      <div className="space-y-2">
                        <button
                          onClick={() => toggleFieldExpand('optional')}
                          className="flex items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground transition-colors w-full"
                        >
                          {expandedFields.has('optional') ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                          Optional Fields (
                          {
                            PARSING_PATTERN_FIELDS.filter(
                              (f) => !['title', 'magnet', 'torrent', 'info_hash'].includes(f.key),
                            ).length
                          }{' '}
                          fields)
                        </button>

                        {expandedFields.has('optional') && (
                          <div className="space-y-3 pl-4 border-l-2 border-muted ml-2">
                            {PARSING_PATTERN_FIELDS.filter(
                              (f) => !['title', 'magnet', 'torrent', 'info_hash'].includes(f.key),
                            ).map((field) => (
                              <div key={field.key} className="space-y-2 p-3 bg-muted/20 rounded">
                                <Label className="text-sm font-medium">{field.label}</Label>
                                <div className="space-y-2">
                                  <div className="space-y-1">
                                    <Label className="text-xs text-muted-foreground">Field Path</Label>
                                    <Input
                                      value={(parsingPatterns as Record<string, string | undefined>)[field.key] || ''}
                                      onChange={(e) => updateParsingPattern(field.key, e.target.value)}
                                      placeholder={field.placeholder}
                                      className="font-mono text-sm"
                                    />
                                    {extractPreviewValue(
                                      (parsingPatterns as Record<string, string>)[field.key] || '',
                                    ) && (
                                      <p className="text-xs text-muted-foreground mt-1 truncate">
                                        <span className="text-emerald-500">Preview:</span>{' '}
                                        {extractPreviewValue(
                                          (parsingPatterns as Record<string, string>)[field.key] || '',
                                        ).substring(0, 60)}
                                        ...
                                      </p>
                                    )}
                                  </div>
                                  {field.hasRegex && (
                                    <div className="space-y-1">
                                      <div className="flex items-center justify-between">
                                        <Label className="text-xs text-muted-foreground">Regex Pattern</Label>
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="h-6 text-xs"
                                          onClick={() => openRegexTest(field.key, field.label)}
                                          disabled={
                                            !extractPreviewValue(
                                              (parsingPatterns as Record<string, string>)[field.key] || '',
                                            )
                                          }
                                        >
                                          <FlaskConical className="h-3 w-3 mr-1" />
                                          Test
                                        </Button>
                                      </div>
                                      <Input
                                        value={
                                          (parsingPatterns as Record<string, string | undefined>)[
                                            `${field.key}_regex`
                                          ] || ''
                                        }
                                        onChange={(e) => updateParsingPattern(`${field.key}_regex`, e.target.value)}
                                        placeholder="e.g., (\d+\.?\d*)\s*(GB|MB|KB)"
                                        className="font-mono text-sm"
                                      />
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </CardContent>
                  </Card>

                  {/* Sample Data Reference */}
                  {testResult?.sample_item && (
                    <Card>
                      <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center justify-between">
                          <span>Sample Data Reference</span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              navigator.clipboard.writeText(JSON.stringify(testResult.sample_item, null, 2))
                            }
                          >
                            <Copy className="h-3 w-3 mr-1" />
                            Copy
                          </Button>
                        </CardTitle>
                      </CardHeader>
                      <CardContent>
                        <ScrollArea className="max-h-40 rounded-lg bg-black/50">
                          <pre className="p-3 font-mono text-xs">{JSON.stringify(testResult.sample_item, null, 2)}</pre>
                          <ScrollBar orientation="horizontal" />
                        </ScrollArea>
                      </CardContent>
                    </Card>
                  )}
                </div>
              )}

              {/* Step 3: Filters */}
              {currentStep === 'filters' && (
                <div className="space-y-6">
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">Configure Filters (Optional)</CardTitle>
                      <CardDescription>Set up filters to include or exclude torrents based on criteria</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {FILTER_FIELDS.map((field) => (
                        <div key={field.key} className="space-y-2">
                          <Label className="text-sm">{field.label}</Label>
                          {field.type === 'number' ? (
                            <Input
                              type="number"
                              value={(filters as Record<string, number | undefined>)[field.key] || ''}
                              onChange={(e) =>
                                updateFilter(field.key, e.target.value ? parseInt(e.target.value) : undefined)
                              }
                              placeholder={field.placeholder}
                            />
                          ) : field.key === 'category_filter' ? (
                            <Input
                              value={Array.isArray(filters.category_filter) ? filters.category_filter.join(', ') : ''}
                              onChange={(e) =>
                                updateFilter(
                                  'category_filter',
                                  e.target.value ? e.target.value.split(',').map((s) => s.trim()) : undefined,
                                )
                              }
                              placeholder={field.placeholder}
                            />
                          ) : (
                            <Input
                              value={(filters as Record<string, string | undefined>)[field.key] || ''}
                              onChange={(e) => updateFilter(field.key, e.target.value || undefined)}
                              placeholder={field.placeholder}
                            />
                          )}
                        </div>
                      ))}
                    </CardContent>
                  </Card>

                  {/* Catalog Settings */}
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg flex items-center justify-between">
                        <span>Catalog Auto-Detection</span>
                        <Switch checked={autoDetectCatalog} onCheckedChange={setAutoDetectCatalog} />
                      </CardTitle>
                      <CardDescription>
                        Automatically assign items to catalogs based on content patterns
                      </CardDescription>
                    </CardHeader>
                    {autoDetectCatalog && (
                      <CardContent>
                        <CatalogPatternsEditor
                          patterns={catalogPatterns}
                          onChange={setCatalogPatterns}
                          sampleData={testResult?.sample_item}
                        />
                      </CardContent>
                    )}
                  </Card>
                </div>
              )}

              {/* Step 4: Review */}
              {currentStep === 'review' && (
                <div className="space-y-6">
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">Review Configuration</CardTitle>
                      <CardDescription>Verify your RSS feed settings before saving</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1">
                          <Label className="text-xs text-muted-foreground">Feed Name</Label>
                          <p className="font-medium">{name}</p>
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs text-muted-foreground">Status</Label>
                          <Badge variant={isActive ? 'default' : 'secondary'}>{isActive ? 'Active' : 'Paused'}</Badge>
                        </div>
                        <div className="space-y-1 col-span-2">
                          <Label className="text-xs text-muted-foreground">URL</Label>
                          <p className="font-mono text-sm break-all">{url}</p>
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs text-muted-foreground">Source</Label>
                          <p>{source || 'Not specified'}</p>
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs text-muted-foreground">Torrent Type</Label>
                          <Badge variant="outline">{torrentType}</Badge>
                        </div>
                      </div>

                      <div className="border-t pt-4">
                        <Label className="text-xs text-muted-foreground">Parsing Patterns</Label>
                        <div className="mt-2 grid grid-cols-2 gap-2">
                          {Object.entries(parsingPatterns)
                            .filter(([, v]) => v)
                            .map(([key, value]) => (
                              <div key={key} className="flex items-center gap-2 text-sm">
                                <span className="text-muted-foreground">{key}:</span>
                                <code className="bg-muted px-1 rounded text-xs">{value}</code>
                              </div>
                            ))}
                        </div>
                      </div>

                      {Object.keys(filters).filter((k) => (filters as Record<string, unknown>)[k]).length > 0 && (
                        <div className="border-t pt-4">
                          <Label className="text-xs text-muted-foreground">Filters</Label>
                          <div className="mt-2 grid grid-cols-2 gap-2">
                            {Object.entries(filters)
                              .filter(([, v]) => v)
                              .map(([key, value]) => (
                                <div key={key} className="flex items-center gap-2 text-sm">
                                  <span className="text-muted-foreground">{key}:</span>
                                  <code className="bg-muted px-1 rounded text-xs">
                                    {Array.isArray(value) ? value.join(', ') : String(value)}
                                  </code>
                                </div>
                              ))}
                          </div>
                        </div>
                      )}

                      {autoDetectCatalog && catalogPatterns.length > 0 && (
                        <div className="border-t pt-4">
                          <Label className="text-xs text-muted-foreground">Catalog Patterns</Label>
                          <p className="text-sm mt-1">{catalogPatterns.length} pattern(s) configured</p>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                </div>
              )}
            </div>
          </ScrollArea>

          {/* Footer */}
          <div className="flex items-center justify-between px-6 py-4 border-t bg-muted/30">
            <Button variant="outline" onClick={currentStepIndex === 0 ? onClose : prevStep}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              {currentStepIndex === 0 ? 'Cancel' : 'Back'}
            </Button>

            {currentStep === 'review' ? (
              <Button onClick={handleSave} disabled={isPending} className="bg-gradient-to-r from-primary to-primary/80">
                {isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Saving...
                  </>
                ) : (
                  <>
                    <Check className="mr-2 h-4 w-4" />
                    {isEdit ? 'Save Changes' : 'Create Feed'}
                  </>
                )}
              </Button>
            ) : (
              <Button onClick={nextStep} disabled={!canProceed()}>
                Next
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Regex Test Modal */}
      <RegexTestModal
        open={regexTestModal.open}
        onClose={() => setRegexTestModal((prev) => ({ ...prev, open: false }))}
        fieldName={regexTestModal.fieldName}
        sourceContent={regexTestModal.sourceContent}
        initialPattern={regexTestModal.initialPattern}
        onApply={handleRegexApply}
      />
    </>
  )
}
