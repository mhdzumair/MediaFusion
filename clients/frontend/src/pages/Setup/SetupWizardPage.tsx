import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Loader2, Eye, EyeOff, Check, Shield, ArrowRight, CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo, LogoText } from '@/components/ui/logo'
import { ThemeSelector } from '@/components/ui/theme-selector'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useInstance } from '@/contexts/InstanceContext'
import { apiClient, completeSetup } from '@/lib/api'

const setupSchema = z
  .object({
    apiPassword: z.string().min(1, 'API password is required'),
    email: z.string().email('Please enter a valid email'),
    username: z.string().trim().min(3, 'Username must be at least 3 characters'),
    password: z
      .string()
      .min(8, 'Password must be at least 8 characters')
      .regex(/[A-Z]/, 'Password must contain at least one uppercase letter')
      .regex(/[a-z]/, 'Password must contain at least one lowercase letter')
      .regex(/[0-9]/, 'Password must contain at least one number'),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ['confirmPassword'],
  })

type SetupForm = z.infer<typeof setupSchema>

const passwordRequirements = [
  { label: 'At least 8 characters', test: (p: string) => p.length >= 8 },
  { label: 'One uppercase letter', test: (p: string) => /[A-Z]/.test(p) },
  { label: 'One lowercase letter', test: (p: string) => /[a-z]/.test(p) },
  { label: 'One number', test: (p: string) => /[0-9]/.test(p) },
]

type SetupStep = 'create-admin' | 'complete'

