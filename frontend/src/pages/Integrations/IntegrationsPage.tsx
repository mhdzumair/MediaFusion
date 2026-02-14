import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { 
  Link as LinkIcon, 
  Copy, 
  Check,
  ExternalLink,
  Tv2,
  Smartphone,
  Monitor,
  Play,
  Loader2,
  AlertCircle,
  Search,
} from 'lucide-react'
import { useProfiles, useManifestUrl } from '@/hooks'
import { useAuth } from '@/contexts/AuthContext'
import { getAppConfig, associateKodiManifest } from '@/lib/api'
import { ExternalPlatformIntegrations } from '@/components/integrations/ExternalPlatformIntegrations'

export function IntegrationsPage() {
  const { data: profiles, isLoading: profilesLoading } = useProfiles()
  const { user } = useAuth()
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null)
  const [copiedField, setCopiedField] = useState<string | null>(null)

  // Fetch app config to check if Torznab is enabled
  const { data: appConfig } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
    staleTime: 5 * 60 * 1000, // 5 minutes
  })

  // Auto-select default profile when profiles load
  if (profiles && selectedProfileId === null) {
    const defaultProfile = profiles.find(p => p.is_default) || profiles[0]
    if (defaultProfile) {
      setSelectedProfileId(defaultProfile.id)
    }
  }

  const { data: manifestData, isLoading: manifestLoading, error: manifestError } = useManifestUrl(selectedProfileId ?? undefined)

  // Kodi pairing state
  const [kodiCode, setKodiCode] = useState('')
  const [kodiLinking, setKodiLinking] = useState(false)
  const [kodiLinkStatus, setKodiLinkStatus] = useState<'idle' | 'success' | 'error'>('idle')
  const [kodiLinkError, setKodiLinkError] = useState<string | null>(null)

  // Build Torznab API key based on whether authentication is required
  const torznabApiKey = user?.uuid 
    ? (appConfig?.authentication_required 
        ? `<api_password>:${user.uuid}` 
        : user.uuid)
    : null
  const torznabUrl = appConfig?.host_url ? `${appConfig.host_url}/torznab` : null

  const selectedProfile = profiles?.find(p => p.id === selectedProfileId)

  const copyToClipboard = async (text: string, field: string) => {
    await navigator.clipboard.writeText(text)
    setCopiedField(field)
    setTimeout(() => setCopiedField(null), 2000)
  }

  const handleLinkKodi = async () => {
    if (!kodiCode.trim() || !manifestData?.manifest_url) return

    setKodiLinking(true)
    setKodiLinkStatus('idle')
    setKodiLinkError(null)

    try {
      await associateKodiManifest(kodiCode.trim(), manifestData.manifest_url)
      setKodiLinkStatus('success')
      setKodiCode('')
    } catch (error) {
      setKodiLinkStatus('error')
      setKodiLinkError(error instanceof Error ? error.message : 'Failed to link Kodi device')
    } finally {
      setKodiLinking(false)
    }
  }

  const openStremioInstall = () => {
    if (manifestData?.stremio_install_url) {
      window.open(manifestData.stremio_install_url, '_blank')
    }
  }

  if (profilesLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-6 md:grid-cols-2">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </div>
    )
  }

  if (!profiles || profiles.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <AlertCircle className="h-12 w-12 text-muted-foreground mb-4" />
        <h2 className="text-xl font-semibold mb-2">No Profiles Found</h2>
        <p className="text-muted-foreground mb-4">
          Create a profile in the Configure page to get started with integrations.
        </p>
        <Button asChild>
          <a href="/configure">Go to Configure</a>
        </Button>
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div className="space-y-1">
            <h1 className="text-2xl md:text-3xl font-bold flex items-center gap-3">
              <div className="p-2 rounded-lg bg-gradient-to-br from-primary to-primary/80">
                <LinkIcon className="h-6 w-6 text-white" />
              </div>
              Integrations
            </h1>
            <p className="text-muted-foreground">
              Connect MediaFusion to your favorite streaming apps
            </p>
          </div>

          {/* Profile selector */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-muted-foreground">Profile:</span>
            <Select
              value={selectedProfileId?.toString() ?? ''}
              onValueChange={(value) => setSelectedProfileId(parseInt(value, 10))}
            >
              <SelectTrigger className="w-[200px]">
                <SelectValue placeholder="Select profile" />
              </SelectTrigger>
              <SelectContent>
                {profiles.map((profile) => (
                  <SelectItem key={profile.id} value={profile.id.toString()}>
                    <div className="flex items-center gap-2">
                      <span>{profile.name}</span>
                      {profile.is_default && (
                        <Badge variant="secondary" className="text-xs">Default</Badge>
                      )}
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Profile info */}
        {selectedProfile && !selectedProfile.streaming_providers?.has_debrid && (
          <Card className="border-primary/50 bg-primary/10">
            <CardContent className="flex items-center gap-4 py-4">
              <AlertCircle className="h-5 w-5 text-primary flex-shrink-0" />
              <div>
                <p className="font-medium text-primary">Profile not fully configured</p>
                <p className="text-sm text-muted-foreground">
                  This profile doesn't have a streaming provider configured. Some features may not work.
                </p>
              </div>
              <Button variant="outline" size="sm" className="ml-auto" asChild>
                <a href="/configure">Configure</a>
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Integration cards */}
        <div className="grid gap-6 md:grid-cols-2">
          {/* Stremio */}
          <Card className="relative overflow-hidden">
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-br from-primary/20 to-primary/5 rounded-full -translate-y-1/2 translate-x-1/2" />
            <CardHeader>
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-gradient-to-br from-primary to-primary/80">
                  <Play className="h-5 w-5 text-white" />
                </div>
                <div>
                  <CardTitle>Stremio</CardTitle>
                  <CardDescription>Stream on any device with Stremio</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {manifestLoading ? (
                <div className="space-y-3">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : manifestError ? (
                <div className="text-sm text-red-500">
                  Failed to generate manifest URL. Please check your profile configuration.
                </div>
              ) : manifestData ? (
                <>
                  {/* Install button */}
                  <Button 
                    onClick={openStremioInstall}
                    className="w-full bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
                  >
                    <ExternalLink className="h-4 w-4 mr-2" />
                    Install in Stremio
                  </Button>

                  {/* Manifest URL */}
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Manifest URL</label>
                    <div className="flex gap-2">
                      <Input 
                        value={manifestData.manifest_url} 
                        readOnly 
                        className="font-mono text-xs"
                      />
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button 
                            variant="outline" 
                            size="icon"
                            onClick={() => copyToClipboard(manifestData.manifest_url, 'manifest')}
                          >
                            {copiedField === 'manifest' ? (
                              <Check className="h-4 w-4 text-emerald-500" />
                            ) : (
                              <Copy className="h-4 w-4" />
                            )}
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          {copiedField === 'manifest' ? 'Copied!' : 'Copy URL'}
                        </TooltipContent>
                      </Tooltip>
                    </div>
                  </div>

                  {/* Supported platforms */}
                  <div className="flex gap-4 pt-2">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <div className="flex items-center gap-2 text-muted-foreground">
                          <Monitor className="h-4 w-4" />
                          <span className="text-xs">Desktop</span>
                        </div>
                      </TooltipTrigger>
                      <TooltipContent>Windows, macOS, Linux</TooltipContent>
                    </Tooltip>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <div className="flex items-center gap-2 text-muted-foreground">
                          <Smartphone className="h-4 w-4" />
                          <span className="text-xs">Mobile</span>
                        </div>
                      </TooltipTrigger>
                      <TooltipContent>Android, iOS</TooltipContent>
                    </Tooltip>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <div className="flex items-center gap-2 text-muted-foreground">
                          <Tv2 className="h-4 w-4" />
                          <span className="text-xs">TV</span>
                        </div>
                      </TooltipTrigger>
                      <TooltipContent>Android TV, Samsung TV, LG webOS</TooltipContent>
                    </Tooltip>
                  </div>
                </>
              ) : null}

              {/* Instructions */}
              <Accordion type="single" collapsible className="w-full">
                <AccordionItem value="instructions" className="border-none">
                  <AccordionTrigger className="text-sm py-2">
                    Installation Instructions
                  </AccordionTrigger>
                  <AccordionContent className="text-sm text-muted-foreground space-y-2">
                    <p><strong>Method 1:</strong> Click "Install in Stremio" button above. Stremio will open and prompt you to install MediaFusion.</p>
                    <p><strong>Method 2:</strong> Copy the manifest URL, open Stremio, go to Add-ons, click "Install from URL", and paste the URL.</p>
                    <p><strong>Note:</strong> Make sure you have Stremio installed on your device first.</p>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </CardContent>
          </Card>

          {/* Kodi */}
          <Card className="relative overflow-hidden">
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-br from-blue-500/20 to-blue-600/5 rounded-full -translate-y-1/2 translate-x-1/2" />
            <CardHeader>
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-gradient-to-br from-blue-500 to-blue-700">
                  <Tv2 className="h-5 w-5 text-white" />
                </div>
                <div>
                  <CardTitle>Kodi</CardTitle>
                  <CardDescription>Use with Kodi media center</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {manifestLoading ? (
                <div className="space-y-3">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : manifestError ? (
                <div className="text-sm text-red-500">
                  Failed to generate manifest URL. Please check your profile configuration.
                </div>
              ) : manifestData ? (
                <>
                  {/* Kodi pairing code input */}
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Setup Code from Kodi</label>
                    <p className="text-xs text-muted-foreground">
                      Enter the 6-digit code shown on your Kodi screen
                    </p>
                    <div className="flex gap-2">
                      <Input 
                        value={kodiCode}
                        onChange={(e) => {
                          setKodiCode(e.target.value)
                          setKodiLinkStatus('idle')
                          setKodiLinkError(null)
                        }}
                        placeholder="e.g. a1b2c3"
                        maxLength={6}
                        className="font-mono text-center text-lg tracking-widest"
                      />
                      <Button
                        onClick={handleLinkKodi}
                        className="bg-gradient-to-r from-blue-500 to-blue-700 hover:from-blue-600 hover:to-blue-800 whitespace-nowrap"
                        disabled={kodiLinking || kodiCode.trim().length < 6}
                      >
                        {kodiLinking ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          'Link Kodi'
                        )}
                      </Button>
                    </div>
                  </div>

                  {/* Status feedback */}
                  {kodiLinkStatus === 'success' && (
                    <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/30 p-3 rounded-lg">
                      <Check className="h-4 w-4 flex-shrink-0" />
                      <span>Kodi linked successfully! Your configuration will appear in Kodi shortly.</span>
                    </div>
                  )}
                  {kodiLinkStatus === 'error' && (
                    <div className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30 p-3 rounded-lg">
                      <AlertCircle className="h-4 w-4 flex-shrink-0" />
                      <span>{kodiLinkError || 'Failed to link Kodi device. Please check the code and try again.'}</span>
                    </div>
                  )}
                </>
              ) : null}

              {/* Supported platforms */}
              <div className="flex gap-4 pt-2">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Monitor className="h-4 w-4" />
                      <span className="text-xs">Desktop</span>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>Windows, macOS, Linux</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Smartphone className="h-4 w-4" />
                      <span className="text-xs">Mobile</span>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>Android, iOS</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Tv2 className="h-4 w-4" />
                      <span className="text-xs">TV</span>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>Fire TV, Raspberry Pi, LibreELEC</TooltipContent>
                </Tooltip>
              </div>

              {/* Instructions */}
              <Accordion type="single" collapsible className="w-full">
                <AccordionItem value="instructions" className="border-none">
                  <AccordionTrigger className="text-sm py-2">
                    Setup Instructions
                  </AccordionTrigger>
                  <AccordionContent className="text-sm text-muted-foreground space-y-2">
                    <p><strong>Step 1:</strong> Install the MediaFusion addon in Kodi from the MediaFusion repository.</p>
                    <p><strong>Step 2:</strong> Open the addon in Kodi and click "Configure Secret" to generate a setup code.</p>
                    <p><strong>Step 3:</strong> Enter the 6-digit code displayed on your Kodi screen into the field above.</p>
                    <p><strong>Step 4:</strong> Click "Link Kodi" and your configuration will be sent to the Kodi addon automatically.</p>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </CardContent>
          </Card>
        </div>

        {/* Torznab API Section */}
        {appConfig?.torznab_enabled && (
          <Card className="relative overflow-hidden">
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-br from-emerald-500/20 to-emerald-600/5 rounded-full -translate-y-1/2 translate-x-1/2" />
            <CardHeader>
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-gradient-to-br from-emerald-500 to-emerald-700">
                  <Search className="h-5 w-5 text-white" />
                </div>
                <div>
                  <CardTitle>Torznab API</CardTitle>
                  <CardDescription>Use MediaFusion as an indexer in Sonarr, Radarr, or Prowlarr</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Torznab URL */}
              {torznabUrl && (
                <div className="space-y-2">
                  <label className="text-sm font-medium">Torznab URL</label>
                  <div className="flex gap-2">
                    <Input 
                      value={torznabUrl} 
                      readOnly 
                      className="font-mono text-xs"
                    />
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button 
                          variant="outline" 
                          size="icon"
                          onClick={() => copyToClipboard(torznabUrl, 'torznab-url')}
                        >
                          {copiedField === 'torznab-url' ? (
                            <Check className="h-4 w-4 text-emerald-500" />
                          ) : (
                            <Copy className="h-4 w-4" />
                          )}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        {copiedField === 'torznab-url' ? 'Copied!' : 'Copy URL'}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                </div>
              )}

              {/* API Key */}
              {torznabApiKey && (
                <div className="space-y-2">
                  <label className="text-sm font-medium">API Key</label>
                  <div className="flex gap-2">
                    <Input 
                      value={torznabApiKey} 
                      readOnly 
                      className="font-mono text-xs"
                    />
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button 
                          variant="outline" 
                          size="icon"
                          onClick={() => copyToClipboard(torznabApiKey, 'torznab-key')}
                        >
                          {copiedField === 'torznab-key' ? (
                            <Check className="h-4 w-4 text-emerald-500" />
                          ) : (
                            <Copy className="h-4 w-4" />
                          )}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        {copiedField === 'torznab-key' ? 'Copied!' : 'Copy API Key'}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                  {appConfig?.authentication_required && (
                    <p className="text-xs text-muted-foreground">
                      Replace <code className="bg-muted px-1 rounded">&lt;api_password&gt;</code> with your server's API password
                    </p>
                  )}
                </div>
              )}

              {/* Compatible apps */}
              <div className="flex gap-4 pt-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <span className="text-xs">Sonarr</span>
                </div>
                <div className="flex items-center gap-2 text-muted-foreground">
                  <span className="text-xs">Radarr</span>
                </div>
                <div className="flex items-center gap-2 text-muted-foreground">
                  <span className="text-xs">Prowlarr</span>
                </div>
                <div className="flex items-center gap-2 text-muted-foreground">
                  <span className="text-xs">Jackett</span>
                </div>
              </div>

              {/* Instructions */}
              <Accordion type="single" collapsible className="w-full">
                <AccordionItem value="instructions" className="border-none">
                  <AccordionTrigger className="text-sm py-2">
                    Setup Instructions
                  </AccordionTrigger>
                  <AccordionContent className="text-sm text-muted-foreground space-y-2">
                    <p><strong>For Prowlarr:</strong> Add a new indexer → Generic Torznab → Enter the URL and API Key above.</p>
                    <p><strong>For Sonarr/Radarr:</strong> Settings → Indexers → Add → Torznab → Custom → Enter URL and API Key.</p>
                    <p><strong>Categories:</strong> Movies (2000-2060), TV (5000-5070)</p>
                    <p><strong>Note:</strong> Searches by IMDb ID or title are supported.</p>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </CardContent>
          </Card>
        )}

        {/* Other apps section */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Other Compatible Apps</CardTitle>
            <CardDescription>
              MediaFusion works with any app that supports the Stremio manifest format
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3">
              <div className="p-4 rounded-lg border bg-card">
                <h4 className="font-medium mb-1">Streamio</h4>
                <p className="text-xs text-muted-foreground">Alternative Stremio client</p>
              </div>
              <div className="p-4 rounded-lg border bg-card">
                <h4 className="font-medium mb-1">Stremio Web</h4>
                <p className="text-xs text-muted-foreground">Browser-based streaming</p>
              </div>
              <div className="p-4 rounded-lg border bg-card">
                <h4 className="font-medium mb-1">Any M3U Player</h4>
                <p className="text-xs text-muted-foreground">For live TV streams</p>
              </div>
            </div>

            {manifestData?.manifest_url && (
              <div className="mt-4 p-4 rounded-lg bg-muted">
                <p className="text-sm font-medium mb-2">Generic Manifest URL</p>
                <p className="text-xs text-muted-foreground mb-2">
                  Use this URL with any app that supports Stremio manifest format:
                </p>
                <code className="text-xs bg-background p-2 rounded block break-all">
                  {manifestData.manifest_url}
                </code>
              </div>
            )}
          </CardContent>
        </Card>

        {/* External Platform Integrations (Trakt, Simkl, etc.) */}
        {user && <ExternalPlatformIntegrations />}
      </div>
    </TooltipProvider>
  )
}

