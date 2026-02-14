/**
 * External Platform Integrations Component
 *
 * Allows users to connect/disconnect and sync with external platforms
 * like Trakt, Simkl, MAL, etc.
 */

import { useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Link2,
  Unlink,
  RefreshCw,
  Settings2,
  ExternalLink,
  Check,
  AlertCircle,
  Clock,
  Loader2,
  RotateCcw,
} from 'lucide-react'
import {
  useIntegrations,
  useOAuthUrl,
  useConnectTrakt,
  useConnectSimkl,
  useDisconnectIntegration,
  useUpdateIntegrationSettings,
  useTriggerSync,
  useTriggerSyncAll,
} from '@/hooks/useIntegrations'
import type { IntegrationType, SyncDirection } from '@/lib/api/integrations'
import { formatDistanceToNow } from 'date-fns'

// Platform metadata
const PLATFORM_INFO: Record<
  IntegrationType,
  {
    name: string
    description: string
    icon: string
    color: string
    gradient: string
    url: string
    supported: boolean
  }
> = {
  trakt: {
    name: 'Trakt',
    description: 'Track movies and TV shows you watch',
    icon: '🎬',
    color: 'text-red-500',
    gradient: 'from-red-500/20 to-red-600/5',
    url: 'https://trakt.tv',
    supported: true,
  },
  simkl: {
    name: 'Simkl',
    description: 'Track anime, movies, and TV shows',
    icon: '📺',
    color: 'text-blue-500',
    gradient: 'from-blue-500/20 to-blue-600/5',
    url: 'https://simkl.com',
    supported: true,
  },
  myanimelist: {
    name: 'MyAnimeList',
    description: 'Track your anime and manga',
    icon: '🎌',
    color: 'text-blue-400',
    gradient: 'from-blue-400/20 to-blue-500/5',
    url: 'https://myanimelist.net',
    supported: false,
  },
  anilist: {
    name: 'AniList',
    description: 'Track anime and manga with social features',
    icon: '🌸',
    color: 'text-cyan-500',
    gradient: 'from-cyan-500/20 to-cyan-600/5',
    url: 'https://anilist.co',
    supported: false,
  },
  letterboxd: {
    name: 'Letterboxd',
    description: 'Social film discovery and logging',
    icon: '🎥',
    color: 'text-orange-500',
    gradient: 'from-orange-500/20 to-orange-600/5',
    url: 'https://letterboxd.com',
    supported: false,
  },
  tvtime: {
    name: 'TV Time',
    description: 'Track TV shows and discover new ones',
    icon: '📱',
    color: 'text-yellow-500',
    gradient: 'from-yellow-500/20 to-yellow-600/5',
    url: 'https://tvtime.com',
    supported: false,
  },
}

interface ConnectDialogProps {
  platform: IntegrationType
  open: boolean
  onOpenChange: (open: boolean) => void
}