export function SetupWizardPage() {
  const [step, setStep] = useState<SetupStep>('create-admin')
  const [error, setError] = useState<string | null>(null)
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [showApiPassword, setShowApiPassword] = useState(false)
  const { instanceInfo, isLoading, refetchInstanceInfo } = useInstance()
  const navigate = useNavigate()

  const addonName = instanceInfo?.addon_name || 'MediaFusion'

  // Redirect away if setup is already complete
  useEffect(() => {
    if (!isLoading && instanceInfo && !instanceInfo.setup_required) {
      navigate('/', { replace: true })
    }
  }, [isLoading, instanceInfo, navigate])

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<SetupForm>({
    resolver: zodResolver(setupSchema),
  })

  // eslint-disable-next-line react-hooks/incompatible-library -- react-hook-form's watch() is inherently incompatible with React Compiler
  const password = watch('password', '')

  const onSubmit = async (data: SetupForm) => {
    try {
      setError(null)
      const response = await completeSetup({
        api_password: data.apiPassword,
        email: data.email,
        username: data.username.trim(),
        password: data.password,
      })

      // Set the new admin's tokens
      apiClient.setTokens(response.access_token, response.refresh_token)

      // Refresh instance info to clear setup_required
      await refetchInstanceInfo()

      setStep('complete')
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Setup failed'
      setError(errorMessage)
    }
  }

  const handleGoToDashboard = () => {
    navigate('/dashboard', { replace: true })
  }

  return (
    <div className="relative min-h-screen flex overflow-hidden">
      {/* Theme selector - fixed position */}
      <div className="fixed top-4 right-4 z-50">
        <ThemeSelector />
      </div>

      {/* Cinematic background */}
      <div className="fixed inset-0 -z-10">
        <div className="absolute inset-0 bg-background" />
        <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-primary/5 rounded-full blur-3xl" />
        <div className="absolute bottom-0 right-1/4 w-[500px] h-[500px] bg-primary/3 rounded-full blur-3xl" />
        <div
          className="absolute inset-0 opacity-[0.015]"
          style={{
            backgroundImage: `linear-gradient(hsl(var(--foreground) / 0.15) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--foreground) / 0.15) 1px, transparent 1px)`,
            backgroundSize: '80px 80px',
          }}
        />
      </div>

      {/* Left side - Branding (hidden on mobile) */}
      <div className="hidden lg:flex lg:w-1/2 items-center justify-center p-12">
        <div className="max-w-lg space-y-8 animate-fade-in">
          <div className="flex items-center gap-3">
            <Logo size="xl" />
            <LogoText addonName={addonName} size="3xl" />
          </div>
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight">
            Welcome to <span className="gradient-text">{addonName}</span>
          </h1>
          <p className="text-lg text-muted-foreground">
            Let's get your instance set up. Verify your API password and create your admin account.
          </p>

          {/* Step indicators */}
          <div className="space-y-4">
            {[
              { id: 'create-admin', label: 'Verify API password & create admin account' },
              { id: 'complete', label: 'Start using your instance' },
            ].map((s, idx) => {
              const isActive = s.id === step
              const isCompleted = s.id === 'create-admin' && step === 'complete'

              return (
                <div key={s.id} className="flex items-center gap-3">
                  <div
                    className={`h-8 w-8 rounded-full flex items-center justify-center text-sm font-medium border-2 transition-colors ${
                      isCompleted
                        ? 'bg-emerald-500/20 border-emerald-500 text-emerald-500'
                        : isActive
                          ? 'bg-primary/10 border-primary text-primary'
                          : 'bg-muted/50 border-muted-foreground/20 text-muted-foreground'
                    }`}
                  >
                    {isCompleted ? <Check className="h-4 w-4" /> : idx + 1}
                  </div>
                  <span
                    className={`text-sm ${
                      isActive
                        ? 'text-foreground font-medium'
                        : isCompleted
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : 'text-muted-foreground'
                    }`}
                  >
                    {s.label}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Right side - Setup content */}
      <div className="flex-1 flex items-center justify-center p-6">
        {/* Step 1: Create Admin Account */}
        {step === 'create-admin' && (
          <Card className="w-full max-w-md animate-fade-in">
            <CardHeader className="space-y-1 text-center">
              <div className="flex justify-center mb-4 lg:hidden">
                <Logo size="lg" />
              </div>
              <div className="flex items-center justify-center gap-2 mb-2">
                <Shield className="h-5 w-5 text-primary" />
                <span className="text-xs font-medium text-primary uppercase tracking-wider">Initial Setup</span>
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Create Admin Account</CardTitle>
              <CardDescription>Enter your API password and set up your admin account to get started.</CardDescription>
            </CardHeader>
            <form onSubmit={handleSubmit(onSubmit)}>
              <CardContent className="space-y-4">
                {error && (
                  <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive border border-destructive/20">
                    {error}
                  </div>
                )}

                {/* API Password */}
                <div className="space-y-2">
                  <Label htmlFor="apiPassword">API Password</Label>
                  <div className="relative">
                    <Input
                      id="apiPassword"
                      type={showApiPassword ? 'text' : 'password'}
                      placeholder="API_PASSWORD from your .env"
                      autoComplete="off"
                      {...register('apiPassword')}
                      className="pr-10"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                      onClick={() => setShowApiPassword(!showApiPassword)}
                    >
                      {showApiPassword ? (
                        <EyeOff className="h-4 w-4 text-muted-foreground" />
                      ) : (
                        <Eye className="h-4 w-4 text-muted-foreground" />
                      )}
                    </Button>
                  </div>
                  {errors.apiPassword && <p className="text-sm text-destructive">{errors.apiPassword.message}</p>}
                  <p className="text-xs text-muted-foreground">
                    The <code className="px-1 py-0.5 rounded bg-muted text-foreground text-[11px]">API_PASSWORD</code>{' '}
                    from your environment configuration.
                  </p>
                </div>

                <div className="border-t pt-4">
                  <p className="text-sm font-medium text-muted-foreground mb-3">Admin Account Details</p>
                </div>

                {/* Email */}
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input
                    id="email"
                    type="email"
                    placeholder="admin@yourdomain.com"
                    autoComplete="email"
                    {...register('email')}
                  />
                  {errors.email && <p className="text-sm text-destructive">{errors.email.message}</p>}
                </div>

                {/* Username */}
                <div className="space-y-2">
                  <Label htmlFor="username">Username</Label>
                  <Input
                    id="username"
                    type="text"
                    placeholder="admin"
                    autoComplete="username"
                    {...register('username')}
                  />
                  {errors.username && <p className="text-sm text-destructive">{errors.username.message}</p>}
                </div>

                {/* Password */}
                <div className="space-y-2">
                  <Label htmlFor="password">Password</Label>
                  <div className="relative">
                    <Input
                      id="password"
                      type={showPassword ? 'text' : 'password'}
                      placeholder="Choose a strong password"
                      autoComplete="new-password"
                      {...register('password')}
                      className="pr-10"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                      onClick={() => setShowPassword(!showPassword)}
                    >
                      {showPassword ? (
                        <EyeOff className="h-4 w-4 text-muted-foreground" />
                      ) : (
                        <Eye className="h-4 w-4 text-muted-foreground" />
                      )}
                    </Button>
                  </div>
                  {errors.password && <p className="text-sm text-destructive">{errors.password.message}</p>}

                  <div className="mt-2 grid grid-cols-2 gap-1">
                    {passwordRequirements.map((req, index) => (
                      <div key={index} className="flex items-center gap-1.5 text-xs">
                        <div
                          className={`h-3.5 w-3.5 rounded-full flex items-center justify-center ${
                            req.test(password) ? 'bg-emerald-500/20 text-emerald-500' : 'bg-muted text-muted-foreground'
                          }`}
                        >
                          <Check className="h-2.5 w-2.5" />
                        </div>
                        <span
                          className={
                            req.test(password) ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'
                          }
                        >
                          {req.label}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Confirm Password */}
                <div className="space-y-2">
                  <Label htmlFor="confirmPassword">Confirm Password</Label>
                  <div className="relative">
                    <Input
                      id="confirmPassword"
                      type={showConfirmPassword ? 'text' : 'password'}
                      placeholder="Re-enter your password"
                      autoComplete="new-password"
                      {...register('confirmPassword')}
                      className="pr-10"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                      onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                    >
                      {showConfirmPassword ? (
                        <EyeOff className="h-4 w-4 text-muted-foreground" />
                      ) : (
                        <Eye className="h-4 w-4 text-muted-foreground" />
                      )}
                    </Button>
                  </div>
                  {errors.confirmPassword && (
                    <p className="text-sm text-destructive">{errors.confirmPassword.message}</p>
                  )}
                </div>
              </CardContent>
              <CardFooter>
                <Button type="submit" variant="gold" className="w-full" disabled={isSubmitting}>
                  {isSubmitting ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Shield className="mr-2 h-4 w-4" />
                  )}
                  Create Admin Account
                </Button>
              </CardFooter>
            </form>
          </Card>
        )}

        {/* Step 2: Complete */}
        {step === 'complete' && (
          <Card className="w-full max-w-md animate-fade-in">
            <CardHeader className="space-y-1 text-center">
              <div className="flex justify-center mb-4">
                <div className="h-16 w-16 rounded-full bg-emerald-500/10 border-2 border-emerald-500 flex items-center justify-center">
                  <CheckCircle2 className="h-8 w-8 text-emerald-500" />
                </div>
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Setup Complete!</CardTitle>
              <CardDescription>
                Your admin account has been created. You're all set to start using {addonName}.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="rounded-lg bg-emerald-500/5 border border-emerald-500/20 p-4 space-y-2">
                <p className="text-sm font-medium text-emerald-700 dark:text-emerald-300">What's next?</p>
                <ul className="text-sm text-muted-foreground space-y-1.5">
                  <li className="flex items-start gap-2">
                    <Check className="h-4 w-4 text-emerald-500 mt-0.5 shrink-0" />
                    Configure your streaming providers and catalogs
                  </li>
                  <li className="flex items-start gap-2">
                    <Check className="h-4 w-4 text-emerald-500 mt-0.5 shrink-0" />
                    Set up scrapers and RSS feeds for content
                  </li>
                  <li className="flex items-start gap-2">
                    <Check className="h-4 w-4 text-emerald-500 mt-0.5 shrink-0" />
                    Invite users or keep it as a private instance
                  </li>
                </ul>
              </div>
            </CardContent>
            <CardFooter>
              <Button variant="gold" className="w-full" onClick={handleGoToDashboard}>
                <ArrowRight className="mr-2 h-4 w-4" />
                Go to Dashboard
              </Button>
            </CardFooter>
          </Card>
        )}
      </div>
    </div>
  )
}
