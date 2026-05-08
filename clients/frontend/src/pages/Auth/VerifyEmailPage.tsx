import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertCircle, ArrowUpRight, CheckCircle2, Loader2, MailCheck, MailX, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Logo, LogoText } from '@/components/ui/logo'
import { ThemeSelector } from '@/components/ui/theme-selector'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useInstance } from '@/contexts/InstanceContext'
import { authApi } from '@/lib/api/auth'
import { ApiRequestError } from '@/lib/api/client'

type VerifyState = 'verifying' | 'success' | 'error' | 'waiting'

const MAILBOX_URL_BY_DOMAIN: Array<[string, string]> = [
  ['gmail.com', 'https://mail.google.com'],
  ['googlemail.com', 'https://mail.google.com'],
  ['outlook.com', 'https://outlook.live.com/mail/0/'],
  ['hotmail.com', 'https://outlook.live.com/mail/0/'],
  ['live.com', 'https://outlook.live.com/mail/0/'],
  ['yahoo.com', 'https://mail.yahoo.com'],
  ['icloud.com', 'https://www.icloud.com/mail'],
  ['me.com', 'https://www.icloud.com/mail'],
  ['proton.me', 'https://mail.proton.me'],
  ['protonmail.com', 'https://mail.proton.me'],
]

function getInboxUrl(email: string | null): string | null {
  if (!email || !email.includes('@')) return null
  const domain = email.split('@')[1]?.toLowerCase().trim()
  if (!domain) return null

  const matched = MAILBOX_URL_BY_DOMAIN.find(
    ([providerDomain]) => domain === providerDomain || domain.endsWith(`.${providerDomain}`),
  )
  return matched?.[1] ?? null
}

