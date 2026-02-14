import { useState, useEffect, useRef, useMemo } from 'react'
import {
  Eye,
  EyeOff,
  ExternalLink,
  Loader2,
  Copy,
  CheckCircle2,
  AlertCircle,
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  Power,
  PowerOff,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { STREAMING_PROVIDERS, STREMTHRU_STORES } from './constants'
import type {
  ConfigSectionProps,
  QBittorrentConfig,
  SABnzbdConfig,
  NZBGetConfig,
  EasynewsConfig,
  StreamingProviderConfigType,
} from './types'
import { getDeviceCode, authorizeWithDeviceCode, type DeviceCodeResponse } from '@/lib/api/debrid-oauth'

const SIGNUP_LINKS: Record<string, string | string[]> = {
  realdebrid: ['https://real-debrid.com/?id=9490816', 'https://real-debrid.com/?id=3351376'],
  alldebrid: 'https://alldebrid.com/?uid=3ndha&lang=en',
  premiumize: 'https://www.premiumize.me',
  debridlink: 'https://debrid-link.com/id/kHgZs',
  torbox: [
    'https://torbox.app/subscription?referral=38f1c266-8a6c-40b2-a6d2-2148e77dafc9',
    'https://torbox.app/subscription?referral=339b923e-fb23-40e7-8031-4af39c212e3c',
    'https://torbox.app/subscription?referral=e2a28977-99ed-43cd-ba2c-e90dc398c49c',
  ],
  seedr: 'https://www.seedr.cc/?r=2726511',
  offcloud: 'https://offcloud.com/?=9932cd9f',
  pikpak: 'https://mypikpak.com/drive/activity/invited?invitation-code=52875535',
  easydebrid: 'https://paradise-cloud.com/products/easydebrid',
  debrider: 'https://debrider.app/pricing',
  qbittorrent:
    'https://github.com/mhdzumair/MediaFusion/tree/main/streaming_providers/qbittorrent#qbittorrent-webdav-setup-options-with-mediafusion',
  stremthru: 'https://github.com/MunifTanjim/stremthru?tab=readme-ov-file#configuration',
}

// Helper function to get a random link from an array or return the single link
const getRandomSignupLink = (links: string | string[] | undefined): string | null => {
  if (!links) return null
  if (Array.isArray(links)) {
    return links[Math.floor(Math.random() * links.length)]
  }
  return links
}

const MAX_PROVIDERS = 5

interface SingleProviderEditorProps {
  provider: StreamingProviderConfigType
  index: number
  isExpanded: boolean
  onToggleExpand: () => void
  onUpdate: (updates: Partial<StreamingProviderConfigType>) => void
  onRemove: () => void
  onMoveUp: () => void
  onMoveDown: () => void
  canMoveUp: boolean
  canMoveDown: boolean
  totalProviders: number
  disabledProviders: string[]
  hasMediaFlowConfigured: boolean // Whether MediaFlow is globally configured
}

function SingleProviderEditor({
  provider,
  index,
  isExpanded,
  onToggleExpand,
  onUpdate,
  onRemove,
  onMoveUp,
  onMoveDown,
  canMoveUp,
  canMoveDown,
  totalProviders,
  disabledProviders,
  hasMediaFlowConfigured,
}: SingleProviderEditorProps) {
  // Filter available providers based on disabled list
  const availableProviders = useMemo(() => {
    return STREAMING_PROVIDERS.filter((p) => p.value && !disabledProviders.includes(p.value))
  }, [disabledProviders])

  // Filter StremThru stores
  const availableStremThruStores = useMemo(() => {
    return STREMTHRU_STORES.filter((s) => !s.value || !disabledProviders.includes(s.value))
  }, [disabledProviders])
  const [showToken, setShowToken] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [showQbPassword, setShowQbPassword] = useState(false)
  const [showWdPassword, setShowWdPassword] = useState(false)
  const [isAuthorizing, setIsAuthorizing] = useState(false)

  // OAuth Dialog State
  const [oauthDialogOpen, setOauthDialogOpen] = useState(false)
  const [oauthDeviceCode, setOauthDeviceCode] = useState<DeviceCodeResponse | null>(null)
  const [oauthError, setOauthError] = useState<string | null>(null)
  const [oauthSuccess, setOauthSuccess] = useState(false)
  const [codeCopied, setCodeCopied] = useState(false)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const selectedProvider = STREAMING_PROVIDERS.find((p) => p.value === (provider.sv || ''))
  const signupLink = getRandomSignupLink(provider.sv ? SIGNUP_LINKS[provider.sv] : undefined)
  const isEnabled = provider.en !== false

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
      }
    }
  }, [])

  const updateQBConfig = (updates: Partial<QBittorrentConfig>) => {
    const currentQbc = provider.qbc || {
      qur: '',
      qus: '',
      qpw: '',
      stl: 1440,
      srl: 1.0,
      pva: 100,
      cat: 'MediaFusion',
      wur: '',
      wus: '',
      wpw: '',
      wdp: '/',
    }
    onUpdate({ qbc: { ...currentQbc, ...updates } })
  }

  const updateSABnzbdConfig = (updates: Partial<SABnzbdConfig>) => {
    const currentSbc = provider.sbc || {
      u: '',
      ak: '',
      cat: 'MediaFusion',
      wur: '',
      wus: '',
      wpw: '',
      wdp: '/',
    }
    onUpdate({ sbc: { ...currentSbc, ...updates } })
  }

  const updateNZBGetConfig = (updates: Partial<NZBGetConfig>) => {
    const currentNgc = provider.ngc || {
      u: '',
      un: '',
      pw: '',
      cat: 'MediaFusion',
      wur: '',
      wus: '',
      wpw: '',
      wdp: '/',
    }
    onUpdate({ ngc: { ...currentNgc, ...updates } })
  }

  const updateEasynewsConfig = (updates: Partial<EasynewsConfig>) => {
    const currentEnc = provider.enc || { un: '', pw: '' }
    onUpdate({ enc: { ...currentEnc, ...updates } })
  }

  // Start OAuth flow
  const startOAuthFlow = async () => {
    if (!selectedProvider?.value) return

    setIsAuthorizing(true)
    setOauthError(null)
    setOauthSuccess(false)
    setCodeCopied(false)

    try {
      const deviceCode = await getDeviceCode(selectedProvider.value)
      setOauthDeviceCode(deviceCode)
      setOauthDialogOpen(true)

      const pollInterval = (deviceCode.interval || 5) * 1000
      let attempts = 0
      const maxAttempts = Math.ceil((deviceCode.expires_in || 600) / (deviceCode.interval || 5))

      pollIntervalRef.current = setInterval(async () => {
        attempts++
        if (attempts > maxAttempts) {
          clearInterval(pollIntervalRef.current!)
          setOauthError('Authorization timeout. Please try again.')
          setIsAuthorizing(false)
          return
        }

        try {
          const result = await authorizeWithDeviceCode(selectedProvider.value, deviceCode.device_code)

          if (result.token) {
            clearInterval(pollIntervalRef.current!)
            onUpdate({ tk: result.token })
            setOauthSuccess(true)
            setIsAuthorizing(false)

            setTimeout(() => {
              setOauthDialogOpen(false)
              setOauthDeviceCode(null)
            }, 2000)
          }
        } catch (err) {
          const errorMessage = err instanceof Error ? err.message : 'Unknown error'
          if (!errorMessage.includes('pending') && !errorMessage.includes('waiting')) {
            if (errorMessage.includes('expired')) {
              clearInterval(pollIntervalRef.current!)
              setOauthError('Authorization expired. Please try again.')
              setIsAuthorizing(false)
            }
          }
        }
      }, pollInterval)
    } catch (err) {
      setOauthError(err instanceof Error ? err.message : 'Failed to start authorization')
      setIsAuthorizing(false)
    }
  }

  const cancelOAuthFlow = () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current)
    }
    setOauthDialogOpen(false)
    setOauthDeviceCode(null)
    setOauthError(null)
    setIsAuthorizing(false)
  }

  const copyCode = async () => {
    if (oauthDeviceCode?.user_code) {
      await navigator.clipboard.writeText(oauthDeviceCode.user_code)
      setCodeCopied(true)
      setTimeout(() => setCodeCopied(false), 2000)
    }
  }

  const verificationUrl =
    oauthDeviceCode?.direct_verification_url || oauthDeviceCode?.verification_url || oauthDeviceCode?.verification_uri

  return (
    <div
      className={cn(
        'border rounded-lg transition-colors',
        isEnabled ? 'border-border' : 'border-border/50 bg-muted/30',
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2 p-3 cursor-pointer" onClick={onToggleExpand}>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={(e) => {
              e.stopPropagation()
              onMoveUp()
            }}
            disabled={!canMoveUp}
          >
            <ChevronUp className="h-3 w-3" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={(e) => {
              e.stopPropagation()
              onMoveDown()
            }}
            disabled={!canMoveDown}
          >
            <ChevronDown className="h-3 w-3" />
          </Button>
        </div>

        <Badge variant="outline" className="w-6 h-6 p-0 justify-center shrink-0">
          {index + 1}
        </Badge>

        <div className="flex-1 flex items-center gap-2 min-w-0">
          {selectedProvider ? (
            <>
              <span className="text-lg">{selectedProvider.icon}</span>
              <span className="font-medium truncate">{selectedProvider.label}</span>
              {provider.n && (
                <Badge variant="outline" className="text-xs font-mono">
                  {provider.n}
                </Badge>
              )}
              <Badge variant="secondary" className="text-xs">
                {selectedProvider.type}
              </Badge>
            </>
          ) : (
            <span className="text-muted-foreground">Select a provider...</span>
          )}
        </div>

        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => onUpdate({ en: !isEnabled })}>
            {isEnabled ? (
              <Power className="h-4 w-4 text-green-500" />
            ) : (
              <PowerOff className="h-4 w-4 text-muted-foreground" />
            )}
          </Button>

          {totalProviders > 1 && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-destructive hover:text-destructive"
              onClick={onRemove}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="p-4 pt-0 space-y-4 border-t">
          {/* Provider Select */}
          <div className="space-y-2">
            <Label>Service</Label>
            <Select
              value={provider.sv || ''}
              onValueChange={(value) => {
                // Reset provider-specific fields when changing service
                onUpdate({
                  sv: value,
                  tk: undefined,
                  em: undefined,
                  pw: undefined,
                  u: undefined,
                  stsn: undefined,
                  qbc: undefined,
                })
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select streaming provider" />
              </SelectTrigger>
              <SelectContent>
                {availableProviders.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    <div className="flex items-center gap-2">
                      <span>{p.icon}</span>
                      <span>{p.label}</span>
                      <Badge variant="secondary" className="ml-auto text-xs">
                        {p.type}
                      </Badge>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {signupLink && (
              <a
                href={signupLink}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-primary hover:underline flex items-center gap-1"
              >
                Don't have an account? Sign up here
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>

          {/* Provider Name (unique identifier) */}
          {provider.sv && (
            <div className="space-y-2">
              <Label>{provider.sv === 'p2p' ? 'Display Name' : 'Provider Name'}</Label>
              <Input
                value={provider.n || ''}
                onChange={(e) => onUpdate({ n: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '') })}
                placeholder={provider.sv === 'p2p' ? 'e.g., p2p, torrent, direct' : 'e.g., main, backup, rd-premium'}
                maxLength={20}
              />
              <p className="text-xs text-muted-foreground">
                {provider.sv === 'p2p'
                  ? 'Custom display name for P2P streams. Shows in provider tabs when switching between services.'
                  : 'Unique identifier for this provider. Used when selecting which debrid service to use for playback. Only lowercase letters, numbers, hyphens, and underscores.'}
              </p>
            </div>
          )}

          {/* P2P Info */}
          {provider.sv === 'p2p' && (
            <div className="text-sm text-muted-foreground p-3 bg-muted/50 rounded-lg">
              <p>Direct P2P streaming without a debrid service. Your IP will be visible to other peers.</p>
            </div>
          )}

          {/* StremThru Store Selection */}
          {selectedProvider?.hasStoreSelect && (
            <div className="space-y-2">
              <Label>Backend Store</Label>
              <Select
                value={provider.stsn || 'select'}
                onValueChange={(value) => onUpdate({ stsn: value === 'select' ? undefined : value })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select store" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="select">Select a store...</SelectItem>
                  {availableStremThruStores.map((store) => (
                    <SelectItem key={store.value} value={store.value}>
                      {store.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Service URL */}
          {selectedProvider?.needsUrl && (
            <div className="space-y-2">
              <Label>Service URL</Label>
              <Input
                value={provider.u || ''}
                onChange={(e) => onUpdate({ u: e.target.value })}
                placeholder="https://..."
              />
            </div>
          )}

          {/* OAuth Button */}
          {selectedProvider?.hasOAuth && (
            <div className="space-y-2">
              <Label>Authorization</Label>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  onClick={startOAuthFlow}
                  disabled={isAuthorizing}
                  className="flex items-center gap-2"
                >
                  {isAuthorizing ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Authorizing...
                    </>
                  ) : (
                    <>
                      <ExternalLink className="h-4 w-4" />
                      Authorize {selectedProvider.label}
                    </>
                  )}
                </Button>
                <span className="text-xs text-muted-foreground">Recommended</span>
              </div>
              {oauthError && (
                <Alert variant="destructive" className="mt-2">
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>{oauthError}</AlertDescription>
                </Alert>
              )}
            </div>
          )}

          {/* Token Input */}
          {selectedProvider?.needsToken && (
            <div className="space-y-2">
              <Label>API Token</Label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Input
                    type={showToken ? 'text' : 'password'}
                    value={provider.tk || ''}
                    onChange={(e) => onUpdate({ tk: e.target.value })}
                    placeholder="Enter your API token"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="absolute right-0 top-0 h-full px-3"
                    onClick={() => setShowToken(!showToken)}
                  >
                    {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Email/Password */}
          {selectedProvider?.needsEmail && (
            <div className="space-y-2">
              <Label>Email</Label>
              <Input
                type="email"
                value={provider.em || ''}
                onChange={(e) => onUpdate({ em: e.target.value })}
                placeholder="your@email.com"
              />
            </div>
          )}

          {selectedProvider?.needsPassword && (
            <div className="space-y-2">
              <Label>Password</Label>
              <div className="relative">
                <Input
                  type={showPassword ? 'text' : 'password'}
                  value={provider.pw || ''}
                  onChange={(e) => onUpdate({ pw: e.target.value })}
                  placeholder="Enter password"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0 h-full px-3"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
            </div>
          )}

          {/* qBittorrent Config */}
          {selectedProvider?.needsQBitConfig && (
            <Accordion type="single" collapsible className="w-full">
              <AccordionItem value="qbittorrent">
                <AccordionTrigger>qBittorrent Configuration</AccordionTrigger>
                <AccordionContent className="space-y-4 pt-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>qBittorrent URL</Label>
                      <Input
                        value={provider.qbc?.qur || ''}
                        onChange={(e) => updateQBConfig({ qur: e.target.value })}
                        placeholder="http://192.168.1.100:8080"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Username (optional)</Label>
                      <Input
                        value={provider.qbc?.qus || ''}
                        onChange={(e) => updateQBConfig({ qus: e.target.value })}
                        placeholder="admin"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Password (optional)</Label>
                      <div className="relative">
                        <Input
                          type={showQbPassword ? 'text' : 'password'}
                          value={provider.qbc?.qpw || ''}
                          onChange={(e) => updateQBConfig({ qpw: e.target.value })}
                          placeholder="••••••••"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3"
                          onClick={() => setShowQbPassword(!showQbPassword)}
                        >
                          {showQbPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label>Category</Label>
                      <Input
                        value={provider.qbc?.cat || 'MediaFusion'}
                        onChange={(e) => updateQBConfig({ cat: e.target.value })}
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label>WebDAV URL</Label>
                    <Input
                      value={provider.qbc?.wur || ''}
                      onChange={(e) => updateQBConfig({ wur: e.target.value })}
                      placeholder="http://192.168.1.100:8080/webdav"
                    />
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>WebDAV Username</Label>
                      <Input
                        value={provider.qbc?.wus || ''}
                        onChange={(e) => updateQBConfig({ wus: e.target.value })}
                        placeholder="webdav_user"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>WebDAV Password</Label>
                      <div className="relative">
                        <Input
                          type={showWdPassword ? 'text' : 'password'}
                          value={provider.qbc?.wpw || ''}
                          onChange={(e) => updateQBConfig({ wpw: e.target.value })}
                          placeholder="••••••••"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3"
                          onClick={() => setShowWdPassword(!showWdPassword)}
                        >
                          {showWdPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                      </div>
                    </div>
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          )}

          {/* SABnzbd Config */}
          {selectedProvider?.needsSABnzbdConfig && (
            <Accordion type="single" collapsible className="w-full">
              <AccordionItem value="sabnzbd">
                <AccordionTrigger>SABnzbd Configuration</AccordionTrigger>
                <AccordionContent className="space-y-4 pt-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>SABnzbd URL</Label>
                      <Input
                        value={provider.sbc?.u || ''}
                        onChange={(e) => updateSABnzbdConfig({ u: e.target.value })}
                        placeholder="http://192.168.1.100:8080"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>API Key</Label>
                      <div className="relative">
                        <Input
                          type={showToken ? 'text' : 'password'}
                          value={provider.sbc?.ak || ''}
                          onChange={(e) => updateSABnzbdConfig({ ak: e.target.value })}
                          placeholder="SABnzbd API Key"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3"
                          onClick={() => setShowToken(!showToken)}
                        >
                          {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label>Category</Label>
                      <Input
                        value={provider.sbc?.cat || 'MediaFusion'}
                        onChange={(e) => updateSABnzbdConfig({ cat: e.target.value })}
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label>WebDAV URL (for streaming)</Label>
                    <Input
                      value={provider.sbc?.wur || ''}
                      onChange={(e) => updateSABnzbdConfig({ wur: e.target.value })}
                      placeholder="http://192.168.1.100:8080/webdav"
                    />
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>WebDAV Username</Label>
                      <Input
                        value={provider.sbc?.wus || ''}
                        onChange={(e) => updateSABnzbdConfig({ wus: e.target.value })}
                        placeholder="webdav_user"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>WebDAV Password</Label>
                      <div className="relative">
                        <Input
                          type={showWdPassword ? 'text' : 'password'}
                          value={provider.sbc?.wpw || ''}
                          onChange={(e) => updateSABnzbdConfig({ wpw: e.target.value })}
                          placeholder="••••••••"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3"
                          onClick={() => setShowWdPassword(!showWdPassword)}
                        >
                          {showWdPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                      </div>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label>WebDAV Downloads Path</Label>
                    <Input
                      value={provider.sbc?.wdp || '/'}
                      onChange={(e) => updateSABnzbdConfig({ wdp: e.target.value })}
                      placeholder="/"
                    />
                    <p className="text-xs text-muted-foreground">Path to completed downloads folder on WebDAV server</p>
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          )}

          {/* NZBGet Config */}
          {selectedProvider?.needsNZBGetConfig && (
            <Accordion type="single" collapsible className="w-full">
              <AccordionItem value="nzbget">
                <AccordionTrigger>NZBGet Configuration</AccordionTrigger>
                <AccordionContent className="space-y-4 pt-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>NZBGet URL</Label>
                      <Input
                        value={provider.ngc?.u || ''}
                        onChange={(e) => updateNZBGetConfig({ u: e.target.value })}
                        placeholder="http://192.168.1.100:6789"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Username</Label>
                      <Input
                        value={provider.ngc?.un || ''}
                        onChange={(e) => updateNZBGetConfig({ un: e.target.value })}
                        placeholder="nzbget"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Password</Label>
                      <div className="relative">
                        <Input
                          type={showPassword ? 'text' : 'password'}
                          value={provider.ngc?.pw || ''}
                          onChange={(e) => updateNZBGetConfig({ pw: e.target.value })}
                          placeholder="••••••••"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3"
                          onClick={() => setShowPassword(!showPassword)}
                        >
                          {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label>Category</Label>
                      <Input
                        value={provider.ngc?.cat || 'MediaFusion'}
                        onChange={(e) => updateNZBGetConfig({ cat: e.target.value })}
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label>WebDAV URL (for streaming)</Label>
                    <Input
                      value={provider.ngc?.wur || ''}
                      onChange={(e) => updateNZBGetConfig({ wur: e.target.value })}
                      placeholder="http://192.168.1.100:8080/webdav"
                    />
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>WebDAV Username</Label>
                      <Input
                        value={provider.ngc?.wus || ''}
                        onChange={(e) => updateNZBGetConfig({ wus: e.target.value })}
                        placeholder="webdav_user"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>WebDAV Password</Label>
                      <div className="relative">
                        <Input
                          type={showWdPassword ? 'text' : 'password'}
                          value={provider.ngc?.wpw || ''}
                          onChange={(e) => updateNZBGetConfig({ wpw: e.target.value })}
                          placeholder="••••••••"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3"
                          onClick={() => setShowWdPassword(!showWdPassword)}
                        >
                          {showWdPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                      </div>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label>WebDAV Downloads Path</Label>
                    <Input
                      value={provider.ngc?.wdp || '/'}
                      onChange={(e) => updateNZBGetConfig({ wdp: e.target.value })}
                      placeholder="/"
                    />
                    <p className="text-xs text-muted-foreground">Path to completed downloads folder on WebDAV server</p>
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          )}

          {/* Easynews Config */}
          {selectedProvider?.needsEasynewsConfig && (
            <Accordion type="single" collapsible className="w-full" defaultValue="easynews">
              <AccordionItem value="easynews">
                <AccordionTrigger>Easynews Configuration</AccordionTrigger>
                <AccordionContent className="space-y-4 pt-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>Username</Label>
                      <Input
                        value={provider.enc?.un || ''}
                        onChange={(e) => updateEasynewsConfig({ un: e.target.value })}
                        placeholder="your_easynews_username"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Password</Label>
                      <div className="relative">
                        <Input
                          type={showPassword ? 'text' : 'password'}
                          value={provider.enc?.pw || ''}
                          onChange={(e) => updateEasynewsConfig({ pw: e.target.value })}
                          placeholder="••••••••"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="absolute right-0 top-0 h-full px-3"
                          onClick={() => setShowPassword(!showPassword)}
                        >
                          {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                      </div>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Easynews provides direct streaming without needing a separate downloader.
                  </p>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          )}

          {/* Provider Options - Only show for debrid providers (not P2P, qBittorrent, or Usenet-only) */}
          {selectedProvider &&
            selectedProvider.value &&
            selectedProvider.value !== 'p2p' &&
            selectedProvider.value !== 'qbittorrent' &&
            selectedProvider.value !== 'sabnzbd' &&
            selectedProvider.value !== 'nzbget' &&
            selectedProvider.value !== 'easynews' && (
              <div className="space-y-3 pt-2 border-t">
                <p className="text-sm font-medium">Provider Options</p>

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>Enable Watchlist Catalogs</Label>
                    <p className="text-xs text-muted-foreground">Show your watchlist as a catalog</p>
                  </div>
                  <Switch checked={provider.ewc !== false} onCheckedChange={(checked) => onUpdate({ ewc: checked })} />
                </div>

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>Only Show Cached Streams</Label>
                    <p className="text-xs text-muted-foreground">Hide uncached torrents</p>
                  </div>
                  <Switch checked={provider.oscs === true} onCheckedChange={(checked) => onUpdate({ oscs: checked })} />
                </div>

                {/* Use MediaFlow Proxy - only show when MediaFlow is globally configured */}
                {hasMediaFlowConfigured && (
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>Use MediaFlow Proxy</Label>
                      <p className="text-xs text-muted-foreground">Route streams through MediaFlow for this provider</p>
                    </div>
                    <Switch
                      checked={provider.umf !== false}
                      onCheckedChange={(checked) => onUpdate({ umf: checked })}
                    />
                  </div>
                )}
              </div>
            )}
        </div>
      )}

      {/* OAuth Dialog */}
      <Dialog open={oauthDialogOpen} onOpenChange={(open) => !open && cancelOAuthFlow()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {selectedProvider?.icon} Authorize {selectedProvider?.label}
            </DialogTitle>
            <DialogDescription>Follow the steps below to connect your account</DialogDescription>
          </DialogHeader>

          {oauthSuccess ? (
            <div className="flex flex-col items-center py-6 space-y-4">
              <div className="p-3 rounded-full bg-green-500/10">
                <CheckCircle2 className="h-12 w-12 text-green-500" />
              </div>
              <p className="text-lg font-medium text-green-600">Authorization Successful!</p>
              <p className="text-sm text-muted-foreground">Your account has been connected.</p>
            </div>
          ) : oauthDeviceCode ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <p className="text-sm font-medium">Step 1: Copy this code</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 p-3 text-2xl font-mono text-center bg-muted rounded-lg tracking-widest">
                    {oauthDeviceCode.user_code || oauthDeviceCode.device_code.substring(0, 8)}
                  </code>
                  <Button variant="outline" size="icon" onClick={copyCode}>
                    {codeCopied ? <CheckCircle2 className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium">Step 2: Visit the authorization page</p>
                <Button className="w-full" onClick={() => verificationUrl && window.open(verificationUrl, '_blank')}>
                  <ExternalLink className="h-4 w-4 mr-2" />
                  Open {selectedProvider?.label} Authorization
                </Button>
                {verificationUrl && (
                  <p className="text-xs text-muted-foreground text-center break-all">{verificationUrl}</p>
                )}
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium">Step 3: Enter the code and authorize</p>
                <p className="text-sm text-muted-foreground">
                  Paste the code on the website and click authorize. This page will automatically detect when you're
                  done.
                </p>
              </div>

              <div className="flex items-center justify-center gap-2 pt-4 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Waiting for authorization...
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          )}

          {!oauthSuccess && (
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={cancelOAuthFlow}>
                Cancel
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function MultiProviderConfig({ config, onChange }: ConfigSectionProps) {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(0)
  const [disabledProviders, setDisabledProviders] = useState<string[]>([])

  // Fetch disabled providers from app config
  useEffect(() => {
    fetch('/api/v1/instance/app-config')
      .then((res) => res.json())
      .then((data) => {
        if (data.disabled_providers) {
          setDisabledProviders(data.disabled_providers)
        }
      })
      .catch((err) => console.error('Failed to fetch app config:', err))
  }, [])

  // Get providers from multi-provider array or migrate from single provider
  const getProviders = (): StreamingProviderConfigType[] => {
    if (config.sps && config.sps.length > 0) {
      return config.sps
    }
    if (config.sp && config.sp.sv) {
      return [{ ...config.sp, pr: 0, en: true }]
    }
    return []
  }

  const providers = getProviders()

  const updateProviders = (newProviders: StreamingProviderConfigType[]) => {
    // Update priority based on order
    const providersWithPriority = newProviders.map((p, i) => ({
      ...p,
      pr: i,
      en: p.en !== false,
    }))

    onChange({
      ...config,
      sps: providersWithPriority,
      sp: undefined, // Clear legacy field
    })
  }

  const generateDefaultName = (index: number, service?: string): string => {
    // Generate a unique name based on service type or index
    let baseName: string
    if (service === 'p2p') {
      baseName = 'p2p'
    } else if (index === 0) {
      baseName = 'main'
    } else {
      baseName = `provider${index + 1}`
    }

    // Check if name already exists
    const existingNames = providers.map((p) => p.n)
    if (!existingNames.includes(baseName)) return baseName
    // If exists, append number
    let counter = 1
    while (existingNames.includes(`${baseName}_${counter}`)) counter++
    return `${baseName}_${counter}`
  }

  const addProvider = () => {
    if (providers.length >= MAX_PROVIDERS) return

    const newProvider: StreamingProviderConfigType = {
      n: generateDefaultName(providers.length),
      sv: '',
      pr: providers.length,
      en: true,
    }

    updateProviders([...providers, newProvider])
    setExpandedIndex(providers.length)
  }

  const removeProvider = (index: number) => {
    const newProviders = providers.filter((_, i) => i !== index)
    updateProviders(newProviders)

    if (expandedIndex === index) {
      setExpandedIndex(newProviders.length > 0 ? 0 : null)
    } else if (expandedIndex !== null && expandedIndex > index) {
      setExpandedIndex(expandedIndex - 1)
    }
  }

  const updateProvider = (index: number, updates: Partial<StreamingProviderConfigType>) => {
    const newProviders = [...providers]
    const currentProvider = newProviders[index]

    // If service is being changed and name is still default, update name to match service
    if (updates.sv && updates.sv !== currentProvider.sv) {
      const currentName = currentProvider.n || ''
      const isDefaultName =
        currentName === '' || currentName === 'main' || currentName.startsWith('provider') || currentName === 'p2p'

      if (isDefaultName) {
        updates.n = generateDefaultName(index, updates.sv)
      }
    }

    newProviders[index] = { ...currentProvider, ...updates }
    updateProviders(newProviders)
  }

  const moveProvider = (index: number, direction: 'up' | 'down') => {
    const newIndex = direction === 'up' ? index - 1 : index + 1
    if (newIndex < 0 || newIndex >= providers.length) return

    const newProviders = [...providers]
    ;[newProviders[index], newProviders[newIndex]] = [newProviders[newIndex], newProviders[index]]
    updateProviders(newProviders)

    setExpandedIndex(newIndex)
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">🔌 Streaming Providers</CardTitle>
            <CardDescription>
              Configure your debrid services. Higher priority providers are tried first.
            </CardDescription>
          </div>
          <Badge variant="secondary">
            {providers.filter((p) => p.en !== false && p.sv).length} active / {MAX_PROVIDERS} max
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {providers.length === 0 ? (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              No streaming providers configured. Add a provider to enable debrid streaming. Without a provider, only P2P
              streaming will be available.
            </AlertDescription>
          </Alert>
        ) : (
          <div className="space-y-3">
            {providers.map((provider, index) => (
              <SingleProviderEditor
                key={index}
                provider={provider}
                index={index}
                isExpanded={expandedIndex === index}
                onToggleExpand={() => setExpandedIndex(expandedIndex === index ? null : index)}
                onUpdate={(updates) => updateProvider(index, updates)}
                onRemove={() => removeProvider(index)}
                onMoveUp={() => moveProvider(index, 'up')}
                onMoveDown={() => moveProvider(index, 'down')}
                canMoveUp={index > 0}
                canMoveDown={index < providers.length - 1}
                totalProviders={providers.length}
                disabledProviders={disabledProviders}
                hasMediaFlowConfigured={!!(config.mfc?.pu && config.mfc?.ap)}
              />
            ))}
          </div>
        )}

        {providers.length < MAX_PROVIDERS && (
          <Button variant="outline" className="w-full" onClick={addProvider}>
            <Plus className="h-4 w-4 mr-2" />
            Add Streaming Provider
          </Button>
        )}

        {providers.length > 0 && (
          <p className="text-xs text-muted-foreground text-center">
            Providers are tried in order from top to bottom. Drag or use arrows to reorder.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
