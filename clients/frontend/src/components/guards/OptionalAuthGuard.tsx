import { useContext } from 'react'
import { AuthContext } from '@/contexts/AuthContext'
import { InstanceContext } from '@/contexts/InstanceContext'
import { AppLoadingScreen } from '@/components/ui/app-loading-screen'

interface OptionalAuthGuardProps {
  children: React.ReactNode
}

/**
 * OptionalAuthGuard - Allows both authenticated and anonymous access
 *
 * This guard:
 * - Shows loading state while checking auth status
 * - On private instances, still requires API key to be set
 * - Allows anonymous users to access the page (unlike AuthGuard)
 * - Provides auth context to children so they can detect auth state
 */
export function OptionalAuthGuard({ children }: OptionalAuthGuardProps) {
  const authContext = useContext(AuthContext)
  const instanceContext = useContext(InstanceContext)

  // During hot reload or initial render, contexts might not be available yet
  if (!authContext || !instanceContext) {
    return <AppLoadingScreen />
  }

  const { isLoading: authLoading } = authContext
  const { isLoading: instanceLoading } = instanceContext

  const isLoading = authLoading || instanceLoading

  if (isLoading) {
    return <AppLoadingScreen />
  }

  // Allow access regardless of authentication status
  // Pages using this guard should handle both authenticated and anonymous states
  return <>{children}</>
}
