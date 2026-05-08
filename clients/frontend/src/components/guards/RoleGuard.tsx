import { Navigate } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import type { UserRole } from '@/types'

interface RoleGuardProps {
  children: React.ReactNode
  requiredRole: UserRole
  fallback?: React.ReactNode
}

export function RoleGuard({ children, requiredRole, fallback }: RoleGuardProps) {
  const { hasMinimumRole, isLoading } = useAuth()

  if (isLoading) {
    return null
  }

  if (!hasMinimumRole(requiredRole)) {
    if (fallback) {
      return <>{fallback}</>
    }
    return <Navigate to="/dashboard" replace />
  }

  return <>{children}</>
}
