import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { AppLoadingScreen } from '@/components/ui/app-loading-screen'

interface GuestGuardProps {
  children: React.ReactNode
}

export function GuestGuard({ children }: GuestGuardProps) {
  const { isAuthenticated, isLoading } = useAuth()
  const location = useLocation()

  // Show loading state while checking authentication
  if (isLoading) {
    return <AppLoadingScreen />
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
    const fromLocation = (location.state as { from?: { pathname?: string; search?: string; hash?: string } } | null)
      ?.from
    const from = `${fromLocation?.pathname || '/dashboard'}${fromLocation?.search || ''}${fromLocation?.hash || ''}`
    return <Navigate to={from} replace />
  }

  return <>{children}</>
}
