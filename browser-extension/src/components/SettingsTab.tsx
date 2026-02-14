import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { api } from '@/lib/api'
import type { ExtensionSettings, ContentType } from '@/lib/types'
import { Loader2, Check, ExternalLink, Server, LogIn, User, LogOut, Shield } from 'lucide-react'

interface SettingsTabProps {
  settings: ExtensionSettings
  onUpdate: (settings: Partial<ExtensionSettings>) => void
  onConfigured?: () => void
  onLogout?: () => void
}

export function SettingsTab({ settings, onUpdate, onConfigured, onLogout }: SettingsTabProps) {
  const [instanceUrl, setInstanceUrl] = useState(settings.instanceUrl || '')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)

  const isAuthenticated = !!settings.authToken && !!settings.user

  async function handleTestConnection() {
    if (!instanceUrl) return
    
    setTesting(true)
    setTestResult(null)

    try {
      // Save the URL first
      await onUpdate({ instanceUrl: instanceUrl.replace(/\/+$/, '') })
      
      // Test the connection
      const success = await api.testConnection()
      
      if (success) {
        setTestResult({ success: true, message: 'Connection successful!' })
        onConfigured?.()
      } else {
        setTestResult({ success: false, message: 'Could not connect to the server' })
      }
    } catch (err) {
      setTestResult({ 
        success: false, 
        message: err instanceof Error ? err.message : 'Connection failed' 
      })
    } finally {
      setTesting(false)
    }
  }

  function handleLoginViaWebsite() {
    if (!settings.instanceUrl) return
    
    // Open the extension auth page in a new tab
    const authUrl = `${settings.instanceUrl}/app/extension-auth`
    
    // Use browser API to open tab
    if (typeof browser !== 'undefined' && browser.tabs) {
      browser.tabs.create({ url: authUrl })
    } else if (typeof chrome !== 'undefined' && chrome.tabs) {
      chrome.tabs.create({ url: authUrl })
    } else {
      window.open(authUrl, '_blank')
    }
  }

  function handleContentTypeChange(value: string) {
    onUpdate({ defaultContentType: value as ContentType })
  }

  return (
    <div className="space-y-4">
      {/* Instance URL */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Server className="h-4 w-4" />
            MediaFusion Instance
          </CardTitle>
          <CardDescription className="text-xs">
            Enter your MediaFusion server URL
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="instanceUrl" className="text-xs">Server URL</Label>
            <Input
              id="instanceUrl"
              type="url"
              value={instanceUrl}
              onChange={(e) => setInstanceUrl(e.target.value)}
              placeholder="https://mediafusion.example.com"
              disabled={testing}
            />
          </div>

          {testResult && (
            <Alert variant={testResult.success ? 'success' : 'destructive'}>
              <AlertDescription className="text-xs">
                {testResult.message}
              </AlertDescription>
            </Alert>
          )}

          <Button 
            onClick={handleTestConnection} 
            disabled={!instanceUrl || testing}
            className="w-full"
            size="sm"
          >
            {testing ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Testing...
              </>
            ) : testResult?.success ? (
              <>
                <Check className="h-4 w-4" />
                Connected
              </>
            ) : (
              'Test Connection'
            )}
          </Button>
        </CardContent>
      </Card>

      {/* Authentication Status */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <User className="h-4 w-4" />
            Account
          </CardTitle>
          <CardDescription className="text-xs">
            {isAuthenticated ? 'Connected to your account' : 'Login to upload torrents'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {isAuthenticated && settings.user ? (
            <>
              {/* User info display */}
              <div className="p-3 rounded-lg bg-muted/50 border border-border/50">
                <div className="flex items-center gap-3">
                  <div className="h-8 w-8 rounded-full bg-primary/20 flex items-center justify-center">
                    <span className="font-semibold text-primary text-sm">
                      {settings.user.display_name?.charAt(0).toUpperCase() || settings.user.email.charAt(0).toUpperCase()}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-sm truncate">{settings.user.display_name || 'User'}</p>
                    <p className="text-xs text-muted-foreground truncate">{settings.user.email}</p>
                  </div>
                  <div className="flex items-center gap-1 text-xs text-muted-foreground bg-muted px-2 py-1 rounded">
                    <Shield className="h-3 w-3" />
                    {settings.user.role}
                  </div>
                </div>
              </div>
              
              <Button 
                onClick={onLogout}
                variant="outline"
                className="w-full"
                size="sm"
              >
                <LogOut className="h-4 w-4" />
                Sign Out
              </Button>
            </>
          ) : (
            <>
              {!settings.instanceUrl ? (
                <Alert>
                  <AlertDescription className="text-xs">
                    Enter and test your server URL first, then login via website.
                  </AlertDescription>
                </Alert>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">
                    Click the button below to login through the MediaFusion website. 
                    This ensures all security settings (like API keys) are properly configured.
                  </p>
                  <Button 
                    onClick={handleLoginViaWebsite}
                    className="w-full"
                    size="sm"
                  >
                    <LogIn className="h-4 w-4" />
                    Login via Website
                  </Button>
                </>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Default Content Type */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Default Content Type</CardTitle>
          <CardDescription className="text-xs">
            Pre-selected type when analyzing torrents
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Select 
            value={settings.defaultContentType} 
            onValueChange={handleContentTypeChange}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select content type" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="movie">Movie</SelectItem>
              <SelectItem value="series">Series</SelectItem>
              <SelectItem value="sports">Sports</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {/* Links */}
      <div className="flex justify-center gap-4 pt-2">
        <a
          href={settings.instanceUrl ? `${settings.instanceUrl}/app` : 'https://mediafusion.dev'}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-muted-foreground hover:text-primary flex items-center gap-1"
        >
          Open MediaFusion
          <ExternalLink className="h-3 w-3" />
        </a>
        <a
          href="https://github.com/mhdzumair/MediaFusion"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-muted-foreground hover:text-primary flex items-center gap-1"
        >
          GitHub
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>

      <div className="text-center text-xs text-muted-foreground pt-2">
        MediaFusion Browser Extension v2.0.0
      </div>
    </div>
  )
}

// Type declarations for browser APIs
declare const browser: {
  tabs: {
    create(options: { url: string }): void
  }
}
