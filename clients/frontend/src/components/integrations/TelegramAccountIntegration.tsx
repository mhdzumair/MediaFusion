import { useState } from 'react'
import {
  Send,
  Link,
  Unlink,
  Info,
  CheckCircle2,
  Loader2,
  Shield,
  Trash2,
  Eye,
  EyeOff,
  AlertTriangle,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Input } from '@/components/ui/input'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getAppConfig } from '@/lib/api'
import { apiClient } from '@/lib/api/client'
import { telegramApi } from '@/lib/api/telegram'
import { TelegramScrapingChannels } from '@/components/integrations/TelegramScrapingChannels'

interface TelegramConfig {
  enabled: boolean
  account_linked: boolean
  telegram_user_id?: string
  session_connected: boolean
  session_telegram_account_id?: number
}

type LoginStep = 'idle' | 'phone' | 'code' | 'password'

export function TelegramAccountIntegration() {
  const queryClient = useQueryClient()

  const { data: appConfig, isLoading: appConfigLoading } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
  })

  const telegramEnabled = appConfig?.telegram?.enabled ?? false
  const botConfigured = appConfig?.telegram?.bot_configured ?? false

  const { data: telegramConfig, isLoading: telegramLoading } = useQuery<TelegramConfig>({
    queryKey: ['telegramConfig'],
    queryFn: () => apiClient.get<TelegramConfig>('/telegram/config'),
    enabled: telegramEnabled,
  })

  const telegramLinked = telegramConfig?.account_linked ?? false
  const sessionConnected = telegramConfig?.session_connected ?? false

  const [unlinkError, setUnlinkError] = useState<string | null>(null)
  const [sessionError, setSessionError] = useState<string | null>(null)
  const [loginStep, setLoginStep] = useState<LoginStep>('idle')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [passwordHint, setPasswordHint] = useState<string | null>(null)
  const [showPassword, setShowPassword] = useState(false)

  const unlinkMutation = useMutation({
    mutationFn: () => telegramApi.unlinkAccount(),
    onSuccess: () => {
      setUnlinkError(null)
      void queryClient.invalidateQueries({ queryKey: ['telegramConfig'] })
    },
    onError: (error) => {
      setUnlinkError(error instanceof Error ? error.message : 'Failed to unlink Telegram account')
    },
  })

  const startSessionMutation = useMutation({
    mutationFn: (value: string) => telegramApi.startSessionLogin(value),
    onSuccess: () => {
      setSessionError(null)
      setLoginStep('code')
    },
    onError: (error) => {
      setSessionError(error instanceof Error ? error.message : 'Failed to start Telegram login')
    },
  })

  const verifyCodeMutation = useMutation({
    mutationFn: (value: string) => telegramApi.verifySessionCode(value),
    onSuccess: (response) => {
      setSessionError(null)
      if (response.status === 'password_required') {
        setPasswordHint(response.hint ?? null)
        setLoginStep('password')
        return
      }
      resetLoginForm()
      void queryClient.invalidateQueries({ queryKey: ['telegramConfig'] })
      void queryClient.invalidateQueries({ queryKey: ['telegramSessionStatus'] })
    },
    onError: (error) => {
      setSessionError(error instanceof Error ? error.message : 'Invalid verification code')
    },
  })

  const verifyPasswordMutation = useMutation({
    mutationFn: (value: string) => telegramApi.verifySessionPassword(value),
    onSuccess: () => {
      setSessionError(null)
      resetLoginForm()
      void queryClient.invalidateQueries({ queryKey: ['telegramConfig'] })
      void queryClient.invalidateQueries({ queryKey: ['telegramSessionStatus'] })
    },
    onError: (error) => {
      setSessionError(error instanceof Error ? error.message : 'Invalid 2FA password')
    },
  })

  const deleteSessionMutation = useMutation({
    mutationFn: () => telegramApi.deleteSession(),
    onSuccess: () => {
      setSessionError(null)
      resetLoginForm()
      void queryClient.invalidateQueries({ queryKey: ['telegramConfig'] })
      void queryClient.invalidateQueries({ queryKey: ['telegramSessionStatus'] })
    },
    onError: (error) => {
      setSessionError(error instanceof Error ? error.message : 'Failed to remove Telegram session')
    },
  })

  const resetLoginForm = () => {
    setLoginStep('idle')
    setPhone('')
    setCode('')
    setPassword('')
    setPasswordHint(null)
    setShowPassword(false)
  }

  if (appConfigLoading) {
    return (
      <Card className="border-border/50">
        <CardContent className="py-8 flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading Telegram settings...
        </CardContent>
      </Card>
    )
  }

  if (!telegramEnabled) {
    return (
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Send className="h-5 w-5" />
            Telegram
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              Telegram integration is not enabled on this instance. Contact the administrator if you'd like this
              feature.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Send className="h-5 w-5" />
          Telegram
        </CardTitle>
        <CardDescription>
          Account-level Telegram settings — scraping session and bot playback are linked to your user account, not a
          profile.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Connecting a Telegram scraping session logs MediaFusion into your Telegram account. Use a dedicated
            non-personal account when possible. Never enter login codes in the Telegram bot chat — only here on the web
            UI. The developer and instance hoster are not responsible for any account loss, bans, or data exposure.
          </AlertDescription>
        </Alert>

        <div className="space-y-4 pt-2 border-t">
          <div className="space-y-0.5">
            <Label className="text-base flex items-center gap-2">
              Scraping Session
              {telegramLoading ? (
                <Badge variant="outline" className="bg-muted text-muted-foreground">
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                  Checking...
                </Badge>
              ) : sessionConnected ? (
                <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/30">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  Connected
                </Badge>
              ) : (
                <Badge variant="outline" className="bg-yellow-500/10 text-yellow-500 border-yellow-500/30">
                  <Unlink className="h-3 w-3 mr-1" />
                  Not Connected
                </Badge>
              )}
            </Label>
            <p className="text-sm text-muted-foreground">
              {sessionConnected
                ? `Your Telegram account (ID: ${telegramConfig?.session_telegram_account_id}) is connected for channel scraping.`
                : 'Connect with your phone number to scrape channels your account can access.'}
            </p>
          </div>

          <Alert>
            <Shield className="h-4 w-4" />
            <AlertDescription>
              Your session is encrypted at rest. It is never shown again after login. Revoke it at{' '}
              <a href="https://my.telegram.org" className="underline" target="_blank" rel="noopener noreferrer">
                my.telegram.org
              </a>{' '}
              for full account safety.
            </AlertDescription>
          </Alert>

          {!sessionConnected && loginStep === 'idle' && (
            <Button variant="outline" onClick={() => setLoginStep('phone')}>
              Connect Telegram for scraping
            </Button>
          )}

          {!sessionConnected && loginStep === 'phone' && (
            <div className="space-y-3">
              <Label htmlFor="telegram-phone">Phone number</Label>
              <Input
                id="telegram-phone"
                placeholder="+15551234567"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
              <div className="flex gap-2">
                <Button
                  onClick={() => startSessionMutation.mutate(phone)}
                  disabled={startSessionMutation.isPending || phone.trim().length < 8}
                >
                  {startSessionMutation.isPending ? 'Sending code...' : 'Send verification code'}
                </Button>
                <Button variant="ghost" onClick={resetLoginForm}>
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {!sessionConnected && loginStep === 'code' && (
            <div className="space-y-3">
              <Label htmlFor="telegram-code">Verification code</Label>
              <p className="text-xs text-muted-foreground">
                Enter the code from the official Telegram app. Do not send this code to the MediaFusion bot.
              </p>
              <Input id="telegram-code" placeholder="12345" value={code} onChange={(e) => setCode(e.target.value)} />
              <div className="flex gap-2">
                <Button
                  onClick={() => verifyCodeMutation.mutate(code)}
                  disabled={verifyCodeMutation.isPending || code.trim().length === 0}
                >
                  {verifyCodeMutation.isPending ? 'Verifying...' : 'Verify code'}
                </Button>
                <Button variant="ghost" onClick={resetLoginForm}>
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {!sessionConnected && loginStep === 'password' && (
            <div className="space-y-3">
              <Label htmlFor="telegram-password">2FA password{passwordHint ? ` (hint: ${passwordHint})` : ''}</Label>
              <div className="relative">
                <Input
                  id="telegram-password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pr-10"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                  onClick={() => setShowPassword((prev) => !prev)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
              <div className="flex gap-2">
                <Button
                  onClick={() => verifyPasswordMutation.mutate(password)}
                  disabled={verifyPasswordMutation.isPending || password.trim().length === 0}
                >
                  {verifyPasswordMutation.isPending ? 'Signing in...' : 'Complete login'}
                </Button>
                <Button variant="ghost" onClick={resetLoginForm}>
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {sessionConnected && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (!window.confirm('Remove your stored Telegram scraping session from MediaFusion?')) {
                  return
                }
                deleteSessionMutation.mutate()
              }}
              disabled={deleteSessionMutation.isPending}
            >
              {deleteSessionMutation.isPending ? (
                <>
                  <Loader2 className="h-3 w-3 mr-2 animate-spin" />
                  Removing...
                </>
              ) : (
                <>
                  <Trash2 className="h-3 w-3 mr-2" />
                  Remove scraping session
                </>
              )}
            </Button>
          )}

          {sessionError && (
            <Alert variant="destructive">
              <Info className="h-4 w-4" />
              <AlertDescription>{sessionError}</AlertDescription>
            </Alert>
          )}

          <TelegramScrapingChannels />
        </div>

        <div className="space-y-4 pt-4 border-t">
          <div className="space-y-0.5">
            <Label className="text-base flex items-center gap-2">
              Bot Playback Link
              {telegramLoading ? (
                <Badge variant="outline" className="bg-muted text-muted-foreground">
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                  Checking...
                </Badge>
              ) : telegramLinked ? (
                <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/30">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  Linked
                </Badge>
              ) : (
                <Badge variant="outline" className="bg-yellow-500/10 text-yellow-500 border-yellow-500/30">
                  <Unlink className="h-3 w-3 mr-1" />
                  Not Linked
                </Badge>
              )}
            </Label>
            <p className="text-sm text-muted-foreground">
              {telegramLinked
                ? `Bot playback linked to Telegram ID ${telegramConfig?.telegram_user_id}. Streams can be sent to your DM.`
                : 'Link via the Telegram bot to receive streams in your DM for playback.'}
            </p>
          </div>

          {!telegramLinked && botConfigured && (
            <Alert>
              <Link className="h-4 w-4" />
              <AlertDescription>Send /login to the MediaFusion Telegram bot to link playback.</AlertDescription>
            </Alert>
          )}

          {telegramLinked && (
            <Alert>
              <Unlink className="h-4 w-4" />
              <AlertDescription className="flex items-center justify-between gap-4">
                <span>Unlink bot playback only. Your scraping session stays connected unless you remove it above.</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (!window.confirm('Unlink Telegram bot playback from MediaFusion?')) {
                      return
                    }
                    unlinkMutation.mutate()
                  }}
                  disabled={unlinkMutation.isPending}
                >
                  {unlinkMutation.isPending ? 'Unlinking...' : 'Unlink bot'}
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {unlinkError && (
            <Alert variant="destructive">
              <Info className="h-4 w-4" />
              <AlertDescription>{unlinkError}</AlertDescription>
            </Alert>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
