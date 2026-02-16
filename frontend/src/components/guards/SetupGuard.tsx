import { Navigate } from 'react-router-dom'
import { useContext } from 'react'
import { InstanceContext } from '@/contexts/InstanceContext'
import { Skeleton } from '@/components/ui/skeleton'

interface SetupGuardProps {
  children: React.ReactNode
}

/**
 * SetupGuard - Redirects all routes to /setup when initial setup is required.
 *
 * This guard checks the instance info to determine if the initial admin setup
 * has been completed. If setup_required is true, all routes are redirected
 * to the /setup wizard page.
 */
export function SetupGuard({ children }: SetupGuardProps) {
  const instanceContext = useContext(InstanceContext)

  if (!instanceContext) {
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

  const { setupRequired, isLoading } = instanceContext

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

  if (setupRequired) {
    return <Navigate to="/setup" replace />
  }

  return <>{children}</>
}
