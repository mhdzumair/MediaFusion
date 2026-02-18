import { useState } from 'react'
import { Link, useSearchParams, useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Loader2, Eye, EyeOff, Check, KeyRound, CheckCircle2, ShieldX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/ui/logo'
import { ThemeSelector } from '@/components/ui/theme-selector'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { authApi } from '@/lib/api/auth'
import { ApiRequestError } from '@/lib/api/client'

const resetSchema = z
  .object({
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

type ResetForm = z.infer<typeof resetSchema>

const passwordRequirements = [
  { label: 'At least 8 characters', test: (p: string) => p.length >= 8 },
  { label: 'One uppercase letter', test: (p: string) => /[A-Z]/.test(p) },
  { label: 'One lowercase letter', test: (p: string) => /[a-z]/.test(p) },
  { label: 'One number', test: (p: string) => /[0-9]/.test(p) },
]

export function ResetPasswordPage() {
  'use no memo'
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const token = searchParams.get('token')

  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<ResetForm>({
    resolver: zodResolver(resetSchema),
  })

  // eslint-disable-next-line react-hooks/incompatible-library -- react-hook-form's watch() is inherently incompatible with React Compiler
  const password = watch('password', '')

  const onSubmit = async (data: ResetForm) => {
    if (!token) return
    try {
      setError(null)
      await authApi.resetPassword(token, data.password)
      setSuccess(true)
      setTimeout(() => navigate('/login', { replace: true }), 3000)
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(err.data?.detail || 'Password reset failed. The link may be expired or invalid.')
      } else {
        setError('Password reset failed. Please try again.')
      }
    }
  }

  // No token in URL
  if (!token) {
    return (
      <div className="relative min-h-screen flex items-center justify-center overflow-hidden">
        <div className="fixed top-4 right-4 z-50">
          <ThemeSelector />
        </div>
        <div className="fixed inset-0 -z-10">
          <div className="absolute inset-0 bg-background" />
        </div>
        <Card className="w-full max-w-md mx-4 animate-fade-in">
          <CardHeader className="text-center">
            <div className="flex justify-center mb-4">
              <ShieldX className="h-12 w-12 text-destructive" />
            </div>
            <CardTitle className="font-display text-2xl font-semibold">Invalid reset link</CardTitle>
            <CardDescription>
              This password reset link is missing or malformed. Please request a new one.
            </CardDescription>
          </CardHeader>
          <CardFooter className="flex flex-col space-y-4">
            <Link to="/forgot-password" className="w-full">
              <Button variant="gold" className="w-full">
                Request new reset link
              </Button>
            </Link>
          </CardFooter>
        </Card>
      </div>
    )
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
        {!success ? (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <Link to="/" className="hover:opacity-80 transition-opacity">
                  <Logo size="lg" />
                </Link>
              </div>
              <div className="flex justify-center mb-2">
                <KeyRound className="h-10 w-10 text-primary" />
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Set new password</CardTitle>
              <CardDescription>Enter your new password below.</CardDescription>
            </CardHeader>
            <form onSubmit={handleSubmit(onSubmit)}>
              <CardContent className="space-y-4">
                {error && (
                  <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive border border-destructive/20">
                    {error}
                  </div>
                )}

                <div className="space-y-2">
                  <Label htmlFor="password">New Password</Label>
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
                  <Label htmlFor="confirmPassword">Confirm New Password</Label>
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
                  {errors.confirmPassword && (
                    <p className="text-sm text-destructive">{errors.confirmPassword.message}</p>
                  )}
                </div>
              </CardContent>
              <CardFooter className="flex flex-col space-y-4">
                <Button type="submit" variant="gold" className="w-full" disabled={isSubmitting}>
                  {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Reset password
                </Button>
              </CardFooter>
            </form>
          </>
        ) : (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <CheckCircle2 className="h-12 w-12 text-emerald-500" />
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Password reset</CardTitle>
              <CardDescription>Your password has been updated successfully. Redirecting to login...</CardDescription>
            </CardHeader>
            <CardFooter className="flex flex-col space-y-4">
              <Link to="/login" className="w-full">
                <Button variant="gold" className="w-full">
                  Sign in now
                </Button>
              </Link>
            </CardFooter>
          </>
        )}
      </Card>
    </div>
  )
}
