import { useContext } from 'react'
import { AuthContext } from '@/contexts/AuthContext'
import { InstanceContext } from '@/contexts/InstanceContext'
import { Skeleton } from '@/components/ui/skeleton'

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

  const { isLoading: authLoading } = authContext
  const { isLoading: instanceLoading } = instanceContext

  const isLoading = authLoading || instanceLoading

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

  // Allow access regardless of authentication status
  // Pages using this guard should handle both authenticated and anonymous states
  return <>{children}</>
}
