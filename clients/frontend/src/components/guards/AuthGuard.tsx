import { Navigate, useLocation } from 'react-router-dom'
import { useMemo, useContext } from 'react'
import { AuthContext } from '@/contexts/AuthContext'
import { InstanceContext } from '@/contexts/InstanceContext'
import { AppLoadingScreen } from '@/components/ui/app-loading-screen'

interface AuthGuardProps {
  children: React.ReactNode
}

export function AuthGuard({ children }: AuthGuardProps) {
  const authContext = useContext(AuthContext)
  const instanceContext = useContext(InstanceContext)
  const location = useLocation()

  // Memoize the state object to prevent infinite re-renders (must be before any early returns)
  const navigationState = useMemo(() => ({ from: location }), [location])

  // During hot reload or initial render, contexts might not be available yet
  // Show loading state until contexts are ready
  if (!authContext || !instanceContext) {
    return <AppLoadingScreen />
  }

  const { isAuthenticated, isLoading: authLoading } = authContext
  const { isApiKeyRequired, isApiKeySet, isLoading: instanceLoading } = instanceContext

  const isLoading = authLoading || instanceLoading

  if (isLoading) {
    return <AppLoadingScreen />
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
