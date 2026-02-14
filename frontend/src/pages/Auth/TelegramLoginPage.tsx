import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Check, AlertCircle, Loader2, MessageCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo, LogoText } from '@/components/ui/logo'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { useAuth } from '@/contexts/AuthContext'
import { useInstance } from '@/contexts/InstanceContext'
import { telegramApi } from '@/lib/api/telegram'

export function TelegramLoginPage() {
  const { isAuthenticated, isLoading: authLoading } = useAuth()
  const { instanceInfo } = useInstance()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [linking, setLinking] = useState(false)
  const [success, setSuccess] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const token = searchParams.get('token')
  const addonName = instanceInfo?.addon_name || 'MediaFusion'

  // Redirect to login if not authenticated (after loading completes)
  useEffect(() => {
    if (!authLoading && !isAuthenticated) {
      // Save the current URL with token to return after login
      const returnPath = token ? `/telegram/login?token=${encodeURIComponent(token)}` : '/telegram/login'
      const timer = setTimeout(() => {
        navigate('/login', {
          state: { from: { pathname: returnPath } },
          replace: true,
        })
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [authLoading, isAuthenticated, navigate, token])

  // Link Telegram account when authenticated and token is present
  useEffect(() => {
    if (!authLoading && isAuthenticated && token && !linking && !success && !error) {
      setLinking(true)
      telegramApi
        .linkAccount(token)
        .then((response) => {
          if (response.success) {
            setSuccess(true)
            // Auto-redirect after 3 seconds
            setTimeout(() => {
              navigate('/dashboard', { replace: true })
            }, 3000)
          } else {
            setError(response.message || 'Failed to link Telegram account')
          }
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : 'Failed to link Telegram account')
        })
        .finally(() => {
          setLinking(false)
        })
    }
  }, [authLoading, isAuthenticated, token, linking, success, error, navigate])

  // Show loading state while checking auth or waiting for redirect
  if (authLoading || !isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-4">
          <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto" />
          <div className="text-muted-foreground">
            {authLoading ? 'Loading...' : 'Redirecting to login...'}
          </div>
        </div>
      </div>
    )
  }

  // Show error if no token provided
  if (!token) {
    return (
      <div className="min-h-screen flex flex-col bg-background">
        <div className="flex-1 flex items-center justify-center p-6">
          <Card className="w-full max-w-lg">
            <CardHeader className="text-center space-y-4">
              <div className="flex justify-center">
                <div className="p-3 rounded-full bg-destructive/10">
                  <AlertCircle className="h-8 w-8 text-destructive" />
                </div>
              </div>
              <div className="flex items-center justify-center gap-2">
                <Logo size="sm" />
                <LogoText addonName={addonName} size="lg" />
              </div>
              <CardTitle className="text-xl">Invalid Login Link</CardTitle>
              <CardDescription>No login token provided</CardDescription>
            </CardHeader>
            <CardContent>
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Missing Token</AlertTitle>
                <AlertDescription>
                  This login link is invalid. Please use the link provided by the Telegram bot.
                </AlertDescription>
              </Alert>
              <Button
                onClick={() => navigate('/dashboard')}
                className="w-full mt-4"
                variant="outline"
              >
                Go to Dashboard
              </Button>
            </CardContent>
          </Card>
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
                <MessageCircle className="h-8 w-8 text-primary" />
              </div>
            </div>
            <div className="flex items-center justify-center gap-2">
              <Logo size="sm" />
              <LogoText addonName={addonName} size="lg" />
            </div>
            <CardTitle className="text-xl">Link Telegram Account</CardTitle>
            <CardDescription>
              Connect your Telegram account to {addonName}
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-6">
            {success ? (
              <div className="p-4 rounded-lg bg-green-500/10 border border-green-500/20 text-center">
                <Check className="h-8 w-8 text-green-500 mx-auto mb-2" />
                <p className="font-medium text-green-500">Telegram Account Linked!</p>
                <p className="text-sm text-muted-foreground mt-1">
                  Your Telegram account has been successfully linked to your MediaFusion account.
                </p>
                <p className="text-xs text-muted-foreground mt-2">
                  Redirecting to dashboard...
                </p>
              </div>
            ) : error ? (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Linking Failed</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : linking ? (
              <div className="p-4 rounded-lg bg-muted/50 border border-border/50 text-center">
                <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto mb-2" />
                <p className="font-medium">Linking your Telegram account...</p>
                <p className="text-sm text-muted-foreground mt-1">
                  Please wait while we connect your accounts.
                </p>
              </div>
            ) : (
              <div className="p-4 rounded-lg bg-muted/50 border border-border/50">
                <p className="text-sm text-muted-foreground text-center">
                  Ready to link your Telegram account. Click the button below to proceed.
                </p>
                <Button
                  onClick={() => {
                    setLinking(true)
                    telegramApi
                      .linkAccount(token)
                      .then((response) => {
                        if (response.success) {
                          setSuccess(true)
                          setTimeout(() => {
                            navigate('/dashboard', { replace: true })
                          }, 3000)
                        } else {
                          setError(response.message || 'Failed to link Telegram account')
                        }
                      })
                      .catch((err) => {
                        setError(err instanceof Error ? err.message : 'Failed to link Telegram account')
                      })
                      .finally(() => {
                        setLinking(false)
                      })
                  }}
                  className="w-full mt-4"
                  size="lg"
                  variant="gold"
                  disabled={linking}
                >
                  {linking ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Linking...
                    </>
                  ) : (
                    <>
                      <MessageCircle className="h-4 w-4 mr-2" />
                      Link Telegram Account
                    </>
                  )}
                </Button>
              </div>
            )}

            {!success && !error && (
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>What happens next?</AlertTitle>
                <AlertDescription>
                  <ul className="list-disc list-inside mt-2 space-y-1 text-sm">
                    <li>Your Telegram account will be linked to your MediaFusion account</li>
                    <li>Content you forward to the bot will be stored with your account</li>
                    <li>You can manage your Telegram channels in your profile settings</li>
                  </ul>
                </AlertDescription>
              </Alert>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