function ConnectDialog({ platform, open, onOpenChange }: ConnectDialogProps) {
  const [code, setCode] = useState('')
  const [customClientId, setCustomClientId] = useState('')
  const [customClientSecret, setCustomClientSecret] = useState('')
  const [step, setStep] = useState<'auth' | 'code'>('auth')

  const getOAuthUrl = useOAuthUrl()
  const connectTrakt = useConnectTrakt()
  const connectSimkl = useConnectSimkl()

  const info = PLATFORM_INFO[platform]
  const isLoading = getOAuthUrl.isPending || connectTrakt.isPending || connectSimkl.isPending

  const handleGetAuthUrl = async () => {
    try {
      const result = await getOAuthUrl.mutateAsync({
        platform,
        clientId: customClientId || undefined,
      })
      window.open(result.auth_url, '_blank')
      setStep('code')
    } catch (error) {
      console.error('Failed to get auth URL:', error)
    }
  }

  const handleConnect = async () => {
    try {
      // Only pass custom credentials if BOTH are provided
      const hasCustomCreds = customClientId && customClientSecret

      if (platform === 'trakt') {
        await connectTrakt.mutateAsync({
          code,
          clientId: hasCustomCreds ? customClientId : undefined,
          clientSecret: hasCustomCreds ? customClientSecret : undefined,
        })
      } else if (platform === 'simkl') {
        await connectSimkl.mutateAsync({
          code,
          clientId: hasCustomCreds ? customClientId : undefined,
          clientSecret: hasCustomCreds ? customClientSecret : undefined,
        })
      }
      onOpenChange(false)
      setCode('')
      setStep('auth')
    } catch (error) {
      console.error('Failed to connect:', error)
    }
  }

  const handleClose = () => {
    onOpenChange(false)
    setCode('')
    setStep('auth')
    setCustomClientId('')
    setCustomClientSecret('')
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="text-2xl">{info.icon}</span>
            Connect {info.name}
          </DialogTitle>
          <DialogDescription>
            {step === 'auth'
              ? `Authorize MediaFusion to access your ${info.name} account`
              : `Enter the authorization code from ${info.name}`}
          </DialogDescription>
        </DialogHeader>

        {step === 'auth' ? (
          <div className="space-y-4 py-4">
            {(platform === 'trakt' || platform === 'simkl') && (
              <div className="space-y-4">
                <p className="text-xs text-muted-foreground">
                  Use your own API credentials (optional). Create an app at{' '}
                  {platform === 'trakt' ? (
                    <a
                      href="https://trakt.tv/oauth/applications"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline"
                    >
                      trakt.tv/oauth/applications
                    </a>
                  ) : (
                    <a
                      href="https://simkl.com/settings/developer/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline"
                    >
                      simkl.com/settings/developer
                    </a>
                  )}
                </p>
                <div className="space-y-2">
                  <Label htmlFor="client-id">Client ID</Label>
                  <Input
                    id="client-id"
                    placeholder="Leave empty to use server default"
                    value={customClientId}
                    onChange={(e) => setCustomClientId(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="client-secret">Client Secret</Label>
                  <Input
                    id="client-secret"
                    type="password"
                    placeholder="Leave empty to use server default"
                    value={customClientSecret}
                    onChange={(e) => setCustomClientSecret(e.target.value)}
                  />
                </div>
                {(customClientId || customClientSecret) && !(customClientId && customClientSecret) && (
                  <p className="text-xs text-amber-500">
                    ⚠️ Both Client ID and Client Secret are required when using custom credentials
                  </p>
                )}
              </div>
            )}

            <p className="text-sm text-muted-foreground">
              Click the button below to open {info.name} authorization page. After authorizing, you'll receive a code to
              paste here.
            </p>
          </div>
        ) : (
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="auth-code">Authorization Code</Label>
              <Input
                id="auth-code"
                placeholder="Paste the code here"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            Cancel
          </Button>
          {step === 'auth' ? (
            <Button onClick={handleGetAuthUrl} disabled={isLoading}>
              {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Open {info.name}
              <ExternalLink className="ml-2 h-4 w-4" />
            </Button>
          ) : (
            <Button onClick={handleConnect} disabled={!code || isLoading}>
              {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Connect
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface PlatformCardProps {
  platform: IntegrationType
  connected: boolean
  syncEnabled: boolean
  syncDirection: string
  lastSyncAt: string | null
  lastSyncStatus: string | null
  lastSyncError: string | null
  onConnect: () => void
}

function PlatformCard({
  platform,
  connected,
  syncEnabled,
  syncDirection,
  lastSyncAt,
  lastSyncStatus,
  lastSyncError,
  onConnect,
}: PlatformCardProps) {
  const info = PLATFORM_INFO[platform]
  const disconnect = useDisconnectIntegration()
  const updateSettings = useUpdateIntegrationSettings()
  const triggerSync = useTriggerSync()

  // Skip rendering if platform info is not found
  if (!info) {
    console.warn(`Unknown platform: ${platform}`)
    return null
  }

  const handleDisconnect = async () => {
    if (confirm(`Are you sure you want to disconnect ${info.name}?`)) {
      await disconnect.mutateAsync(platform)
    }
  }

  const handleSyncToggle = async (enabled: boolean) => {
    await updateSettings.mutateAsync({
      platform,
      settings: { sync_enabled: enabled },
    })
  }

  const handleDirectionChange = async (direction: string) => {
    await updateSettings.mutateAsync({
      platform,
      settings: { sync_direction: direction as SyncDirection },
    })
  }

  const handleSync = async (fullSync = false) => {
    await triggerSync.mutateAsync({ platform, fullSync })
  }

  const isSyncing = triggerSync.isPending

  return (
    <Card className="relative overflow-hidden">
      <div
        className={`absolute top-0 right-0 w-32 h-32 bg-gradient-to-br ${info.gradient} rounded-full -translate-y-1/2 translate-x-1/2`}
      />

      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-3xl">{info.icon}</span>
            <div>
              <CardTitle className="text-lg">{info.name}</CardTitle>
              <CardDescription className="text-xs">{info.description}</CardDescription>
            </div>
          </div>
          {connected ? (
            <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20">
              <Check className="mr-1 h-3 w-3" />
              Connected
            </Badge>
          ) : (
            <Badge variant="outline" className="text-muted-foreground">
              Not Connected
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {!info.supported ? (
          <p className="text-sm text-muted-foreground italic">Coming soon - not yet implemented</p>
        ) : connected ? (
          <>
            {/* Sync Status */}
            {lastSyncAt && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" />
                <span>
                  Last synced{' '}
                  {(() => {
                    try {
                      // Handle both ISO format with and without timezone
                      const date = new Date(lastSyncAt.endsWith('Z') ? lastSyncAt : lastSyncAt + 'Z')
                      if (isNaN(date.getTime())) return 'unknown'
                      return formatDistanceToNow(date, { addSuffix: true })
                    } catch {
                      return 'unknown'
                    }
                  })()}
                </span>
                {lastSyncStatus === 'success' && <Check className="h-3 w-3 text-green-500" />}
                {lastSyncStatus === 'failed' && (
                  <Tooltip>
                    <TooltipTrigger>
                      <AlertCircle className="h-3 w-3 text-red-500" />
                    </TooltipTrigger>
                    <TooltipContent>{lastSyncError}</TooltipContent>
                  </Tooltip>
                )}
              </div>
            )}

            {/* Settings */}
            <Accordion type="single" collapsible className="w-full">
              <AccordionItem value="settings" className="border-none">
                <AccordionTrigger className="text-sm py-2">
                  <div className="flex items-center gap-2">
                    <Settings2 className="h-4 w-4" />
                    Settings
                  </div>
                </AccordionTrigger>
                <AccordionContent className="space-y-4 pt-2">
                  {/* Sync Enable */}
                  <div className="flex items-center justify-between">
                    <Label htmlFor={`sync-${platform}`} className="text-sm">
                      Auto-sync enabled
                    </Label>
                    <Switch id={`sync-${platform}`} checked={syncEnabled} onCheckedChange={handleSyncToggle} />
                  </div>

                  {/* Sync Direction */}
                  <div className="space-y-2">
                    <Label className="text-sm">Sync Direction</Label>
                    <Select value={syncDirection} onValueChange={handleDirectionChange}>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="bidirectional">Bidirectional</SelectItem>
                        <SelectItem value="import">Import only</SelectItem>
                        <SelectItem value="export">Export only</SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      {syncDirection === 'bidirectional' && 'Sync watch history both ways'}
                      {syncDirection === 'import' && 'Only import from ' + info.name}
                      {syncDirection === 'export' && 'Only export to ' + info.name}
                    </p>
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            {/* Actions */}
            <div className="flex flex-wrap gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={() => handleSync(false)} disabled={isSyncing}>
                {isSyncing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                Sync Now
              </Button>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="outline" size="sm" onClick={() => handleSync(true)} disabled={isSyncing}>
                    {isSyncing ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <RotateCcw className="mr-2 h-4 w-4" />
                    )}
                    Full Sync
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Re-import all history from {info.name}, ignoring last sync time</p>
                </TooltipContent>
              </Tooltip>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleDisconnect}
                className="text-destructive hover:text-destructive"
              >
                <Unlink className="mr-2 h-4 w-4" />
                Disconnect
              </Button>
            </div>
          </>
        ) : (
          <Button onClick={onConnect} className="w-full">
            <Link2 className="mr-2 h-4 w-4" />
            Connect {info.name}
          </Button>
        )}

        {/* External Link */}
        <a
          href={info.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          Visit {info.name}
          <ExternalLink className="h-3 w-3" />
        </a>
      </CardContent>
    </Card>
  )
}

export function ExternalPlatformIntegrations() {
  const { data, isLoading } = useIntegrations()
  const syncAll = useTriggerSyncAll()
  const [connectPlatform, setConnectPlatform] = useState<IntegrationType | null>(null)

  const hasConnectedPlatforms = data?.integrations.some((i) => i.connected && PLATFORM_INFO[i.platform].supported)

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Watch History Sync</CardTitle>
          <CardDescription>Loading integrations...</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Watch History Sync</h2>
          <p className="text-sm text-muted-foreground">Connect external platforms to sync your watch history</p>
        </div>
        {hasConnectedPlatforms && (
          <Button variant="outline" onClick={() => syncAll.mutate()} disabled={syncAll.isPending}>
            {syncAll.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            Sync All
          </Button>
        )}
      </div>

      {/* Platform Cards */}
      <div className="grid gap-4 md:grid-cols-2">
        {data?.integrations.map((integration) => (
          <PlatformCard
            key={integration.platform}
            platform={integration.platform}
            connected={integration.connected}
            syncEnabled={integration.sync_enabled}
            syncDirection={integration.sync_direction}
            lastSyncAt={integration.last_sync_at}
            lastSyncStatus={integration.last_sync_status}
            lastSyncError={integration.last_sync_error}
            onConnect={() => setConnectPlatform(integration.platform)}
          />
        ))}
      </div>

      {/* Connect Dialog */}
      {connectPlatform && (
        <ConnectDialog
          platform={connectPlatform}
          open={!!connectPlatform}
          onOpenChange={(open) => !open && setConnectPlatform(null)}
        />
      )}
    </div>
  )
}
