import { useState, useEffect } from 'react'
import {
  Eye,
  EyeOff,
  Plus,
  Trash2,
  TestTube,
  Loader2,
  CheckCircle2,
  XCircle,
  Newspaper,
  Settings2,
  ChevronDown,
  ChevronUp,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { useToast } from '@/hooks/use-toast'
import { testNewznabIndexer } from '@/lib/api'
import type { ProfileConfig, NewznabIndexerConfig } from './types'

// Generate a simple unique ID for new indexers
function generateIndexerId(): string {
  return Math.random().toString(36).substring(2, 10)
}

// Default Newznab categories
const DEFAULT_MOVIE_CATEGORIES = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060]
const DEFAULT_TV_CATEGORIES = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070, 5080]

interface UsenetSettingsProps {
  config: ProfileConfig
  onChange: (config: ProfileConfig) => void
}

export function UsenetSettings({ config, onChange }: UsenetSettingsProps) {
  const { toast } = useToast()

  // Local state
  const [enableUsenet, setEnableUsenet] = useState(config.eus ?? true)
  const [preferUsenet, setPreferUsenet] = useState(config.puot ?? false)
  // Newznab indexers are stored in indexer_config.nz (ic.nz)
  const [indexers, setIndexers] = useState<NewznabIndexerConfig[]>(config.ic?.nz ?? [])

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)

  // Test results
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string }>>({})
  const [testingId, setTestingId] = useState<string | null>(null)

  // Sync with config changes
  useEffect(() => {
    setEnableUsenet(config.eus ?? true)
    setPreferUsenet(config.puot ?? false)
    setIndexers(config.ic?.nz ?? [])
  }, [config.eus, config.puot, config.ic?.nz])

  // Update parent config
  const updateConfig = (newEnableUsenet: boolean, newPreferUsenet: boolean, newIndexers: NewznabIndexerConfig[]) => {
    // Store Newznab indexers in indexer_config.nz (ic.nz)
    const updatedIc = {
      ...(config.ic || {}),
      nz: newIndexers.length > 0 ? newIndexers : undefined,
    }
    // Clean up ic if it's empty
    const hasIndexerConfig = updatedIc.pr || updatedIc.jk || updatedIc.tz?.length || updatedIc.nz?.length

    onChange({
      ...config,
      eus: newEnableUsenet,
      puot: newPreferUsenet,
      ic: hasIndexerConfig ? updatedIc : undefined,
    })
  }

  // Handlers
  const handleEnableUsenetChange = (checked: boolean) => {
    setEnableUsenet(checked)
    updateConfig(checked, preferUsenet, indexers)
  }

  const handlePreferUsenetChange = (checked: boolean) => {
    setPreferUsenet(checked)
    updateConfig(enableUsenet, checked, indexers)
  }

  const addIndexer = (data: Omit<NewznabIndexerConfig, 'i'>) => {
    const newIndexer: NewznabIndexerConfig = {
      ...data,
      i: generateIndexerId(),
    }
    const newIndexers = [...indexers, newIndexer]
    setIndexers(newIndexers)
    updateConfig(enableUsenet, preferUsenet, newIndexers)
    setDialogOpen(false)
    setEditingIndex(null)
  }

  const updateIndexer = (index: number, data: Omit<NewznabIndexerConfig, 'i'>) => {
    const updated = [...indexers]
    updated[index] = { ...data, i: indexers[index].i }
    setIndexers(updated)
    updateConfig(enableUsenet, preferUsenet, updated)
    setDialogOpen(false)
    setEditingIndex(null)
  }

  const deleteIndexer = (index: number) => {
    const newIndexers = indexers.filter((_, i) => i !== index)
    setIndexers(newIndexers)
    updateConfig(enableUsenet, preferUsenet, newIndexers)
  }

  const testIndexer = async (indexer: NewznabIndexerConfig) => {
    setTestingId(indexer.i)
    try {
      // Test connection via backend API to avoid CORS issues
      const result = await testNewznabIndexer({
        name: indexer.n,
        url: indexer.u,
        api_key: indexer.ak,
        enabled: indexer.en ?? true,
        categories: [...(indexer.mc ?? []), ...(indexer.tc ?? [])],
      })

      setTestResults((prev) => ({
        ...prev,
        [indexer.i]: { success: result.success, message: result.message },
      }))

      if (result.success) {
        toast({
          title: 'Connection successful',
          description: result.message,
        })
      } else {
        toast({
          title: 'Connection failed',
          description: result.message,
          variant: 'destructive',
        })
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Connection failed'
      setTestResults((prev) => ({
        ...prev,
        [indexer.i]: { success: false, message },
      }))
      toast({
        title: 'Connection failed',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setTestingId(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Newspaper className="h-5 w-5" />
          Usenet Settings
        </CardTitle>
        <CardDescription>Configure Usenet streaming and Newznab indexers for NZB content</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Global Usenet Toggle */}
        <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg">
          <div className="space-y-0.5">
            <Label className="text-base">Enable Usenet Streams</Label>
            <p className="text-sm text-muted-foreground">Show Usenet/NZB streams alongside torrent streams</p>
          </div>
          <Switch checked={enableUsenet} onCheckedChange={handleEnableUsenetChange} />
        </div>

        {enableUsenet && (
          <>
            {/* Prefer Usenet Toggle */}
            <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg">
              <div className="space-y-0.5">
                <Label>Prefer Usenet Over Torrent</Label>
                <p className="text-sm text-muted-foreground">
                  When enabled, Usenet streams will be prioritized over torrent streams
                </p>
              </div>
              <Switch checked={preferUsenet} onCheckedChange={handlePreferUsenetChange} />
            </div>

            {/* Newznab Indexers */}
            <Accordion type="single" collapsible defaultValue="indexers">
              <AccordionItem value="indexers" className="border-none">
                <AccordionTrigger className="hover:no-underline">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">Newznab Indexers</span>
                    {indexers.length > 0 && (
                      <Badge variant="secondary" className="bg-primary/10 text-primary">
                        {indexers.filter((i) => i.en !== false).length} active
                      </Badge>
                    )}
                  </div>
                </AccordionTrigger>
                <AccordionContent className="space-y-4 pt-4">
                  <p className="text-sm text-muted-foreground">
                    Add Newznab-compatible indexers (NZBgeek, NZBFinder, DrunkenSlug, etc.) to search for NZB content
                  </p>

                  {/* Indexer List */}
                  {indexers.length > 0 ? (
                    <div className="space-y-2">
                      {indexers.map((indexer, index) => (
                        <IndexerCard
                          key={indexer.i}
                          indexer={indexer}
                          testResult={testResults[indexer.i]}
                          onEdit={() => {
                            setEditingIndex(index)
                            setDialogOpen(true)
                          }}
                          onDelete={() => {
                            if (confirm(`Delete indexer "${indexer.n}"?`)) {
                              deleteIndexer(index)
                            }
                          }}
                          onTest={() => testIndexer(indexer)}
                          isTesting={testingId === indexer.i}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-6 text-muted-foreground">
                      <Newspaper className="h-8 w-8 mx-auto mb-2 opacity-50" />
                      <p>No Newznab indexers configured</p>
                      <p className="text-xs mt-1">Add an indexer to enable Usenet searching</p>
                    </div>
                  )}

                  {/* Add Indexer Button */}
                  <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                    <DialogTrigger asChild>
                      <Button variant="outline" className="w-full" onClick={() => setEditingIndex(null)}>
                        <Plus className="h-4 w-4 mr-2" />
                        Add Newznab Indexer
                      </Button>
                    </DialogTrigger>
                    <IndexerDialog
                      indexer={editingIndex !== null ? indexers[editingIndex] : null}
                      onSave={(data) => {
                        if (editingIndex !== null) {
                          updateIndexer(editingIndex, data)
                        } else {
                          addIndexer(data)
                        }
                      }}
                      onClose={() => {
                        setDialogOpen(false)
                        setEditingIndex(null)
                      }}
                    />
                  </Dialog>
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            {/* Info Alert */}
            <Alert>
              <Newspaper className="h-4 w-4" />
              <AlertDescription>
                To use Usenet streams, you need a Usenet-capable streaming provider configured (TorBox, Debrider,
                SABnzbd, NZBGet, or Easynews) in the Provider tab.
              </AlertDescription>
            </Alert>
          </>
        )}
      </CardContent>
    </Card>
  )
}

// Indexer Card Component
function IndexerCard({
  indexer,
  testResult,
  onEdit,
  onDelete,
  onTest,
  isTesting,
}: {
  indexer: NewznabIndexerConfig
  testResult?: { success: boolean; message: string }
  onEdit: () => void
  onDelete: () => void
  onTest: () => void
  isTesting: boolean
}) {
  const isEnabled = indexer.en !== false

  return (
    <div className="p-3 bg-muted/50 rounded-lg space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-2 h-2 rounded-full ${isEnabled ? 'bg-emerald-500' : 'bg-gray-400'}`} />
          <div>
            <div className="flex items-center gap-2">
              <p className="font-medium">{indexer.n}</p>
              {indexer.p !== undefined && indexer.p !== 1 && (
                <Badge variant="outline" className="text-xs">
                  Priority {indexer.p}
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground truncate max-w-[250px]">{new URL(indexer.u).hostname}</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={onTest} disabled={isTesting}>
            {isTesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <TestTube className="h-4 w-4" />}
          </Button>
          <Button variant="ghost" size="icon" onClick={onEdit}>
            <Settings2 className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" onClick={onDelete}>
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
      </div>

      {/* Test result inline */}
      {testResult && (
        <div
          className={`text-xs px-2 py-1 rounded ${testResult.success ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600'}`}
        >
          {testResult.success ? (
            <CheckCircle2 className="h-3 w-3 inline mr-1" />
          ) : (
            <XCircle className="h-3 w-3 inline mr-1" />
          )}
          {testResult.message}
        </div>
      )}
    </div>
  )
}

// Indexer Dialog Component
function IndexerDialog({
  indexer,
  onSave,
  onClose,
}: {
  indexer: NewznabIndexerConfig | null
  onSave: (data: Omit<NewznabIndexerConfig, 'i'>) => void
  onClose: () => void
}) {
  const [name, setName] = useState(indexer?.n || '')
  const [url, setUrl] = useState(indexer?.u || '')
  const [apiKey, setApiKey] = useState(indexer?.ak || '')
  const [enabled, setEnabled] = useState(indexer?.en ?? true)
  const [priority, setPriority] = useState(indexer?.p ?? 1)
  const [showApiKey, setShowApiKey] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [movieCategories, setMovieCategories] = useState<number[]>(indexer?.mc ?? DEFAULT_MOVIE_CATEGORIES)
  const [tvCategories, setTvCategories] = useState<number[]>(indexer?.tc ?? DEFAULT_TV_CATEGORIES)

  useEffect(() => {
    setName(indexer?.n || '')
    setUrl(indexer?.u || '')
    setApiKey(indexer?.ak || '')
    setEnabled(indexer?.en ?? true)
    setPriority(indexer?.p ?? 1)
    setMovieCategories(indexer?.mc ?? DEFAULT_MOVIE_CATEGORIES)
    setTvCategories(indexer?.tc ?? DEFAULT_TV_CATEGORIES)
  }, [indexer])

  const handleSubmit = () => {
    // Clean URL - ensure it ends with /api or similar
    let cleanUrl = url.trim()
    if (!cleanUrl.endsWith('/api') && !cleanUrl.includes('/api?')) {
      cleanUrl = cleanUrl.replace(/\/$/, '') + '/api'
    }

    onSave({
      n: name,
      u: cleanUrl,
      ak: apiKey,
      en: enabled,
      p: priority,
      mc: movieCategories.length > 0 ? movieCategories : undefined,
      tc: tvCategories.length > 0 ? tvCategories : undefined,
    })
  }

  return (
    <DialogContent className="max-w-lg">
      <DialogHeader>
        <DialogTitle>{indexer ? 'Edit' : 'Add'} Newznab Indexer</DialogTitle>
        <DialogDescription>Configure a Newznab-compatible NZB indexer</DialogDescription>
      </DialogHeader>

      <div className="space-y-4">
        <div className="space-y-2">
          <Label>Name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="NZBgeek" />
        </div>

        <div className="space-y-2">
          <Label>Indexer URL</Label>
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.nzbgeek.info" />
          <p className="text-xs text-muted-foreground">Base URL of the indexer (e.g., https://api.nzbgeek.info)</p>
        </div>

        <div className="space-y-2">
          <Label>API Key</Label>
          <div className="relative">
            <Input
              type={showApiKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter API key"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-0 top-0 h-full px-3"
              onClick={() => setShowApiKey(!showApiKey)}
            >
              {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        <div className="space-y-2">
          <Label>Priority</Label>
          <Input
            type="number"
            min={1}
            max={100}
            value={priority}
            onChange={(e) => setPriority(parseInt(e.target.value) || 1)}
          />
          <p className="text-xs text-muted-foreground">Lower numbers = higher priority (1 is highest)</p>
        </div>

        <div className="flex items-center justify-between">
          <Label>Enabled</Label>
          <Switch checked={enabled} onCheckedChange={setEnabled} />
        </div>

        {/* Advanced Settings */}
        <div className="space-y-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="w-full justify-between"
          >
            <span>Advanced Settings</span>
            {showAdvanced ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>

          {showAdvanced && (
            <div className="space-y-4 p-3 bg-muted/30 rounded-lg">
              <div className="space-y-2">
                <Label>Movie Categories (comma-separated)</Label>
                <Input
                  value={movieCategories.join(', ')}
                  onChange={(e) => {
                    const cats = e.target.value
                      .split(',')
                      .map((c) => parseInt(c.trim()))
                      .filter((c) => !isNaN(c))
                    setMovieCategories(cats)
                  }}
                  placeholder="2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060"
                />
              </div>

              <div className="space-y-2">
                <Label>TV Categories (comma-separated)</Label>
                <Input
                  value={tvCategories.join(', ')}
                  onChange={(e) => {
                    const cats = e.target.value
                      .split(',')
                      .map((c) => parseInt(c.trim()))
                      .filter((c) => !isNaN(c))
                    setTvCategories(cats)
                  }}
                  placeholder="5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070, 5080"
                />
              </div>

              <p className="text-xs text-muted-foreground">Leave empty to use default Newznab categories</p>
            </div>
          )}
        </div>
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} disabled={!name || !url || !apiKey}>
          {indexer ? 'Update' : 'Add'} Indexer
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