export function VerifyEmailPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const email = searchParams.get('email')
  const inboxUrl = getInboxUrl(email)
  const { instanceInfo } = useInstance()
  const addonName = instanceInfo?.addon_name || 'MediaFusion'

  const [state, setState] = useState<VerifyState>(token ? 'verifying' : 'waiting')
  const [message, setMessage] = useState('')
  const [resendLoading, setResendLoading] = useState(false)
  const [resendMessage, setResendMessage] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(0)
  const resendLabel = cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend verification email'
  const resendIsError = resendMessage ? /failed|wait|error|invalid/i.test(resendMessage) : false

  const verifyToken = useCallback(async () => {
    if (!token) return
    setState('verifying')
    try {
      const response = await authApi.verifyEmail(token)
      setState('success')
      setMessage(response.message)
    } catch (err) {
      setState('error')
      if (err instanceof ApiRequestError) {
        setMessage(err.data?.detail || 'Verification failed. The link may be expired or invalid.')
      } else {
        setMessage('Verification failed. Please try again.')
      }
    }
  }, [token])

  useEffect(() => {
    if (token) {
      verifyToken()
    }
  }, [token, verifyToken])

  useEffect(() => {
    if (cooldown <= 0) return
    const timer = setTimeout(() => setCooldown((c) => c - 1), 1000)
    return () => clearTimeout(timer)
  }, [cooldown])

  const handleResend = async () => {
    if (!email || cooldown > 0) return
    setResendLoading(true)
    setResendMessage(null)
    try {
      const response = await authApi.resendVerification(email)
      setResendMessage(response.message)
      setCooldown(60)
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 429) {
        setResendMessage(err.data?.detail || 'Please wait before requesting another email.')
        setCooldown(60)
      } else {
        setResendMessage('Failed to resend email. Please try again.')
      }
    } finally {
      setResendLoading(false)
    }
  }

  let stateIcon: ReactNode = <MailCheck className="h-8 w-8 text-primary" />
  let title = 'Check your email'
  let description: ReactNode = email ? (
    <>
      We sent a verification link to <strong className="text-foreground">{email}</strong>. Open the latest email and
      click the link to activate your account.
    </>
  ) : (
    'We sent a verification link to your email address. Open the email and confirm your account.'
  )

  if (state === 'verifying') {
    stateIcon = <Loader2 className="h-8 w-8 text-primary animate-spin" />
    title = 'Verifying your email'
    description = 'Please wait while we confirm your verification link.'
  } else if (state === 'success') {
    stateIcon = <CheckCircle2 className="h-8 w-8 text-emerald-500" />
    title = 'Email verified'
    description = message || 'Your account is ready. You can now sign in.'
  } else if (state === 'error') {
    stateIcon = <MailX className="h-8 w-8 text-destructive" />
    title = 'Verification failed'
    description = message || 'The verification link may be expired or invalid.'
  }

  return (
    <div className="min-h-screen flex flex-col bg-background">
      <div className="fixed top-4 right-4 z-50">
        <ThemeSelector />
      </div>

      {/* Background */}
      <div className="fixed inset-0 -z-10">
        <div className="absolute inset-0 bg-background" />
        <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-primary/5 rounded-full blur-3xl" />
        <div className="absolute bottom-0 right-1/4 w-[500px] h-[500px] bg-primary/3 rounded-full blur-3xl" />
      </div>

      <div className="flex-1 flex items-center justify-center p-6">
        <Card className="w-full max-w-lg animate-fade-in">
          <CardHeader className="text-center space-y-4">
            <div className="flex justify-center">
              <div className="p-3 rounded-full bg-primary/10 border border-primary/20">{stateIcon}</div>
            </div>
            <div className="flex items-center justify-center gap-2">
              <Logo size="sm" />
              <LogoText addonName={addonName} size="lg" />
            </div>
            <CardTitle className="text-xl">{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </CardHeader>

          {state === 'verifying' ? (
            <CardContent className="pb-6">
              <div className="p-4 rounded-lg bg-muted/50 border border-border/50 text-center text-sm text-muted-foreground">
                This usually takes only a few seconds.
              </div>
            </CardContent>
          ) : (
            <>
              <CardContent className="space-y-4">
                {state === 'waiting' && (
                  <Alert className="border-border/50 bg-muted/40">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>Didn&apos;t receive the email yet?</AlertTitle>
                    <AlertDescription>
                      <ul className="list-disc list-inside mt-2 space-y-1 text-sm">
                        <li>Check your spam or promotions folder</li>
                        <li>Use the newest email if multiple messages were sent</li>
                        <li>Verification links expire after 24 hours</li>
                      </ul>
                    </AlertDescription>
                  </Alert>
                )}

                {state === 'error' && (
                  <Alert variant="destructive" className="bg-destructive/5 border-destructive/30">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>Need a fresh verification link?</AlertTitle>
                    <AlertDescription>
                      Request another email below and open the latest verification message.
                    </AlertDescription>
                  </Alert>
                )}

                {resendMessage && (
                  <div
                    className={
                      resendIsError
                        ? 'rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive'
                        : 'rounded-md border border-primary/20 bg-primary/10 p-3 text-sm text-primary'
                    }
                  >
                    {resendMessage}
                  </div>
                )}
              </CardContent>

              <CardFooter className="flex flex-col space-y-3">
                {state === 'success' ? (
                  <Link to="/login" className="w-full">
                    <Button variant="gold" className="w-full" size="lg">
                      Sign in to your account
                    </Button>
                  </Link>
                ) : (
                  <>
                    {state === 'waiting' && inboxUrl && (
                      <Button asChild variant="gold" className="w-full" size="lg">
                        <a href={inboxUrl} target="_blank" rel="noopener noreferrer">
                          Open inbox
                          <ArrowUpRight />
                        </a>
                      </Button>
                    )}
                    {email && (
                      <Button
                        variant={state === 'error' ? 'gold-outline' : 'outline'}
                        className="w-full"
                        onClick={handleResend}
                        disabled={resendLoading || cooldown > 0}
                      >
                        {resendLoading ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <RefreshCw className="mr-2 h-4 w-4" />
                        )}
                        {resendLabel}
                      </Button>
                    )}

                    <div className="relative w-full">
                      <div className="absolute inset-0 flex items-center">
                        <span className="w-full border-t border-border/50" />
                      </div>
                      <div className="relative flex justify-center text-xs uppercase">
                        <span className="bg-card px-2 text-muted-foreground">Navigation</span>
                      </div>
                    </div>

                    <Link to="/login" className="w-full">
                      <Button variant="ghost" className="w-full">
                        Back to login
                      </Button>
                    </Link>
                  </>
                )}
              </CardFooter>
            </>
          )}
        </Card>
      </div>
    </div>
  )
}
