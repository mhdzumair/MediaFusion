import { Navigate } from 'react-router-dom'
import { useContext } from 'react'
import { InstanceContext } from '@/contexts/InstanceContext'
import { AppLoadingScreen } from '@/components/ui/app-loading-screen'

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
    return <AppLoadingScreen />
  }

  const { setupRequired, isLoading } = instanceContext

  if (isLoading) {
    return <AppLoadingScreen />
  }

  if (setupRequired) {
    return <Navigate to="/setup" replace />
  }

  return <>{children}</>
}
