import { Navigate, useLocation } from 'react-router-dom'
import { useMemo, useContext } from 'react'
import { AuthContext } from '@/contexts/AuthContext'
import { InstanceContext } from '@/contexts/InstanceContext'
import { Skeleton } from '@/components/ui/skeleton'

interface AuthGuardProps {
  children: React.ReactNode
}

export function AuthGuard({ children }: AuthGuardProps) {
  const authContext = useContext(AuthContext)
  const instanceContext = useContext(InstanceContext)
  const location = useLocation()

  // During hot reload or initial render, contexts might not be available yet
  // Show loading state until contexts are ready
  if (!authContext || !instanceContext) {
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

  const { isAuthenticated, isLoading: authLoading } = authContext
  const { isApiKeyRequired, isApiKeySet, isLoading: instanceLoading } = instanceContext

  const isLoading = authLoading || instanceLoading

  // Memoize the state object to prevent infinite re-renders
  const navigationState = useMemo(() => ({ from: location }), [location.pathname])

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

  // On private instances, require API key to be set before allowing access
  if (isApiKeyRequired && !isApiKeySet) {
    return <Navigate to="/login" state={navigationState} replace />
  }

  if (!isAuthenticated) {
    // Redirect to login page, saving the current location
    return <Navigate to="/login" state={navigationState} replace />
  }

  return <>{children}</>
}

