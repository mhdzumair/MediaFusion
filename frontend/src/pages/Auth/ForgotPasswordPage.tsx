import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Loader2, ArrowLeft, Mail, CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/ui/logo'
import { ThemeSelector } from '@/components/ui/theme-selector'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { authApi } from '@/lib/api/auth'
import { ApiRequestError } from '@/lib/api/client'

const forgotSchema = z.object({
  email: z.string().email('Please enter a valid email'),
})

type ForgotForm = z.infer<typeof forgotSchema>

export function ForgotPasswordPage() {
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ForgotForm>({
    resolver: zodResolver(forgotSchema),
  })

  const onSubmit = async (data: ForgotForm) => {
    try {
      setError(null)
      await authApi.forgotPassword(data.email)
      setSubmitted(true)
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(err.data?.detail || 'Something went wrong. Please try again.')
      } else {
        setError('Something went wrong. Please try again.')
      }
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
        {!submitted ? (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <Logo size="lg" />
              </div>
              <div className="flex justify-center mb-2">
                <Mail className="h-10 w-10 text-primary" />
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Forgot your password?</CardTitle>
              <CardDescription>
                Enter your email address and we&apos;ll send you a link to reset your password.
              </CardDescription>
            </CardHeader>
            <form onSubmit={handleSubmit(onSubmit)}>
              <CardContent className="space-y-4">
                {error && (
                  <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive border border-destructive/20">
                    {error}
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
              </CardContent>
              <CardFooter className="flex flex-col space-y-4">
                <Button type="submit" variant="gold" className="w-full" disabled={isSubmitting}>
                  {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Send reset link
                </Button>
                <Link
                  to="/login"
                  className="inline-flex items-center justify-center gap-1 text-sm text-muted-foreground hover:text-primary transition-colors"
                >
                  <ArrowLeft className="h-3.5 w-3.5" />
                  Back to login
                </Link>
              </CardFooter>
            </form>
          </>
        ) : (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-4">
                <CheckCircle2 className="h-12 w-12 text-emerald-500" />
              </div>
              <CardTitle className="font-display text-2xl font-semibold">Check your email</CardTitle>
              <CardDescription>
                If an account with that email exists, we&apos;ve sent a password reset link. Please check your inbox and
                spam folder.
              </CardDescription>
            </CardHeader>
            <CardFooter className="flex flex-col space-y-4">
              <Link to="/login" className="w-full">
                <Button variant="gold" className="w-full">
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
