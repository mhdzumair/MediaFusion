import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Loader2, Eye, EyeOff, Check, Key, Shield, Clapperboard } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo, LogoText } from '@/components/ui/logo'
import { ThemeSelector } from '@/components/ui/theme-selector'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuth } from '@/contexts/AuthContext'
import { useInstance } from '@/contexts/InstanceContext'
import { ApiRequestError } from '@/lib/api/client'

const registerSchema = z
  .object({
    email: z.string().email('Please enter a valid email'),
    username: z.string().min(3, 'Username must be at least 3 characters').optional().or(z.literal('')),
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

type RegisterForm = z.infer<typeof registerSchema>

const passwordRequirements = [
  { label: 'At least 8 characters', test: (p: string) => p.length >= 8 },
  { label: 'One uppercase letter', test: (p: string) => /[A-Z]/.test(p) },
  { label: 'One lowercase letter', test: (p: string) => /[a-z]/.test(p) },
  { label: 'One number', test: (p: string) => /[0-9]/.test(p) },
]

export function RegisterPage() {
  'use no memo'
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [apiKeyError, setApiKeyError] = useState<string | null>(null)
  const { register: registerUser } = useAuth()
  const { instanceInfo, isApiKeyRequired, isApiKeySet, setApiKey, clearApiKey, apiKey } = useInstance()
  const navigate = useNavigate()

  const addonName = instanceInfo?.addon_name || 'MediaFusion'

  // Initialize API key input from stored value (during render, not in effect)
  // Track whether we've done initial sync — prev value comparison fails for strings available on mount
  const [apiKeySynced, setApiKeySynced] = useState(false)
  if (apiKey && !apiKeySynced) {
    setApiKeySynced(true)
    setApiKeyInput(apiKey)
  }

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<RegisterForm>({
    resolver: zodResolver(registerSchema),
  })

  // eslint-disable-next-line react-hooks/incompatible-library -- react-hook-form's watch() is inherently incompatible with React Compiler
  const password = watch('password', '')

  const handleSaveApiKey = () => {
    if (!apiKeyInput.trim()) {
      setApiKeyError('API key is required')
      return
    }
    setApiKey(apiKeyInput.trim())
    setApiKeyError(null)
  }

  const onSubmit = async (data: RegisterForm) => {
    try {
      setError(null)
      setApiKeyError(null)
      // Check if API key is required but not set
      if (isApiKeyRequired && !isApiKeySet) {
        setError('Please enter and save the API key first')
        return
      }
      await registerUser({
        email: data.email,
        username: data.username || undefined,
        password: data.password,
      })
      navigate('/', { replace: true })
    } catch (err) {
      // Check if error is due to invalid API key
      if (err instanceof ApiRequestError && err.status === 401) {
        const errorDetail = err.data?.detail || err.message
        if (errorDetail.toLowerCase().includes('api key')) {
          setApiKeyError('Invalid API key. Please check and update it.')
          setError('Invalid API key. Please update your API key and try again.')
          // Clear the stored API key so user can enter a new one
          clearApiKey()
          setApiKeyInput('')
          return
        }
      }

      // For other errors, show the error message
      const errorMessage = err instanceof Error ? err.message : 'Registration failed'
      setError(errorMessage)
    }
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
        {/* Spotlight effect */}
        <div className="absolute top-0 right-1/4 w-[600px] h-[600px] bg-primary/5 rounded-full blur-3xl" />
        <div className="absolute bottom-0 left-1/4 w-[500px] h-[500px] bg-primary/3 rounded-full blur-3xl" />
        {/* Subtle grid */}
        <div
          className="absolute inset-0 opacity-[0.015]"
          style={{
            backgroundImage: `linear-gradient(hsl(var(--foreground) / 0.15) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--foreground) / 0.15) 1px, transparent 1px)`,
            backgroundSize: '80px 80px',
          }}
        />
      </div>

      {/* Left side - Form */}
      <div className="flex-1 flex items-center justify-center p-6">
        <Card className="w-full max-w-md animate-fade-in">
          <CardHeader className="space-y-1 text-center">
            {/* Mobile logo */}
            <div className="flex justify-center mb-4 lg:hidden">
              <Logo size="lg" />
            </div>
            <CardTitle className="font-display text-2xl font-semibold">Create an account</CardTitle>
            <CardDescription>Sign up to save your configurations and access more features</CardDescription>
          </CardHeader>
          <form onSubmit={handleSubmit(onSubmit)}>
            <CardContent className="space-y-4">
              {error && (
                <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive border border-destructive/20">
                  {error}
                </div>
              )}

              {/* API Key Section for Private Instances */}
              {isApiKeyRequired && (
                <div className="space-y-3 p-4 rounded-md bg-primary/5 border border-primary/20">
                  <div className="flex items-center gap-2 text-primary">
                    <Shield className="h-4 w-4" />
                    <span className="text-sm font-medium">Private Instance</span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    This is a private instance. Enter the API key provided by the instance owner.
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
                            // Clear error when user starts typing
                            if (apiKeyError) {
                              setApiKeyError(null)
                            }
                          }}
                          className={`pl-10 pr-10 ${
                            apiKeyError ? 'border-destructive focus-visible:ring-destructive' : ''
                          }`}
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
                        API key saved
                      </p>
                    )}
                  </div>
                </div>
              )}

              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="you@example.com"
                  autoComplete="email"
                  {...register('email')}
                />
                {errors.email && <p className="text-sm text-destructive">{errors.email.message}</p>}
              </div>

              <div className="space-y-2">
                <Label htmlFor="username">
                  Username <span className="text-muted-foreground text-xs">(optional)</span>
                </Label>
                <Input
                  id="username"
                  type="text"
                  placeholder="johndoe"
                  autoComplete="username"
                  {...register('username')}
                />
                {errors.username && <p className="text-sm text-destructive">{errors.username.message}</p>}
              </div>

              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    placeholder="••••••••"
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

                {/* Password requirements */}
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

              <div className="space-y-2">
                <Label htmlFor="confirmPassword">Confirm Password</Label>
                <div className="relative">
                  <Input
                    id="confirmPassword"
                    type={showConfirmPassword ? 'text' : 'password'}
                    placeholder="••••••••"
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
                {errors.confirmPassword && <p className="text-sm text-destructive">{errors.confirmPassword.message}</p>}
              </div>
            </CardContent>
            <CardFooter className="flex flex-col space-y-4">
              <Button type="submit" variant="gold" className="w-full" disabled={isSubmitting}>
                {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Create account
              </Button>
              <p className="text-sm text-muted-foreground text-center">
                Already have an account?{' '}
                <Link to="/login" className="text-primary hover:text-primary/80 font-medium">
                  Sign in
                </Link>
              </p>
              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t border-border/50" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-card px-2 text-muted-foreground">Or</span>
                </div>
              </div>
              <p className="text-sm text-muted-foreground text-center">
                <a href="/app/configure" className="text-primary hover:text-primary/80 font-medium">
                  Configure without an account
                </a>
              </p>
            </CardFooter>
          </form>
        </Card>
      </div>

      {/* Right side - Branding (hidden on mobile) */}
      <div className="hidden lg:flex lg:w-1/2 items-center justify-center p-12">
        <div className="max-w-lg space-y-8 animate-fade-in animate-delay-100">
          <Link to="/app/" className="flex items-center gap-3 hover:opacity-80 transition-opacity">
            <Logo size="xl" />
            <LogoText addonName={addonName} size="3xl" />
          </Link>
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight">
            Join the <LogoText addonName={addonName} size="4xl" /> Community
          </h1>
          <p className="text-lg text-muted-foreground">
            Create an account to sync your configurations across devices, contribute to the metadata database, and
            unlock premium features.
          </p>
          <div className="space-y-4">
            {[
              'Sync configurations across devices',
              'Contribute metadata and earn reputation',
              'Access download and watch history',
              'Manage RSS feeds and imports',
            ].map((feature, idx) => (
              <div key={idx} className="flex items-center gap-3 text-muted-foreground">
                <div className="h-6 w-6 rounded-md bg-primary/10 border border-primary/20 flex items-center justify-center">
                  <Clapperboard className="h-3 w-3 text-primary" />
                </div>
                {feature}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
