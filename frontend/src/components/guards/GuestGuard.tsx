import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { Skeleton } from '@/components/ui/skeleton'

interface GuestGuardProps {
  children: React.ReactNode
}

export function GuestGuard({ children }: GuestGuardProps) {
  const { isAuthenticated, isLoading } = useAuth()
  const location = useLocation()

  // Show loading state while checking authentication
  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="space-y-4 w-full max-w-md p-8">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      </div>
    )
  }

  // Only redirect if authenticated AND not already on a guest page
  // This prevents redirect loops when tokens are being cleared
  if (isAuthenticated) {
    const currentPath = location.pathname
    // Don't redirect if we're already on login/register (prevents loops during token clearing)
    if (currentPath === '/login' || currentPath === '/register') {
      // Still show the page content, but the user will be redirected by AuthGuard if needed
      return <>{children}</>
    }

    // Redirect to the page they came from, or dashboard
    const from = (location.state as { from?: { pathname?: string } })?.from?.pathname
    return <Navigate to={from || '/dashboard'} replace />
  }

  return <>{children}</>
}
