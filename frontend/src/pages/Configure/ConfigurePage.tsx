import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import {
  Settings,
  Plus,
  ArrowLeft,
  Loader2,
  AlertCircle,
  Server,
  Sparkles,
  CheckCircle2,
  XCircle,
  UserPlus,
  ExternalLink,
  Copy,
  Check,
  Tv,
  Tv2,
  Shield,
  Key,
  Eye,
  EyeOff,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useProfiles, useCreateProfile, useUpdateProfile, useDeleteProfile, useSetDefaultProfile } from '@/hooks'
import { useAuth } from '@/contexts/AuthContext'
import { useInstance } from '@/contexts/InstanceContext'
import type { Profile } from '@/lib/api'
import { encryptUserData, generateManifestUrls, associateKodiManifest } from '@/lib/api/anonymous'
import { cn } from '@/lib/utils'
import {
  MultiProviderConfig,
  CatalogConfig,
  StreamingPreferences,
  ParentalGuides,
  ExternalServices,
  MDBListConfigComponent,
  ProfileHeader,
  StreamFormatterConfig,
  IndexerSettings,
  UsenetSettings,
  TelegramSettings,
  AceStreamSettings,
  DEFAULT_CONFIG,
} from './components'
import type { ProfileConfig } from './components'

// Profile card for the list view
function ProfileCard({ profile, onSelect }: { profile: Profile; onSelect: () => void }) {
  const setDefault = useSetDefaultProfile()

  const cfg = profile.config as ProfileConfig
  // Get providers from multi-provider array or legacy single provider
  const providers = cfg?.sps?.filter((p) => p.en !== false && p.sv) || []
  const legacySp = cfg?.sp
  const hasProviders = providers.length > 0 || legacySp?.sv

  const getProviderNames = () => {
    if (providers.length > 0) {
      return providers.map((p) => p.sv).join(', ')
    }
    if (legacySp?.sv) {
      return legacySp.sv
    }
    return null
  }

  const providerNames = getProviderNames()

  return (
    <Card
      className={cn(
        'relative overflow-hidden transition-all hover:shadow-lg cursor-pointer group',
        profile.is_default && 'border-primary/50 shadow-primary/10',
      )}
      onClick={onSelect}
    >
      {profile.is_default && (
        <div className="absolute top-0 right-0 px-3 py-1 bg-gradient-to-l from-primary to-primary/80 text-xs text-white font-medium rounded-bl-lg">
          Default
        </div>
      )}
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <CardTitle className="text-lg">{profile.name}</CardTitle>
            <CardDescription className="flex items-center gap-2">
              {hasProviders ? (
                <span className="capitalize">{providerNames}</span>
              ) : (
                <>
                  <AlertCircle className="h-3 w-3 text-primary" />
                  <span>No provider configured</span>
                </>
              )}
              {providers.length > 1 && (
                <Badge variant="outline" className="text-xs">
                  {providers.length} providers
                </Badge>
              )}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>Catalogs:</span>
          <Badge variant="secondary">{profile.catalogs_enabled} enabled</Badge>
        </div>

        <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <Button variant="outline" size="sm" className="flex-1">
            Edit Configuration
          </Button>
          {!profile.is_default && (
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation()
                setDefault.mutate(profile.id)
              }}
              disabled={setDefault.isPending}
            >
              Set Default
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

// Kodi pairing code card (reusable for both anonymous and authenticated flows)
function KodiPairingCard({ manifestUrl }: { manifestUrl: string }) {
  const [kodiCode, setKodiCode] = useState('')
  const [linking, setLinking] = useState(false)
  const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const handleLink = async () => {
    if (!kodiCode.trim() || !manifestUrl) return

    setLinking(true)
    setStatus('idle')
    setErrorMsg(null)

    try {
      await associateKodiManifest(kodiCode.trim(), manifestUrl)
      setStatus('success')
      setKodiCode('')
    } catch (error) {
      setStatus('error')
      setErrorMsg(error instanceof Error ? error.message : 'Failed to link Kodi device')
    } finally {
      setLinking(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Tv2 className="h-5 w-5 text-blue-500" />
          Link to Kodi
        </CardTitle>
        <CardDescription>Enter the 6-digit code from your Kodi addon</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Open the MediaFusion addon in Kodi and click "Configure Secret" to get a setup code, then enter it below.
        </p>
        <div className="flex gap-2">
          <Input
            value={kodiCode}
            onChange={(e) => {
              setKodiCode(e.target.value)
              setStatus('idle')
              setErrorMsg(null)
            }}
            placeholder="e.g. a1b2c3"
            maxLength={6}
            className="font-mono text-center text-lg tracking-widest"
          />
          <Button onClick={handleLink} variant="outline" disabled={linking || kodiCode.trim().length < 6}>
            {linking ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Link Kodi'}
          </Button>
        </div>

        {status === 'success' && (
          <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/30 p-3 rounded-lg">
            <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
            <span>Kodi linked successfully! Your configuration will appear in Kodi shortly.</span>
          </div>
        )}
        {status === 'error' && (
          <div className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30 p-3 rounded-lg">
            <XCircle className="h-4 w-4 flex-shrink-0" />
            <span>{errorMsg || 'Failed to link Kodi device. Check the code and try again.'}</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// Anonymous Install URLs component
function AnonymousInstallUrls({ encryptedStr, onReset }: { encryptedStr: string; onReset: () => void }) {
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)
  const urls = generateManifestUrls(encryptedStr)

  const copyToClipboard = async (url: string, type: string) => {
    await navigator.clipboard.writeText(url)
    setCopiedUrl(type)
    setTimeout(() => setCopiedUrl(null), 2000)
  }

  return (
    <div className="space-y-6">
      <Alert className="border-green-500 bg-green-500/10">
        <CheckCircle2 className="h-4 w-4 text-green-600" />
        <AlertDescription className="text-green-700 dark:text-green-400">
          Your configuration has been generated successfully!
        </AlertDescription>
      </Alert>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Tv className="h-5 w-5 text-primary" />
            Install in Stremio
          </CardTitle>
          <CardDescription>Click the button or copy the manifest URL</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Input value={urls.manifestUrl} readOnly className="font-mono text-sm" />
            <Button variant="outline" size="icon" onClick={() => copyToClipboard(urls.manifestUrl, 'manifest')}>
              {copiedUrl === 'manifest' ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
            </Button>
          </div>
          <Button asChild variant="gold" className="w-full">
            <a href={urls.stremioInstallUrl}>
              <ExternalLink className="h-4 w-4 mr-2" />
              Install in Stremio
            </a>
          </Button>
        </CardContent>
      </Card>

      <KodiPairingCard manifestUrl={urls.manifestUrl} />

      <div className="flex flex-col sm:flex-row gap-3">
        <Button variant="outline" onClick={onReset} className="flex-1">
          <ArrowLeft className="h-4 w-4 mr-2" />
          Modify Configuration
        </Button>
        <Button asChild variant="gold" className="flex-1">
          <Link to="/register">
            <UserPlus className="h-4 w-4 mr-2" />
            Create Account to Save
          </Link>
        </Button>
      </div>
    </div>
  )
}

// Anonymous Configuration Editor
function AnonymousConfigEditor() {
  const { isApiKeyRequired, isApiKeySet, setApiKey, apiKey } = useInstance()
  const [config, setConfig] = useState<ProfileConfig>({ ...DEFAULT_CONFIG })
  const [activeTab, setActiveTab] = useState('provider')
  const [isGenerating, setIsGenerating] = useState(false)
  const [encryptedStr, setEncryptedStr] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // API Key input state for private instances
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)
  const [apiKeyError, setApiKeyError] = useState<string | null>(null)

  // Initialize API key input from stored value
  useEffect(() => {
    if (apiKey) {
      setApiKeyInput(apiKey)
    }
  }, [apiKey])

  const handleSaveApiKey = () => {
    if (!apiKeyInput.trim()) {
      setApiKeyError('API key is required')
      return
    }
    setApiKey(apiKeyInput.trim())
    setApiKeyError(null)
  }

  // If we have an encrypted string, show the install URLs
  if (encryptedStr) {
    return <AnonymousInstallUrls encryptedStr={encryptedStr} onReset={() => setEncryptedStr(null)} />
  }

  const handleGenerate = async () => {
    setIsGenerating(true)
    setError(null)

    try {
      const result = await encryptUserData(config)

      if (result.status === 'error') {
        setError(result.message || 'Failed to generate configuration')
        return
      }

      if (result.encrypted_str) {
        setEncryptedStr(result.encrypted_str)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An unexpected error occurred')
    } finally {
      setIsGenerating(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div className="space-y-1">
          <h1 className="font-display text-2xl md:text-3xl font-semibold flex items-center gap-3 tracking-tight">
            <div className="p-2 rounded-md bg-primary/10 border border-primary/20">
              <Settings className="h-5 w-5 text-primary" />
            </div>
            Configure Add-on
          </h1>
          <p className="text-muted-foreground">Set up your streaming preferences without an account</p>
        </div>

        <Button onClick={handleGenerate} disabled={isGenerating || (isApiKeyRequired && !isApiKeySet)} variant="gold">
          {isGenerating ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Generating...
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4 mr-2" />
              Generate Install URL
            </>
          )}
        </Button>
      </div>

      {/* Error Alert */}
      {error && (
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* API Key Section for Private Instances */}
      {isApiKeyRequired && (
        <Card className="border-primary/20 bg-primary/5">
          <CardContent className="p-4 space-y-3">
            <div className="flex items-center gap-2 text-primary">
              <Shield className="h-4 w-4" />
              <span className="text-sm font-medium">Private Instance</span>
            </div>
            <p className="text-xs text-muted-foreground">
              This is a private instance. Enter the API key provided by the instance owner to configure the addon.
            </p>
            <div className="space-y-2">
              <Label htmlFor="apiKey">API Key</Label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Key className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    id="apiKey"
                    type={showApiKey ? 'text' : 'password'}
                    placeholder="Enter API key"
                    value={apiKeyInput}
                    onChange={(e) => {
                      setApiKeyInput(e.target.value)
                      if (apiKeyError) {
                        setApiKeyError(null)
                      }
                    }}
                    className={`pl-10 pr-10 ${apiKeyError ? 'border-destructive focus-visible:ring-destructive' : ''}`}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                    onClick={() => setShowApiKey(!showApiKey)}
                  >
                    {showApiKey ? (
                      <EyeOff className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <Eye className="h-4 w-4 text-muted-foreground" />
                    )}
                  </Button>
                </div>
                <Button type="button" variant={isApiKeySet ? 'outline' : 'default'} onClick={handleSaveApiKey}>
                  {isApiKeySet ? 'Update' : 'Save'}
                </Button>
              </div>
              {apiKeyError && <p className="text-sm text-destructive">{apiKeyError}</p>}
              {isApiKeySet && (
                <p className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                  <Shield className="h-3 w-3" />
                  API key saved - you can now generate your install URL
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Account Suggestion */}
      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="p-4 flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="flex-1 space-y-1">
            <h3 className="font-medium flex items-center gap-2">
              <UserPlus className="h-4 w-4 text-primary" />
              Want to save your configuration?
            </h3>
            <p className="text-sm text-muted-foreground">
              Create an account to save multiple profiles and access them from anywhere.
            </p>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link to="/register">Create Account</Link>
          </Button>
        </CardContent>
      </Card>

      {/* Configuration Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="w-full h-auto flex-wrap gap-1 bg-muted/50 p-2 rounded-xl">
          <TabsTrigger value="provider" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">🌐</span> Provider
          </TabsTrigger>
          <TabsTrigger value="catalogs" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📚</span> Catalogs
          </TabsTrigger>
          <TabsTrigger value="preferences" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">⚙️</span> Preferences
          </TabsTrigger>
          <TabsTrigger value="parental" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">👨‍👩‍👧‍👦</span> Parental
          </TabsTrigger>
          <TabsTrigger value="services" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">🔌</span> Services
          </TabsTrigger>
          <TabsTrigger value="indexers" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">🔍</span> Indexers
          </TabsTrigger>
          <TabsTrigger value="usenet" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📰</span> Usenet
          </TabsTrigger>
          <TabsTrigger value="telegram" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📨</span> Telegram
          </TabsTrigger>
          <TabsTrigger value="acestream" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📡</span> AceStream
          </TabsTrigger>
          <TabsTrigger value="formatter" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📝</span> Formatter
          </TabsTrigger>
        </TabsList>

        <TabsContent value="provider" className="mt-6">
          <MultiProviderConfig config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="catalogs" className="mt-6">
          <div className="space-y-6">
            <CatalogConfig config={config} onChange={setConfig} />
            <MDBListConfigComponent config={config} onChange={setConfig} />
          </div>
        </TabsContent>

        <TabsContent value="preferences" className="mt-6">
          <StreamingPreferences config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="parental" className="mt-6">
          <ParentalGuides config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="services" className="mt-6">
          <ExternalServices config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="indexers" className="mt-6">
          <IndexerSettings config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="usenet" className="mt-6">
          <UsenetSettings config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="telegram" className="mt-6">
          <TelegramSettings config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="acestream" className="mt-6">
          <AceStreamSettings config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="formatter" className="mt-6">
          <StreamFormatterConfig config={config} onChange={setConfig} />
        </TabsContent>
      </Tabs>

      {/* Bottom Generate Button */}
      <div className="flex justify-end pt-4 border-t">
        <Button onClick={handleGenerate} disabled={isGenerating} variant="gold" size="lg">
          {isGenerating ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Generating...
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4 mr-2" />
              Generate Install URL
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

// Profile Editor Component (for authenticated users)
function ProfileEditor({ profile, onBack, isNew = false }: { profile?: Profile; onBack: () => void; isNew?: boolean }) {
  const createProfile = useCreateProfile()
  const updateProfile = useUpdateProfile()
  const deleteProfile = useDeleteProfile()
  const setDefaultProfile = useSetDefaultProfile()

  const [name, setName] = useState(profile?.name || '')
  const [isDefault, setIsDefault] = useState(profile?.is_default || false)
  const [config, setConfig] = useState<ProfileConfig>(() => {
    if (profile?.config) {
      return profile.config as ProfileConfig
    }
    return { ...DEFAULT_CONFIG }
  })
  const [activeTab, setActiveTab] = useState('provider')
  const [saveStatus, setSaveStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const isPending = createProfile.isPending || updateProfile.isPending

  // Clear status message after 5 seconds
  useEffect(() => {
    if (saveStatus) {
      const timer = setTimeout(() => setSaveStatus(null), 5000)
      return () => clearTimeout(timer)
    }
  }, [saveStatus])

  const handleSave = async () => {
    setSaveStatus(null)
    try {
      if (isNew) {
        await createProfile.mutateAsync({
          name: name || 'New Profile',
          is_default: isDefault,
          config: config as Record<string, unknown>,
        })
        setSaveStatus({ type: 'success', message: 'Profile created successfully!' })
        setTimeout(() => onBack(), 1000) // Delay to show success message
      } else if (profile) {
        const updatedProfile = await updateProfile.mutateAsync({
          profileId: profile.id,
          data: {
            name,
            is_default: isDefault,
            config: config as Record<string, unknown>,
          },
        })
        // Update local state with the response from server
        if (updatedProfile.config) {
          setConfig(updatedProfile.config as ProfileConfig)
        }
        setName(updatedProfile.name)
        setIsDefault(updatedProfile.is_default)
        setSaveStatus({ type: 'success', message: 'Profile saved successfully!' })
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to save profile'
      setSaveStatus({ type: 'error', message })
    }
  }

  const handleDelete = async () => {
    if (profile && confirm('Are you sure you want to delete this profile?')) {
      try {
        await deleteProfile.mutateAsync(profile.id)
        onBack()
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to delete profile'
        setSaveStatus({ type: 'error', message })
      }
    }
  }

  const handleSetDefault = async () => {
    if (profile) {
      try {
        await setDefaultProfile.mutateAsync(profile.id)
        setIsDefault(true)
        setSaveStatus({ type: 'success', message: 'Profile set as default!' })
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to set default'
        setSaveStatus({ type: 'error', message })
      }
    }
  }

  return (
    <div className="space-y-6">
      {/* Status Alert */}
      {saveStatus && (
        <Alert
          variant={saveStatus.type === 'error' ? 'destructive' : 'default'}
          className={cn(
            'transition-all',
            saveStatus.type === 'success' && 'border-green-500 bg-green-500/10 text-green-700 dark:text-green-400',
          )}
        >
          {saveStatus.type === 'success' ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
          <AlertDescription>{saveStatus.message}</AlertDescription>
        </Alert>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack} className="gap-2">
          <ArrowLeft className="h-4 w-4" />
          Back to Profiles
        </Button>

        <div className="flex items-center gap-2">
          <Button onClick={handleSave} disabled={isPending || !name.trim()} variant="gold">
            {isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Saving...
              </>
            ) : isNew ? (
              'Create Profile'
            ) : (
              'Save Changes'
            )}
          </Button>
        </div>
      </div>

      {/* Profile Header Card */}
      <ProfileHeader
        profileId={profile?.id}
        name={name}
        isDefault={isDefault}
        isNew={isNew}
        onNameChange={setName}
        onDefaultChange={setIsDefault}
        onDelete={!isNew ? handleDelete : undefined}
        onSetDefault={!isNew && !isDefault ? handleSetDefault : undefined}
      />

      {/* Configuration Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="w-full h-auto flex-wrap gap-1 bg-muted/50 p-2 rounded-xl">
          <TabsTrigger value="provider" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">🌐</span> Provider
          </TabsTrigger>
          <TabsTrigger value="catalogs" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📚</span> Catalogs
          </TabsTrigger>
          <TabsTrigger value="preferences" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">⚙️</span> Preferences
          </TabsTrigger>
          <TabsTrigger value="parental" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">👨‍👩‍👧‍👦</span> Parental
          </TabsTrigger>
          <TabsTrigger value="services" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">🔌</span> Services
          </TabsTrigger>
          <TabsTrigger value="indexers" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">🔍</span> Indexers
          </TabsTrigger>
          <TabsTrigger value="usenet" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📰</span> Usenet
          </TabsTrigger>
          <TabsTrigger value="telegram" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📨</span> Telegram
          </TabsTrigger>
          <TabsTrigger value="acestream" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📡</span> AceStream
          </TabsTrigger>
          <TabsTrigger value="formatter" className="flex-1 min-w-[100px] py-2.5 px-4 text-sm">
            <span className="mr-2">📝</span> Formatter
          </TabsTrigger>
        </TabsList>

        <TabsContent value="provider" className="mt-6">
          <MultiProviderConfig config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="catalogs" className="mt-6">
          <div className="space-y-6">
            <CatalogConfig config={config} onChange={setConfig} />
            <MDBListConfigComponent config={config} onChange={setConfig} />
          </div>
        </TabsContent>

        <TabsContent value="preferences" className="mt-6">
          <StreamingPreferences config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="parental" className="mt-6">
          <ParentalGuides config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="services" className="mt-6">
          <ExternalServices config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="indexers" className="mt-6">
          <IndexerSettings config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="usenet" className="mt-6">
          <UsenetSettings config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="telegram" className="mt-6">
          <TelegramSettings config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="acestream" className="mt-6">
          <AceStreamSettings config={config} onChange={setConfig} />
        </TabsContent>

        <TabsContent value="formatter" className="mt-6">
          <StreamFormatterConfig config={config} onChange={setConfig} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

// Authenticated Profiles List
function AuthenticatedConfigurePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { data: profiles, isLoading, error } = useProfiles()
  const [selectedProfile, setSelectedProfile] = useState<Profile | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  // Check for edit param
  const editProfileId = searchParams.get('edit')

  // Sync selected profile when edit param or profiles change (during render, not in effect)
  // Use a composite key instead of reference checks — avoids cached-reference bug
  const editKey = editProfileId && profiles ? `${editProfileId}:${profiles.length}` : null
  const [prevEditKey, setPrevEditKey] = useState(editKey)
  if (editProfileId && profiles && editKey !== prevEditKey) {
    setPrevEditKey(editKey)
    const profileIdNum = parseInt(editProfileId, 10)
    const profile = profiles.find((p) => p.id === profileIdNum)
    if (profile) {
      setSelectedProfile(profile)
    }
  }

  // If editing or creating, show the editor
  if (selectedProfile || isCreating) {
    return (
      <ProfileEditor
        profile={selectedProfile || undefined}
        isNew={isCreating}
        onBack={() => {
          setSelectedProfile(null)
          setIsCreating(false)
          navigate('/dashboard/configure', { replace: true })
        }}
      />
    )
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div className="space-y-1">
          <h1 className="font-display text-2xl md:text-3xl font-semibold flex items-center gap-3 tracking-tight">
            <div className="p-2 rounded-md bg-primary/10 border border-primary/20">
              <Settings className="h-5 w-5 text-primary" />
            </div>
            Configure
          </h1>
          <p className="text-muted-foreground">Manage your streaming profiles and settings</p>
        </div>
        <Button onClick={() => setIsCreating(true)} variant="default" disabled={profiles && profiles.length >= 5}>
          <Plus className="h-4 w-4 mr-2" />
          New Profile
        </Button>
      </div>

      {/* Profile Limit Warning */}
      {profiles && profiles.length >= 5 && (
        <Card className="border-primary/50 bg-primary/10">
          <CardContent className="p-4 flex items-center gap-3">
            <AlertCircle className="h-5 w-5 text-primary" />
            <p className="text-sm">
              You've reached the maximum of 5 profiles. Delete an existing profile to create a new one.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Profiles Grid */}
      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Card key={i} className="p-6">
              <Skeleton className="h-6 w-32 mb-2" />
              <Skeleton className="h-4 w-24 mb-4" />
              <Skeleton className="h-8 w-full" />
            </Card>
          ))}
        </div>
      ) : error ? (
        <Card className="p-6 text-center">
          <AlertCircle className="h-12 w-12 mx-auto text-red-500 mb-4" />
          <h3 className="text-lg font-medium mb-2">Failed to load profiles</h3>
          <p className="text-muted-foreground text-sm">
            {error instanceof Error ? error.message : 'An error occurred'}
          </p>
        </Card>
      ) : profiles && profiles.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {profiles.map((profile) => (
            <ProfileCard key={profile.id} profile={profile} onSelect={() => setSelectedProfile(profile)} />
          ))}
        </div>
      ) : (
        <Card className="p-12 text-center">
          <Server className="h-16 w-16 mx-auto text-muted-foreground/50 mb-4" />
          <h3 className="text-xl font-medium mb-2">No profiles yet</h3>
          <p className="text-muted-foreground mb-6 max-w-sm mx-auto">
            Create your first profile to configure streaming providers and start watching.
          </p>
          <Button onClick={() => setIsCreating(true)} variant="gold">
            <Plus className="h-4 w-4 mr-2" />
            Create Your First Profile
          </Button>
        </Card>
      )}

      {/* Info card */}
      <Card className="border-primary/20 hero-gradient">
        <CardContent className="p-6">
          <div className="flex items-start gap-4">
            <div className="p-2 rounded-md bg-primary/10 border border-primary/20">
              <Sparkles className="h-5 w-5 text-primary" />
            </div>
            <div className="space-y-2">
              <h3 className="font-medium">About Profiles</h3>
              <p className="text-sm text-muted-foreground">
                Each profile contains your streaming provider configuration, catalog preferences, and quality settings.
                You can have up to 5 profiles for different use cases (e.g., different debrid accounts, family
                profiles). Use the Integrations page to install your profiles in Stremio or Kodi.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

// Main Configure Page - Switches between authenticated and anonymous modes
export function ConfigurePage() {
  const { isAuthenticated, isLoading } = useAuth()

  // Show loading state while checking auth
  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-4 w-48" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Card key={i} className="p-6">
              <Skeleton className="h-6 w-32 mb-2" />
              <Skeleton className="h-4 w-24 mb-4" />
              <Skeleton className="h-8 w-full" />
            </Card>
          ))}
        </div>
      </div>
    )
  }

  // Show anonymous config for non-authenticated users
  if (!isAuthenticated) {
    return <AnonymousConfigEditor />
  }

  // Show authenticated profile management
  return <AuthenticatedConfigurePage />
}
