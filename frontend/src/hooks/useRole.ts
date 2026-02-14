import { useAuth } from '@/contexts/AuthContext'
import type { UserRole } from '@/types'

export function useRole() {
  const { user, hasMinimumRole } = useAuth()

  return {
    role: user?.role ?? null,
    isAdmin: user ? hasMinimumRole('admin') : false,
    isModerator: user ? hasMinimumRole('moderator') : false,
    isPaidUser: user ? hasMinimumRole('paid_user') : false,
    isUser: user ? hasMinimumRole('user') : false,
    hasRole: (role: UserRole) => (user ? hasMinimumRole(role) : false),
  }
}

