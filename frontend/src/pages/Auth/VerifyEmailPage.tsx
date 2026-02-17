import { useState, useEffect, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Loader2, MailCheck, MailX, CheckCircle2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/ui/logo'
import { ThemeSelector } from '@/components/ui/theme-selector'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { authApi } from '@/lib/api/auth'
import { ApiRequestError } from '@/lib/api/client'

type VerifyState = 'verifying' | 'success' | 'error' | 'waiting'

export function VerifyEmailPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const email = searchParams.get('email')

  const [state, setState] = useState<VerifyState>(token ? 'verifying' : 'waiting')
  const [message, setMessage] = useState('')
  const [resendLoading, setResendLoading] = useState(false)
  const [resendMessage, setResendMessage] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(0)

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

  return (
    <div className="relative min-h-screen flex items-center justify-center overflow-hidden">
      <div className="fixed top-4 right-4 z-50">
        <ThemeSelector />
      </div>

      {/* Background */}
      <div className="fixed inset-0 -z-10">
        <div className="absolute inset-0 bg-background" />
        <div className="absolute top-0 left-1/3 w-[600px] h-[600px] bg-primary/5 rounded-full blur-3xl" />
        <div className="absolute bottom-0 right-1/3 w-[500px] h-[500px] bg-primary/3 rounded-full blur-3xl" />
      </div>

      <Card className="w-full max-w-md mx-4 animate-fade-in">
        {/* Verifying state */}
        {state === 'verifying' && (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <Loader2 className="h-12 w-12 text-primary animate-spin" />
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Verifying your email</CardTitle>
              <CardDescription>Please wait while we verify your email address...</CardDescription>
            </CardHeader>
          </>
        )}

        {/* Success state */}
        {state === 'success' && (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <CheckCircle2 className="h-12 w-12 text-emerald-500" />
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Email verified</CardTitle>
              <CardDescription>{message}</CardDescription>
            </CardHeader>
            <CardFooter className="flex flex-col space-y-4">
              <Link to="/login" className="w-full">
                <Button variant="gold" className="w-full">
                  Sign in to your account
                </Button>
              </Link>
            </CardFooter>
          </>
        )}

        {/* Error state */}
        {state === 'error' && (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <MailX className="h-12 w-12 text-destructive" />
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Verification failed</CardTitle>
              <CardDescription>{message}</CardDescription>
            </CardHeader>
            <CardFooter className="flex flex-col space-y-4">
              {email && (
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={handleResend}
                  disabled={resendLoading || cooldown > 0}
                >
                  {resendLoading ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-2 h-4 w-4" />
                  )}
                  {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend verification email'}
                </Button>
              )}
              <Link to="/login" className="w-full">
                <Button variant="ghost" className="w-full">
                  Back to login
                </Button>
              </Link>
            </CardFooter>
          </>
        )}

        {/* Waiting state (no token, just registered) */}
        {state === 'waiting' && (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <Logo size="lg" />
              </div>
              <div className="flex justify-center mb-2">
                <MailCheck className="h-12 w-12 text-primary" />
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Check your email</CardTitle>
              <CardDescription>
                {email ? (
                  <>
                    We&apos;ve sent a verification link to <strong className="text-foreground">{email}</strong>. Click
                    the link in the email to verify your account.
                  </>
                ) : (
                  "We've sent a verification link to your email address. Click the link to verify your account."
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-md bg-muted/50 p-4 text-sm text-muted-foreground space-y-2">
                <p>Didn&apos;t receive the email? Check your spam folder, or click below to resend.</p>
              </div>
              {resendMessage && (
                <div className="rounded-md bg-primary/10 p-3 text-sm text-primary border border-primary/20">
                  {resendMessage}
                </div>
              )}
            </CardContent>
            <CardFooter className="flex flex-col space-y-3">
              {email && (
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={handleResend}
                  disabled={resendLoading || cooldown > 0}
                >
                  {resendLoading ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-2 h-4 w-4" />
                  )}
                  {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend verification email'}
                </Button>
              )}
              <Link to="/login" className="w-full">
                <Button variant="ghost" className="w-full">
                  Back to login
                </Button>
              </Link>
            </CardFooter>
          </>
        )}
      </Card>
    </div>
  )
}
