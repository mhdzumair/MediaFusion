import { useState, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { 
  Eye, 
  EyeOff, 
  Plus, 
  Trash2, 
  TestTube, 
  Loader2, 
  CheckCircle2, 
  XCircle,
  Server,
  Globe,
  Settings2,
  Circle,
  ChevronDown,
  ChevronUp,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
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
import {
  getGlobalIndexerStatus,
  testProwlarrConnection,
  testJackettConnection,
  testTorznabEndpoint,
  type IndexerInstanceConfig,
  type TorznabEndpoint,
  type ConnectionTestResult,
  type IndexerHealth,
} from '@/lib/api/indexers'
import type { ProfileConfig } from './types'

// Helper component to display indexer health status
function IndexerHealthList({ indexers, title }: { indexers: IndexerHealth[]; title: string }) {
  const [expanded, setExpanded] = useState(false)
  
  const healthyCount = indexers.filter(i => i.status === 'healthy').length
  const unhealthyCount = indexers.filter(i => i.status === 'unhealthy').length
  const warningCount = indexers.filter(i => i.status === 'warning').length
  const disabledCount = indexers.filter(i => i.status === 'disabled').length
  
  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'healthy':
        return <Circle className="h-2.5 w-2.5 fill-emerald-500 text-emerald-500" />
      case 'unhealthy':
        return <Circle className="h-2.5 w-2.5 fill-red-500 text-red-500" />
      case 'warning':
        return <Circle className="h-2.5 w-2.5 fill-primary text-primary" />
      case 'disabled':
        return <Circle className="h-2.5 w-2.5 fill-gray-400 text-gray-400" />
      default:
        return <Circle className="h-2.5 w-2.5 fill-gray-300 text-gray-300" />
    }
  }
  
  return (
    <div className="mt-3 border rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-3 bg-muted/30 hover:bg-muted/50 transition-colors"
      >
        <div className="flex items-center gap-3 text-sm">
          <span className="font-medium">{title}</span>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {healthyCount > 0 && (
              <span className="flex items-center gap-1">
                <Circle className="h-2 w-2 fill-emerald-500 text-emerald-500" />
                {healthyCount}
              </span>
            )}
            {warningCount > 0 && (
              <span className="flex items-center gap-1">
                <Circle className="h-2 w-2 fill-primary text-primary" />
                {warningCount}
              </span>
            )}
            {unhealthyCount > 0 && (
              <span className="flex items-center gap-1">
                <Circle className="h-2 w-2 fill-red-500 text-red-500" />
                {unhealthyCount}
              </span>
            )}
            {disabledCount > 0 && (
              <span className="flex items-center gap-1">
                <Circle className="h-2 w-2 fill-gray-400 text-gray-400" />
                {disabledCount}
              </span>
            )}
          </div>
        </div>
        {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>
      
      {expanded && (
        <div className="max-h-64 overflow-y-auto">
          <div className="divide-y divide-border">
            {indexers.map((indexer, idx) => (
              <div key={idx} className="flex items-center justify-between px-3 py-2 text-sm">
                <div className="flex items-center gap-2">
                  {getStatusIcon(indexer.status)}
                  <span className={indexer.status === 'disabled' ? 'text-muted-foreground' : ''}>
                    {indexer.name}
                  </span>
                  {indexer.priority !== null && indexer.priority !== undefined && (
                    <span className="text-xs text-muted-foreground">
                      (P{indexer.priority})
                    </span>
                  )}
                </div>
                {indexer.error_message && indexer.status !== 'healthy' && (
                  <span className="text-xs text-muted-foreground max-w-[200px] truncate" title={indexer.error_message}>
                    {indexer.error_message}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Generate a simple unique ID for new endpoints
function generateEndpointId(): string {
  return Math.random().toString(36).substring(2, 10)
}

// Indexer config stored in profile (using aliases)
interface ProfileIndexerConfig {
  pr?: { en?: boolean; u?: string; ak?: string; ug?: boolean } | null
  jk?: { en?: boolean; u?: string; ak?: string; ug?: boolean } | null
  tz?: Array<{ i: string; n: string; u: string; h?: Record<string, string> | null; en?: boolean; c?: number[]; p?: number }>
}

// Convert profile config format to UI format
function profileConfigToUI(config: ProfileIndexerConfig | undefined): {
  prowlarr: IndexerInstanceConfig
  jackett: IndexerInstanceConfig
  torznab: TorznabEndpoint[]
} {
  return {
    prowlarr: {
      enabled: config?.pr?.en ?? false,
      url: config?.pr?.u ?? null,
      api_key: config?.pr?.ak ?? null,
      use_global: config?.pr?.ug ?? true,
    },
    jackett: {
      enabled: config?.jk?.en ?? false,
      url: config?.jk?.u ?? null,
      api_key: config?.jk?.ak ?? null,
      use_global: config?.jk?.ug ?? true,
    },
    torznab: (config?.tz ?? []).map(ep => ({
      id: ep.i,
      name: ep.n,
      url: ep.u,
      headers: ep.h ?? null,
      enabled: ep.en ?? true,
      categories: ep.c ?? [],
      priority: ep.p ?? 1,
    })),
  }
}

// Convert UI format back to profile config format
function uiToProfileConfig(
  prowlarr: IndexerInstanceConfig,
  jackett: IndexerInstanceConfig,
  torznab: TorznabEndpoint[]
): ProfileIndexerConfig {
  const config: ProfileIndexerConfig = {}
  
  // Only include if enabled or has custom settings
  if (prowlarr.enabled || prowlarr.url || prowlarr.api_key) {
    config.pr = {
      en: prowlarr.enabled,
      u: prowlarr.url ?? undefined,
      ak: prowlarr.api_key ?? undefined,
      ug: prowlarr.use_global,
    }
  }
  
  if (jackett.enabled || jackett.url || jackett.api_key) {
    config.jk = {
      en: jackett.enabled,
      u: jackett.url ?? undefined,
      ak: jackett.api_key ?? undefined,
      ug: jackett.use_global,
    }
  }
  
  if (torznab.length > 0) {
    config.tz = torznab.map(ep => ({
      i: ep.id || generateEndpointId(),
      n: ep.name,
      u: ep.url,
      h: ep.headers ?? undefined,
      en: ep.enabled,
      c: ep.categories,
      p: ep.priority,
    }))
  }
  
  return config
}

interface IndexerSettingsProps {
  config: ProfileConfig
  onChange: (config: ProfileConfig) => void
}

export function IndexerSettings({ config, onChange }: IndexerSettingsProps) {
  const { toast } = useToast()
  
  // Fetch global indexer status
  const { data: globalStatus } = useQuery({
    queryKey: ['globalIndexerStatus'],
    queryFn: getGlobalIndexerStatus,
  })
  
  // Parse indexer config from profile config
  const profileIndexerConfig = config.ic as ProfileIndexerConfig | undefined
  const uiConfig = profileConfigToUI(profileIndexerConfig)
  
  // Local state for UI (derived from config)
  const [prowlarrConfig, setProwlarrConfig] = useState<IndexerInstanceConfig>(uiConfig.prowlarr)
  const [jackettConfig, setJackettConfig] = useState<IndexerInstanceConfig>(uiConfig.jackett)
  const [torznabEndpoints, setTorznabEndpoints] = useState<TorznabEndpoint[]>(uiConfig.torznab)
  
  // Password visibility
  const [showProwlarrKey, setShowProwlarrKey] = useState(false)
  const [showJackettKey, setShowJackettKey] = useState(false)
  
  // Test results
  const [prowlarrTestResult, setProwlarrTestResult] = useState<ConnectionTestResult | null>(null)
  const [jackettTestResult, setJackettTestResult] = useState<ConnectionTestResult | null>(null)
  const [torznabTestResults, setTorznabTestResults] = useState<Record<string, ConnectionTestResult>>({})
  
  // Torznab dialog state
  const [torznabDialogOpen, setTorznabDialogOpen] = useState(false)
  const [editingEndpointIndex, setEditingEndpointIndex] = useState<number | null>(null)
  
  // Sync local state when config changes from outside
  useEffect(() => {
    const uiCfg = profileConfigToUI(config.ic as ProfileIndexerConfig | undefined)
    
    // Default to enabled if global is available and not explicitly configured
    if (globalStatus?.prowlarr_available && !profileIndexerConfig?.pr) {
      uiCfg.prowlarr = { enabled: true, url: null, api_key: null, use_global: true }
    }
    if (globalStatus?.jackett_available && !profileIndexerConfig?.jk) {
      uiCfg.jackett = { enabled: true, url: null, api_key: null, use_global: true }
    }
    
    setProwlarrConfig(uiCfg.prowlarr)
    setJackettConfig(uiCfg.jackett)
    setTorznabEndpoints(uiCfg.torznab)
  }, [config.ic, globalStatus])
  
  // Update parent config when local state changes
  const updateParentConfig = (
    newProwlarr: IndexerInstanceConfig,
    newJackett: IndexerInstanceConfig,
    newTorznab: TorznabEndpoint[]
  ) => {
    const indexerConfig = uiToProfileConfig(newProwlarr, newJackett, newTorznab)
    onChange({
      ...config,
      ic: indexerConfig,
    })
  }
  
  const testProwlarrMutation = useMutation({
    mutationFn: testProwlarrConnection,
    onSuccess: (result) => setProwlarrTestResult(result),
    onError: () => setProwlarrTestResult({ success: false, message: 'Connection test failed', indexer_count: null, indexer_names: null, indexers: null }),
  })
  
  const testJackettMutation = useMutation({
    mutationFn: testJackettConnection,
    onSuccess: (result) => setJackettTestResult(result),
    onError: () => setJackettTestResult({ success: false, message: 'Connection test failed', indexer_count: null, indexer_names: null, indexers: null }),
  })
  
  const testEndpointMutation = useMutation({
    mutationFn: testTorznabEndpoint,
  })
  
  // Update helpers that propagate to parent
  const updateProwlarr = (newConfig: IndexerInstanceConfig) => {
    setProwlarrConfig(newConfig)
    updateParentConfig(newConfig, jackettConfig, torznabEndpoints)
  }
  
  const updateJackett = (newConfig: IndexerInstanceConfig) => {
    setJackettConfig(newConfig)
    updateParentConfig(prowlarrConfig, newConfig, torznabEndpoints)
  }
  
  // Torznab endpoint management
  const addTorznabEndpointLocal = (data: Omit<TorznabEndpoint, 'id'>) => {
    const newEndpoint: TorznabEndpoint = {
      ...data,
      id: generateEndpointId(),
    }
    const newEndpoints = [...torznabEndpoints, newEndpoint]
    setTorznabEndpoints(newEndpoints)
    updateParentConfig(prowlarrConfig, jackettConfig, newEndpoints)
    setTorznabDialogOpen(false)
    setEditingEndpointIndex(null)
  }
  
  const updateTorznabEndpointLocal = (index: number, data: Omit<TorznabEndpoint, 'id'>) => {
    const updated = [...torznabEndpoints]
    updated[index] = { ...data, id: torznabEndpoints[index].id }
    setTorznabEndpoints(updated)
    updateParentConfig(prowlarrConfig, jackettConfig, updated)
    setTorznabDialogOpen(false)
    setEditingEndpointIndex(null)
  }
  
  const deleteTorznabEndpointLocal = (index: number) => {
    const newEndpoints = torznabEndpoints.filter((_, i) => i !== index)
    setTorznabEndpoints(newEndpoints)
    updateParentConfig(prowlarrConfig, jackettConfig, newEndpoints)
  }
  
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Server className="h-5 w-5" />
          Indexer Settings
        </CardTitle>
        <CardDescription>
          Configure Prowlarr, Jackett, or custom Torznab indexers for enhanced search
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Accordion type="multiple" className="w-full" defaultValue={['prowlarr', 'jackett', 'torznab']}>
          {/* Prowlarr */}
          <AccordionItem value="prowlarr">
            <AccordionTrigger>
              <div className="flex items-center gap-2">
                <span>Prowlarr</span>
                {prowlarrConfig.enabled && (
                  <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-500">
                    {prowlarrConfig.use_global ? 'Global' : 'Custom'}
                  </Badge>
                )}
                {globalStatus?.prowlarr_available && (
                  <Badge variant="outline" className="text-xs">
                    <Globe className="h-3 w-3 mr-1" />
                    Global Available
                  </Badge>
                )}
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4 pt-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Enable Prowlarr</Label>
                  <p className="text-xs text-muted-foreground">
                    Use Prowlarr for indexer management
                  </p>
                </div>
                <Switch
                  checked={prowlarrConfig.enabled}
                  onCheckedChange={(checked) => 
                    updateProwlarr({ ...prowlarrConfig, enabled: checked })
                  }
                />
              </div>
              
              {prowlarrConfig.enabled && (
                <>
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Use Global Instance</Label>
                      <p className="text-xs text-muted-foreground">
                        {globalStatus?.prowlarr_available 
                          ? 'Use the server\'s Prowlarr instance'
                          : 'No global instance available'}
                      </p>
                    </div>
                    <Switch
                      checked={prowlarrConfig.use_global}
                      onCheckedChange={(checked) => 
                        updateProwlarr({ ...prowlarrConfig, use_global: checked })
                      }
                      disabled={!globalStatus?.prowlarr_available}
                    />
                  </div>
                  
                  {!prowlarrConfig.use_global && (
                    <>
                      <div className="space-y-2">
                        <Label>Prowlarr URL</Label>
                        <Input
                          value={prowlarrConfig.url || ''}
                          onChange={(e) => 
                            updateProwlarr({ ...prowlarrConfig, url: e.target.value })
                          }
                          placeholder="http://localhost:9696"
                        />
                      </div>
                      
                      <div className="space-y-2">
                        <Label>API Key</Label>
                        <div className="relative">
                          <Input
                            type={showProwlarrKey ? 'text' : 'password'}
                            value={prowlarrConfig.api_key || ''}
                            onChange={(e) => 
                              updateProwlarr({ ...prowlarrConfig, api_key: e.target.value })
                            }
                            placeholder="Enter API key"
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="absolute right-0 top-0 h-full px-3"
                            onClick={() => setShowProwlarrKey(!showProwlarrKey)}
                          >
                            {showProwlarrKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                          </Button>
                        </div>
                      </div>
                    </>
                  )}
                  
                  {/* Test Result */}
                  {prowlarrTestResult && (
                    <div className="space-y-2">
                      <Alert variant={prowlarrTestResult.success ? 'default' : 'destructive'}>
                        {prowlarrTestResult.success ? (
                          <CheckCircle2 className="h-4 w-4" />
                        ) : (
                          <XCircle className="h-4 w-4" />
                        )}
                        <AlertDescription>
                          {prowlarrTestResult.message}
                        </AlertDescription>
                      </Alert>
                      
                      {/* Indexer Health List */}
                      {prowlarrTestResult.success && prowlarrTestResult.indexers && prowlarrTestResult.indexers.length > 0 && (
                        <IndexerHealthList 
                          indexers={prowlarrTestResult.indexers} 
                          title={`Indexers (${prowlarrTestResult.indexer_count} healthy)`}
                        />
                      )}
                    </div>
                  )}
                  
                  <Button
                    variant="outline"
                    onClick={() => testProwlarrMutation.mutate(prowlarrConfig)}
                    disabled={testProwlarrMutation.isPending}
                  >
                    {testProwlarrMutation.isPending ? (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    ) : (
                      <TestTube className="h-4 w-4 mr-2" />
                    )}
                    Test Connection
                  </Button>
                </>
              )}
            </AccordionContent>
          </AccordionItem>
          
          {/* Jackett */}
          <AccordionItem value="jackett">
            <AccordionTrigger>
              <div className="flex items-center gap-2">
                <span>Jackett</span>
                {jackettConfig.enabled && (
                  <Badge variant="secondary" className="bg-primary/10 text-primary">
                    {jackettConfig.use_global ? 'Global' : 'Custom'}
                  </Badge>
                )}
                {globalStatus?.jackett_available && (
                  <Badge variant="outline" className="text-xs">
                    <Globe className="h-3 w-3 mr-1" />
                    Global Available
                  </Badge>
                )}
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4 pt-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Enable Jackett</Label>
                  <p className="text-xs text-muted-foreground">
                    Use Jackett for indexer management
                  </p>
                </div>
                <Switch
                  checked={jackettConfig.enabled}
                  onCheckedChange={(checked) => 
                    updateJackett({ ...jackettConfig, enabled: checked })
                  }
                />
              </div>
              
              {jackettConfig.enabled && (
                <>
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Use Global Instance</Label>
                      <p className="text-xs text-muted-foreground">
                        {globalStatus?.jackett_available 
                          ? 'Use the server\'s Jackett instance'
                          : 'No global instance available'}
                      </p>
                    </div>
                    <Switch
                      checked={jackettConfig.use_global}
                      onCheckedChange={(checked) => 
                        updateJackett({ ...jackettConfig, use_global: checked })
                      }
                      disabled={!globalStatus?.jackett_available}
                    />
                  </div>
                  
                  {!jackettConfig.use_global && (
                    <>
                      <div className="space-y-2">
                        <Label>Jackett URL</Label>
                        <Input
                          value={jackettConfig.url || ''}
                          onChange={(e) => 
                            updateJackett({ ...jackettConfig, url: e.target.value })
                          }
                          placeholder="http://localhost:9117"
                        />
                      </div>
                      
                      <div className="space-y-2">
                        <Label>API Key</Label>
                        <div className="relative">
                          <Input
                            type={showJackettKey ? 'text' : 'password'}
                            value={jackettConfig.api_key || ''}
                            onChange={(e) => 
                              updateJackett({ ...jackettConfig, api_key: e.target.value })
                            }
                            placeholder="Enter API key"
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="absolute right-0 top-0 h-full px-3"
                            onClick={() => setShowJackettKey(!showJackettKey)}
                          >
                            {showJackettKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                          </Button>
                        </div>
                      </div>
                    </>
                  )}
                  
                  {/* Test Result */}
                  {jackettTestResult && (
                    <div className="space-y-2">
                      <Alert variant={jackettTestResult.success ? 'default' : 'destructive'}>
                        {jackettTestResult.success ? (
                          <CheckCircle2 className="h-4 w-4" />
                        ) : (
                          <XCircle className="h-4 w-4" />
                        )}
                        <AlertDescription>
                          {jackettTestResult.message}
                        </AlertDescription>
                      </Alert>
                      
                      {/* Indexer Health List */}
                      {jackettTestResult.success && jackettTestResult.indexers && jackettTestResult.indexers.length > 0 && (
                        <IndexerHealthList 
                          indexers={jackettTestResult.indexers} 
                          title={`Indexers (${jackettTestResult.indexer_count} healthy)`}
                        />
                      )}
                    </div>
                  )}
                  
                  <Button
                    variant="outline"
                    onClick={() => testJackettMutation.mutate(jackettConfig)}
                    disabled={testJackettMutation.isPending}
                  >
                    {testJackettMutation.isPending ? (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    ) : (
                      <TestTube className="h-4 w-4 mr-2" />
                    )}
                    Test Connection
                  </Button>
                </>
              )}
            </AccordionContent>
          </AccordionItem>
          
          {/* Custom Torznab Endpoints */}
          <AccordionItem value="torznab">
            <AccordionTrigger>
              <div className="flex items-center gap-2">
                <span>Custom Torznab Endpoints</span>
                {torznabEndpoints.length > 0 && (
                  <Badge variant="secondary" className="bg-primary/10 text-primary">
                    {torznabEndpoints.length} configured
                  </Badge>
                )}
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4 pt-4">
              <p className="text-sm text-muted-foreground">
                Add direct Torznab API endpoints for indexers not managed by Prowlarr or Jackett
              </p>
              
              {/* Endpoint List */}
              {torznabEndpoints.length > 0 ? (
                <div className="space-y-2">
                  {torznabEndpoints.map((endpoint, index) => (
                    <TorznabEndpointCard
                      key={endpoint.id || index}
                      endpoint={endpoint}
                      testResult={torznabTestResults[endpoint.id || index.toString()]}
                      onEdit={() => {
                        setEditingEndpointIndex(index)
                        setTorznabDialogOpen(true)
                      }}
                      onDelete={() => {
                        if (confirm('Delete this endpoint?')) {
                          deleteTorznabEndpointLocal(index)
                        }
                      }}
                      onTest={async () => {
                        try {
                          const result = await testEndpointMutation.mutateAsync({
                            name: endpoint.name,
                            url: endpoint.url,
                            headers: endpoint.headers,
                            enabled: endpoint.enabled,
                            categories: endpoint.categories,
                            priority: endpoint.priority,
                          })
                          setTorznabTestResults(prev => ({
                            ...prev,
                            [endpoint.id || index.toString()]: result,
                          }))
                          toast({
                            title: result.success ? 'Connection successful' : 'Connection failed',
                            description: result.message,
                            variant: result.success ? 'default' : 'destructive',
                          })
                        } catch {
                          toast({
                            title: 'Test failed',
                            description: 'Could not test endpoint connection',
                            variant: 'destructive',
                          })
                        }
                      }}
                      isTestPending={testEndpointMutation.isPending}
                    />
                  ))}
                </div>
              ) : (
                <div className="text-center py-6 text-muted-foreground">
                  <Settings2 className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p>No Torznab endpoints configured</p>
                </div>
              )}
              
              {/* Add Endpoint Button */}
              <Dialog open={torznabDialogOpen} onOpenChange={setTorznabDialogOpen}>
                <DialogTrigger asChild>
                  <Button 
                    variant="outline" 
                    className="w-full"
                    onClick={() => setEditingEndpointIndex(null)}
                  >
                    <Plus className="h-4 w-4 mr-2" />
                    Add Torznab Endpoint
                  </Button>
                </DialogTrigger>
                <TorznabEndpointDialog
                  endpoint={editingEndpointIndex !== null ? torznabEndpoints[editingEndpointIndex] : null}
                  onSave={(data) => {
                    if (editingEndpointIndex !== null) {
                      updateTorznabEndpointLocal(editingEndpointIndex, data)
                    } else {
                      addTorznabEndpointLocal(data)
                    }
                  }}
                  onClose={() => {
                    setTorznabDialogOpen(false)
                    setEditingEndpointIndex(null)
                  }}
                />
              </Dialog>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </CardContent>
    </Card>
  )
}

// Torznab Endpoint Card Component
function TorznabEndpointCard({
  endpoint,
  testResult,
  onEdit,
  onDelete,
  onTest,
  isTestPending,
}: {
  endpoint: TorznabEndpoint
  testResult?: ConnectionTestResult
  onEdit: () => void
  onDelete: () => void
  onTest: () => void
  isTestPending: boolean
}) {
  const headerCount = endpoint.headers ? Object.keys(endpoint.headers).length : 0
  
  return (
    <div className="p-3 bg-muted/50 rounded-lg space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-2 h-2 rounded-full ${endpoint.enabled ? 'bg-emerald-500' : 'bg-gray-400'}`} />
          <div>
            <div className="flex items-center gap-2">
              <p className="font-medium">{endpoint.name}</p>
              {headerCount > 0 && (
                <Badge variant="outline" className="text-xs">
                  {headerCount} header{headerCount > 1 ? 's' : ''}
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground truncate max-w-[250px]">{endpoint.url}</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={onTest} disabled={isTestPending}>
            {isTestPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <TestTube className="h-4 w-4" />
            )}
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
        <div className={`text-xs px-2 py-1 rounded ${testResult.success ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600'}`}>
          {testResult.success ? <CheckCircle2 className="h-3 w-3 inline mr-1" /> : <XCircle className="h-3 w-3 inline mr-1" />}
          {testResult.message}
        </div>
      )}
    </div>
  )
}

// Torznab Endpoint Dialog Component
function TorznabEndpointDialog({
  endpoint,
  onSave,
  onClose,
}: {
  endpoint: TorznabEndpoint | null
  onSave: (data: Omit<TorznabEndpoint, 'id'>) => void
  onClose: () => void
}) {
  const [name, setName] = useState(endpoint?.name || '')
  const [url, setUrl] = useState(endpoint?.url || '')
  const [headers, setHeaders] = useState<Array<{ key: string; value: string }>>(
    endpoint?.headers 
      ? Object.entries(endpoint.headers).map(([key, value]) => ({ key, value }))
      : []
  )
  const [enabled, setEnabled] = useState(endpoint?.enabled ?? true)
  const [showHeaders, setShowHeaders] = useState(false)
  
  useEffect(() => {
    setName(endpoint?.name || '')
    setUrl(endpoint?.url || '')
    setHeaders(
      endpoint?.headers 
        ? Object.entries(endpoint.headers).map(([key, value]) => ({ key, value }))
        : []
    )
    setEnabled(endpoint?.enabled ?? true)
  }, [endpoint])
  
  const handleSubmit = () => {
    // Convert headers array to object, filtering out empty keys
    const headersObj = headers
      .filter(h => h.key.trim())
      .reduce((acc, h) => ({ ...acc, [h.key]: h.value }), {} as Record<string, string>)
    
    onSave({
      name,
      url,
      headers: Object.keys(headersObj).length > 0 ? headersObj : null,
      enabled,
      categories: endpoint?.categories || [],
      priority: endpoint?.priority || 1,
    })
  }
  
  const addHeader = () => {
    setHeaders([...headers, { key: '', value: '' }])
  }
  
  const removeHeader = (index: number) => {
    setHeaders(headers.filter((_, i) => i !== index))
  }
  
  const updateHeader = (index: number, field: 'key' | 'value', value: string) => {
    const newHeaders = [...headers]
    newHeaders[index][field] = value
    setHeaders(newHeaders)
  }
  
  return (
    <DialogContent className="max-w-lg">
      <DialogHeader>
        <DialogTitle>{endpoint ? 'Edit' : 'Add'} Torznab Endpoint</DialogTitle>
        <DialogDescription>
          Configure a direct Torznab API endpoint. Include API key in URL if required (e.g., ?apikey=xxx)
        </DialogDescription>
      </DialogHeader>
      
      <div className="space-y-4">
        <div className="space-y-2">
          <Label>Name</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Indexer"
          />
        </div>
        
        <div className="space-y-2">
          <Label>Torznab URL</Label>
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://indexer.example.com/api?apikey=xxx"
          />
          <p className="text-xs text-muted-foreground">
            Include API key in URL if required (e.g., ?apikey=your_key)
          </p>
        </div>
        
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>Custom Headers (Optional)</Label>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setShowHeaders(!showHeaders)}
            >
              {showHeaders ? 'Hide' : 'Show'}
            </Button>
          </div>
          
          {showHeaders && (
            <div className="space-y-2 p-3 bg-muted/30 rounded-lg">
              {headers.map((header, index) => (
                <div key={index} className="flex gap-2">
                  <Input
                    value={header.key}
                    onChange={(e) => updateHeader(index, 'key', e.target.value)}
                    placeholder="Header name"
                    className="flex-1"
                  />
                  <Input
                    value={header.value}
                    onChange={(e) => updateHeader(index, 'value', e.target.value)}
                    placeholder="Value"
                    className="flex-1"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => removeHeader(index)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={addHeader}
                className="w-full"
              >
                <Plus className="h-4 w-4 mr-2" />
                Add Header
              </Button>
              <p className="text-xs text-muted-foreground">
                Use for APIs requiring headers like X-Api-Key or Authorization
              </p>
            </div>
          )}
        </div>
        
        <div className="flex items-center justify-between">
          <Label>Enabled</Label>
          <Switch checked={enabled} onCheckedChange={setEnabled} />
        </div>
      </div>
      
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button
          onClick={handleSubmit}
          disabled={!name || !url}
        >
          {endpoint ? 'Update' : 'Add'} Endpoint
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
