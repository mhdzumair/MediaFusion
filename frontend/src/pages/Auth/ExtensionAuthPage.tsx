import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, Copy, ExternalLink, Puzzle, Shield, AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo, LogoText } from '@/components/ui/logo'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { useAuth } from '@/contexts/AuthContext'
import { useInstance } from '@/contexts/InstanceContext'
import { apiClient } from '@/lib/api'

export function ExtensionAuthPage() {
  const { user, isAuthenticated, isLoading } = useAuth()
  const { instanceInfo, apiKey } = useInstance()
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)
  const [authorized, setAuthorized] = useState(false)

  const addonName = instanceInfo?.addon_name || 'MediaFusion'
  const accessToken = apiClient.getAccessToken()

  // Redirect to login if not authenticated (after loading completes)
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      // Small delay to ensure the navigation happens after render
      const timer = setTimeout(() => {
        navigate('/login', { 
          state: { from: { pathname: '/extension-auth' } },
          replace: true 
        })
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [isLoading, isAuthenticated, navigate])

  // Set up the auth data for the extension to read
  useEffect(() => {
    if (isAuthenticated && user && accessToken) {
      // Create a data element that the extension's content script can read
      const authDataElement = document.getElementById('mediafusion-extension-auth')
      if (authDataElement) {
        authDataElement.remove()
      }

      const dataElement = document.createElement('div')
      dataElement.id = 'mediafusion-extension-auth'
      dataElement.style.display = 'none'
      dataElement.setAttribute('data-ready', 'true')
      dataElement.setAttribute('data-token', accessToken)
      dataElement.setAttribute('data-user-id', String(user.id))
      dataElement.setAttribute('data-user-email', user.email)
      dataElement.setAttribute('data-user-name', user.username || user.email)
      dataElement.setAttribute('data-user-role', user.role)
      if (apiKey) {
        dataElement.setAttribute('data-api-key', apiKey)
      }
      document.body.appendChild(dataElement)

      // Dispatch custom event for extension
      window.dispatchEvent(new CustomEvent('mediafusion-extension-auth-ready', {
        detail: {
          token: accessToken,
          user: {
            id: user.id,
            email: user.email,
            username: user.username,
            role: user.role,
          },
          apiKey: apiKey || null,
        }
      }))

      return () => {
        dataElement.remove()
      }
    }
  }, [isAuthenticated, user, accessToken, apiKey])

  const handleCopyToken = async () => {
    if (accessToken) {
      await navigator.clipboard.writeText(accessToken)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const handleAuthorize = () => {
    setAuthorized(true)
    // Dispatch event for extension to capture
    window.dispatchEvent(new CustomEvent('mediafusion-extension-authorized', {
      detail: {
        token: accessToken,
        user: {
          id: user?.id,
          email: user?.email,
          username: user?.username,
          role: user?.role,
        },
        apiKey: apiKey || null,
      }
    }))
  }

  // Show loading state while checking auth or waiting for redirect
  if (isLoading || !isAuthenticated || !user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-4">
          <div className="animate-pulse text-muted-foreground">
            {isLoading ? 'Loading...' : 'Redirecting to login...'}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex flex-col bg-background">
      {/* Background effects */}
      <div className="fixed inset-0 -z-10">
        <div className="absolute inset-0 bg-background" />
        <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-primary/5 rounded-full blur-3xl" />
        <div className="absolute bottom-0 right-1/4 w-[500px] h-[500px] bg-primary/3 rounded-full blur-3xl" />
      </div>

      <div className="flex-1 flex items-center justify-center p-6">
        <Card className="w-full max-w-lg">
          <CardHeader className="text-center space-y-4">
            <div className="flex justify-center">
              <div className="p-3 rounded-full bg-primary/10">
                <Puzzle className="h-8 w-8 text-primary" />
              </div>
            </div>
            <div className="flex items-center justify-center gap-2">
              <Logo size="sm" />
              <LogoText addonName={addonName} size="lg" />
            </div>
            <CardTitle className="text-xl">Browser Extension Authorization</CardTitle>
            <CardDescription>
              Connect your browser extension to {addonName}
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-6">
            {/* User info */}
            <div className="p-4 rounded-lg bg-muted/50 border border-border/50">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-full bg-primary/20 flex items-center justify-center">
                  <span className="font-semibold text-primary">
                    {user.username?.charAt(0).toUpperCase() || user.email.charAt(0).toUpperCase()}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{user.username || 'User'}</p>
                  <p className="text-sm text-muted-foreground truncate">{user.email}</p>
                </div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground bg-muted px-2 py-1 rounded">
                  <Shield className="h-3 w-3" />
                  {user.role}
                </div>
              </div>
            </div>

            {/* Permissions info */}
            <Alert>
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>The browser extension will be able to:</AlertTitle>
              <AlertDescription>
                <ul className="list-disc list-inside mt-2 space-y-1 text-sm">
                  <li>Upload torrents and magnet links to {addonName}</li>
                  <li>Access your upload catalogs and preferences</li>
                  <li>View your contribution history</li>
                </ul>
              </AlertDescription>
            </Alert>

            {authorized ? (
              <div className="p-4 rounded-lg bg-green-500/10 border border-green-500/20 text-center">
                <Check className="h-8 w-8 text-green-500 mx-auto mb-2" />
                <p className="font-medium text-green-500">Extension Authorized!</p>
                <p className="text-sm text-muted-foreground mt-1">
                  You can now close this tab and use the extension.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                <Button 
                  onClick={handleAuthorize}
                  className="w-full"
                  size="lg"
                  variant="gold"
                >
                  <Check className="h-4 w-4 mr-2" />
                  Authorize Extension
                </Button>

                <div className="relative">
                  <div className="absolute inset-0 flex items-center">
                    <span className="w-full border-t border-border/50" />
                  </div>
                  <div className="relative flex justify-center text-xs uppercase">
                    <span className="bg-card px-2 text-muted-foreground">Or copy manually</span>
                  </div>
                </div>

                <Button 
                  onClick={handleCopyToken}
                  variant="outline"
                  className="w-full"
                >
                  {copied ? (
                    <>
                      <Check className="h-4 w-4 mr-2 text-green-500" />
                      Copied!
                    </>
                  ) : (
                    <>
                      <Copy className="h-4 w-4 mr-2" />
                      Copy Access Token
                    </>
                  )}
                </Button>
              </div>
            )}

            <p className="text-xs text-center text-muted-foreground">
              Don&apos;t have the extension?{' '}
              <a
                href="https://addons.mozilla.org/en-US/firefox/addon/mediafusion-torrent-uploader/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline inline-flex items-center gap-1"
              >
                Get it here <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Hidden auth data element for extension */}
      <div 
        id="mediafusion-extension-auth-container"
        data-extension-page="true"
        style={{ display: 'none' }}
      />
    </div>
  )
}
